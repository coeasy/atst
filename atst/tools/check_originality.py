# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""原创性检查工具（Tier B / B4）：确保代码非抄袭、许可合规。

设计要点
--------
* **零依赖**：仅使用 Python 标准库（ast / dataclasses / pathlib / json / re / sys）。
* **AST 优先**：用 ast 模块提取 docstring 与 import，避免脆弱的正则误判。
* **多重信号**：许可头 + 许可声明 + 样板 docstring + 已知项目指纹 + 外部导入审计。
* **可修复**：``--fix`` 自动补上缺失的许可头。
* **CI 友好**：``--strict`` 在有任一问题时返回非零退出码。

命令行::

    python -m atst.tools.check_originality atst/
    python -m atst.tools.check_originality --strict --json atst/
    python atst/tools/check_originality.py --fix atst/codec/

结果结构
--------
:dataclass:`OriginalityResult` 每个文件一条，字段::

    file                文件路径
    license_detected    检出的 SPDX 许可 id（未知为 "unknown"）
    license_ok          是否在许可白名单内
    license_header      是否含预期的版权/许可头注释
    suspicious_patterns 触发的启发式指纹列表（抄袭证据）
    external_imports    非标准库、非 atst 内部的第三方导入列表
    is_original         综合判断：suspicious_patterns 为空即为 True
    notes               信息性提示（如外部项目引用，不影响 is_original）

许可白名单见 ``ORIGINALITY/LICENSE_ALLOWLIST.md``。
"""

from __future__ import annotations

import argparse
import ast
import contextlib
import json
import re
import sys
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path

from ._console import setup_console

__all__ = [
    "OriginalityResult",
    "ALLOWED_LICENSES",
    "check_file",
    "check_directory",
    "fix_file",
    "main",
]


# --------------------------------------------------------------------------- #
# 常量
# --------------------------------------------------------------------------- #

#: 许可白名单（SPDX id）。详见 ``ORIGINALITY/LICENSE_ALLOWLIST.md``。
ALLOWED_LICENSES: frozenset[str] = frozenset(
    {
        "MIT",
        "BSD-2-Clause",
        "BSD-3-Clause",
        "Apache-2.0",
        "GPL-2.0",
        "GPL-3.0",
        "LGPL-2.1",
        "LGPL-3.0",
        "MPL-2.0",
    }
)

#: 默认许可头（``--fix`` 模式使用）。
DEFAULT_LICENSE_HEADER: str = (
    "# Copyright (c) 2026 atst contributors\n# Licensed under the MIT License\n"
)

#: 项目已知的可选外部依赖（来自 pyproject.toml）。
#: 预期外部依赖的顶层包名。这份名单的口径是**``atst/`` 真实 import 到的外部根**，
#: 不是"打包时声明过什么"：第 23 轮清幻影 extra 时量出来它两头都过期——
#: ``pydantic`` / ``mcp`` 全仓 0 处 import（``mcp`` 那格尤其误导，本库自己的子包就叫
#: ``atst.integration.mcp``），而 ``websockets`` / ``zstandard`` / ``tomli`` /
#: ``typing_extensions`` 四处真实 import 从没登记过，于是 ``unknown external import``
#: 的普查里长期挂着 5 条噪声。等式由
#: ``tests/architecture/test_declared_knobs.py::test_originality_external_import_whitelist_matches_reality``
#: 逐名核对，改一处 import 或加一个 extra 都要在这里同步，否则门禁先红。
KNOWN_EXTERNAL_IMPORTS: frozenset[str] = frozenset(
    {
        "pandas",
        "pyarrow",
        "duckdb",
        "httpx",
        "prometheus_client",
        "fastapi",
        "uvicorn",
        "websockets",
        "zstandard",
        "tomli",
        "typing_extensions",
    }
)

#: 预期许可头检测模式（任一命中即视为有头）。
_HEADER_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^\s*#.*[Cc]opyright.*\batst\b.*$", re.M),
    re.compile(r"^\s*#\s*SPDX-License-Identifier:", re.M),
    re.compile(
        r"^\s*#.*[Ll]icensed\s+under\b.*\b(MIT|BSD|Apache|GPL|LGPL|MPL)",
        re.M | re.I,
    ),
)

#: 许可检测模式（正则 → SPDX id），按优先级排列。
_LICENSE_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"SPDX-License-Identifier:\s*MIT\b", re.I), "MIT"),
    (re.compile(r"\bMIT\s+License\b", re.I), "MIT"),
    (re.compile(r"SPDX-License-Identifier:\s*BSD-2-Clause\b", re.I), "BSD-2-Clause"),
    (re.compile(r"BSD\s+2-Clause", re.I), "BSD-2-Clause"),
    (re.compile(r"SPDX-License-Identifier:\s*BSD-3-Clause\b", re.I), "BSD-3-Clause"),
    (re.compile(r"BSD\s+3-Clause", re.I), "BSD-3-Clause"),
    (re.compile(r"SPDX-License-Identifier:\s*Apache-2\.0\b", re.I), "Apache-2.0"),
    (re.compile(r"Apache\s+License,\s*Version\s*2\.0", re.I), "Apache-2.0"),
    (re.compile(r"SPDX-License-Identifier:\s*GPL-2\.0\b", re.I), "GPL-2.0"),
    (re.compile(r"\bGPL[- ]v?2\b", re.I), "GPL-2.0"),
    (re.compile(r"SPDX-License-Identifier:\s*GPL-3\.0\b", re.I), "GPL-3.0"),
    (re.compile(r"\bGPL[- ]v?3\b", re.I), "GPL-3.0"),
    (re.compile(r"SPDX-License-Identifier:\s*LGPL-2\.1\b", re.I), "LGPL-2.1"),
    (re.compile(r"\bLGPL[- ]v?2\.1\b", re.I), "LGPL-2.1"),
    (re.compile(r"SPDX-License-Identifier:\s*LGPL-3\.0\b", re.I), "LGPL-3.0"),
    (re.compile(r"\bLGPL[- ]v?3\b", re.I), "LGPL-3.0"),
    (re.compile(r"SPDX-License-Identifier:\s*MPL-2\.0\b", re.I), "MPL-2.0"),
    (re.compile(r"Mozilla\s+Public\s+License,\s*Version\s*2\.0", re.I), "MPL-2.0"),
)

#: 样板 docstring 检测模式。
_BOILERPLATE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^\s*pass\s*$", re.I),
    re.compile(r"^\s*(TODO|FIXME|XXX|HACK)\b", re.I),
    re.compile(r"not\s+implemented", re.I),
    re.compile(r"self[- ]explanatory", re.I),
    re.compile(r"see\s+(above|below)", re.I),
)

#: 已知项目指纹——抄袭证据（触发 ``is_original = False``）。
_SUSPICIOUS_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bPorted?\s+from\b", re.I), "comment mentions 'Ported from'"),
    (re.compile(r"\bCopied\s+from\b", re.I), "comment mentions 'Copied from'"),
    (re.compile(r"\bAdapted\s+from\b", re.I), "comment mentions 'Adapted from'"),
    (re.compile(r"\bDerived\s+from\b", re.I), "comment mentions 'Derived from'"),
    (re.compile(r"\bReimplemented\s+from\b", re.I), "comment mentions 'Reimplemented from'"),
    (re.compile(r"\bTaken\s+from\b", re.I), "comment mentions 'Taken from'"),
    (re.compile(r"\bExcerpted\s+from\b", re.I), "comment mentions 'Excerpted from'"),
    (re.compile(r"\bOriginal\s+author\s*:", re.I), "comment mentions 'Original author'"),
)

#: 已知项目指纹——信息性引用（不影响 ``is_original``）。
_INFORMATIONAL_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bmootdx\b", re.I), "reference to external project 'mootdx'"),
    (re.compile(r"\bpytdx\b", re.I), "reference to external project 'pytdx'"),
    (re.compile(r"\btdxpy\b", re.I), "reference to external project 'tdxpy'"),
    (re.compile(r"\btdx-hq\b", re.I), "reference to external project 'tdx-hq'"),
    (re.compile(r"\beasyquotation\b", re.I), "reference to external project 'easyquotation'"),
    (re.compile(r"\beasy_tdx\b", re.I), "reference to external project 'easy_tdx'"),
    (re.compile(r"\beltdx\b", re.I), "reference to external project 'eltdx'"),
)

#: 二进制文件扩展名（``check_directory`` 跳过）。
_BINARY_EXTENSIONS: frozenset[str] = frozenset(
    {
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".bmp",
        ".ico",
        ".pdf",
        ".zip",
        ".tar",
        ".gz",
        ".bz2",
        ".xz",
        ".7z",
        ".rar",
        ".exe",
        ".dll",
        ".so",
        ".dylib",
        ".a",
        ".o",
        ".obj",
        ".pyc",
        ".pyo",
        ".class",
        ".jar",
        ".war",
        ".bin",
        ".dat",
        ".db",
        ".sqlite",
        ".mp3",
        ".mp4",
        ".avi",
        ".mov",
        ".wav",
        ".woff",
        ".woff2",
        ".ttf",
        ".otf",
        ".eot",
    }
)

#: ``check_directory`` 跳过的目录名。
_SKIP_DIRS: frozenset[str] = frozenset(
    {
        "__pycache__",
        ".git",
        ".hg",
        ".svn",
        ".tox",
        ".nox",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".venv",
        "venv",
        "env",
        "node_modules",
        "build",
        "dist",
        ".workbuddy",
    }
)

#: ``--fix`` 模式可修复的文件扩展名。
_FIXABLE_EXTENSIONS: frozenset[str] = frozenset({".py", ".pyi"})


# --------------------------------------------------------------------------- #
# 结果结构
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class OriginalityResult:
    """单个文件的原创性检查结果。"""

    file: str
    #: 检出的 SPDX 许可 id；未检出为 ``"unknown"``。
    license_detected: str = "unknown"
    #: 检出许可是否在 :data:`ALLOWED_LICENSES` 白名单内。
    license_ok: bool = False
    #: 文件首部是否含预期的版权/许可头注释。
    license_header: bool = False
    #: 触发的启发式指纹描述列表（抄袭证据）。
    suspicious_patterns: list[str] = field(default_factory=list)
    #: 非标准库、非 atst 内部的第三方导入列表。
    external_imports: list[str] = field(default_factory=list)
    #: 综合判断：``suspicious_patterns`` 为空即为 ``True``。
    is_original: bool = True
    #: 信息性提示（如外部项目引用），不影响 ``is_original``。
    notes: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------- #
# 内部辅助
# --------------------------------------------------------------------------- #
def _read_text(path: Path) -> str | None:
    """尝试以 UTF-8 读取文件；二进制或不可读返回 ``None``。"""
    try:
        data = path.read_bytes()
    except OSError:
        return None
    # 简单的二进制检测：含 NUL 字节即视为二进制。
    if b"\x00" in data[:4096]:
        return None
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        # 带替换解码——可能是编码问题而非二进制。
        text = data.decode("utf-8", errors="replace")
    return text.lstrip("\ufeff")  # 去掉 UTF-8 BOM


def _detect_license(text: str) -> str:
    """从文本中检测 SPDX 许可 id。"""
    for pattern, spdx in _LICENSE_PATTERNS:
        if pattern.search(text):
            return spdx
    return "unknown"


def _has_license_header(text: str) -> bool:
    """检查文件首部（前 30 行）是否含预期许可头。"""
    head = "\n".join(text.splitlines()[:30])
    return any(p.search(head) for p in _HEADER_PATTERNS)


def _extract_docstrings(tree: ast.AST) -> list[tuple[str, str]]:
    """从 AST 提取所有 docstring，返回 ``(name, docstring)`` 列表。"""
    results: list[tuple[str, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            ds = ast.get_docstring(node, clean=False)
            if ds is not None:
                results.append((getattr(node, "name", "<module>"), ds))
    return results


def _extract_comments(text: str) -> str:
    """提取 Python 源码中的注释文本（含行注释与内联注释）。"""
    lines = text.splitlines()
    comments: list[str] = []
    for line in lines:
        hash_pos = line.find("#")
        if hash_pos >= 0:
            comments.append(line[hash_pos:])
    return "\n".join(comments)


def _get_docstrings(tree: ast.AST) -> list[str]:
    """从 AST 提取所有 docstring 文本。"""
    docstrings: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            ds = ast.get_docstring(node, clean=False)
            if ds is not None:
                docstrings.append(ds)
    return docstrings


def _get_searchable_text(text: str, tree: ast.AST | None, is_python: bool) -> str:
    """获取用于指纹搜索的文本。

    对 Python 文件仅搜索注释与 docstring（避免匹配正则定义本身）；
    对其他文件搜索全文。
    """
    if is_python:
        parts: list[str] = [_extract_comments(text)]
        if tree is not None:
            parts.extend(_get_docstrings(tree))
        return "\n".join(parts)
    return text


def _extract_imports(tree: ast.AST) -> tuple[list[str], list[str]]:
    """提取导入，返回 ``(internal_imports, external_imports)``。"""
    internal: list[str] = []
    external: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".")[0]
                if root in sys.stdlib_module_names or root == "atst":
                    internal.append(alias.name)
                else:
                    external.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level and node.level > 0:
                internal.append(f".{'.' * (node.level - 1)}{node.module or ''}")
            elif node.module:
                root = node.module.split(".")[0]
                if root in sys.stdlib_module_names or root == "atst":
                    internal.append(node.module)
                else:
                    external.append(node.module)
    return internal, external


def _check_docstring_boilerplate(qualname: str, docstring: str) -> str | None:
    """返回 docstring 样板描述，无问题返回 ``None``。"""
    stripped = docstring.strip()
    if not stripped:
        return f"empty docstring in {qualname}"
    for pattern in _BOILERPLATE_PATTERNS:
        if pattern.search(stripped):
            return f"boilerplate docstring in {qualname}: {stripped!r}"
    if qualname != "<module>" and stripped == qualname:
        return f"docstring equals name in {qualname}: {stripped!r}"
    return None


def _analyze_patterns(
    text: str, tree: ast.AST | None, is_python: bool
) -> tuple[list[str], list[str]]:
    """分析可疑指纹与信息性提示。

    对 Python 文件仅搜索注释与 docstring，避免匹配正则定义本身；
    对其他文件搜索全文。

    返回 ``(suspicious, notes)`` 两个列表。
    """
    suspicious: list[str] = []
    notes: list[str] = []

    # 获取可搜索文本（注释 + docstring）
    search_text = _get_searchable_text(text, tree, is_python)

    # 1. 指纹——抄袭证据
    for pattern, desc in _SUSPICIOUS_PATTERNS:
        if pattern.search(search_text):
            suspicious.append(desc)

    # 2. 指纹——信息性引用
    for pattern, desc in _INFORMATIONAL_PATTERNS:
        if pattern.search(search_text):
            notes.append(desc)

    # 3. docstring 样板检测（AST）
    if tree is not None:
        for qualname, docstring in _extract_docstrings(tree):
            result = _check_docstring_boilerplate(qualname, docstring)
            if result is not None:
                suspicious.append(result)

    return suspicious, notes


# --------------------------------------------------------------------------- #
# 公开 API
# --------------------------------------------------------------------------- #
def check_file(path: str) -> OriginalityResult:
    """检查单个文件的原创性与许可合规。

    参数:
        path: 文件路径。

    返回:
        :class:`OriginalityResult` 实例。二进制或不可读文件返回
        无告警的结果（无法判断原创性，但无抄袭证据）。
    """
    p = Path(path)
    if not p.exists():
        return OriginalityResult(
            file=str(p),
            license_detected="unknown",
            license_ok=False,
            license_header=False,
            suspicious_patterns=["file_not_found"],
            is_original=False,
        )
    if p.is_dir():
        return OriginalityResult(
            file=str(p),
            license_detected="unknown",
            license_ok=True,
            license_header=True,
            is_original=True,
            notes=["path is a directory, not a file"],
        )

    text = _read_text(p)
    if text is None:
        # 二进制或不可读：无法检查，但无抄袭证据。
        return OriginalityResult(
            file=str(p),
            license_detected="unknown",
            license_ok=True,
            license_header=True,
            is_original=True,
        )

    license_detected = _detect_license(text)
    license_ok = license_detected in ALLOWED_LICENSES
    license_header = _has_license_header(text)

    # AST 分析（仅对 .py 文件）
    tree: ast.AST | None = None
    if p.suffix == ".py":
        with contextlib.suppress(SyntaxError):  # 语法错误不影响其他检查
            tree = ast.parse(text)

    suspicious, notes = _analyze_patterns(text, tree, p.suffix == ".py")

    # 外部导入审计
    external_imports: list[str] = []
    if tree is not None:
        _, ext = _extract_imports(tree)
        external_imports = sorted(ext)
        for imp in external_imports:
            root = imp.split(".")[0]
            if root not in KNOWN_EXTERNAL_IMPORTS:
                notes.append(f"unknown external import: {imp}")

    is_original = len(suspicious) == 0

    return OriginalityResult(
        file=str(p),
        license_detected=license_detected,
        license_ok=license_ok,
        license_header=license_header,
        suspicious_patterns=suspicious,
        external_imports=external_imports,
        is_original=is_original,
        notes=notes,
    )


def check_directory(path: str) -> list[OriginalityResult]:
    """递归检查目录下所有源文件的原创性。

    跳过 ``__pycache__``、``.pyc`` 及常见二进制文件。
    若传入路径为文件则委托给 :func:`check_file`。
    """
    root = Path(path)
    if not root.exists():
        return []
    if root.is_file():
        return [check_file(str(root))]

    results: list[OriginalityResult] = []
    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        if any(part in _SKIP_DIRS for part in p.parts):
            continue
        if p.suffix.lower() in _BINARY_EXTENSIONS:
            continue
        results.append(check_file(str(p)))
    return results


def fix_file(path: str) -> bool:
    """为缺失许可头的文件补上默认许可头。

    仅处理 ``.py`` 和 ``.pyi`` 文件。保留 shebang 行位置。

    返回:
        是否有修改。
    """
    p = Path(path)
    if not p.exists() or not p.is_file():
        return False
    if p.suffix.lower() not in _FIXABLE_EXTENSIONS:
        return False
    text = _read_text(p)
    if text is None or _has_license_header(text):
        return False

    # 找到插入点：shebang 之后，其他内容之前。
    lines = text.split("\n", 1)
    if lines[0].startswith("#!"):
        shebang = lines[0] + "\n"
        rest = lines[1] if len(lines) > 1 else ""
        new_text = shebang + DEFAULT_LICENSE_HEADER + "\n" + rest
    else:
        new_text = DEFAULT_LICENSE_HEADER + "\n" + text

    try:
        p.write_text(new_text, encoding="utf-8")
    except OSError:
        return False
    return True


# --------------------------------------------------------------------------- #
# CLI 输出
# --------------------------------------------------------------------------- #
def _summarize(results: list[OriginalityResult]) -> dict[str, int]:
    """统计检查结果摘要。"""
    total = len(results)
    return {
        "total": total,
        "original": sum(1 for r in results if r.is_original),
        "not_original": sum(1 for r in results if not r.is_original),
        "with_license": sum(1 for r in results if r.license_detected != "unknown"),
        "license_compliant": sum(1 for r in results if r.license_ok),
        "with_header": sum(1 for r in results if r.license_header),
        "missing_header": sum(1 for r in results if not r.license_header),
        "suspicious_findings": sum(len(r.suspicious_patterns) for r in results),
        "external_imports": sum(len(r.external_imports) for r in results),
    }


def _print_human(results: list[OriginalityResult]) -> None:
    """人类可读格式输出。"""
    for r in results:
        flags: list[str] = []
        if not r.license_header:
            flags.append("MISSING_HEADER")
        if r.license_detected != "unknown":
            flags.append(f"license={r.license_detected}")
            if not r.license_ok:
                flags.append("NOT_ALLOWLISTED")
        if not r.is_original:
            flags.append(f"SUSPICIOUS({len(r.suspicious_patterns)})")
        if r.external_imports:
            flags.append(f"imports={len(r.external_imports)}")

        status = "OK" if (r.is_original and r.license_ok and r.license_header) else "WARN"
        print(f"[{status}] {r.file}  {'  '.join(flags)}")
        for p in r.suspicious_patterns:
            print(f"        ! {p}")
        for p in r.notes:
            print(f"        i {p}")

    print()
    s = _summarize(results)
    print(
        f"Total: {s['total']}  "
        f"Original: {s['original']}  "
        f"License OK: {s['license_compliant']}  "
        f"Header OK: {s['with_header']}  "
        f"Suspicious: {s['suspicious_findings']}  "
        f"External imports: {s['external_imports']}"
    )


def _result_to_dict(result: OriginalityResult) -> dict[str, object]:
    """将结果转为 JSON 可序列化字典。"""
    return asdict(result)


# --------------------------------------------------------------------------- #
# CLI 入口
# --------------------------------------------------------------------------- #
def main(argv: Sequence[str] | None = None) -> int:
    """CLI 入口。返回进程退出码。

    参数:
        argv: 命令行参数（默认使用 ``sys.argv[1:]``）。

    返回:
        退出码：0 = 成功；1 = ``--strict`` 模式下有告警；2 = 用法错误。
    """
    setup_console()  # Windows GBK 控制台防乱码（UTF-8 + replace）
    parser = argparse.ArgumentParser(
        prog="check_originality",
        description=(
            "AST 原创性检查工具：检测许可头、样板 docstring、"
            "已知项目指纹与外部导入。零依赖，仅使用 Python 标准库。"
        ),
    )
    parser.add_argument("path", help="文件或目录路径")
    parser.add_argument(
        "--strict",
        action="store_true",
        help="CI 模式：任一问题返回非零退出码",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="JSON 格式输出",
    )
    parser.add_argument(
        "--fix",
        action="store_true",
        help="自动补上缺失的许可头（仅 .py / .pyi；仅当检出许可∈白名单或缺许可声明）",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    target = Path(args.path)
    if not target.exists():
        print(f"error: path not found: {target}", file=sys.stderr)
        return 2

    # 第一次检查
    results = check_directory(str(target)) if target.is_dir() else [check_file(str(target))]

    # 修复模式：先修复，再重新检查以获取更新后的状态。
    if args.fix:
        fixed = 0
        for r in results:
            if r.license_header:
                continue
            # 方向性安全（审计 §2-20）：仅当「检出许可 ∈ 白名单」或
            # 「缺许可声明（unknown）」时才补盖默认 MIT 头；
            # 检出非白名单许可的文件保留原许可，不做改写。
            if (r.license_detected == "unknown" or r.license_ok) and fix_file(r.file):
                fixed += 1
        if fixed:
            results = check_directory(str(target)) if target.is_dir() else [check_file(str(target))]
            if not args.json:
                print(f"Fix: {fixed} file(s) updated", file=sys.stderr)

    # 输出
    if args.json:
        payload = {
            "results": [_result_to_dict(r) for r in results],
            "summary": _summarize(results),
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        _print_human(results)

    # 严格模式：任一问题返回非零退出码。
    if args.strict:
        issues = [
            r for r in results if not r.is_original or not r.license_ok or not r.license_header
        ]
        return 1 if issues else 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
