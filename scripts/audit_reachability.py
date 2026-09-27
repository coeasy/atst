# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""模块可达性门禁（工业审计 F5 固化）。

AST 静态扫描 atst/ 全部模块的 import 边（含 atst/__init__.py 的 _LAZY
字符串边），从进程入口种子出发 BFS，报告**不可达且不在处置白名单**的模块。

已知盲区（由白名单显式收编，不允许新增未登记项）：
* ``python -m`` 入口（atst.tools.* / atst.cli）——已作为种子直接可达；
* 函数体内的惰性 import——脚本识别全部 import 节点（含函数内），无此盲区；
* ``importlib``/getattr 动态导入——若存在须登记白名单并注明理由。

用法::

    python scripts/audit_reachability.py            # 报告
    python scripts/audit_reachability.py --strict   # 有未登记孤儿时 exit 1

白名单：scripts/_reach_allow.txt，每行 ``模块.dotted.path  # 处置理由``。
白名单自身也是被门禁的对象（工业审计 F22）：一条豁免记录只有在**它豁免的模块确实
存在、确实不可达、且理由够长**时才有意义。下列六类缺陷在 ``--strict`` 下与孤儿同权重
失败——死记录会让将来重新变成孤儿的模块静默通过，过期记录则掩盖一次真实的断链：

* 指向不存在模块的死记录（模块被删/改名后忘记撤条目）；
* 指向**已可达**模块的过期记录（曾经需要豁免，现已接线却没人撤）；
* 理由短于 :data:`MIN_REASON_CHARS` 的条目；
* 理由里引用的仓内路径指针已经不存在（``[dead-pointer]``），或整条理由找不出一个
  可核验路径（``[no-pointer]``）——两者都让"谁消费它"退化成无法反驳的自由文本；
* 理由点名的 ``.py`` 文件其实**不触达**它所豁免的模块（``[weak-pointer]``，
  见 :func:`_pointer_defects`）——"某某测试覆盖它"必须真的覆盖它；
* 理由用反引号点名的**公共 API 名**不在该模块的静态命名空间里（``[dead-claim]``，
  见 :func:`_claim_defects`）——路径指针钉住"谁消费它"，这一格钉"它说自己是谁"；
* 重复条目。

种子表也在被核验之列：:data:`SEEDS` 里一个改了名的条目会被 BFS 的
``if s in modules`` 静默丢弃，整条服务面就此从可达集消失（``[dead-seed]``）。
"""

from __future__ import annotations

import argparse
import ast
import contextlib
import re
import sys
from pathlib import Path

# Windows 控制台默认 GBK，成功提示里的 ✓ 会触发 UnicodeEncodeError，使门禁在
# 「零未登记孤儿」时反而以非零码退出。统一重配 stdout 为 UTF-8（replace 兜底），
# 绝不让打印本身决定退出码。
with contextlib.suppress(Exception):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "atst"
ALLOW = ROOT / "scripts" / "_reach_allow.txt"

#: 一条豁免记录至少要说清"谁消费它 + 为什么生产链路不 import 它"，短于此即视为无效理由。
MIN_REASON_CHARS = 40

#: 理由里"谁消费它"的可核验指针：形如 ``tests/trade/``、``docs/api/README.md``、
#: ``atst/profile/detect.py`` 的仓内路径。长度门槛只保证理由**像**一句话，
#: 不保证它指向的东西还在——一条写着"tests/output/ 消费"而该目录已被改名的记录，
#: 和没写理由等价（F-67 的同形教训：把已失效的证据读成绿，比缺证据更糟）。
_CONSUMER_PATH = re.compile(r"[\w.\-]+(?:/[\w.\-]+)+/?")

#: 路径后缀白名单之外的斜杠串不是仓内路径（HTTP 路由、URI、命令行片段）。
_PATH_SUFFIXES = (".py", ".md", ".txt", ".yaml", ".yml", ".json", ".toml", ".bin")


def _consumer_pointers(reason: str) -> tuple[list[str], list[str]]:
    """把理由里的仓内路径指针分成 ``(存在, 已失效)`` 两堆。"""
    live: list[str] = []
    dead: list[str] = []
    for token in _CONSUMER_PATH.findall(reason):
        if not token.endswith("/") and not token.endswith(_PATH_SUFFIXES):
            continue
        if any(part in {"..", "http:", "https:"} for part in token.split("/")):
            continue
        (live if (ROOT / token).exists() else dead).append(token)
    return live, dead


#: 一条豁免记录里"它到底是什么"的可核验锚点：写成 `` `名字` `` 的反引号格。
#: 反引号是**刻意的记号**——不加记号就无法区分"公共 API 名"与"它讲的编码名"：
#: 取证探针按"括号内斜杠分隔的标识符形状"提取时，``atst.charset.encoding`` 那条
#: 写着 GB18030/GBK/Big5 的说明被当成三个符号声明而全部判错（3/3 误报）。
#: 判据也只在静态命名空间上算（定义、导入别名、``__all__``），**不 import 目标模块**：
#: `atst.output` 与 `atst.*.web` 一类模块要装 extras 才导得进来，
#: 让门禁去 import 可选依赖等于把"环境没装全"读成"豁免记录有缺陷"。
_SYMBOL_CLAIM = re.compile(r"`([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*)`")

#: 全清单至少要点名这么多符号格，少了说明记号被人擦掉、判据已经失明。
MIN_SYMBOL_CLAIMS = 30


def _module_namespace(path: Path) -> set[str]:
    """一个模块文件里静态可见的顶层名字：定义、导入别名（含 `as`）、``__all__`` 条目。"""
    names: set[str] = set()
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return names
    for node in tree.body:
        if isinstance(node, ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            names.update(t.id for t in node.targets if isinstance(t, ast.Name))
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
        elif isinstance(node, ast.Import):
            names.update((a.asname or a.name.split(".", 1)[0]) for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            names.update(a.asname or a.name for a in node.names if a.name != "*")
        elif isinstance(node, ast.If | ast.Try):
            # 条件/兜底导入（可选 extras、平台分支）里的名字同样是命名空间的一部分
            for sub in ast.walk(node):
                if isinstance(sub, ast.ImportFrom):
                    names.update(a.asname or a.name for a in sub.names if a.name != "*")
                elif isinstance(sub, ast.Import):
                    names.update((a.asname or a.name.split(".", 1)[0]) for a in sub.names)
        if (
            isinstance(node, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == "__all__" for t in node.targets)
            and isinstance(node.value, ast.List | ast.Tuple | ast.Set)
        ):
            names.update(
                elt.value
                for elt in node.value.elts
                if isinstance(elt, ast.Constant) and isinstance(elt.value, str)
            )
    return names


def _claim_defects(
    records: dict[str, str],
    modules: dict[str, Path],
    root: Path = ROOT,
    min_claims: int = MIN_SYMBOL_CLAIMS,
) -> list[str]:
    """理由点名的**符号**必须真的在该模块的静态命名空间里（``[dead-claim]``）。

    路径指针那一半已经由 :func:`_consumer_pointers` / :func:`_pointer_defects` 钉住
    （存在、且真的触达被豁免的模块）；这一半管的是"它说自己是公共 API"这句话本身：
    一条写着 ``CapabilityContract`` 而模块里其实叫 ``ProviderCapabilityContract`` 的记录，
    按图索骥的人一个也找不到。点名的符号格总数低于 :data:`MIN_SYMBOL_CLAIMS` 时另报
    ``[blind-claims]``——记号被批量删掉与判据从未存在过，在输出上必须区分得开。
    """
    defects: list[str] = []
    checked = 0
    for module in sorted(records):
        claims = sorted(
            c
            for c in set(_SYMBOL_CLAIM.findall(records[module]))
            # 反引号里的路径不是符号声明（`tests/trade/…py` 由指针那两格管）；
            # 带点号的格（`atst.profile.detect_profile`）是"别的模块的名字"，本格不判。
            if "/" not in c and "." not in c and not c.endswith((".py", ".md"))
        )
        if not claims:
            continue
        path = modules.get(module)
        if path is None:
            continue  # 模块本身不存在已由 [dead] 判红，这里不重复报
        namespace = _module_namespace(path)
        checked += len(claims)
        for claim in claims:
            if claim not in namespace:
                defects.append(
                    f"[dead-claim] {module}：理由点名的 `{claim}` 不在该模块的命名空间里"
                )
    if checked < min_claims:
        defects.append(
            f"[blind-claims] 全部豁免记录只点名了 {checked} 格符号（下限 {min_claims}）："
            f"要么记号被擦掉了，要么这条判据从未真正生效"
        )
    return defects


# 进程入口种子（console script / python -m / 顶层包）
# 名称必须是当前真实模块：v16 把 integration 服务面从 http_server/ws_server/
# mcp_server 换成了 runtime_*，写死的旧名会被 `if s in modules` 静默丢弃，
# 使整条服务面在图里消失（漏报为"不可达"的反向风险）。
SEEDS = {
    "atst",
    "atst.cli",
    "atst.integration.runtime_http",  # uvicorn 直挂 / atst serve
    "atst.integration.runtime_ws_server",  # python -m atst.integration.runtime_ws_server
    "atst.integration.mcp",  # python -m / stdio 客户端拉起
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
        else:
            live, dead = _consumer_pointers(reason)
            for token in dead:
                defects.append(
                    f"[dead-pointer] {module}：理由引用的 `{token}` 在磁盘上不存在，"
                    f"这条豁免的证据已经过期"
                )
            if not live and not dead:
                defects.append(
                    f"[no-pointer] {module}：理由没有任何可核验的仓内路径指针"
                    f"（tests/… · docs/… · atst/…），因此无人能证实谁在链外消费它"
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
    ``atst.web`` 的惰性导出边整体消失，把纯惰性 façade 子模块误判成孤儿。

    值可以是点号绝对路径（``"atst.batch"``、``("atst.web.session", "X")``），
    也可以是包内相对名（``"session"``，或带子包的 ``"jsl.adapters"``）；含 ``:``
    的 extras 目标取路径部分。
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

    def _targets(value: ast.expr) -> list[str]:
        raw: object = None
        if isinstance(value, ast.Constant):
            raw = value.value
        elif (
            isinstance(value, ast.Tuple) and value.elts and isinstance(value.elts[0], ast.Constant)
        ):
            raw = value.elts[0].value
        if not isinstance(raw, str):
            return []
        module = raw.split(":")[0]
        # "含点即绝对"是 V20 Phase 3 之前的旧假设：那时包内相对名都是单段
        # （``"session"``），带点的只可能是 ``"atst.batch"`` 这种绝对路径。按
        # Provider 归组后包内相对名也能带点（``"jsl.adapters"``），两种都给出候选，
        # 由调用方用"该模块是否存在"这一唯一判据挑出真实的那条边。
        return [module, f"{pkg}.{module}"]

    for node in ast.walk(tree):
        lazy = _value(node)
        if not isinstance(lazy, ast.Dict):
            continue
        for value in lazy.values:
            edges.update(_targets(value))
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


def _build_graph(
    modules: dict[str, Path], packages: set[str], allow: set[str]
) -> dict[str, set[str]]:
    """建图：模块 → 依赖模块集合（只保留指向 atst 包内或白名单内的边）。"""
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
    return graph


def _entrypoints(modules: dict[str, Path]) -> set[str]:
    """`python -m` 与 console-script 入口：静态图里永无对它们的 import 边，只能作种子。

    把它们当种子（而非登记豁免）是必要的：只把入口自身标可达，会让入口独占的
    依赖（``tools/capture.py`` → 交易日历）被误判成孤儿。

    ``__main__.py`` 按**文件名**判定而不是抄一份模块名清单：``python -m <包>`` 找的就是
    这个文件名，写死的名单会在每加一个入口时静默漏掉它——第 23 轮给 MCP 面补
    ``atst/integration/mcp/__main__.py`` 时，旧名单正会把新入口报成孤儿。
    """
    named = {"atst.cli"}
    return {
        m
        for m, path in modules.items()
        if m in named or m.startswith("atst.tools.") or path.name == "__main__.py"
    }


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


def _seed_defects(modules: set[str], seeds: set[str] = SEEDS) -> list[str]:
    """种子表与当前模块清单的矛盾。

    BFS 的种子写作 ``[s for s in seeds if s in modules]``——一个改了名的种子会被
    **静默丢弃**，于是它连同整条下游依赖一起从图里消失，而门禁只会把它们报成孤儿，
    报不到"种子本身已经烂了"这一格。v16 换名（http_server → runtime_*）就踩过一次，
    当时是靠人读到那行过滤才发现。种子缺失与孤儿同权重失败。
    """
    return [
        f"[dead-seed] SEEDS 里的 `{s}` 不是当前存在的模块：它已被改名或删除，"
        f"整条以它为根的链路从可达集里静默消失"
        for s in sorted(seeds - modules)
    ]


_PY_FILE = re.compile(r"[\w.\-]+(?:/[\w.\-]+)+\.py")


def _module_pointers(reason: str) -> list[str]:
    """理由里点名的**仓内 .py 文件**指针（tests/… 或 atst/…），路径归一成斜杠形式。"""
    return [t.replace("\\", "/") for t in _PY_FILE.findall(reason)]


def _file_imports(path: Path) -> set[str]:
    """一个仓内 .py 文件的静态 import 目标（绝对名）。"""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return set()
    return {
        e
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for e in (a.name for a in node.names)
    } | {
        e
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module
        for e in (node.module, *(f"{node.module}.{a.name}" for a in node.names))
    }


def _pointer_defects(
    records: dict[str, str],
    modules: dict[str, Path],
    graph: dict[str, set[str]],
    root: Path = ROOT,
) -> list[str]:
    """理由点名的 .py 文件必须真的触达被豁免的模块。

    ``[dead-pointer]`` 只证明"那个文件还在"，而一条写着"某某测试覆盖它"的理由，
    在该测试其实测的是**另一个同名族**时依旧绿——本轮实测抓到一例：某条豁免引用
    的测试文件覆盖的是同名不同模块的另一族。因此要求指针**有内容**：
    该文件直接 import 到目标模块（含父包 eager/惰性导出这一跳，即沿 ``graph`` 走），
    或在文本里以点号全名出现（``importlib`` 按字符串解析的边看不见，只认字面名）。
    """
    defects: list[str] = []
    for module in sorted(records):
        pointers = [
            p
            for p in _module_pointers(records[module])
            if (root / p).exists() and p != "conftest.py"
        ]
        if not pointers:
            continue  # 没点名 .py 文件的理由由 [dead-pointer]/[no-pointer] 那两格管
        if any(_reaches(root / p, module, modules, graph) for p in pointers):
            continue
        defects.append(
            f"[weak-pointer] {module}：理由点名的 {', '.join(sorted(set(pointers)))} "
            f"既不 import 也不按全名提到它——这条豁免的证据与它豁免的模块无关"
        )
    return defects


def _reaches(
    source: Path, module: str, modules: dict[str, Path], graph: dict[str, set[str]]
) -> bool:
    """``source`` 这个文件是否（按全名提及、或经 import 闭包）触达 ``module``。"""
    try:
        text = source.read_text(encoding="utf-8")
    except OSError:
        return False
    if re.search(rf"\b{re.escape(module)}\b", text):
        return True  # 字面全名：AST 看不见 importlib 按字符串解析的边
    pkg_module = _mod_name(source) if _in_pkg(source) else None
    if pkg_module is not None:
        frontier = set(graph.get(pkg_module, set()))
    else:
        frontier = _file_imports(source)  # tests/ 与 scripts/ 里的文件：只看绝对 import
    seen: set[str] = set()
    stack = list(frontier)
    while stack:
        dep = stack.pop()
        if dep in seen:
            continue
        seen.add(dep)
        if dep == module or module in _ancestors(dep):
            return True  # 命中自身，或命中它的一个子模块（父包导出即触达）
        stack.extend(graph.get(dep, set()))
    return False


def _in_pkg(path: Path) -> bool:
    try:
        return path.resolve().is_relative_to(PKG)
    except OSError:
        return False


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
    graph = _build_graph(modules, packages, allow)

    # BFS 从种子（包的边已含 eager import 与 _LAZY 惰性导出；入口见 _entrypoints）
    reach: set[str] = set()
    stack = [s for s in sorted(SEEDS | _entrypoints(modules)) if s in modules]
    while stack:
        m = stack.pop()
        if m in reach:
            continue
        reach.add(m)
        # 父包传播：导入 atst.a.b 即执行 atst.a 的 import 边
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
    record_defects += _seed_defects(set(modules))
    record_defects += _pointer_defects(records, modules, graph)
    record_defects += _claim_defects(records, modules)

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
