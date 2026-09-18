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
白名单自身也是被门禁的对象（工业审计 F22）：一条豁免记录只有在**它豁免的模块确实
存在、确实不可达、且理由够长**时才有意义。下列四类缺陷在 ``--strict`` 下与孤儿同权重
失败——死记录会让将来重新变成孤儿的模块静默通过，过期记录则掩盖一次真实的断链：

* 指向不存在模块的死记录（模块被删/改名后忘记撤条目）；
* 指向**已可达**模块的过期记录（曾经需要豁免，现已接线却没人撤）；
* 理由短于 :data:`MIN_REASON_CHARS` 的条目；
* 重复条目。
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

#: 一条豁免记录至少要说清"谁消费它 + 为什么生产链路不 import 它"，短于此即视为无效理由。
MIN_REASON_CHARS = 40

# 进程入口种子（console script / python -m / 顶层包）
# 名称必须是当前真实模块：v16 把 integration 服务面从 http_server/ws_server/
# mcp_server 换成了 runtime_*，写死的旧名会被 `if s in modules` 静默丢弃，
# 使整条服务面在图里消失（漏报为"不可达"的反向风险）。
SEEDS = {
    "tstdx",
    "tstdx.cli",
    "tstdx.integration.runtime_http",  # uvicorn 直挂 / tstdx serve
    "tstdx.integration.runtime_ws_server",  # python -m tstdx.integration.runtime_ws_server
    "tstdx.integration.mcp",  # python -m / stdio 客户端拉起
}


def _load_allow(path: Path = ALLOW) -> tuple[dict[str, str], list[str]]:
    """解析白名单，返回 ``{模块: 理由}`` 与记录级缺陷清单。

    后出现的重复模块会覆盖前一条理由——静默丢记录，因此同样列为缺陷。
    """
    records: dict[str, str] = {}
    defects: list[str] = []
    if not path.exists():
        return records, defects
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        module, _, reason = line.partition("#")
        module = module.strip()
        reason = reason.strip()
        if not module:
            continue
        if module in records:
            defects.append(f"[dup] {module}：同一模块登记多次，后一条静默覆盖前一条")
        if len(reason) < MIN_REASON_CHARS:
            defects.append(
                f"[thin] {module}：理由仅 {len(reason)} 字符（须 ≥{MIN_REASON_CHARS}），"
                f"未说明谁消费它、为何生产链路不 import"
            )
        records[module] = reason
    return records, defects


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


def _lazy_edges(init_path: Path, pkg: str) -> set[str]:
    """解析包 ``__init__`` 里 ``_LAZY`` 映射值指向的模块边。

    两种写法都必须识别：``_LAZY = {...}``（Assign）与
    ``_LAZY: dict[str, str] = {...}``（AnnAssign）。只认前者会让根包与
    ``tstdx.web`` 的惰性导出边整体消失，把纯惰性 façade 子模块误判成孤儿。

    值可以是点号绝对路径（``"tstdx.batch"``、``("tstdx.web.session", "X")``），
    也可以是包内相对名（``"session"``）；含 ``:`` 的 extras 目标取路径部分。
    """
    edges: set[str] = set()
    try:
        tree = ast.parse(init_path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return edges

    def _value(node: ast.AST) -> ast.expr | None:
        if isinstance(node, ast.Assign):
            if any(isinstance(t, ast.Name) and t.id == "_LAZY" for t in node.targets):
                return node.value
            return None
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            return node.value if node.target.id == "_LAZY" else None
        return None

    def _target(value: ast.expr) -> str | None:
        raw: object = None
        if isinstance(value, ast.Constant):
            raw = value.value
        elif (
            isinstance(value, ast.Tuple) and value.elts and isinstance(value.elts[0], ast.Constant)
        ):
            raw = value.elts[0].value
        if not isinstance(raw, str):
            return None
        module = raw.split(":")[0]
        return module if "." in module else f"{pkg}.{module}"

    for node in ast.walk(tree):
        lazy = _value(node)
        if not isinstance(lazy, ast.Dict):
            continue
        for value in lazy.values:
            target = _target(value)
            if target is not None:
                edges.add(target)
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


def _entrypoints(modules: dict[str, Path]) -> set[str]:
    """`python -m` 与 console-script 入口：静态图里永无对它们的 import 边，只能作种子。

    把它们当种子（而非登记豁免）是必要的：只把入口自身标可达，会让入口独占的
    依赖（``tools/capture.py`` → 交易日历）被误判成孤儿。
    """
    named = {"tstdx.cli", "tstdx.__main__"}
    return {m for m in modules if m in named or m.startswith("tstdx.tools.")}


def _allowlist_defects(records: dict[str, str], modules: set[str], reach: set[str]) -> list[str]:
    """豁免记录与被豁免模块现状之间的矛盾（死记录 / 过期记录）。"""
    defects: list[str] = []
    for module in sorted(records):
        if module not in modules:
            defects.append(f"[dead] {module}：模块不存在，豁免记录已失效（应删除该行）")
        elif module in reach:
            defects.append(
                f"[stale] {module}：已从入口可达，保留豁免会把将来真正的断链读成绿（应删除该行）"
            )
    return defects


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--strict",
        action="store_true",
        help="存在未登记孤儿或失效豁免记录时 exit 1",
    )
    args = ap.parse_args()

    modules, packages = _collect_modules()
    records, record_defects = _load_allow()
    allow: set[str] = set(records)

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
        if name in packages:
            # 包的 ``_LAZY`` 导出是该包的静态边：按需属性访问在运行期等价于 import。
            deps |= {e for e in _lazy_edges(path, name) if e in modules or e in allow}
        graph[name] = deps

    # BFS 从种子（包的边已含 eager import 与 _LAZY 惰性导出；入口见 _entrypoints）
    reach: set[str] = set()
    stack = [s for s in sorted(SEEDS | _entrypoints(modules)) if s in modules]
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
        # 任何模块被 __init__ eager-import 时上面的图已覆盖

    orphans = sorted(set(modules) - reach - allow)
    allowed_hit = sorted((set(modules) - reach) & allow)
    record_defects += _allowlist_defects(records, set(modules), reach)

    print(f"模块总数: {len(modules)}  可达: {len(reach)}  白名单豁免: {len(allowed_hit)}")
    for m in allowed_hit:
        print(f"  [allow] {m}")
    if record_defects:
        print(f"白名单记录缺陷（{len(record_defects)} 项，须修记录本身）:")
        for d in sorted(record_defects):
            print(f"  [ALLOW-DEFECT] {d}")
    if orphans:
        print("未登记孤儿（须接线或删除）:")
        for m in orphans:
            print(f"  [ORPHAN] {m}")
    else:
        print("无未登记孤儿 ✓")
    if (orphans or record_defects) and args.strict:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
