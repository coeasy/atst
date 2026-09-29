# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""错误契约的诚实门禁：文档点名的错误类，运行期必须真有其站点；反方向同理。

F-44 的原始形状是"错误码树里有从未发生的叶子"：幻影开关（F-43/F-46）的镜像面就是
幻影异常——用户按文档写 ``except``，那段代码永远不会执行。判据因此钉在"文档承诺 ⇒
有站点"这条方向上，并且每条豁免都必须挂一个仍然开着的 finding 编号——把"未接线"写进
契约是需要用户裁决的产品决定（F-24 的教训同形），而挂上编号至少让它无处可藏。

同一族的另一头是**幻影名**（第 44 步随 F-68 补上）：``docs/providers/*.md`` 的 §Errors
曾成片点名 ``CapabilityUnsupported``/``DataIntegrityError``/``RateLimited`` 一类从未存在于
任何代码的名字，而正向判据对它们完全无感——名字不在树里，就不在"文档承诺 ⇒ 有站点"的
输入集内。于是 :class:`TestPhantomNameGate` 反向钉一次：活文档的错误小节里，凡是被写成
类形状的引用，必须能在代码里找到那个类。

与 :func:`tests.support.field_readers.members_referenced` 的分工：那把尺子量的是
"有没有人引用这个名字"，而 ``except SomeError`` 与 ``__all__`` 里的名字都不是站点——
本文件要的判据必须能区分"提到"和"会发生"，所以自带一遍带投递口与 ``raise <变量>``
回溯的走查。

**构造点的识别按错误树成员判定，不按类名后缀**（第 44 步的教训）：第一版把非 ``raise``
位置的构造过滤成 ``Error|Warning|Violation`` 结尾的名字，于是
``on_error(BackpressureOverflow(...))``（``atst/streaming/base.py``）这样的真实投递
站点被整类看不见——台账据这条盲区把 `BackpressureOverflow` 记成"既无抛点也无投递"，
并让它占了 F-68 五个删除名额里的一个。
"""

from __future__ import annotations

import ast
import functools
import re
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "atst"
DOCS = ROOT / "docs"
LEDGER = "docs/archive/plans/REFACTOR_PLAN_V17_CLOSURE.md"

#: 审计文档不是对外承诺：台账、变更日志与归档本来就负责讨论"未接线"这件事本身。
AUDIT_DOC_PREFIXES = ("docs/REFACTOR", "CHANGELOG", "docs/archive")
#: 首页与安全说明也是对用户的口径，只是不在 ``docs/`` 下——第一版只扫 ``docs/``，
#: 于是 README 里"错误树"那一行的对外声称整个在射程外。
PROMISED_ROOT_DOCS = ("README.md", "SECURITY.md")
BACKTICK = re.compile(r"`([^`\n]+)`")
#: 构造后作为这些投递口实参交出去的错误，同样算站点（流式面的错误就是这么给订阅者的）。
DELIVERY_PREFIXES = ("on_error", "warn", "emit", "_emit")

#: 已登记、尚未裁决的占位叶子：类名 -> (finding 编号, 为什么本步接不了线)。
#: 撤销条件写在门禁里：一旦真出现站点，本表必须同步瘦身（否则又造出一张没人读的豁免表）。
#: 第 44 步按 F-68 裁决 (a) 清空：4 个零站点叶子已随 ``errors.py`` 删除，第 5 个
#: （``BackpressureOverflow``）本就有真实投递站点、豁免理由不成立。空表不是判据失效：
#: 新条目只有在"文档已承诺、运行期确实接不了线、且已在 §0.3 挂了开放 finding"三者同时
#: 成立时才允许进来，而它一旦站点接线就必须撤销（下一条判据守着）。
PROMISE_EXEMPTIONS: dict[str, tuple[str, str]] = {}

#: 抽象基类不进豁免表：``TransportError``/``StreamError``/``ProfileError`` 自身无站点，
#: 但承诺门禁按子树计数（子类有站点即算兑现），所以它们是正常条目而不是豁免。

# ---------------------------------------------------------------------------
# 反向往事：活文档不许点名不存在的错误类
# ---------------------------------------------------------------------------

#: 错误小节的标题形状。判据只覆盖"这一节是在讲错误"的位置——全仓 CamelCase 词太多
#: （``QueryPlan``/``ChannelSpec`` 都不是错误），把它们全部纳入只会逼出一张白名单。
ERROR_SECTION = re.compile(r"错误|异常|症状|排查|失败|Error|Exception", re.IGNORECASE)
#: 候选名形状 = 至少两个词峰的 CamelCase。``E1010``/``422``/``provider``/``HTTP`` 都不在这个形状里。
CAMEL_HUMP = re.compile(r"[A-Z][a-z0-9]+(?:[A-Z][a-z0-9]+)+")
#: ADR 是决策快照，记录的是当时的术语判定，不是现行契约——与
#: ``test_doc_code_consistency.py`` 对 ``archive``/``adr`` 的同一口径。
#: 因此 F-68 删掉的 E7050 在 ADR-013 §11 里仍以原文留存，另加了"后续修订"作废说明。
HISTORICAL_DOC_PREFIXES = AUDIT_DOC_PREFIXES + ("docs/adr",)
#: 唯一允许点名"已不存在的错误类"的小节：``docs/errors.md`` §一之二 退役登记。
#: 它自己的诚实由 :meth:`TestRetirementRegister` 把守——登记的名字必须确实不在树里。
RETIREMENT_HEADING = "一之二"


def _sections(text: str) -> list[tuple[str, list[str]]]:
    """切成 ``[(小节标题, 该节正文行)]``；文件头（第一个标题之前）算作空标题节。"""
    out: list[tuple[str, list[str]]] = [("", [])]
    for line in text.splitlines():
        if line.startswith("#"):
            out.append((line, []))
        else:
            out[-1][1].append(line)
    return out


def _live_docs() -> list[Path]:
    files = sorted(DOCS.rglob("*.md")) + [ROOT / name for name in PROMISED_ROOT_DOCS]
    return [
        path
        for path in files
        if path.is_file() and not _relative(path).startswith(HISTORICAL_DOC_PREFIXES)
    ]


def _relative(path: Path) -> str:
    return str(path.relative_to(ROOT)).replace("\\", "/")


@functools.cache
def _not_error_class_names() -> frozenset[str]:
    """合法的非错误 CamelCase 名：Python 内建 + 全仓 ``atst/`` 真实定义过的类。

    这一层是从代码现推的，不是手抄名单：``docs/errors.md`` 在错误小节里提到
    ``WarningCode``（``diagnostics.py`` 的枚举）不算说谎。反过来，第三方服务的
    endpoint 名常常长成 PascalCase（如东方财富移动端的 ``FundGradeDetail``），
    那不是本仓类名，得靠下面的小节豁免处理——这类文档一旦整体归档就自动出射程，
    豁免表也必须随之清空，不许留下无主的豁免。
    """
    import builtins

    names = {name for name in dir(builtins) if CAMEL_HUMP.fullmatch(name)}
    for path in sorted(SOURCE.rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ClassDef):
                names.add(node.name)
    return frozenset(names)


@functools.cache
def _error_section_candidates() -> dict[str, set[str]]:
    """``{CamelCase 候选名: {文件::小节}}``：活文档错误小节里点名的每一个类形状名字。"""
    found: dict[str, set[str]] = defaultdict(set)
    for path in _live_docs():
        relative = _relative(path)
        for title, body in _sections(path.read_text(encoding="utf-8")):
            if not ERROR_SECTION.search(title) or RETIREMENT_HEADING in title:
                continue
            for line in body:
                for token in BACKTICK.findall(line):
                    name = token.strip().rstrip(".,;:").removesuffix("()")
                    if CAMEL_HUMP.fullmatch(name):
                        found[name].add(f"{relative}::{title}")
    return found


#: 已知"看起来像类名但其实不是本仓类"的小节：key 是 ``文件::小节标题``，
#: 必须与 :meth:`TestPhantomNameGate.test_section_exemptions_are_still_needed` 同时撤销。
#: 第 15 轮（V18 R-15）清空：唯一的条目挂在 ``docs/archive/parity/tiantian_fund_extensions.md`` §六，
#: 该文档连同另外 4 份现在时引用已删除 ``UnifiedQuoteAPI`` 门面的对标件一起移入
#: ``docs/archive/parity/``，落在 :data:`HISTORICAL_DOC_PREFIXES` 射程之外。
#: 与 :data:`PROMISE_EXEMPTIONS` 同形：空表不是判据失效，新条目只有在
#: "确属第三方命名、所在文档仍在活文档射程内"时才允许进来。
SECTION_NAME_EXEMPTIONS: dict[str, str] = {}


@functools.cache
def _phantom_doc_names() -> dict[str, set[str]]:
    """``{幻影名: {文件::小节}}``——错误小节点名、代码里却不存在，且所在小节未获豁免。"""
    allowed = _not_error_class_names() | _error_names()
    found: dict[str, set[str]] = {}
    for name, where in _error_section_candidates().items():
        if name in allowed:
            continue
        unexempted = {location for location in where if location not in SECTION_NAME_EXEMPTIONS}
        if unexempted:
            found[name] = unexempted
    return found


@functools.cache
def _retired_names() -> frozenset[str]:
    """退役登记表格第一列点名的类名。"""
    errors_doc = (DOCS / "errors.md").read_text(encoding="utf-8")
    for title, body in _sections(errors_doc):
        if RETIREMENT_HEADING not in title:
            continue
        rows = [line for line in body if line.startswith("|")]
        return frozenset(
            name
            for line in rows[2:]  # 表头 + 分隔行
            for chunk in BACKTICK.findall(line.split("|")[1])
            if (name := chunk.strip()) and CAMEL_HUMP.fullmatch(name)
        )
    return frozenset()


def _name(target: ast.expr | None) -> str:
    if isinstance(target, ast.Call):
        return _name(target.func)
    if isinstance(target, ast.Name):
        return target.id
    if isinstance(target, ast.Attribute):
        return target.attr
    return ""


def _module_sites(tree: ast.AST, error_names: frozenset[str]) -> list[tuple[str, str]]:
    """单个模块的站点表 ``[(类名, raise|raise-var|delivered|other)]``。

    ``raise X(...)`` 的内层 Call 只算一次抛点；``raise <变量>`` 的（web 层重试环的
    ``last_exc`` 就是这个形状）顺着同作用域赋值回溯到构造点——第一版探针没做这一步，
    把已经接线的 ``AntiSpiderBlocked`` 虚报成了幽灵。

    非 ``raise`` 位置的构造按**错误树成员**筛（``error_names``），不按类名后缀筛：
    后缀是一种猜测，而 ``on_error(BackpressureOverflow(...))`` 这条真实投递站点正是被
    ``Error|Warning|Violation`` 后缀判据抹掉的。
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
        if built not in error_names:
            continue
        sites.append((built, "delivered" if id(node) in delivered else "other"))
    return sites


def _children(parents: dict[str, list[str]]) -> dict[str, list[str]]:
    children: dict[str, list[str]] = defaultdict(list)
    for cls, bases in parents.items():
        for base in bases:
            children[base].append(cls)
    return children


@functools.cache
def _class_bases() -> dict[str, list[str]]:
    parents: dict[str, list[str]] = {}
    for path in sorted(SOURCE.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                parents[node.name] = [ast.unparse(b).split(".")[-1] for b in node.bases]
    return parents


@functools.cache
def _error_names() -> frozenset[str]:
    """错误树成员：``TdxError`` 子树 + 仓内 ``Warning`` 子树，全部由继承关系现推。"""
    parents = _class_bases()
    children = _children(parents)
    roots = {"TdxError"} | {
        cls
        for cls, bases in parents.items()
        if set(bases) & {"Warning", "UserWarning", "Exception"}
    }
    names: set[str] = set()
    for root in roots:
        names.add(root)
        names |= _subtree(root, children, set())
    return frozenset(names)


def _parents_and_sites() -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    """全仓一遍走查：继承表 + 按类聚合的站点表。

    三条判据共用同一份扫描结果，所以缓存一次——每多跑一遍就是全仓 191 个模块重解析。
    """
    return _class_bases(), _scan_sites()


@functools.cache
def _scan_sites() -> dict[str, list[str]]:
    """两遍走查：先由继承关系推出错误树，再只统计树内成员的站点。

    分成两遍是因为"是不是错误"这件事只能从代码自身推导——任何名字后缀清单都会把
    新起的类名（``...Overflow``、``...Rejected``）静默排除在判据之外。
    """
    names = _error_names()
    sites: dict[str, list[str]] = defaultdict(list)
    for path in sorted(SOURCE.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for cls, kind in _module_sites(tree, names):
            sites[cls].append(kind)
    return sites


def _subtree(root: str, children: dict[str, list[str]], seen: set[str]) -> set[str]:
    for child in children.get(root, []):
        if child not in seen:
            seen.add(child)
            _subtree(child, children, seen)
    return seen


@functools.cache
def _documented_classes() -> dict[str, set[str]]:
    """``{错误类名: {点名它的对外文档}}``——只认反引号包裹且确在错误树里的名字。

    树成员由 :func:`_error_names` 从代码现推，不再手写名单：手写的 ``{"TdxError",
    "CompatibilityWarning"}`` 在 ``CompatibilityWarning`` 被删掉后自己变成了幻影名。
    """
    tree = _error_names()
    promised: dict[str, set[str]] = defaultdict(set)
    for path in sorted(DOCS.rglob("*.md")) + sorted(ROOT / name for name in PROMISED_ROOT_DOCS):
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
        #: 只靠投递给用户的类必须被算成站点：它们不 ``raise``，名字也不以 ``Error`` 结尾，
        #: 是后缀判据唯一会漏掉的那一类——F-68 的误计数正是从这里来的。
        assert "BackpressureOverflow" in wired, (
            "atst/streaming/base.py 的 on_error(BackpressureOverflow(...)) 没被算成站点，"
            "投递口扫描又失效了"
        )
        promised = _documented_classes()
        assert len(promised) > 30, f"文档承诺只扫到 {len(promised)} 类，点名扫描失效"
        #: 反向判据的自检：错误小节里连真实存在的类都扫不到，那条判据就是在空转。
        live = {name for name in _error_section_candidates() if name in _error_names()}
        assert len(live) > 25, f"错误小节只扫到 {len(live)} 个树内类名，反向扫描失效"
        assert "FreshnessViolation" in live, (
            "反向扫描看不见 docs/errors.md 与 docs/providers/*.md 都点名的 FreshnessViolation，"
            "小节过滤条件已经空转"
        )

    def test_every_documented_error_class_is_wired_or_exempt(self) -> None:
        parents, sites = _parents_and_sites()
        children = _children(parents)

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


class TestPhantomNameGate:
    """反向往事：文档点名的错误类必须存在——F-68 那一半是"树里有名字却不会发生"，
    这一半是"名字连树都不在"，用户照后者写 ``except`` 同样永远不会执行。
    """

    def test_live_docs_never_name_a_class_that_does_not_exist(self) -> None:
        phantoms = _phantom_doc_names()
        assert phantoms == {}, "活文档的错误小节点名了代码里不存在的类（幻影名）：" + "; ".join(
            f"{name} <- {sorted(where)}" for name, where in sorted(phantoms.items())
        )

    def test_section_exemptions_are_still_needed(self) -> None:
        """豁免表不许变成新的沉淀：所在小节不再产生幻影名，就该撤销。"""
        allowed = _not_error_class_names() | _error_names()
        candidates = _error_section_candidates()
        for key in SECTION_NAME_EXEMPTIONS:
            used = [
                name for name, where in candidates.items() if name not in allowed and key in where
            ]
            assert used, f"{key} 的小节豁免已无对象，请撤销（曾为 endpoint 名那类误报）"


class TestRetirementRegister:
    """``docs/errors.md`` §一之二 是唯一允许点名退役类的地方，它自己也要说真话。"""

    def test_the_register_is_readable(self) -> None:
        assert _retired_names(), (
            "退役登记表读不出任何名字：表格形状或小节标题变了，幻影名判据就此失去唯一的豁免出口"
        )

    def test_register_lists_only_names_that_are_really_gone(self) -> None:
        still_alive = sorted(_retired_names() & _error_names())
        assert still_alive == [], (
            f"退役登记表里的 {still_alive} 其实还在错误树里，登记表已失真：要么删条目要么改名"
        )
