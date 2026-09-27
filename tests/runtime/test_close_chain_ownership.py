# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""G41 / G40 — ``Client.close()`` 这条释放链的每一格都量一遍：谁关、关什么、关不到什么。

第 31 轮第 3 遍是对着这条链核文档的。三面（HTTP 的 lifespan、WS 的
``RuntimeJsonRpcHandler.close()``、MCP 的 ``stop()``）都把自己造的那份 ``Client`` 交给它收尾，
用户文档于是把它写成"释放内核连接"、WS 的注释写成"连带它的连接池"。实测三条，都在本轮取证：

* 构造一个 ``Client()`` 之后进程线程数增量为 **0**，它手里只有 ``_bindings`` / ``config`` /
  ``hosts`` / ``timeout`` / ``vipdoc_root`` 五个值，没有任何跨调用连接；
* 内置的 ``DirectProviderExecutor`` **没有** ``close()``：家族客户端不在它身上，每一跳都是
  构造 → ``try`` → ``finally: client.close()``（第 31 轮 31-B4 起手、31-C3 收完：执行器里
  现扫 13 处交接全在 :meth:`DirectProviderExecutor._client_session` 之后，判据
  ``tests/architecture/test_client_family_transport.py`` 第 4 件事）；
* 注入的执行器只要定义了 ``close()``，``UnifiedRuntime.close()`` 就敲得到它。

所以这条链在默认内核上是**空转**，它真正兑现的是两件别的东西：**谁造谁关**的所有权协议
（借来的 runtime 不许被借走的人关掉），和注入执行器那条形同协议的口子。文档里"释放连接池"
那句是过头主张，本轮改成如实口径；本文件把上面三条钉住，谁把口径改回去它就红。

变异台账（G14 改前必须红，见 ``docs/REFACTOR_PLAN_V19_RESTRUCTURE.md`` §11；本轮合成一本
``Temp/mut31d_all.py``，候选树落锚、按字节回滚复核 sha256，该电池读数 **19 条 / BAD=0**，
本文件基线 7 格全绿）：
``M9a`` 把 ``UnifiedRuntime.close`` 的探测换成 ``close = None``（永远不调）→ **2 红 / 5 绿**：
``test_close_reaches_an_injected_executor_that_owns_a_pool`` 与
``test_repeated_close_releases_an_owned_executor_exactly_once`` 一起红——口子焊死之后，"注入的
执行器会被关到"和"关两次只释放一次"是同一条链上的两格；
``M9b`` 把 ``Client.__init__`` 的所有权判定改成永远持有（``_owns_runtime = True``）→ **1 红**：
``test_a_borrowed_runtime_is_not_closed_by_the_client``；
``M10`` 去掉 ``Client.close`` 里"关后置回 ``_owns_runtime``"那一行 → **1 红**：
``test_repeated_close_releases_an_owned_executor_exactly_once``；
``M11`` 去掉 ``AsyncClient.close`` 里同一条回置 → **1 红**：
``test_repeated_async_close_releases_the_built_client_exactly_once``。
"""

from __future__ import annotations

import asyncio
import threading
from typing import Any

import pytest

from tstdx.client.api import AsyncClient, Client
from tstdx.runtime.kernel import UnifiedRuntime


class _RecordingExecutor:
    """带 ``close()`` 的执行器：注入进来，用来量释放链通不通。"""

    def __init__(self) -> None:
        self.executed = 0
        self.closed = 0

    def execute(self, plan: Any) -> Any:  # pragma: no cover - 本文件不发查询
        self.executed += 1
        return None

    def close(self) -> None:
        self.closed += 1


def _new_threads(snapshot: set[int]) -> list[str]:
    return [t.name for t in threading.enumerate() if id(t) not in snapshot]


def test_constructing_a_client_leaves_no_thread_behind() -> None:
    """``Client()`` 不起手任何线程，因此 ``close()`` 在默认内核上没有池可关。

    这条量的是"没有后台线程"这一事实本身：一旦有人在构造期起了池或心跳线程，这里立刻红，
    而那次红必须同时回答"``close()`` 关不关得到它"——否则用户文档里那句话就又变成一次
    凭空主张。
    """
    baseline = set(map(id, threading.enumerate()))
    client = Client()
    try:
        assert _new_threads(baseline) == []
    finally:
        client.close()
    assert _new_threads(baseline) == []


def test_the_default_executor_holds_nothing_between_calls() -> None:
    """执行器实例字段只有配置读数，没有任何"下一次还要用"的连接。"""
    executor = UnifiedRuntime().executor
    assert {name for name in vars(executor) if not name.startswith("__")} == {
        "_bindings",
        "config",
        "hosts",
        "timeout",
        "vipdoc_root",
    }
    assert not callable(getattr(executor, "close", None))


def test_close_reaches_an_injected_executor_that_owns_a_pool() -> None:
    """口子是活的：注入执行器定义了 ``close()``，``UnifiedRuntime.close()`` 必须敲到它。"""
    executor = _RecordingExecutor()
    runtime = UnifiedRuntime(executor=executor)
    runtime.close()
    assert executor.closed == 1
    runtime.close()
    assert executor.closed == 2, "close 不幂等与否由执行器自己定，内核只负责转达"


def test_a_borrowed_runtime_is_not_closed_by_the_client() -> None:
    """所有权口径：``Client(runtime=...)`` 借来的那份内核，客户端不关。"""
    executor = _RecordingExecutor()
    runtime = UnifiedRuntime(executor=executor)
    Client(runtime=runtime).close()
    assert executor.closed == 0
    assert runtime.executor is executor, "close 之后内核还得是原来那份"


def test_an_async_client_closes_only_the_client_it_built() -> None:
    """异步镜像同一条口径：``AsyncClient(client=...)`` 传入的那份客户端归调用方收尾。"""
    executor = _RecordingExecutor()
    borrowed = Client(runtime=UnifiedRuntime(executor=executor))
    asyncio.run(AsyncClient(client=borrowed).close())
    assert executor.closed == 0


@pytest.mark.unit
def test_repeated_close_releases_an_owned_executor_exactly_once() -> None:
    """``with`` 出口和显式 ``close()`` 抢着关，也只关一次——注入的执行器不必自己防重。

    ``Client(executor=...)`` 是 ``Client`` 自己造内核、内核自己带池的那条形同协议的口子：
    构造参数原样递进 ``UnifiedRuntime``，所以这里注入的执行器就是被释放的那一份。
    """
    executor = _RecordingExecutor()
    with Client(executor=executor) as client:
        client.close()
    assert executor.closed == 1


@pytest.mark.unit
def test_repeated_async_close_releases_the_built_client_exactly_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """异步侧同一格：第二次 ``await close()`` 不得再敲自建的客户端。"""

    class _SpyClient:
        def __init__(self, **kwargs: Any) -> None:
            self.closed = 0

        def close(self) -> None:
            self.closed += 1

    monkeypatch.setattr("tstdx.client.api.Client", _SpyClient)
    fresh = AsyncClient()
    assert isinstance(fresh.client, _SpyClient)
    asyncio.run(_close_twice(fresh))
    assert fresh.client.closed == 1


async def _close_twice(client: Any) -> None:
    await client.close()
    await client.close()
