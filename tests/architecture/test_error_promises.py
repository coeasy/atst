# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""错误契约的诚实门禁：对外文档点名的错误类，运行期必须真有其站点。

F-44 的原始形状是"错误码树里有从未发生的叶子"：``errors.py`` 的 46 个类中 5 个叶子既不被 ``raise``、
也不作为投递口（``on_error(...)``）的实参交出去，其中 4 个是**面向用户的文档**明文承诺
的对外行为。幻影开关（F-43/F-46）的镜像面就是幻影异常：用户按文档写 ``except``，
那段代码永远不会执行。

判据因此钉在"文档承诺 ⇒ 有站点"这条方向上，并且每条豁免都必须挂一个仍然开着的
finding 编号——把"未接线"写进契约是需要用户裁决的产品决定（F-24 的教训同形），
而挂上编号至少让它无处可藏。

与 :func:`tests.support.field_readers.members_referenced` 的分工：那把尺子量的是
"有没有人引用这个名字"，而 ``except SourceUnavailable`` 与 ``__all__`` 里的名字都不是
站点——本文件要的判据必须能区分"提到"和"会发生"，所以自带一遍带投递口与
``raise <变量>`` 回溯的走查。
"""

from __future__ import annotations

import ast
import functools
import re
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "tstdx"
DOCS = ROOT / "docs"
LEDGER = "docs/REFACTOR_PLAN_V17_CLOSURE.md"

#: 审计文档不是对外承诺：台账、变更日志与归档本来就负责讨论"未接线"这件事本身。
AUDIT_DOC_PREFIXES = ("docs/REFACTOR", "CHANGELOG", "docs/archive")
BACKTICK = re.compile(r"`([^`\n]+)`")
#: 构造后作为这些投递口实参交出去的错误，同样算站点（流式面的错误就是这么给订阅者的）。
DELIVERY_PREFIXES = ("on_error", "warn", "emit", "_emit")

#: 已登记、尚未裁决的占位叶子：类名 -> (finding 编号, 为什么本步接不了线)。
#: 撤销条件写在门禁里：一旦真出现站点，本表必须同步瘦身（否则又造出一张没人读的豁免表）。
PROMISE_EXEMPTIONS: dict[str, tuple[str, str]] = {
    "SourceUnavailable": (
        "F-44",
        "§12 承诺的「选定 Provider 不可用」面：2100 个 (provider, capability) 组合里 0 个能走到"
        "执行器缺 binding 分支，Provider 真实失败由传输层原异常承担，而 §12 同时规定 Provider 的"
        " ``TdxError`` 必须原样保留——(a) 接线在这一面无落点，等 (b) 删除 / (c) 占位裁决",
    ),
    "ChecksumMismatch": (
        "F-44",
        "协议与传输层没有任何校验和读取点（``checksum|crc|xor`` 在 tstdx/protocol 与 tstdx/transport"
        " 命中 0 处），接线要先在解码层落校验判据",
    ),
    "UnknownCommand": (
        "F-44",
        "运行期不发送未知命令：唯一相关事实是 TDX 对未知命令回短帧（ ``transport/base.py`` 的注释），"
        "消费方只有 tools/capture 与 ProtocolSniffer 两条离线工具链",
    ),
    "BackpressureOverflow": (
        "F-44",
        " ``BackpressureQueue.put`` 的既定语义是丢最旧元素并计数，从不抛：文档承诺的溢出信号"
        "在该设计下不存在，要么改队列语义要么改文档口径",
    ),
    "CompatibilityWarning": (
        "F-44",
        "从未发射的 ``UserWarning``：与幻影开关同族，兼容性判定走 ``CompatibilityError`` 异常面",
    ),
}

#: 抽象基类不进豁免表：``TransportError``/``StreamError``/``ProfileError`` 自身无站点，
#: 但承诺门禁按子树计数（子类有站点即算兑现），所以它们是正常条目而不是豁免。


def _name(target: ast.expr | None) -> str:
    if isinstance(target, ast.Call):
        return _name(target.func)
    if isinstance(target, ast.Name):
        return target.id
    if isinstance(target, ast.Attribute):
        return target.attr
    return ""


def _module_sites(tree: ast.AST) -> list[tuple[str, str]]:
    """单个模块的站点表 ``[(类名, raise|raise-var|delivered|other)]``。

    ``raise X(...)`` 的内层 Call 只算一次抛点；``raise <变量>`` 的（web 层重试环的
    ``last_exc`` 就是这个形状）顺着同作用域赋值回溯到构造点——第一版探针没做这一步，
    把已经接线的 ``AntiSpiderBlocked`` 虚报成了幽灵。
    """
    sites: list[tuple[str, str]] = []
    raise_nodes: set[int] = set()
    delivered: set[int] = set()
    bindings: dict[int, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
    owner: dict[int, ast.AST] = {}

    def walk(node: ast.AST, scope: ast.AST) -> None:
        owner[id(node)] = scope
        inner = node if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) else scope
        if isinstance(node, ast.Assign | ast.AugAssign | ast.AnnAssign):
            value = getattr(node, "value", None)
            if isinstance(value, ast.Call):
                built = _name(value.func)
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for target in targets:
                    if isinstance(target, ast.Name) and built:
                        bindings[id(scope)][target.id].append(built)
        if isinstance(node, ast.Call) and _name(node.func).lower().startswith(DELIVERY_PREFIXES):
            delivered.update(id(arg) for arg in node.args if isinstance(arg, ast.Call))
        for child in ast.iter_child_nodes(node):
            walk(child, inner)

    walk(tree, tree)

    for node in ast.walk(tree):
        if isinstance(node, ast.Raise) and node.exc is not None:
            raise_nodes.add(id(node.exc))
            if isinstance(node.exc, ast.Call):
                raise_nodes.add(id(node.exc.func))
                sites.append((_name(node.exc.func), "raise"))
            else:
                var = _name(node.exc)
                for built in bindings[id(owner[id(node)])].get(var, []):
                    sites.append((built, "raise-var"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or id(node) in raise_nodes:
            continue
        built = _name(node.func)
        if not built.endswith(("Error", "Warning", "Violation")):
            continue
        sites.append((built, "delivered" if id(node) in delivered else "other"))
    return sites


def _parents_and_sites() -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    """全仓一遍走查：继承表 + 按类聚合的站点表。

    三条判据共用同一份扫描结果，所以缓存一次——每多跑一遍就是全仓 191 个模块重解析。
    """
    return _scan_source()


@functools.cache
def _scan_source() -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    parents: dict[str, list[str]] = {}
    sites: dict[str, list[str]] = defaultdict(list)
    for path in sorted(SOURCE.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                parents[node.name] = [ast.unparse(b).split(".")[-1] for b in node.bases]
        for cls, kind in _module_sites(tree):
            sites[cls].append(kind)
    return parents, sites


def _subtree(root: str, children: dict[str, list[str]], seen: set[str]) -> set[str]:
    for child in children.get(root, []):
        if child not in seen:
            seen.add(child)
            _subtree(child, children, seen)
    return seen


@functools.cache
def _documented_classes() -> dict[str, set[str]]:
    """``{错误类名: {点名它的对外文档}}``——只认反引号包裹且确在错误树里的名字。"""
    parents, _ = _parents_and_sites()
    children: dict[str, list[str]] = defaultdict(list)
    for cls, bases in parents.items():
        for base in bases:
            children[base].append(cls)
    tree = {"TdxError", "CompatibilityWarning"} | _subtree("TdxError", children, set())
    promised: dict[str, set[str]] = defaultdict(set)
    for path in sorted(DOCS.rglob("*.md")):
        relative = str(path.relative_to(ROOT)).replace("\\", "/")
        if relative.startswith(AUDIT_DOC_PREFIXES):
            continue
        for chunk in BACKTICK.findall(path.read_text(encoding="utf-8")):
            token = chunk.strip().rstrip(".,;:").removesuffix("()")
            if token in tree:
                promised[token].add(relative)
    return promised


class TestPromiseGate:
    def test_the_scan_itself_finds_the_tree_the_docs_promise(self) -> None:
        """自检：扫不到站点或扫不到承诺，门禁就成了空转的形状检查。"""
        parents, sites = _parents_and_sites()
        assert len(parents) > 400, f"只扫到 {len(parents)} 个类，走查自身失效"
        wired = {
            cls for cls, kinds in sites.items() if set(kinds) & {"raise", "raise-var", "delivered"}
        }
        assert len(wired) > 25, f"只扫到 {len(wired)} 个有站点的错误类，判据失效"
        promised = _documented_classes()
        assert len(promised) > 30, f"文档承诺只扫到 {len(promised)} 类，点名扫描失效"

    def test_every_documented_error_class_is_wired_or_exempt(self) -> None:
        parents, sites = _parents_and_sites()
        children: dict[str, list[str]] = defaultdict(list)
        for cls, bases in parents.items():
            for base in bases:
                children[base].append(cls)

        def tally(cls: str) -> int:
            names = {cls} | _subtree(cls, children, set())
            return sum(
                1
                for name in names
                for kind in sites.get(name, [])
                if kind in ("raise", "raise-var", "delivered")
            )

        phantoms = sorted(
            cls
            for cls in _documented_classes()
            if tally(cls) == 0 and cls not in PROMISE_EXEMPTIONS
        )
        assert phantoms == [], (
            "对外文档点名了这些错误类，运行期却没有任何站点（幻影异常）："
            f"{phantoms}；要么接线，要么在 {LEDGER} 登记裁决后再进豁免表"
        )

    def test_exemptions_never_outlive_their_condition(self) -> None:
        """豁免表不许变成第二个没人读的名单：已接线、或编号不再成立，都要撤销豁免。"""
        _, sites = _parents_and_sites()
        ledger = (ROOT / LEDGER).read_text(encoding="utf-8")
        stale_wired: list[str] = []
        stale_finding: list[str] = []
        for cls, (finding, _) in PROMISE_EXEMPTIONS.items():
            #: 只看**自身**站点：豁免针对的是"这一类永远不会发生"，子类接线不算兑现。
            if any(kind in ("raise", "raise-var", "delivered") for kind in sites.get(cls, [])):
                stale_wired.append(cls)
            if f"{finding} " not in ledger:
                stale_finding.append(f"{cls}->{finding}")
        assert stale_wired == [], f"这些类已经接线，请撤销豁免：{sorted(stale_wired)}"
        assert stale_finding == [], f"豁免挂的 finding 编号已不在台账里：{stale_finding}"

    def test_currentness_verifier_is_where_the_freshness_promise_lives(self) -> None:
        """F-44 (a) 的落点单独钉一次：承诺的判据不许搬去无人读的地方。"""
        tree = ast.parse((SOURCE / "runtime" / "freshness.py").read_text(encoding="utf-8"))
        raised = any(
            isinstance(node, ast.Raise)
            and isinstance(node.exc, ast.Call)
            and _name(node.exc.func) == "FreshnessViolation"
            for node in ast.walk(tree)
        )
        records = any(
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "record_warning"
            for node in ast.walk(tree)
        )
        assert raised and records, (
            "freshness.py 不再既抛 FreshnessViolation 又发 currentness_unproven"
        )
