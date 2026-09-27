"""G21：每个 ``while`` 都要能当场说出它凭什么停下来。

第 21 轮「主体流程全部联通 / 不存在死循环」这一遍的做法：不逐个人肉读，而是把
全仓 :mod:`tstdx` 的每个 ``while`` 语句现扫出来，按**形状**给它归类一个终止守卫；
归不出类的必须落在下面的命名豁免表里并写明理由，否则当场红。

四条形状守卫（第 1~3 条判据逐条核对：先证明尺子认得四种形状，再证明它会对没形状的循环
当场报错，最后才拿它数全包）：

``escape``
    循环体内有 ``break`` / ``return`` / ``raise``，且不穿过更内层的 ``for`` / ``while``
    ——内层循环里的 ``break`` 属于内层，拿它给外层作证是假绿。
``progress``
    循环条件读到的名字/属性在本循环体内被重新赋值（条件有人拧）。
``trampoline``
    外层是 ``try/except StopIteration`` 的生成器驱动循环（``gen.send`` 回来的值
    喂给下一轮，生成器耗尽即 ``StopIteration``）。这是本仓两处 trampoline 的形状。
``stop-flag``
    条件是 ``not <flag>`` / ``not <flag>.is_set()`` / ``not <flag>.wait(超时)``，
    且该 flag 在本文件里被赋值过或被 ``.set()`` / ``.clear()`` 动过——即"退出键确实存在"。

**这把尺子证不到什么，写清楚**：形状有守卫 ≠ 一定向前推进。本轮真正抓到的那格
停不下来，恰恰是形状完全合格的那一格（:meth:`tstdx.transport.ratelimit.TokenBucket.acquire`
的 ``while True`` 有 ``return``）：入口处的 ``requested > burst`` 容量守卫读的是**锁外**
的一次性快照，而 ``set_rate`` 会在等待期间把容量收缩到再也补不满，于是已入站的调用方
永久停在那里。这条由最后一条判据单独钉住（守卫必须在锁内、每轮重做），
并由 ``tests/transport/test_ratelimit_contract.py`` 里那两线程的真实复现兜底。

**这一格有多可达，也写清楚**：包内五处限流调用点（``tstdx/transport/pool.py`` 三处、
``tstdx/transport/async_.py`` 两处）全部只取 1 枚令牌，而 ``burst`` 有 ``max(1.0, rate)``
的下限，所以这条要 ``tokens > 1`` 才会落到 shipped 路径上——本轮没有在线上观察到停等，
被抓的是 :class:`TokenBucket` 这个**导出符号**自己的阻塞契约：``acquire(tokens=50)``
配一次并发的 ``set_rate(1.0)``，旧实现既不报错也不返回。
"""

from __future__ import annotations

import ast
from pathlib import Path

from tests.support.field_readers import REPO_ROOT

PACKAGE_ROOT = REPO_ROOT / "tstdx"

#: 条件形状属于 ``stop-flag`` 的调用名：Event 的两个动词，语义就是"有人在别处拧它"。
_FLAG_METHODS = frozenset({"is_set", "wait", "set", "clear", "store"})

#: 命名豁免：形状归不出类、但终止理由写得出来的循环。键为 ``模块相对路径::函数名``。
#: 表必须与现扫结果**双向一致**——多一条（守卫已经不需要豁免了）与少一条（新出现的
#: 无形状守卫循环）都当场红，所以这张表不会悄悄长大。
TERMINATION_EXEMPTIONS: dict[str, str] = {
    "tstdx/transport/async_.py::_acquire_rate": (
        "``while not limiter.try_acquire()``：条件不是 flag 而是令牌桶。它一定翻转，"
        "因为令牌按 ``rate > 0`` 单调回填，而唯一的入参 ``tokens=1.0`` 恒不超过 "
        "``burst = max(1.0, rate)``；``strict=True`` 时这一格根本不走轮询，直接 "
        "``limiter.acquire()`` 抛 :class:`RateLimitedLocal`。容量收缩会把这一格变成"
        "显式 ``ValueError`` 而不是永久等待——见第 6 条判据。"
    ),
    "tstdx/transport/async_.py::_await_cleanup_before_cancellation": (
        "``while not cleanup_task.done()``：条件读的是一个 :class:`asyncio.Task` 的"
        "完成位，循环体只有 ``await asyncio.shield(...)``——它挂起而不是忙转，"
        "被取消时记一笔再回到同一个 await。终止由那个任务自己完成，不由本循环推进。"
    ),
    "tstdx/streaming/base.py::stop": (
        "``while task is not None and not task.done()``（第 29 轮补入）：与上一条同形同理由"
        "——条件读的是 poll worker 那条 :class:`asyncio.Task` 的完成位，循环体只有 "
        "``await asyncio.shield(task)``；被取消时记一笔再回到同一个 await，终止由 worker "
        "自己在下一轮循环顶读到 ``_stop`` 完成。它等得起多久写在 G41 判据七的登记表里"
        "（``AsyncQuoteStream.stop`` 那一格），这里只负责「它不是忙等」。"
    ),
}


class _LoopGuard(ast.NodeVisitor):
    """按形状给文件里每个 ``while`` 归类；``offenders`` 收集归不出类的那些。"""

    def __init__(self, source: str) -> None:
        self.source = source
        self.findings: list[tuple[int, str, str, str]] = []  # (行号, 条件源码, 类别, 函数)
        self._functions: list[str] = []
        self._try_stack: list[ast.Try] = []

    # -- 上下文跟踪 ------------------------------------------------------- #
    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._functions.append(node.name)
        self.generic_visit(node)
        self._functions.pop()

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_Try(self, node: ast.Try) -> None:
        self._try_stack.append(node)
        self.generic_visit(node)
        self._try_stack.pop()

    # -- 判定 ------------------------------------------------------------- #
    def visit_While(self, node: ast.While) -> None:
        test = ast.get_source_segment(self.source, node.test) or ""
        category = _classify(node, test, self._enclosing_trampoline())
        func = self._functions[-1] if self._functions else "<module>"
        self.findings.append((node.lineno, test.strip(), category, func))
        self.generic_visit(node)

    def _enclosing_trampoline(self) -> bool:
        """本循环是否被 ``except StopIteration`` 直接罩着（生成器驱动的形状）。"""
        for try_node in reversed(self._try_stack):
            for handler in try_node.handlers:
                names = {
                    getattr(h, "id", None)
                    for h in ast.walk(handler.type)
                    if isinstance(h, ast.Name)
                }
                if "StopIteration" in names:
                    return True
        return False


def _enclosing_loops(loop: ast.While) -> set[int]:  # pragma: no cover - 文档用
    raise NotImplementedError


def _escapes(body: list[ast.stmt]) -> bool:
    """本循环体内是否存在不穿过更内层循环就能走掉的 ``break``/``return``/``raise``。"""

    def walk(node: ast.AST) -> bool:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.For, ast.AsyncFor, ast.While)):
                continue  # 归内层循环所有
            if isinstance(child, (ast.Break, ast.Return, ast.Raise)):
                return True
            if walk(child):
                return True
        return False

    return any(
        isinstance(stmt, (ast.Break, ast.Return, ast.Raise))
        or (not isinstance(stmt, (ast.For, ast.AsyncFor, ast.While)) and walk(stmt))
        for stmt in body
    )


def _names(node: ast.AST) -> set[str]:
    out: set[str] = set()
    for sub in ast.walk(node):
        if isinstance(sub, ast.Name):
            out.add(sub.id)
        elif isinstance(sub, ast.Attribute):
            out.add(sub.attr)
    return out


def _written(body: list[ast.stmt]) -> set[str]:
    out: set[str] = set()
    for node in ast.walk(ast.Module(body=body, type_ignores=[])):
        if isinstance(node, (ast.Assign, ast.AugAssign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                out |= _names(target)
        elif isinstance(node, ast.For):
            out |= {n.id for n in ast.walk(node.target) if isinstance(n, ast.Name)}
    return out


def _flag_root(node: ast.AST) -> str | None:
    """``not X`` / ``not X.is_set()`` / ``not X.wait(t)`` 里那个被拧的 flag 名。

    属性形式取**属性名**（``self._closed`` → ``_closed``），因为
    :func:`_flag_movers` 记的就是赋值/``set()`` 的目标属性名。
    """
    if not (isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not)):
        return None
    inner = node.operand
    if isinstance(inner, ast.Name):
        return inner.id
    if isinstance(inner, ast.Attribute):
        return inner.attr
    if isinstance(inner, ast.Call) and isinstance(inner.func, ast.Attribute):
        if inner.func.attr not in _FLAG_METHODS:
            return None
        base = inner.func.value
        if isinstance(base, ast.Attribute):
            return base.attr
        if isinstance(base, ast.Name):
            return base.id
    return None


def _classify(loop: ast.While, test: str, in_trampoline: bool) -> str:
    """返回类别名，归不出来时返回 ``""``（即 offender）。"""
    if _escapes(loop.body):
        return "escape"
    if _names(loop.test) & _written(loop.body):
        return "progress"
    if in_trampoline and test.strip() == "True":
        return "trampoline"
    return ""


def _scan_file(path: Path, source: str) -> list[tuple[str, int, str, str, str]]:
    """→ ``[(模块相对路径, 行号, 条件源码, 类别, 函数名), ...]``，含 stop-flag 复核。"""
    tree = ast.parse(source)
    visitor = _LoopGuard(source)
    visitor.visit(tree)
    rel = path.relative_to(PACKAGE_ROOT.parent).as_posix()
    flag_movers = _flag_movers(tree)
    rows: list[tuple[str, int, str, str, str]] = []
    for lineno, test, category, func in visitor.findings:
        if not category:
            root = _flag_root(ast.parse(test).body[0].value)  # type: ignore[union-attr]
            if root is not None and root in flag_movers:
                category = f"stop-flag:{root}"
        rows.append((rel, lineno, test.strip(), category, func))
    return rows


def _flag_movers(tree: ast.AST) -> set[str]:
    """本文件里"确实会被人拧的 flag"：被赋值过，或被 ``.set()`` / ``.clear()`` 动过。"""
    movers: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Assign, ast.AugAssign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Attribute):
                    movers.add(target.attr)
                elif isinstance(target, ast.Name):
                    movers.add(target.id)
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr in {"set", "clear", "store"}:
                base = node.func.value
                if isinstance(base, ast.Attribute):
                    movers.add(base.attr)
                elif isinstance(base, ast.Name):
                    movers.add(base.id)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            movers |= {a.arg for a in node.args.args}
    return movers


def _iter_package_sources() -> list[tuple[Path, str]]:
    return [(p, p.read_text(encoding="utf-8")) for p in sorted(PACKAGE_ROOT.rglob("*.py"))]


def _all_loops() -> list[tuple[str, int, str, str, str]]:
    rows: list[tuple[str, int, str, str, str]] = []
    for path, source in _iter_package_sources():
        rows.extend(_scan_file(path, source))
    return rows


def _key(row: tuple[str, int, str, str, str]) -> str:
    return f"{row[0]}::{row[4]}"


# --------------------------------------------------------------------------- #
# 判据 1：尺子自己不瞎——四类形状各造一次，且"没守卫"必须被抓到
# --------------------------------------------------------------------------- #
def test_the_loop_ruler_sees_all_four_guard_shapes() -> None:
    samples = {
        "escape": "def f():\n    while True:\n        if x:\n            break\n",
        "progress": "def f():\n    n = 3\n    while n > 0:\n        n -= 1\n",
        "trampoline": (
            "def f(gen):\n"
            "    try:\n"
            "        op = next(gen)\n"
            "        while True:\n"
            "            op = gen.send(op)\n"
            "    except StopIteration as stop:\n"
            "        return stop.value\n"
        ),
        "stop-flag": (
            "import threading\n"
            "_EV = threading.Event()\n"
            "def f():\n"
            "    while not _EV.is_set():\n"
            "        poll()\n"
            "def stop():\n"
            "    _EV.set()\n"
        ),
    }
    for expected, source in samples.items():
        tree = ast.parse(source)
        loops = [n for n in ast.walk(tree) if isinstance(n, ast.While)]
        assert len(loops) == 1, f"样本 {expected} 应当恰好一个 while"
        visitor = _LoopGuard(source)
        visitor.visit(tree)
        (lineno, test, category, func) = visitor.findings[0]
        if expected == "stop-flag":
            root = _flag_root(ast.parse(test).body[0].value)  # type: ignore[union-attr]
            assert root in _flag_movers(tree), f"{expected} 形状没被认出（flag {root!r}）"
        else:
            assert category == expected, f"{expected} 形状被归成了 {category!r}"


def test_the_loop_ruler_flags_a_loop_with_no_way_out() -> None:
    """正控：``while True: pass`` 这种形状必须归不出类。"""
    for source in (
        "def f():\n    while True:\n        pass\n",
        "def f():\n    while busy:\n        do_work()\n",
        # 内层循环里的 break 不能替外层作证
        "def f():\n    while True:\n        for row in rows:\n            break\n",
    ):
        tree = ast.parse(source)
        visitor = _LoopGuard(source)
        visitor.visit(tree)
        lineno, test, category, _func = visitor.findings[0]
        assert category == "", f"这格本该红，却被归成了 {category!r}：{source!r}"


# --------------------------------------------------------------------------- #
# 判据 2：全仓现扫——没有一条 while 落在"既无形状守卫、又未登记豁免"
# --------------------------------------------------------------------------- #
def test_every_while_loop_in_the_package_can_name_its_guard() -> None:
    rows = _all_loops()
    # 反洞自查：扫到 0 条一定是扫描器瞎了，而不是"全仓没有循环"。
    assert len(rows) > 40, f"现扫只看到 {len(rows)} 条 while，扫描器不可信"
    unguarded = [row for row in rows if not row[3]]
    offenders = sorted(row for row in unguarded if _key(row) not in TERMINATION_EXEMPTIONS)
    assert offenders == [], (
        "这些 while 说不出终止理由（补形状、或登记豁免并写明理由）：\n"
        + "\n".join(
            f"  {rel}:{lineno}  while {test}  (in {func})"
            for rel, lineno, test, _c, func in offenders
        )
    )


def test_the_termination_exemption_table_is_not_stale() -> None:
    """豁免表不许长大，也不许留着已经不需要的条目。"""
    rows = _all_loops()
    unguarded_keys = {_key(row) for row in rows if not row[3]}
    missing = sorted(unguarded_keys - set(TERMINATION_EXEMPTIONS))
    stale = sorted(set(TERMINATION_EXEMPTIONS) - unguarded_keys)
    assert missing == [], f"新的无形状守卫循环，未登记终止理由: {missing}"
    assert stale == [], f"豁免已不再需要，删掉它: {stale}"


# --------------------------------------------------------------------------- #
# 判据 3：本轮那格真缺陷的形状——容量守卫不许退回锁外的一次性检查
# --------------------------------------------------------------------------- #
def test_bucket_capacity_guard_lives_inside_the_wait_loop() -> None:
    """``TokenBucket.acquire`` 的容量守卫必须在等待循环里，而不是锁外的一次性检查。

    写在 ``while`` 之前等于没写：``set_rate`` 会在等待期间收缩容量，届时已入站的
    调用方再也补不满令牌，而守卫那一步早就走完了——本轮实测到的那次永久停等
    就是这个形状（``scratch_v18b21/hang_repro.py``）。
    """
    path = PACKAGE_ROOT / "transport" / "ratelimit.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    bucket = next(
        n for n in ast.walk(tree) if isinstance(n, ast.ClassDef) and n.name == "TokenBucket"
    )
    acquire = next(n for n in bucket.body if isinstance(n, ast.FunctionDef) and n.name == "acquire")
    loops = [n for n in ast.walk(acquire) if isinstance(n, ast.While)]
    assert len(loops) == 1, f"期望恰好一个等待循环，实际 {len(loops)} 个"
    loop = loops[0]
    inside = _burst_guards(loop.body)
    outside = [node for node in _burst_guards(acquire.body) if not _within(node, loop)]
    assert inside, "容量守卫被移出了等待循环——锁外的一次性检查挡不住收缩后的永久停等"
    assert outside == [], "容量守卫不该在 while 之前再抄一份（两份里就会有一份过期）"


def _burst_guards(body: list[ast.stmt]) -> list[ast.Compare]:
    """找出 ``requested > self.burst`` 这一形状的比较节点。"""
    hits: list[ast.Compare] = []
    for node in ast.walk(ast.Module(body=body, type_ignores=[])):
        if not isinstance(node, ast.Compare) or len(node.comparators) != 1:
            continue
        left, right = node.left, node.comparators[0]
        if isinstance(left, ast.Name) and left.id == "requested" and _is_self_burst(right):
            hits.append(node)
    return hits


def _is_self_burst(node: ast.expr) -> bool:
    return (
        isinstance(node, ast.Attribute)
        and node.attr == "burst"
        and isinstance(node.value, ast.Name)
        and node.value.id == "self"
    )


def _within(node: ast.AST, parent: ast.AST) -> bool:
    return any(child is node for child in ast.walk(parent))
