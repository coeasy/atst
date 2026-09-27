# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""G50/G40 — 流式订阅的上下文协议必须两面同形：``with`` 与 ``async with`` 都能起止停。

``Client.stream()`` 交回来的 :class:`~tstdx.streaming.stateful.StatefulQuoteStream` 继承了
:class:`~tstdx.streaming.base.QuoteStream` 的 ``__enter__``/``__exit__``（进 = ``start()``、
出 = ``stop()``），所以 ``with client.stream(...) as s:`` 一直是可用的公开写法。它的异步孪生
:func:`~tstdx.client.api.AsyncClient.stream` 交回 :class:`AsyncStatefulQuoteStream`，而
:class:`~tstdx.streaming.base.AsyncQuoteStream` **没有** ``__aenter__``/``__aexit__``——
``async with client.stream(...)`` 当场 :class:`AttributeError`。接口文档写着"`AsyncClient` 是
同名异步镜像"（§2），这一格两个面给了不同答案；第 31 轮之前全仓也没有一条判据碰过这两对
dunder（同步那半边同样是零测试的"声明了没人量"）。

判据看四件事，不看注释：

① 两张公开生命周期类各自带齐自己那一面的上下文协议（缺半边即红）；
② 同步：进块后 worker 线程活着且状态 ``RUNNING``，出块后线程退出且状态 ``CLOSED``；
③ 异步：同一件事在任务句柄与状态机上成立；
④ 块体抛错时出块照样收尾（这是 G41 第四条"own 来的资源不许留在早退分支之后"在流上的形状）。

替身 runtime 的 ``quotes()`` 一律抛 :class:`~tstdx.errors.TdxError`：轮询内核按退避等
``stop()`` 叫醒（``_stop.wait`` / ``_sleep_or_stop``），所以 worker 会一直活着而不发任何真请求。

变异台账（G14 改前必须红，见 ``docs/REFACTOR_PLAN_V19_RESTRUCTURE.md`` §11；本轮合成一本
``Temp/mut31d_all.py``，候选树落锚、按字节回滚复核 sha256，该电池读数 **19 条 / BAD=0**，
本文件基线 8 格全绿）：
``M12`` 把 ``AsyncQuoteStream.__aexit__`` 的 ``await self.stop()`` 去掉 → **2 红 / 6 绿**：
``test_the_async_stream_starts_on_enter_and_stops_on_exit``（出块没收尾）与
``test_a_raising_block_still_stops_the_async_stream``（块体炸掉那一支同样没人收尾）；
``M13`` 把 ``__aenter__`` 改成 ``return self``（不起 worker）→ **1 红 / 7 绿**：
``test_the_async_stream_starts_on_enter_and_stops_on_exit``——"进块"这件事只剩形状。
"""

from __future__ import annotations

import asyncio
import threading

import pytest

from tstdx.errors import TdxError
from tstdx.streaming import AsyncStatefulQuoteStream, StatefulQuoteStream
from tstdx.streaming.base import AsyncQuoteStream, QuoteStream
from tstdx.streaming.state import StreamState


class _FailingRuntime:
    """只满足轮询内核的外部依赖：一被问行情就抛错，并记下自己被关过没有。"""

    def __init__(self) -> None:
        self.closed = 0

    def quotes(self, symbols, **kwargs):  # noqa: ANN001, ANN202 - 替身按内核形状写
        del symbols, kwargs
        raise TdxError("离线判据：不发出任何真实请求")

    def close(self) -> None:
        self.closed += 1


@pytest.mark.unit
@pytest.mark.parametrize(
    ("cls", "protocol"),
    [
        (QuoteStream, ("__enter__", "__exit__")),
        (StatefulQuoteStream, ("__enter__", "__exit__")),
        (AsyncQuoteStream, ("__aenter__", "__aexit__")),
        (AsyncStatefulQuoteStream, ("__aenter__", "__aexit__")),
    ],
)
def test_each_half_carries_its_own_context_protocol(cls: type, protocol: tuple) -> None:
    """镜像声明的落点：同步半边有的那对 dunder，异步半边必须有它的 ``a`` 版。"""
    missing = [name for name in protocol if not callable(getattr(cls, name, None))]
    assert not missing, f"{cls.__name__} 少了 {missing}：这一面的起止停没有上下文写法"


@pytest.mark.unit
def test_the_sync_stream_starts_on_enter_and_stops_on_exit() -> None:
    runtime = _FailingRuntime()
    stream = StatefulQuoteStream(runtime=runtime, provider="tdx")
    stream.subscribe("sh600000", interval=0.05)
    with stream as entered:
        assert entered is stream
        assert stream.state is StreamState.RUNNING
        worker: threading.Thread | None = stream._thread
        assert worker is not None and worker.is_alive()
    assert stream.state is StreamState.CLOSED
    assert worker is not None and not worker.is_alive()


@pytest.mark.unit
def test_the_async_stream_starts_on_enter_and_stops_on_exit() -> None:
    runtime = _FailingRuntime()

    async def _scenario() -> None:
        stream = AsyncStatefulQuoteStream(runtime=runtime, provider="tdx")
        stream.subscribe("sh600000", interval=0.05)
        async with stream as entered:
            assert entered is stream
            assert stream.state is StreamState.RUNNING
            task = stream._task
            assert task is not None and not task.done()
            await asyncio.sleep(0)
        assert stream.state is StreamState.CLOSED
        assert task is not None and task.done()

    asyncio.run(_scenario())


@pytest.mark.unit
def test_a_raising_block_still_stops_the_sync_stream() -> None:
    stream = StatefulQuoteStream(runtime=_FailingRuntime(), provider="tdx")
    stream.subscribe("sh600000", interval=0.05)
    with pytest.raises(RuntimeError, match="块体抛错"), stream:
        raise RuntimeError("块体抛错")
    assert stream.state is StreamState.CLOSED
    assert stream._thread is None


@pytest.mark.unit
def test_a_raising_block_still_stops_the_async_stream() -> None:
    async def _scenario() -> tuple[AsyncStatefulQuoteStream, object]:
        stream = AsyncStatefulQuoteStream(runtime=_FailingRuntime(), provider="tdx")
        stream.subscribe("sh600000", interval=0.05)
        with pytest.raises(RuntimeError, match="块体抛错"):
            async with stream:
                raise RuntimeError("块体抛错")
        return stream, stream._task

    stream, task = asyncio.run(_scenario())
    assert stream.state is StreamState.CLOSED
    assert task is None or task.done()
