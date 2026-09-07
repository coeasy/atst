# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""模块可达性门禁（工业审计 F5 固化）。

AST 静态扫描 tstdx/ 全部模块的 import 边（含 tstdx/__init__.py 的 _LAZY
字符串边），从进程入口种子出发 BFS，报告**不可达且不在处置白名单**的模块。

已知盲区（由白名单显式收编，不允许新增未登记项）：
* ``python -m`` 入口（tstdx.tools.* / tstdx.cli）——已作为种子直接可达；
* 函数体内的惰性 import——脚本识别全部 import 节点（含函数内），无此盲区；
* ``importlib``/getattr 动态导入——若存在须登记白名单并注明理由。

用法::

    python scripts/audit_reachability.py            # 报告
    python scripts/audit_reachability.py --strict   # 有未登记孤儿时 exit 1

白名单：scripts/_reach_allow.txt，每行 ``模块.dotted.path  # 处置理由``。
"""

from __future__ import annotations

import argparse
import ast
import contextlib
import sys
from pathlib import Path

# Windows 控制台默认 GBK，成功提示里的 ✓ 会触发 UnicodeEncodeError，使门禁在
# 「零未登记孤儿」时反而以非零码退出。统一重配 stdout 为 UTF-8（replace 兜底），
# 绝不让打印本身决定退出码。
with contextlib.suppress(Exception):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "tstdx"
ALLOW = ROOT / "scripts" / "_reach_allow.txt"

# 进程入口种子（console script / python -m / 顶层包）
SEEDS = {
    "tstdx",
    "tstdx.cli",
    "tstdx.integration.http_server",  # tstdx serve 子命令以外亦有 uvicorn 直挂场景
    "tstdx.integration.ws_server",  # python -m tstdx.integration.ws_server
    "tstdx.integration.mcp_server",  # python -m / stdio 客户端拉起
}


def _ancestors(mod: str) -> list[str]:
    """子模块导入会触发全部父包 import——可达性必须向父链传播。"""
    parts = mod.split(".")
    return [".".join(parts[: i + 1]) for i in range(len(parts))]


def _mod_name(path: Path) -> str:
    rel = path.relative_to(ROOT).with_suffix("")
    parts = list(rel.parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _collect_modules() -> tuple[dict[str, Path], set[str]]:
    mods: dict[str, Path] = {}
    packages: set[str] = set()
    for p in PKG.rglob("*.py"):
        name = _mod_name(p)
        mods[name] = p
        if p.name == "__init__.py":
            packages.add(name)
    return mods, packages


def _lazy_edges(init_path: Path) -> set[str]:
    """解析 tstdx/__init__.py 里 _LAZY 字典值的模块字符串边。"""
    edges: set[str] = set()
    try:
        tree = ast.parse(init_path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return edges
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id == "_LAZY" and isinstance(node.value, ast.Dict):
                    for v in node.value.values:
                        target = None
                        if isinstance(v, ast.Constant) and isinstance(v.value, str):
                            target = v.value
                        elif (
                            isinstance(v, ast.Tuple)
                            and v.elts
                            and isinstance(v.elts[0], ast.Constant)
                        ):
                            target = v.elts[0].value
                        if isinstance(target, str):
                            edges.add(target.split(":")[0])
    return edges


def _import_edges(tree: ast.Module, pkg: str, is_pkg: bool) -> set[str]:
    """一个模块的全部静态 import 目标（绝对 + 相对 + from-import 子模块）。

    要点：相对导入必须用 node.module 拼完整模块路径；且 ``__init__.py``
    自身代表其所在包，level=1 的 ``from .x`` 解析到 ``pkg.x`` 而非父包
    （普通模块的 ``from .x`` 才解析到父包的兄弟 ``sibling.x``）。
    """
    edges: set[str] = set()
    parts = pkg.split(".")
    # 相对层数基准：包模块去掉 (level-1) 层，普通模块去掉 level 层
    drop = 1 if is_pkg else 0

    def _base(level: int) -> str:
        cut = len(parts) - max(0, level - drop)
        return ".".join(parts[: max(0, cut)])

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                edges.add(a.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0:
                mod = node.module or ""
                edges.add(mod)
                for a in node.names:
                    edges.add(f"{mod}.{a.name}" if mod else a.name)
            else:
                prefix = _base(node.level)
                mod = f"{prefix}.{node.module}" if node.module else prefix
                edges.add(mod)
                for a in node.names:
                    edges.add(f"{mod}.{a.name}")
    return edges


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--strict", action="store_true", help="未登记孤儿 → exit 1")
    args = ap.parse_args()

    modules, packages = _collect_modules()
    allow: set[str] = set()
    if ALLOW.exists():
        for line in ALLOW.read_text(encoding="utf-8").splitlines():
            line = line.split("#", 1)[0].strip()
            if line:
                allow.add(line)

    # 建图：模块 → 依赖模块集合（只保留指向 tstdx 包内的边）
    graph: dict[str, set[str]] = {}
    for name, path in modules.items():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError):
            graph[name] = set()
            continue
        deps = {
            e for e in _import_edges(tree, name, name in packages) if e in modules or e in allow
        }
        graph[name] = deps

    # 包 __init__ 与子模块天然互为整体：子包 init 视为该包目录的连接点
    for name in list(modules):
        if name.endswith(".__init__"):
            graph.setdefault(name, set()).update(
                m for m in modules if m.startswith(name[: -len(".__init__")] + ".")
            )

    # BFS 从种子（__init__ 的边包含 _LAZY 与 import）
    reach: set[str] = set()
    stack = [s for s in SEEDS if s in modules]
    lazy = _lazy_edges(modules["tstdx"]) if "tstdx" in modules else set()
    while stack:
        m = stack.pop()
        if m in reach:
            continue
        reach.add(m)
        # 父包传播：导入 tstdx.a.b 即执行 tstdx.a 的 import 边
        for anc in _ancestors(m):
            if anc in modules and anc not in reach:
                reach.add(anc)
                stack.extend(graph.get(anc, set()))
        for d in graph.get(m, set()):
            if d not in reach:
                stack.append(d)
        if m == "tstdx":
            for d in lazy:
                for mm in modules:
                    if (mm == d or mm.startswith(d + ".")) and mm not in reach:
                        stack.append(mm)
        # 任何模块被 __init__ eager-import 时上面的图已覆盖

    # tools 与 cli 是进程入口：包内全部视为可达（python -m 场景）
    for m in modules:
        if m.startswith("tstdx.tools") or m == "tstdx.cli":
            reach.add(m)

    orphans = sorted(set(modules) - reach - allow)
    allowed_hit = sorted((set(modules) - reach) & allow)

    print(f"模块总数: {len(modules)}  可达: {len(reach)}  白名单豁免: {len(allowed_hit)}")
    for m in allowed_hit:
        print(f"  [allow] {m}")
    if orphans:
        print("未登记孤儿（须接线或删除）:")
        for m in orphans:
            print(f"  [ORPHAN] {m}")
        if args.strict:
            return 1
    else:
        print("无未登记孤儿 ✓")
    return 0


if __name__ == "__main__":
    sys.exit(main())
