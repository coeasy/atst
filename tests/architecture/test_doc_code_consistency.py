"""文档-代码一致性门禁（V17 Phase 4）。

活文档里的每一条 ``from tstdx... import X``、每一个 ``tstdx.a.b`` 引用、README
宣称的每个数字以及项目结构树里的每个 ``name/`` 与 ``name.py`` 条目，都必须与运行期
事实一致。历史快照（``docs/archive/``、``docs/adr/``、``DESIGN.md``）记录的是当时
语境，不参与门禁。
"""

from __future__ import annotations

import ast
import importlib
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

#: 参与门禁的活文档：仓库根说明 + docs/（排除归档与 ADR 历史语境）。
EXCLUDED_PARTS = {"archive", "adr", "node_modules", ".venv"}

NON_EXPORTS = {"__version__", "configure", "get_config"}


def active_docs() -> list[Path]:
    files = [ROOT / "README.md", ROOT / "SECURITY.md", ROOT / "CONTRIBUTING.md"]
    files.extend(sorted((ROOT / "docs").rglob("*.md")))
    return [
        path for path in files if path.is_file() and not EXCLUDED_PARTS.intersection(path.parts)
    ]


def fenced_code(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8")
    return re.findall(r"```[^\n]*\n(.*?)```", text, flags=re.S)


#: 只描述"当前事实"的文档：反引号里的 ``tstdx.*`` 必须是可解析的真实路径。
#: 迁移指南/发布说明/规划案里出现已删除路径是刻意的历史语境，不参与该检查。
FACT_DOC_PATHS = (
    "README.md",
    "docs/ARCHITECTURE.md",
    "docs/configuration.md",
    "docs/quickstart.md",
    "docs/errors.md",
    "docs/api/README.md",
    "docs/api/interfaces.md",
)
FACT_DOC_DIRS = ("docs/cookbook",)


def fact_docs() -> list[Path]:
    paths = [ROOT / rel for rel in FACT_DOC_PATHS]
    for rel in FACT_DOC_DIRS:
        paths.extend(sorted((ROOT / rel).glob("*.md")))
    return [path for path in paths if path.is_file()]


def import_nodes(source: str) -> list[ast.stmt]:
    try:
        tree = ast.parse(source)
    except SyntaxError:  # 伪代码块：只按行提取可辨识的 import 语句
        nodes: list[ast.stmt] = []
        for line in source.splitlines():
            if re.match(r"\s*(from|import)\s+\S", line):
                try:
                    nodes.extend(ast.parse(line).body)
                except SyntaxError:
                    continue
        return nodes
    return [n for n in tree.body if isinstance(n, ast.Import | ast.ImportFrom)]


# --------------------------------------------------------------------------
# 顶层导出面
# --------------------------------------------------------------------------


def test_root_export_map_and_dunder_all_agree() -> None:
    """``__all__`` 与惰性导入表必须是同一份事实，不得各有超集。"""
    import tstdx

    declared = set(tstdx.__all__) - NON_EXPORTS
    lazy = set(tstdx._LAZY)
    assert declared - lazy == set(), f"__all__ 有名字无法从根解析: {sorted(declared - lazy)}"
    assert lazy - declared == set(), f"惰性表偷偷扩大了公开面: {sorted(lazy - declared)}"


def test_every_documented_root_name_resolves() -> None:
    import tstdx

    for name in tstdx.__all__:
        assert hasattr(tstdx, name), f"tstdx.{name} 在 __all__ 里却不可解析"


# --------------------------------------------------------------------------
# 文档里的 import / 模块引用
# --------------------------------------------------------------------------


def _resolve(module: str, name: str | None) -> None:
    mod = importlib.import_module(module)
    if name is not None and not hasattr(mod, name):
        raise AttributeError(f"{module} 没有 {name}")


def test_markdown_import_statements_resolve() -> None:
    offenders: list[str] = []
    for path in active_docs():
        for block in fenced_code(path):
            for node in import_nodes(block):
                if isinstance(node, ast.ImportFrom):
                    if node.level or not node.module or not node.module.startswith("tstdx"):
                        continue
                    for alias in node.names:
                        if alias.name == "*":
                            continue
                        try:
                            _resolve(node.module, alias.name)
                        except Exception as exc:  # noqa: BLE001 - 报告而非中断
                            offenders.append(
                                f"{path.relative_to(ROOT)}: from {node.module} import "
                                f"{alias.name} -> {type(exc).__name__}: {exc}"
                            )
                else:
                    for alias in node.names:
                        if not alias.name.startswith("tstdx"):
                            continue
                        try:
                            _resolve(alias.name, None)
                        except Exception as exc:  # noqa: BLE001
                            offenders.append(
                                f"{path.relative_to(ROOT)}: import {alias.name} -> "
                                f"{type(exc).__name__}: {exc}"
                            )
    assert offenders == [], "\n".join(offenders)


def test_backticked_tstdx_paths_are_importable() -> None:
    offenders: list[str] = []
    pattern = re.compile(r"`(tstdx(?:\.[A-Za-z_][A-Za-z0-9_]*)+)`")
    for path in fact_docs():
        text = path.read_text(encoding="utf-8")
        for ref in sorted(set(pattern.findall(text))):
            if _resolves(ref):
                continue
            offenders.append(f"{path.relative_to(ROOT)}: 无法解析 `{ref}`")
    assert offenders == [], "\n".join(offenders)


def _resolves(dotted: str) -> bool:
    """``tstdx.a.b.C`` 可以是模块、类或属性——逐段回退解析即可。"""
    parts = dotted.split(".")
    for cut in range(len(parts), 0, -1):
        module = ".".join(parts[:cut])
        try:
            obj: object = importlib.import_module(module)
        except Exception:  # noqa: BLE001 - 继续尝试更短的模块前缀
            continue
        try:
            for attr in parts[cut:]:
                obj = getattr(obj, attr)
        except AttributeError:
            return False
        return True
    return False


# --------------------------------------------------------------------------
# README 宣称的数字
# --------------------------------------------------------------------------


def _readme() -> str:
    return (ROOT / "README.md").read_text(encoding="utf-8")


def _actual_facts() -> dict[str, int]:
    from tstdx import Client
    from tstdx.cli import build_parser
    from tstdx.integration.mcp import TOOLS
    from tstdx.integration.runtime_http import create_runtime_app
    from tstdx.providers import PROVIDERS

    parser = build_parser()
    commands: dict[str, object] = {}
    for action in parser._actions:
        if action.dest == "command" and action.choices:
            commands = dict(action.choices)
            break
    routes = [
        route
        for route in create_runtime_app().routes
        if getattr(route, "path", "").startswith("/v13/")
        and getattr(route, "methods", None)
        and route.methods & {"GET", "POST"}
    ]
    return {
        "cli_subcommands": len(commands),
        "http_endpoints": len(routes),
        "mcp_tools": len(TOOLS),
        "capabilities": len(Client().capabilities()),
        "providers": len(PROVIDERS.ids()),
    }


@pytest.mark.parametrize(
    ("fact", "pattern"),
    [
        ("cli_subcommands", r"CLI\s*(\d+)\s*子命令"),
        ("mcp_tools", r"MCP\s*(\d+)\s*工具"),
        ("http_endpoints", r"HTTP REST\s*(\d+)\s*端点"),
        ("capabilities", r"(\d+)\s*capability"),
        ("providers", r"(\d+)\s*个?\s*Provider 注册表"),
    ],
)
def test_readme_numbers_match_runtime(fact: str, pattern: str) -> None:
    claimed = {int(n) for n in re.findall(pattern, _readme())}
    assert claimed, f"README 不再声明 {fact}，门禁失效"
    assert claimed == {_actual_facts()[fact]}, f"README {fact}={sorted(claimed)} 与运行期事实不符"


# --------------------------------------------------------------------------
# README 项目结构树（审计 F-23）
# --------------------------------------------------------------------------

_BRANCH = re.compile(r"^\s*[├└]──\s+(.*)$")


def _readme_tree_names() -> list[str]:
    """树状图里出现的 ``name/`` 与 ``name.py`` 条目（注释列不算）。"""
    names: list[str] = []
    for line in _readme().splitlines():
        head = line.split("#", 1)[0]
        matched = _BRANCH.match(head)
        if not matched:
            continue
        names.extend(token for token in matched.group(1).split() if token.endswith(("/", ".py")))
    return names


def test_readme_tree_lists_existing_paths() -> None:
    names = _readme_tree_names()
    assert names, "README 不再含项目结构树，门禁失效"
    missing = [
        name
        for name in names
        if not (ROOT / "tstdx" / name.rstrip("/")).exists()
        and not (ROOT / "tstdx" / name.rstrip("/") / "__init__.py").exists()
    ]
    assert not missing, f"README 结构树指向磁盘不存在的路径：{missing}"


def test_readme_tree_covers_every_top_level_package() -> None:
    on_disk = {
        path.name + "/"
        for path in (ROOT / "tstdx").iterdir()
        if path.is_dir() and path.name != "__pycache__"
    }
    unlisted = sorted(on_disk - set(_readme_tree_names()))
    assert not unlisted, f"新增顶层包未写进 README 结构树：{unlisted}"


def test_readme_tree_lists_every_top_level_module() -> None:
    on_disk = {
        path.name
        for path in (ROOT / "tstdx").glob("*.py")
        if path.name == "__main__.py" or not path.name.startswith("_")
    }
    listed = set(_readme_tree_names())
    assert on_disk <= listed, f"新增顶层模块未写进 README 结构树：{sorted(on_disk - listed)}"
