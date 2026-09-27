# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""G47 — 异步池的心跳 / 空闲回收者必须在**真实客户端路径**上起跑，而且只有一处起跑点。

第 31 轮第 2 遍实测到的断链形状：:class:`~atst.transport.pool.ConnectionPool` 在
``__init__`` 末尾自己起跑心跳线程，而 :class:`~atst.transport.async_.AsyncConnectionPool`
把起跑写在 ``start_heartbeat()`` 里、只有 ``__aenter__`` 调它。``AsyncTdxClient`` 家族走的
是 ``open()``（构造池 → 惰性建连），从不进池的 ``async with``——于是** shipped 路径上
从来没有那条循环**：``idle_timeout`` 与 ``heartbeat_interval`` 两个旋钮在异步面是幻影旋钮，
一台长期存活的 ``AsyncTdxClient`` 会把每个槽位那条 TLS 连接一直握到 ``close()``，
熔断/运行期健康状态机也收不到任何探活回报。同步面同一个键是生效的。

第 26 轮 F-89 给异步池补上了 ``idle_timeout`` 与 ``_sweep_idle``，F-88 给起跑条件补上了
"两种 knob 各自成立"——这两刀都只保证**跑起来的那条循环**行为正确，没有一问"谁让它跑起来"
（量接缝两侧而不量接缝，V19 §10 教训 3 同族）。G41 判据五同理：它点名起跑函数、要求函数体
读全 knob，现扫确认过 ``start_heartbeat`` 读的是 ``heartbeat_interval`` + ``idle_timeout``，
但它不数调用点——所以那格一直是绿的，而生产里没人调用。

实测证据（同轮日志 ``Temp/g47_before.out``，修复前）：

* ``ARMED after a request: False`` —— 一次成功请求跑完，``pool._hb is None``；
* 对照组 ``async with pool`` → ``armed after __aenter__: True`` —— 唯一起跑点确实是那道门；
* ``conn reclaimed by sweeper: False`` —— ``idle_timeout=0.2`` 摆 1.4 秒，槽位那条连接没被
  回收（没有回收者）。

修复后的口径：起跑点搬到**池里第一次握住一条真 socket** 的地方
（:meth:`AsyncConnectionPool._get_conn_locked`），``async with`` 那道门删掉——没有连接就没
有可回收的东西，留着它只会多出第二个起跑点；进不进 ``async with`` 都不影响回收是否发生。

判据不看分支写法也不看注释，只看三件事：① 走真实客户端对象（``client._pool``）发一次请求
之后回收者确实在跑；② ``idle_timeout`` 在那条路上真的把闲置连接扫走；③ 起跑调用点在包里
按函数名现扫，每个起跑函数恰好一处且落在池自己的类里——搬到客户端面上、或者把旧门种回去，
都当场红（最后一格自己就是那次种桩）。
"""

from __future__ import annotations

import ast
import asyncio
import sys
from pathlib import Path

import pytest

from atst.client.async_ import AsyncTdxClient
from atst.transport.async_ import AsyncConnectionPool
from atst.transport.hosts import HostEntry
from atst.transport.pool import ConnectionPool

REPO_ROOT = Path(__file__).resolve().parents[2]
PACKAGE_ROOT = REPO_ROOT / "atst"

sys.path.insert(0, str(Path(__file__).parent))
from fake_server import ECHO_CMD, FakeTdxServer  # noqa: E402

#: 回收者/心跳的起跑函数名：同步池一个、异步池一个。名字各自唯一，所以名字就是登记键。
ARM_FUNCTIONS = frozenset({"_start_heartbeat", "start_heartbeat"})

#: 每个起跑函数在包里**只允许**有一个调用点，而且必须住在池自己的类里：
#: 同步池落在 ``__init__``（构造即持有槽位），异步池落在 ``_get_conn_locked``
#: （第一次握住真 socket 时武装）。
EXPECTED_ARM_SITES = {
    "_start_heartbeat": {"atst/transport/pool.py::ConnectionPool.__init__"},
    "start_heartbeat": {"atst/transport/async_.py::AsyncConnectionPool._get_conn_locked"},
}


def _label(call: ast.Call, parents: dict[ast.AST, ast.AST]) -> str:
    """调用点写成 ``模块路径::类.方法``：从调用往上爬，先撞见的方法再撞见的类。"""
    fn: ast.FunctionDef | ast.AsyncFunctionDef | None = None
    cur: ast.AST | None = call
    while cur is not None:
        if fn is None and isinstance(cur, (ast.FunctionDef, ast.AsyncFunctionDef)):
            fn = cur
        if isinstance(cur, ast.ClassDef):
            return f"{cur.name}.{fn.name}" if fn is not None else cur.name
        cur = parents.get(cur)
    return f"<module>.{fn.name}" if fn is not None else "<module>"


def _arm_sites(source: str, rel: str) -> dict[str, set[str]]:
    """现扫一份源码：``起跑函数名 -> {调用点}``。

    用 AST 而不是子串（G42 同一把尺）：注释与文档字符串里提到函数名不算调用点。
    接收者不作限制——抄成 ``self._pool.start_heartbeat()`` 同样是第二个起跑点，
    它比 ``self.start_heartbeat()`` 更坏，因为它在池外重新解释了池的节奏。
    """
    mod = ast.parse(source)
    parents: dict[ast.AST, ast.AST] = {
        child: node for node in ast.walk(mod) for child in ast.iter_child_nodes(node)
    }
    found: dict[str, set[str]] = {}
    for call in [n for n in ast.walk(mod) if isinstance(n, ast.Call)]:
        func = call.func
        if isinstance(func, ast.Attribute) and func.attr in ARM_FUNCTIONS:
            found.setdefault(func.attr, set()).add(f"{rel}::{_label(call, parents)}")
    return found


def _scanned_arm_sites() -> dict[str, set[str]]:
    merged: dict[str, set[str]] = {}
    for path in sorted(PACKAGE_ROOT.rglob("*.py")):
        rel = path.relative_to(REPO_ROOT).as_posix()
        for arm, sites in _arm_sites(path.read_text(encoding="utf-8"), rel).items():
            merged.setdefault(arm, set()).update(sites)
    return merged


def _client_on(server: FakeTdxServer, **pool_kwargs: object) -> AsyncTdxClient:
    """按 shipped 路径建客户端：构造 → ``open()``，**不进** ``async with pool``。"""
    return AsyncTdxClient(
        hosts=[HostEntry("127.0.0.1", server.port)],
        slots_per_host=1,
        timeout=5.0,
        handshake=False,
        **pool_kwargs,  # type: ignore[arg-type]
    )


@pytest.mark.unit
def test_a_request_through_the_client_arms_the_reclaimer() -> None:
    """① 真实客户端路径上的一次请求之后，回收者任务必须存在。

    修复前实测 ``ARMED after a request: False``（``Temp/g47_before.out``）。
    """

    async def main() -> bool:
        with FakeTdxServer() as server:
            client = _client_on(server, heartbeat_interval=0, idle_timeout=300.0)
            try:
                frame = await client._pool.request(ECHO_CMD, b"\x07")
                assert frame.payload[4:] == b"\x07", "罐头主站没回显，这一格量的不是传输路径"
                return client._pool._hb is not None
            finally:
                await client.close()

    assert asyncio.run(main()), "跑完一次请求回收者仍未起跑：idle_timeout 是幻影旋钮"


@pytest.mark.unit
def test_arming_is_idempotent_across_requests() -> None:
    """起跑点搬到热路径后，重复请求不得攒出第二条循环（``_hb`` 只有一条）。"""

    async def main() -> tuple[object, object]:
        with FakeTdxServer() as server:
            client = _client_on(server, heartbeat_interval=0, idle_timeout=300.0)
            try:
                await client._pool.request(ECHO_CMD, b"\x01")
                first = client._pool._hb
                await client._pool.request(ECHO_CMD, b"\x02")
                return first, client._pool._hb
            finally:
                await client.close()

    first, second = asyncio.run(main())
    assert first is not None and second is not None
    assert first is second, "两次请求起了两条回收循环"


@pytest.mark.unit
def test_idle_timeout_reclaims_on_the_client_path() -> None:
    """② ``idle_timeout`` 在真实客户端路径上必须把闲置连接扫走。

    节奏读的是异步循环自己的现算式：``heartbeat_interval=0`` 时 ``tick =
    max(1.0, idle_timeout / 4)``，所以 0.2 秒阈值也要等满一拍才回收（修复前实测
    ``conn reclaimed by sweeper: False``）。
    """

    async def main() -> tuple[bool, bool]:
        with FakeTdxServer() as server:
            pool = AsyncConnectionPool(
                [HostEntry("127.0.0.1", server.port)],
                slots_per_host=1,
                timeout=5.0,
                heartbeat_interval=0,
                idle_timeout=0.2,
                handshake=False,
            )
            try:
                await pool.request(ECHO_CMD, b"\x01")
                held = pool._slots[0].conn is not None
                await asyncio.sleep(1.4)
                return held, pool._slots[0].conn is None
            finally:
                await pool.close()

    held, reclaimed = asyncio.run(main())
    assert held, "请求之后槽位没握住连接，这一格量不到回收"
    assert reclaimed, "回收者没跑或 idle_timeout 没被它读到"


@pytest.mark.unit
def test_the_reclaimer_has_exactly_one_arm_site_per_pool() -> None:
    """③ 起跑点唯一：每个起跑函数的调用点在包里只出现一次，且落在池自己家。

    这条判据防的是"抄第二份"——第 10 到 12 轮把八张面收回一份声明表之后，各面手抄名单
    已经清零，传输面再长出第二个起跑点就是回潮（G40 同族：声明与行动各写一处）。
    """
    found = _scanned_arm_sites()
    assert found == EXPECTED_ARM_SITES, f"起跑点与登记表不一致：现扫 {found}"


@pytest.mark.unit
def test_planted_second_arm_site_is_caught() -> None:
    """判据自身不是装饰：把旧门种回 ``__aenter__``，或者把起跑抄到客户端面上，都必须被看见。

    两个桩都是第 31 轮真实存在过的形状——前者是修复前的唯一起跑点，后者是"搬到热路径顺手
    在客户端也补一刀"的直觉写法。
    """
    rel = "atst/transport/async_.py"
    real = (REPO_ROOT / "atst" / "transport" / "async_.py").read_text(encoding="utf-8")
    assert _arm_sites(real, rel) == {
        "start_heartbeat": {f"{rel}::AsyncConnectionPool._get_conn_locked"}
    }, "对照基准就不对，这一格自证失败"

    anchor = "    async def __aenter__(self) -> AsyncConnectionPool:\n"
    old_door = real.replace(anchor, anchor + "        self.start_heartbeat()\n", 1)
    assert old_door != real, "种桩锚点没命中，这一格自证失败"
    assert _arm_sites(old_door, rel)["start_heartbeat"] == {
        f"{rel}::AsyncConnectionPool._get_conn_locked",
        f"{rel}::AsyncConnectionPool.__aenter__",
    }, "旧门种回去之后判据没看见第二个起跑点"

    client_rel = "atst/client/async_.py"
    client_src = (REPO_ROOT / "atst" / "client" / "async_.py").read_text(encoding="utf-8")
    anchor = "        if bestip:\n"
    copied = client_src.replace(anchor, "        self._pool.start_heartbeat()\n" + anchor, 1)
    assert copied != client_src, "客户端种桩锚点没命中"
    assert _arm_sites(copied, client_rel) == {
        "start_heartbeat": {f"{client_rel}::AsyncTdxClient.open"}
    }, "起跑抄到池外之后判据没看见它"


@pytest.mark.unit
def test_sync_face_still_arms_in_its_constructor() -> None:
    """同步孪生不得跟着搬走：它没有惰性建连，构造即持有槽位，``__init__`` 就是那唯一一处。

    这里量的是行为（线程真在跑），不是登记表自己等于自己。
    """
    with FakeTdxServer() as server:
        pool = ConnectionPool(
            [HostEntry("127.0.0.1", server.port)],
            slots_per_host=1,
            timeout=5.0,
            heartbeat_interval=0,
            idle_timeout=300.0,
            handshake=False,
        )
        try:
            assert pool._hb is not None and pool._hb.is_alive(), "同步面的起跑点被搬走了"
            source = (REPO_ROOT / "atst" / "transport" / "pool.py").read_text(encoding="utf-8")
            assert _arm_sites(source, "atst/transport/pool.py") == {
                "_start_heartbeat": {"atst/transport/pool.py::ConnectionPool.__init__"}
            }, "同步池的起跑点不再唯一落在构造里"
        finally:
            pool.close()
