# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""T#1 回归：BackpressureQueue 溢出回调必须在锁外执行。

旧实现持 ``threading.Lock`` 调用 ``on_drop``，而 QuoteChannel 的回调会上报
``qsize()``（再抢同一把不可重入锁）——队列**首次溢出即 100% 确定性死锁**，
调用 tick() 的轮询线程永久挂死。
"""

from __future__ import annotations

import threading

import pytest

from atst.streaming.engine import BackpressureQueue, QuoteChannel


@pytest.mark.unit
class TestBackpressureNoDeadlock:
    """溢出回调锁外执行。"""

    def test_on_drop_calls_qsize_safely(self) -> None:
        """on_drop 内调用 qsize()（旧实现死锁路径）不再挂死。"""
        calls: list[tuple[object, int]] = []
        q = BackpressureQueue(maxsize=2, on_drop=lambda item: calls.append((item, q.qsize())))
        for i in range(5):
            q.put(i)
        assert len(calls) == 3, f"maxsize=2 放 5 条应弃 3 条，实际 {len(calls)}"
        assert all(size == 2 for _, size in calls), "回调内 qsize 可安全执行（锁外）"
        assert q.qsize() == 2

    def test_quote_channel_overflow_does_not_deadlock(self) -> None:
        """QuoteChannel 溢出丢弃 → 背压埋点 → qsize 上报全链路无死锁。"""
        ch = QuoteChannel(poll=lambda symbols: [], symbols=["sh600519"], max_queue=1)
        done = threading.Event()

        def _overflow() -> None:  # 模拟消费停滞下的连续入队
            for i in range(4):
                ch._bp.put({"code": "600519", "i": i})
            done.set()

        t = threading.Thread(target=_overflow, daemon=True)
        t.start()
        assert done.wait(timeout=5.0), "溢出路径 5s 内未完成 —— 疑似死锁回归"
        t.join(timeout=1.0)
        assert not t.is_alive()
