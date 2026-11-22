# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""清理任务忽略第一次取消时，``_await_cleanup_before_cancellation`` 不得永久挂住。

此前 deadline 到点后写的是裸 ``await asyncio.shield(task)``：shield 只隔离调用方
取消，本身没有超时。一个暂时卡住（吞掉第一次 cancel）的清理任务会让整条关闭链
无限等待，墙钟 deadline 形同虚设。现在收尾走 ``asyncio.wait(..., timeout=0.1)``。
"""

from __future__ import annotations

import asyncio
import contextlib
import time

import pytest

import atst.transport.async_ as async_transport
from atst.transport.async_ import _await_cleanup_before_cancellation

pytestmark = pytest.mark.unit


@pytest.mark.asyncio
async def test_cleanup_that_swallows_first_cancel_cannot_hang_forever(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # 把内部 deadline 缩到极短，令到点后的"收尾等待"成为唯一可能的挂点。
    monkeypatch.setattr(async_transport, "_CLEANUP_SHIELD_TIMEOUT", 0.01)
    started = asyncio.Event()

    async def swallows_once() -> None:
        started.set()
        # 吞掉第一次 cancel，模拟卡在不可取消的 to_thread 收尾上。
        with contextlib.suppress(asyncio.CancelledError):
            await asyncio.sleep(3600)

    task = asyncio.create_task(swallows_once())
    await started.wait()
    # 不加外层 wait_for：它自己的 cancel 会污染函数内部的 cancelled 标记。
    # 直接测函数本身能否在 deadline + 收尾窗口内返回（裸 shield 会永久挂住）。
    wall = time.monotonic()
    await _await_cleanup_before_cancellation(task)
    assert time.monotonic() - wall < 1.0, "收尾等待没有上界"
    assert task.done(), "收尾后清理任务应已结束"
