# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""CLI 形状与文档示例的共享读数（v2 / W10 守卫合并后的公共底座）。

原 ``test_cli_reference_table`` / ``test_doc_code_consistency`` 两个投影型守卫已退役，
但 ``test_cli_kernel_seam`` 仍需"argparse 现值 → 叶子命令 → 落点"与"文档里的 CLI 示例"
这两份读数。抽到这里，只保留被消费的部分，不放投影断言本身。
"""

from __future__ import annotations

import ast
import inspect
import re
import shlex
from pathlib import Path
from typing import Any

from atst.cli.parser import build_parser

ROOT = Path(__file__).resolve().parents[2]

#: 参与门禁的活文档：仓库根说明 + docs/。
EXCLUDED_PARTS = {"archive", "adr", "node_modules", ".venv"}

_NONE = "—"


def active_docs() -> list[Path]:
    files = [ROOT / "README.md", ROOT / "SECURITY.md", ROOT / "CONTRIBUTING.md"]
    files.extend(sorted((ROOT / "docs").rglob("*.md")))
    return [
        path for path in files if path.is_file() and not EXCLUDED_PARTS.intersection(path.parts)
    ]


def fenced_code(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8")
    return re.findall(r"```[^\n]*\n(.*?)```", text, flags=re.S)


# ---------------------------------------------------------------------------
# argparse 现值 → 叶子命令
# ---------------------------------------------------------------------------
def _subparsers() -> dict[str, Any]:
    action = next(a for a in build_parser()._actions if a.dest == "command")
    return action.choices


def _group_action(sp: Any):
    """子命令组自身的那个位置参数（``feedback``/``fund``/``hosts``/``index``）。"""
    for a in sp._actions:
        if (
            a.choices
            and a.dest != "command"
            and all(not o.startswith("-") for o in a.option_strings)
        ):
            return a
    return None


def _positionals(sp: Any, *, skip: str) -> list[str]:
    return [
        a.metavar or a.dest
        for a in sp._actions
        if not a.option_strings and a.dest not in ("help", "command", skip)
    ]


def _flags(sp: Any) -> list[str]:
    return [
        "/".join(a.option_strings) for a in sp._actions if a.option_strings and a.dest != "help"
    ]


def _landing(fn: Any) -> str:
    """一支命令的落点：只读处理器源码，不读文档。"""
    source = inspect.getsource(fn)
    tree = ast.parse(source)
    names = {
        n.func.id
        for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
    }
    attrs = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    idents = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    seen = names | attrs | idents
    if "create_runtime_app" in seen:
        return "服务面宿主"
    if "_ClientRows" in names:
        return "内核·rows"
    if {"TdxClient", "get_client", "family_client"} & names:
        return "直连传输层"
    if {"FeedbackReporter", "UserStats"} & names:
        return "反馈"
    if {"resolve_hosts", "speedtest", "speedtest_and_save", "host_audit_main"} & names:
        return "传输·诊断"
    if {"list_universe", "universe_report", "asset_class_names"} & names:
        return "本地清单"
    if "__version__" in idents:
        return "元信息"
    if "Client" in seen:
        return "内核·typed" if "Client" in names else "元信息"
    raise AssertionError(f"{getattr(fn, '__name__', fn)} 的落点不在词汇表里——先扩判据，别猜")


def runtime_leaves() -> dict[str, dict[str, Any]]:
    """``{"bars": {"positional": ..., "flags": [...], "landing": ..., "handler": ...}, ...}``。"""
    out: dict[str, dict[str, Any]] = {}
    for name, sp in _subparsers().items():
        group = _group_action(sp)
        if group is None:
            func = sp._defaults["func"]
            out[name] = {
                "positional": " ".join(_positionals(sp, skip="")) or _NONE,
                "flags": _flags(sp),
                "landing": _landing(func),
                "handler": func.__name__,
            }
            continue
        for leaf, child in sorted(group.choices.items()):
            func = child._defaults.get("func") or sp._defaults.get("func")
            out[f"{name} {leaf}"] = {
                "positional": " ".join(_positionals(child, skip=group.dest)) or _NONE,
                "flags": _flags(child),
                "landing": _landing(func),
                "handler": func.__name__,
            }
    return out


# ---------------------------------------------------------------------------
# 文档里的 CLI 示例
# ---------------------------------------------------------------------------
_CMD_LINE = re.compile(r"^\s*(?:\$ )?atst(?:\.exe)?\s+(\S+)(.*)$")
_USAGE_SYNTAX = re.compile(r"\[--|<[^>\s]+>|\||…|\.\.\.")
_INLINE_CODE = re.compile(r"`([^`\n]+)`")


def _cli_examples() -> list[tuple[str, str, list[str] | None]]:
    """活文档中所有形如 ``atst <sub> …`` 的可执行示例（围栏块 + 行内代码）。"""
    found: list[tuple[str, str, list[str] | None]] = []
    for path in active_docs():
        text = path.read_text(encoding="utf-8")
        lines: list[str] = []
        for block in fenced_code(path):
            lines.extend(block.splitlines())
        for line in text.splitlines():
            lines.extend(_INLINE_CODE.findall(line))
        for line in lines:
            matched = _CMD_LINE.match(line.split("#", 1)[0].strip())
            if matched is None:
                continue
            rest = matched.group(2)
            if _USAGE_SYNTAX.search(rest):
                continue
            try:
                tokens: list[str] | None = [matched.group(1), *shlex.split(rest)]
            except ValueError:
                tokens = None
            found.append((path.relative_to(ROOT).as_posix(), line.strip(), tokens))
    return found
