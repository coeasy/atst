"""G41：资源生命周期与停机路径的七条形状判据（V18 第 26 轮第 2 遍，V19 第 28 轮加第七条）。

第 2 遍的口令落点是"链路跑完之后归不归零"。一条链路不只在"有没有人调用它"上会断，
也会在**收尾**上断，而收尾这一族断得安静：功能面全绿、离线全量全绿，只有长跑的进程
慢慢多出几条没人收的线程、几张再没人看的退役槽位、一个关不掉的端口。七条形状判据
全部按整包现扫判定，不靠名单放行——登记表本身也被现扫结果双向核对：

一、每条线程 spawn 必须落到句柄上，句柄必须接到一次 ``join``（F-87/F-92/F-97/F-101）。
二、每次 ``join`` 自己得带截止时间；不带的那一格要就地写明凭什么等得起（第 21 轮 G21
    管循环终止，这一条管"等线程退出的人自己会不会永久等"）。
三、不可重入锁的临界区里不许再进取同一把锁的方法——那是自死锁，不是慢（F-94）。
四、own 来的资源不许留在早退分支之后：收尾路径上每个 ``return``，要么前面已经放过
    一次，要么它本身就在"按 own 与否 / 按幂等 flag 决定放不放"的护栏里（F-91/F-100）。
五、探活与空闲回收共用一条线程时，启动门必须读到这条线程会读的每个 knob（F-88/F-89）
    ——只读一半就是"把心跳调成 0 顺手关掉了空闲回收"。
六、换了别人的钩子（往目标上打 ``__wrapped__``）的模块必须导出逆操作（F-95）。
七、异步停机路径上的每个 ``await`` 要么自带截止期，要么就地写明等得起（第 28 轮 28-B-1）。
    判据二管的是"等线程的人"，这一条管"等传输/等任务的人"：``await writer.wait_closed()``
    当时是本模块关停路径上唯一没有截止期的 await，而它等的是 asyncio 的 ``connection_lost``
    回调——``close()`` 之后那个回调不一定到来。它挂在两处会要命的地方：池关停逐个槽位
    排空（N 个槽位乘 N 次），以及读帧失败路径上 ``await self.close()`` 之后才抛
    :class:`~tstdx.errors.ConnectionClosed`（读超时到点之后，调用方仍然回不来）。

**这把尺子证不到什么，写清楚**：判据一只证明"句柄接到了 join"，不证明那条 join 一定
被执行（走不走得到由运行时决定，那一半由 ``tests/transport/test_pool_generation_provenance.py``
和 ``tests/streaming`` 里的真实停机用例兜底）；判据三有两处看不见：一是经字段默认工厂
造的锁（``AsyncSlot.lock`` 走 ``field(default_factory=asyncio.Lock)``，AST 里读不到构造点，
所以它只认 ``self.<name> = XxxLock()`` 这个形状），二是**锁由调用方持有、被调方法自己
再取一次**的跨方法重入——它只看得到同一个方法体内 ``with self.<lock>`` 包住的重入调用。
后一条不是推演：变异台账第 3 格先挑的是 ``_request_locked()``（持锁方是 ``request()``），
判据三当时纹丝不动，换成 ``ping()`` 才红，所以那一段的账由 ``_locked`` 命名约定与并发
用例来付；判据四只在类里确实有 ``_owns_*`` 开关时生效，纯函数式的卸下路径
（:func:`tstdx.transport.sniff.detach`）由判据六管；判据七**不含锁等待**——``async with
self._lock`` 在 AST 里根本不产生 ``Await`` 节点，把它算成"无界等待"要给整包补一张锁表，
而锁的上界来自持锁方，那正是判据三与判据七其余四格合起来管的事。它同样只覆盖名单里的
收尾动词：``AsyncQuoteStream.stop()`` 的 ``await asyncio.shield(task)`` 不在射程内，
那一条按第 28 轮 P2-F 登记在方案文档里，不在这里偷偷放行也不在这里假装管到。
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

from tests.support.field_readers import REPO_ROOT

PACKAGE_ROOT = REPO_ROOT / "tstdx"

# --------------------------------------------------------------------------- #
# 共享的 AST 小工具
# --------------------------------------------------------------------------- #


def _modules() -> list[tuple[Path, ast.Module]]:
    out: list[tuple[Path, ast.Module]] = []
    for path in sorted(PACKAGE_ROOT.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        out.append((path, ast.parse(path.read_text(encoding="utf-8"))))
    return out


def _rel(path: Path) -> str:
    return path.relative_to(REPO_ROOT).as_posix()


def _self_attr(node: ast.AST) -> str | None:
    """``self.<attr>`` 的 attr 名，否则 ``None``。"""
    if (
        isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "self"
    ):
        return node.attr
    return None


def _is_self(node: ast.AST) -> bool:
    return isinstance(node, ast.Name) and node.id == "self"


def _func_name(node: ast.AST) -> str | None:
    if isinstance(node, (ast.Attribute, ast.Name)):
        return node.id if isinstance(node, ast.Name) else node.attr
    return None


def _parents(root: ast.AST) -> dict[ast.AST, ast.AST]:
    out: dict[ast.AST, ast.AST] = {}
    for node in ast.walk(root):
        for child in ast.iter_child_nodes(node):
            out[child] = node
    return out


def _qualname(parents: dict[ast.AST, ast.AST], node: ast.AST) -> str:
    """``类名.方法名``（模块级函数省略类名），从父链现算。"""
    chain: list[str] = []
    cur: ast.AST | None = node
    while cur is not None:
        if isinstance(cur, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            chain.append(cur.name)
        cur = parents.get(cur)
    return ".".join(reversed(chain))


def _class_scoped(parents: dict[ast.AST, ast.AST], node: ast.AST) -> str | None:
    cur: ast.AST | None = node
    while cur is not None:
        if isinstance(cur, ast.ClassDef):
            return cur.name
        cur = parents.get(cur)
    return None


def _methods(cls: ast.ClassDef) -> dict[str, ast.FunctionDef | ast.AsyncFunctionDef]:
    return {n.name: n for n in cls.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}


def _scope_of(site: str) -> str:
    """spawn 现场所属的作用域名（类名；模块级函数则为空串）。"""
    return site.split("::", 1)[1].rsplit(".", 1)[0]


def _thread_call(node: ast.AST) -> bool:
    """本节点是不是 ``threading.Thread(...)`` / ``Thread(...)`` 构造。"""
    if not isinstance(node, ast.Call):
        return False
    if _func_name(node.func) != "Thread":
        return False
    holder = node.func.value if isinstance(node.func, ast.Attribute) else None
    return holder is None or (isinstance(holder, ast.Name) and holder.id == "threading")


# --------------------------------------------------------------------------- #
# 判据一：每条线程 spawn 都接到一个会 join 它的句柄
# --------------------------------------------------------------------------- #

#: ``spawn 现场 -> (句柄落点, 收尸函数)``。现场清单由判据一现扫，表与清单**双向一致**；
#: 句柄与收尸两处都还要被机器复核（见该判据体内注释），所以登记表抄不得现值。
SPAWN_OWNERSHIP: dict[str, tuple[str, str]] = {
    "tstdx/observability/statsd_exporter.py::StatsdExporter.start_pushing": (
        "self._push_thread",
        "stop_pushing",
    ),
    "tstdx/streaming/base.py::QuoteStream.start": ("self._thread", "stop"),
    "tstdx/streaming/engine.py::StreamEngine.start": ("self._thread", "stop"),
    "tstdx/transport/pool.py::ConnectionPool._start_heartbeat": ("self._hb", "close"),
    "tstdx/transport/pool.py::ConnectionPool._trigger_background_speedtest": (
        "self._speedtest_threads",
        "close",
    ),
    "tstdx/web/sina/adapters.py::SinaSource.fetch_all": ("local:threads", "fetch_all"),
}


@dataclass(frozen=True)
class Spawn:
    site: str  # ``模块::类.方法``
    handle: str  # 现扫出的句柄落点：``self.X`` / ``local:name`` / ``dropped``
    function: str  # 所在函数名（用于复核"句柄是否被这条函数存住"）


def _spawns(path: Path, mod: ast.Module) -> list[Spawn]:
    parents = _parents(mod)
    out: list[Spawn] = []
    for call in [n for n in ast.walk(mod) if _thread_call(n)]:
        handle = "dropped"
        cur: ast.AST | None = call
        while cur is not None:
            parent = parents.get(cur)
            if isinstance(parent, ast.Assign):
                target = parent.targets[0]
                if (attr := _self_attr(target)) is not None:
                    handle = f"self.{attr}"
                elif isinstance(target, ast.Name):
                    handle = f"local:{target.id}"
                break
            if isinstance(parent, (ast.ListComp, ast.GeneratorExp, ast.Tuple)):
                # ``threads = [Thread(...) for _ in ...]``：句柄是那次赋值
                grand = parents.get(parent)
                if isinstance(grand, ast.Assign) and isinstance(grand.targets[0], ast.Name):
                    handle = f"local:{grand.targets[0].id}"
                break
            if isinstance(parent, ast.Expr):
                break  # ``Thread(...).start()``：起跑即失联
            cur = parent
        fnWalker: ast.AST | None = call
        function = "<module>"
        while fnWalker is not None:
            if isinstance(fnWalker, (ast.FunctionDef, ast.AsyncFunctionDef)):
                function = fnWalker.name
                break
            fnWalker = parents.get(fnWalker)
        out.append(Spawn(f"{_rel(path)}::{_qualname(parents, call)}", handle, function))
    return out


def _deposited(fn_node: ast.AST, attr: str, local: str) -> bool:
    """``local`` 是否被这条函数存进 ``self.<attr>``（赋值或 append/add）。"""
    for node in ast.walk(fn_node):
        if (
            isinstance(node, ast.Assign)
            and any(_self_attr(t) == attr for t in node.targets)
            and any(isinstance(n, ast.Name) and n.id == local for n in ast.walk(node.value))
        ):
            return True
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in {"append", "add", "extend"}
            and _self_attr(node.func.value) == attr
            and any(
                isinstance(n, ast.Name) and n.id == local
                for arg in node.args
                for n in ast.walk(arg)
            )
        ):
            return True
    return False


def test_every_thread_spawn_is_wired_to_a_handle_and_a_reaper() -> None:
    for path, mod in _modules():
        spawns = _spawns(path, mod)
        if not spawns:
            continue
        scope = _rel(path)
        declared = {k: v for k, v in SPAWN_OWNERSHIP.items() if k.startswith(f"{scope}::")}
        assert {s.site for s in spawns} == set(declared), (
            f"{scope} 的线程 spawn 现场与登记表不符：现扫 {sorted(s.site for s in spawns)}，"
            f"登记 {sorted(declared)}"
        )
        parents = _parents(mod)
        scoped_functions = {
            _qualname(parents, n): n
            for n in ast.walk(mod)
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        for spawn in spawns:
            declared_handle, reaper_name = declared[spawn.site]
            assert declared_handle.startswith(("self.", "local:")), f"{spawn.site} 的句柄形状不认识"
            if spawn.handle != declared_handle:
                # 唯一的合法错位：现扫到的是局部名，登记的是它被存进去的那个 self 属性。
                assert spawn.handle.startswith("local:") and declared_handle.startswith("self."), (
                    f"{spawn.site} 的句柄现扫是 {spawn.handle}，登记表写 {declared_handle}"
                )
                holder = next(
                    (
                        n
                        for n in ast.walk(mod)
                        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                        and n.name == spawn.function
                        and _qualname(parents, n).split(".")[-1] == spawn.function
                    ),
                    None,
                )
                assert holder is not None, f"{spawn.site} 找不到所在函数 {spawn.function}()"
                assert _deposited(
                    holder, declared_handle.split(".", 1)[1], spawn.handle.split(":", 1)[1]
                ), f"{spawn.site}：{spawn.handle} 没有被存进 {declared_handle}"
            reaper = scoped_functions.get(f"{_scope_of(spawn.site)}.{reaper_name}")
            assert reaper is not None, (
                f"{spawn.site} 声称由同类的 {reaper_name}() 收尸，包里没有这个形状"
            )
            joins = [
                n
                for n in ast.walk(reaper)
                if isinstance(n, ast.Call)
                and isinstance(n.func, ast.Attribute)
                and n.func.attr == "join"
            ]
            assert joins, f"{reaper_name}() 里没有任何 join，收不了 {declared_handle}"
            token = (
                declared_handle.split(".", 1)[1]
                if declared_handle.startswith("self.")
                else declared_handle.split(":", 1)[1]
            )
            read_names = {
                (n.attr if isinstance(n, ast.Attribute) else n.id)
                for n in ast.walk(reaper)
                if isinstance(n, (ast.Attribute, ast.Name))
            }
            assert token in read_names, f"{reaper_name}() 没读过句柄 {declared_handle}"


# --------------------------------------------------------------------------- #
# 判据二：join 自带截止时间；等得起的必须就地说明
# --------------------------------------------------------------------------- #

#: 不带超时的 ``join``：键为 ``模块::类.方法``，值为"凭什么等得起"。双向一致。
UNBOUNDED_JOIN_SITES: dict[str, str] = {
    "tstdx/web/sina/adapters.py::SinaSource.fetch_all": (
        "这几条分页 worker 的寿命由**分页**而不是由网络决定：每轮要么 return，要么把 "
        "``next_page`` 往前推，推到 ``max_pages`` 即退；单次 HTTP 又有 :mod:`tstdx.web` "
        "自己的请求超时兜着。给它加 join 超时反而是假绿——超时会把没跑完的页当已跑完，"
        "把截断结果当全量交出去（``web_eastmoney_page_limit`` 那一族告警就是这么来的）。"
    ),
}


def test_every_join_gives_itself_a_deadline_or_a_reason() -> None:
    found: dict[str, int] = {}
    for path, mod in _modules():
        parents = _parents(mod)
        for node in ast.walk(mod):
            if not (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "join"
            ):
                continue
            if node.args or any(k.arg in {"timeout", "t"} for k in node.keywords):
                continue
            found[f"{_rel(path)}::{_qualname(parents, node)}"] = node.lineno
    assert set(found) == set(UNBOUNDED_JOIN_SITES), (
        f"无截止时间的 join 现扫 {sorted(found.items())}，登记表 {sorted(UNBOUNDED_JOIN_SITES)}"
    )


# --------------------------------------------------------------------------- #
# 判据三：不可重入锁的临界区不重入
# --------------------------------------------------------------------------- #

#: 可重入锁的构造名：落在这些名字上的锁不进判据三（重入它是设计，不是缺陷）。
REENTRANT_LOCK_CONSTRUCTORS = frozenset({"RLock"})


def _lock_attrs(cls: ast.ClassDef | ast.Module) -> set[str]:
    """本节点里以 ``self.<attr> = XxxLock()`` 形状出现的**不可重入**锁名。"""
    out: set[str] = set()
    for node in ast.walk(cls):
        if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Call):
            continue
        ctor = _func_name(node.value.func)
        if ctor is None or ctor in REENTRANT_LOCK_CONSTRUCTORS:
            continue
        if not (ctor == "Lock" or ctor.endswith("Lock")):
            continue
        for target in node.targets:
            if (attr := _self_attr(target)) is not None:
                out.add(attr)
    return out


def _acquires(fn: ast.AST, lock_attrs: set[str]) -> bool:
    return any(
        isinstance(with_node, (ast.With, ast.AsyncWith))
        and any(
            (ctx := _self_attr(item.context_expr)) and ctx in lock_attrs for item in with_node.items
        )
        for with_node in ast.walk(fn)
    )


def _self_calls_inside_critical_section(fn: ast.AST, lock_attrs: set[str]) -> set[str]:
    """**发生在取这些锁的 with 体内**的 ``self.X()`` 调用名。"""
    hits: set[str] = set()

    def walk(node: ast.AST, inside: bool) -> None:
        for child in ast.iter_child_nodes(node):
            now = inside or (
                isinstance(child, (ast.With, ast.AsyncWith))
                and any(
                    (ctx := _self_attr(item.context_expr)) and ctx in lock_attrs
                    for item in child.items
                )
            )
            if (
                now
                and isinstance(child, ast.Call)
                and isinstance(child.func, ast.Attribute)
                and _is_self(child.func.value)
            ):
                hits.add(child.func.attr)
            walk(child, now)

    walk(fn, False)
    return hits


def test_nonreentrant_locks_are_not_reentered_from_their_own_critical_section() -> None:
    violated: list[str] = []
    for path, mod in _modules():
        scopes: list[tuple[str, ast.ClassDef | ast.Module, dict]] = [
            (cls.name, cls, _methods(cls))
            for cls in [n for n in ast.walk(mod) if isinstance(n, ast.ClassDef)]
        ]
        scopes.append(("<module>", mod, {}))
        for name, scope, methods in scopes:
            lock_attrs = _lock_attrs(scope)
            if not lock_attrs or not methods:
                continue
            holders = {n for n, fn in methods.items() if _acquires(fn, lock_attrs)}
            for holder in sorted(holders):
                for callee in sorted(
                    _self_calls_inside_critical_section(methods[holder], lock_attrs)
                ):
                    if callee != holder and callee in holders:
                        violated.append(
                            f"{_rel(path)}::{name}.{holder}() 在 {sorted(lock_attrs)} 的临界区内调了"
                            f" .{callee}()，而后者自己会取同一把锁"
                        )
    assert not violated, "不可重入锁上自嵌套 = 自死锁：\n" + "\n".join(violated)


# --------------------------------------------------------------------------- #
# 判据四：停机路径不把自己的资源留在早退分支之后
# --------------------------------------------------------------------------- #

_RELEASE_NAMES = frozenset(
    {"close", "stop", "shutdown", "disconnect", "release", "aclose", "_close_owned_runtime"}
)


def _ownership_flags(cls: ast.ClassDef) -> set[str]:
    return {
        attr
        for node in ast.walk(cls)
        if isinstance(node, (ast.Assign, ast.AnnAssign))
        for target in ([node.target] if isinstance(node, ast.AnnAssign) else node.targets)
        if (attr := _self_attr(target)) and "owns" in attr
    }


def _assigned_flags(fn: ast.AST, flags: set[str]) -> set[str]:
    return {
        attr
        for node in ast.walk(fn)
        if isinstance(node, (ast.Assign, ast.AnnAssign))
        for target in ([node.target] if isinstance(node, ast.AnnAssign) else node.targets)
        if (attr := _self_attr(target)) and attr in flags
    }


def test_shutdown_paths_do_not_return_before_releasing_what_they_own() -> None:
    skipped: list[str] = []
    for path, mod in _modules():
        for cls in [n for n in ast.walk(mod) if isinstance(n, ast.ClassDef)]:
            flags = _ownership_flags(cls)
            if not flags:
                continue
            parents = _parents(cls)
            for fn in _methods(cls).values():
                releases = [
                    n
                    for n in ast.walk(fn)
                    if isinstance(n, ast.Call) and _func_name(n.func) in _RELEASE_NAMES
                ]
                if not releases:
                    continue
                # 只在"这是它自己的收尾路径"时生效：方法名是收尾动词，或它亲手改过 own 开关。
                if fn.name not in _RELEASE_NAMES and not _assigned_flags(fn, flags):
                    continue
                for ret in [n for n in ast.walk(fn) if isinstance(n, ast.Return)]:
                    if any(r.lineno < ret.lineno for r in releases):
                        continue  # 返回前已经放过一次
                    chain: list[ast.AST] = []
                    cur: ast.AST | None = parents.get(ret)
                    while cur is not None and cur is not fn:
                        chain.append(cur)
                        cur = parents.get(cur)
                    ifs = [c for c in chain if isinstance(c, ast.If)]
                    if any(_reads_names(c.test, flags) for c in ifs):
                        continue  # 按"是不是我的"决定放不放，正当
                    if any(_guarded_by_idempotency(c, fn) for c in ifs):
                        continue  # 幂等护栏：先到的人已经放过
                    if any(
                        isinstance(c, ast.Try)
                        and any(ret in list(ast.walk(h)) for h in c.finalbody)
                        for c in chain
                    ):
                        continue  # finally 里收尾
                    skipped.append(f"{_rel(path)}::{cls.name}.{fn.name}() return@{ret.lineno}")
    assert not skipped, (
        "停机早退把 own 资源留在身后（连接池/端口不会随这次 stop 释放）：\n" + "\n".join(skipped)
    )


def _reads_names(node: ast.AST, names: set[str]) -> bool:
    return any(
        isinstance(a, ast.Attribute) and a.attr in names and _is_self(a.value)
        for a in ast.walk(node)
    )


def _guarded_by_idempotency(if_node: ast.If, fn: ast.AST) -> bool:
    """``if self.<ev>.is_set(): return`` 且本方法自己 ``self.<ev>.set()`` 在放手之前。"""
    asked = {
        _self_attr(cast_attr.func.value)
        for cast_attr in ast.walk(if_node.test)
        if isinstance(cast_attr, ast.Call)
        and isinstance(cast_attr.func, ast.Attribute)
        and cast_attr.func.attr == "is_set"
        and (_self_attr(cast_attr.func.value) is not None)
    }
    asked.discard(None)
    if not asked:
        return False
    set_sites = {
        _self_attr(s.func.value)
        for s in ast.walk(fn)
        if isinstance(s, ast.Call)
        and isinstance(s.func, ast.Attribute)
        and s.func.attr == "set"
        and _self_attr(s.func.value) is not None
    }
    return bool(asked & set_sites)


# --------------------------------------------------------------------------- #
# 判据五：共用一条线程的多个 knob，启动门必须全读到
# --------------------------------------------------------------------------- #

#: ``类 -> (起跑函数, 线程体读的那个 sweeper)``。起跑函数由门点名，所以"门读了哪些 knob"
#: 与"线程会读哪些 knob"两边都能现扫。新加一条 knob 线程要登记，抽掉一条要删。
SHARED_SWEEPER_SITES: dict[str, str] = {
    "tstdx/transport/pool.py::ConnectionPool": "_start_heartbeat",
    "tstdx/transport/async_.py::AsyncConnectionPool": "start_heartbeat",
}


def _constructor_knobs(cls: ast.ClassDef) -> set[str]:
    """``__init__`` 里由入参赋出来的属性名——调用方真能拧的开关。"""
    init = _methods(cls).get("__init__")
    if init is None:
        return set()
    params = {a.arg for a in [*init.args.args, *init.args.kwonlyargs] if a.arg != "self"}
    out: set[str] = set()
    for node in ast.walk(init):
        if not isinstance(node, (ast.Assign, ast.AnnAssign)) or node.value is None:
            continue
        targets = [node.target] if isinstance(node, ast.AnnAssign) else node.targets
        names = {n.id for n in ast.walk(node.value) if isinstance(n, ast.Name)} & params
        for target in targets:
            if (attr := _self_attr(target)) is not None and attr in names:
                out.add(attr)
    return out


def test_start_gates_read_every_knob_the_sweeper_thread_touches() -> None:
    sweepers = {
        f"{_rel(path)}::{_class_scoped(_parents(mod), cls) or cls.name}"
        for path, mod in _modules()
        for cls in [n for n in ast.walk(mod) if isinstance(n, ast.ClassDef)]
        if _constructor_knobs(cls) >= {"heartbeat_interval", "idle_timeout"}
    }
    assert sweepers == set(SHARED_SWEEPER_SITES), (
        f"带心跳 + 空闲回收两个 knob 的池现扫 {sorted(sweepers)}，登记表 {sorted(SHARED_SWEEPER_SITES)}"
    )
    for key, starter_name in SHARED_SWEEPER_SITES.items():
        rel, _, cls_name = key.partition("::")
        mod = ast.parse((REPO_ROOT / rel).read_text(encoding="utf-8"))
        cls = next(n for n in ast.walk(mod) if isinstance(n, ast.ClassDef) and n.name == cls_name)
        methods = _methods(cls)
        starter = methods.get(starter_name)
        assert starter is not None, f"{key} 没有 {starter_name}()"
        knobs = _constructor_knobs(cls)
        # "能关掉东西的 knob" = 在类里任何判断位置被问过一次的构造器 knob。只当数据
        # 传的（比如发出去探活的那条命令号）不该要求启动门读它——那是把判据五变成
        # "启动门要读完线程体碰过的一切"，第一格就会假红。
        decisions: list[ast.AST] = []
        for node in ast.walk(cls):
            if isinstance(node, (ast.If, ast.IfExp)):
                decisions.append(node.test)
            elif isinstance(node, (ast.BoolOp, ast.Compare)):
                decisions.append(node)
        disabling = {
            a.attr
            for test in decisions
            for a in ast.walk(test)
            if isinstance(a, ast.Attribute) and _is_self(a.value) and a.attr in knobs
        }
        touched = {
            a.attr
            for a in ast.walk(starter)
            if isinstance(a, ast.Attribute) and _is_self(a.value) and a.attr in knobs & disabling
        }
        assert touched, f"{key} 的 {starter_name}() 不读任何构造器 knob，这一格该从表里删掉"
        # 两种门都算：(a) 类里点名调用起跑函数的 if（同步池在 ``__init__`` 里那道），
        # (b) 起跑函数自己体内**包住 spawn 那一行**的 if（异步池把门和起跑写在一起）。
        gates = [
            node
            for node in ast.walk(cls)
            if isinstance(node, ast.If)
            and any(
                isinstance(c, ast.Call)
                and isinstance(c.func, ast.Attribute)
                and c.func.attr == starter_name
                and _is_self(c.func.value)
                for stmt in node.body
                for c in ast.walk(stmt)
            )
        ]
        starter_parents = _parents(starter)
        for spawn_node in [
            n
            for n in ast.walk(starter)
            if _thread_call(n)
            or (
                isinstance(n, ast.Call)
                and isinstance(n.func, ast.Attribute)
                and n.func.attr == "create_task"
            )
        ]:
            cur: ast.AST | None = starter_parents.get(spawn_node)
            while cur is not None and cur is not starter:
                if isinstance(cur, ast.If):
                    gates.append(cur)
                cur = starter_parents.get(cur)
        assert gates, f"找不到 {key} 里拦住 {starter_name}() 起跑的 if 门"
        for gate in gates:
            read = {
                a.attr
                for a in ast.walk(gate.test)
                if isinstance(a, ast.Attribute) and _is_self(a.value)
            }
            missing = touched - read
            assert not missing, (
                f"{key} 的启动门只读到 {sorted(read)}，而它起的 {starter_name}() 还会读 {sorted(missing)}"
                f"——把没读到的那个 knob 调成关闭值会顺手关掉整条线程（第 26 轮 F-88 的形状）"
            )


# --------------------------------------------------------------------------- #
# 判据六：换了别人的钩子，就得给出逆操作
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class HookSite:
    module: str
    stamps: int
    reads: int


def _wrapped_mentions(node: ast.AST) -> tuple[int, int]:
    """本节点里对 ``__wrapped__`` 的 (写入次数, 读取次数)；``getattr(o, "__wrapped__")`` 算读。"""
    writes = reads = 0
    for n in ast.walk(node):
        if isinstance(n, ast.Attribute) and n.attr == "__wrapped__":
            writes += isinstance(n.ctx, ast.Store)
            reads += not isinstance(n.ctx, ast.Store)
        if (
            isinstance(n, ast.Call)
            and _func_name(n.func) == "getattr"
            and any(isinstance(a, ast.Constant) and a.value == "__wrapped__" for a in n.args)
        ):
            reads += 1
    return writes, reads


def test_hook_replacement_modules_export_their_inverse() -> None:
    sites: list[HookSite] = []
    for path, mod in _modules():
        writes, reads = _wrapped_mentions(mod)
        if writes or reads:
            sites.append(HookSite(_rel(path), writes, reads))
    assert sites, "全包没有任何换钩子现场：这条判据该和它的实现一起消失"
    for site in sites:
        assert site.stamps, f"{site.module} 只读 __wrapped__ 不写它，不属于本判据"
        assert site.reads, (
            f"{site.module} 往别人的对象上写了 {site.stamps} 次 __wrapped__，全包却没人读它"
            "——记了原方法又不去取回，等于没有还原之路"
        )
        mod = ast.parse((REPO_ROOT / site.module).read_text(encoding="utf-8"))
        exported: set[str] = set()
        for node in mod.body:
            if (
                isinstance(node, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id == "__all__" for t in node.targets)
                and isinstance(node.value, (ast.List, ast.Tuple))
            ):
                exported |= {e.value for e in node.value.elts if isinstance(e, ast.Constant)}
            if (
                isinstance(node, ast.Expr)
                and isinstance(node.value, ast.Call)
                and isinstance(node.value.func, ast.Attribute)
                and node.value.func.attr == "append"
                and _self_attr(node.value.func.value) is None
                and getattr(node.value.func.value, "id", "") == "__all__"
            ):
                for arg in node.value.args:
                    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                        exported.add(arg.value)
        inverses = []
        for name in sorted(exported):
            fn = next(
                (
                    n
                    for n in ast.walk(mod)
                    if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name
                ),
                None,
            )
            if fn is None:
                continue
            _, fn_reads = _wrapped_mentions(fn)
            restores = any(
                isinstance(n, ast.Assign)
                and any(isinstance(t, ast.Attribute) and t.attr != "__wrapped__" for t in n.targets)
                for n in ast.walk(fn)
            )
            if fn_reads and restores:
                inverses.append(name)
        assert inverses, (
            f"{site.module} 往别人的对象上打了 {site.stamps} 次钩子标记，公开面里却没有既读"
            " __wrapped__ 又把属性换回去的逆操作（attach 无 detach 就是永久替换）"
        )


# --------------------------------------------------------------------------- #
# 判据七：停机路径上的每个 await 要么自带截止期，要么就地写明等得起
# --------------------------------------------------------------------------- #

#: 停机图的函数名：从 ``AsyncConnectionPool.close()`` 走到"会悬住的等待"之前经过的每一层。
#: 名单必须**闭合**——这里每个名字都得在包里真有一个异步函数，否则当场红。指向改名幽灵的
#: 名单会让"内部调用因此可信任"变成橡皮图章：判据七对内部调用的信任，是靠"被调方自己也
#: 在这张判据下被逐格核对"换来的，不是靠名字像收尾。
_SHUTDOWN_FUNCS = frozenset(
    {
        "close",
        "__aexit__",
        "_drop",
        "_sweep_idle",
        "_cleanup_committed_close",
        "_await_cleanup_before_cancellation",
    }
)

#: 到点必回的 await：``asyncio.wait_for`` 自己就是截止期，``asyncio.sleep`` 睡的是定时器。
_DEADLINED_CALLS = frozenset({"wait_for", "sleep"})

#: 锁动词。单列一类不算绿也不算红，因为这条判据的射程本来就不含锁等待（见 docstring 末段）。
_LOCK_CALLS = frozenset({"acquire", "release"})

#: 归为"会自己悬住的等待"（leaf）的现场：键为 ``模块::类.方法``，值为"凭什么等得起"。
#: 双向一致——多一条（等待已经转成带截止期，登记表忘了收）与少一条（收尾路径上新增一次
#: 无界等待）都当场红。
UNBOUNDED_SHUTDOWN_AWAITS: dict[str, str] = {
    "tstdx/client/api.py::AsyncClient.close": (
        "``asyncio.to_thread(self.client.close)``：把**同步**池的收尾挪去线程，等的是那条"
        "线程跑完 ``ConnectionPool.close()``。它的上界由判据二给：那函数里两次 join 各带"
        "``_HEARTBEAT_JOIN_SECONDS`` / ``_SPEEDTEST_JOIN_SECONDS``，逐槽位 ``_drop`` 走的是"
        "同步套接字 close（本地系统调用，不等对端）。所以这一格等的是自己人的收尾，"
        "不是网络。"
    ),
    "tstdx/transport/async_.py::AsyncConnectionPool._cleanup_committed_close": (
        "``await heartbeat`` 的前一行就是 ``heartbeat.cancel()``：等的是那台心跳/回收循环"
        "自己退到下一个 await 边界，不等网络。它体内的等待只有三种——``asyncio.sleep`` 的"
        "节奏、``conn.ping()`` 那条 wait_for 读帧链、以及本判据逐格核对过的 ``_drop`` / "
        "``_release_*`` / ``_mark_*`` 收尾。"
    ),
    "tstdx/transport/async_.py::_await_cleanup_before_cancellation": (
        "``await asyncio.shield(cleanup_task)``：被 shield 的正是上一条那个 "
        "``_cleanup_committed_close`` 任务，它的等待由本判据核对；shield 改变的是"
        "「谁先被取消」（调用方取消不打断清理），不新增一次等待。"
    ),
}


def _await_verdict(node: ast.Await) -> str:
    """``deadline`` / ``internal`` / ``lock`` / ``leaf`` 四选一。"""
    val = node.value
    name = _func_name(val.func) if isinstance(val, ast.Call) else None
    if name in _DEADLINED_CALLS:
        return "deadline"
    if name in _SHUTDOWN_FUNCS:
        return "internal"
    if name in _LOCK_CALLS:
        return "lock"
    return "leaf"


def test_shutdown_path_awaits_carry_a_deadline_or_a_reason() -> None:
    leaves: dict[str, int] = {}
    seen_names: set[str] = set()
    checked = 0
    for path, mod in _modules():
        parents = _parents(mod)
        for fn in [n for n in ast.walk(mod) if isinstance(n, ast.AsyncFunctionDef)]:
            if fn.name not in _SHUTDOWN_FUNCS:
                continue
            seen_names.add(fn.name)
            for node in ast.walk(fn):
                if not isinstance(node, ast.Await):
                    continue
                checked += 1
                if _await_verdict(node) == "leaf":
                    leaves[f"{_rel(path)}::{_qualname(parents, node)}"] = node.lineno
    assert seen_names == set(_SHUTDOWN_FUNCS), (
        f"停机名单里有名字在包里已经不存在：现扫 {sorted(seen_names)}，名单 {sorted(_SHUTDOWN_FUNCS)}"
    )
    assert checked, "全包停机路径上一个 await 都没有：这条判据在空转"
    assert set(leaves) == set(UNBOUNDED_SHUTDOWN_AWAITS), (
        "停机路径上的无界等待与登记表不一致（现扫 "
        f"{sorted(leaves.items())}，登记表 {sorted(UNBOUNDED_SHUTDOWN_AWAITS)}）"
    )
