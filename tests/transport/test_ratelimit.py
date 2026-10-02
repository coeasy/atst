# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""C7 回归：TokenBucket 死等消除（tokens > burst 入口报错）+ SessionRateLimiter 行为保持。"""

from __future__ import annotations

import time

import pytest

from atst.errors import RateLimitedLocal
from atst.transport.ratelimit import SessionRateLimiter, SessionState, TokenBucket


def test_acquire_over_burst_raises_instead_of_dead_wait():
    """tokens > burst 数学上永不满足 → 入口 ValueError（旧实现 while True 永等）。"""
    tb = TokenBucket(rate=10.0, burst=5.0)
    with pytest.raises(ValueError, match="超过桶容量"):
        tb.acquire(6.0)
    with pytest.raises(ValueError):
        tb.acquire(5.1)
    # 边界：tokens == burst 允许
    assert tb.acquire(5.0) is True


def test_acquire_nonblocking_unchanged():
    tb = TokenBucket(rate=1.0, burst=1.0)
    assert tb.acquire(1.0) is True
    assert tb.acquire(1.0, blocking=False) is False  # 桶空且不等待


def test_acquire_blocking_timeout_unchanged():
    tb = TokenBucket(rate=50.0, burst=1.0)
    assert tb.acquire(1.0) is True
    start = time.monotonic()
    ok = tb.acquire(1.0, blocking=True, timeout=0.001)
    elapsed = time.monotonic() - start
    assert ok is False
    assert elapsed < 0.5  # deadline 生效，不死等


def test_session_limiter_strict_unchanged():
    limiter = SessionRateLimiter(
        {SessionState.CLOSED: 1.0}, state_fn=lambda: SessionState.CLOSED, strict=True
    )
    assert limiter.acquire().state == SessionState.CLOSED
    with pytest.raises(RateLimitedLocal):
        limiter.acquire()  # 桶容量 1 已耗尽 → strict 抛错（行为保持）


def test_session_limiter_blocking_unchanged():
    limiter = SessionRateLimiter(
        {SessionState.CLOSED: 200.0}, state_fn=lambda: SessionState.CLOSED, strict=False
    )
    limiter.acquire()  # 耗尽桶（burst=200 → 不空）
    # 第二次取 1 个：rate=200/s，桶满 → 几乎立即成功，验证阻塞路径可返回
    snap = limiter.acquire()
    assert snap.waited < 0.5
    assert snap.rate == 200.0


def test_session_limiter_over_burst_tokens_raise():
    """限流器路径同样受 C7 入口校验保护（不进入永等）。"""
    limiter = SessionRateLimiter(
        {SessionState.CLOSED: 1.0}, state_fn=lambda: SessionState.CLOSED, strict=False
    )
    with pytest.raises(ValueError):
        limiter.acquire(tokens=2.0)  # burst=1 < 2


class TestSessionStateUsesMarketTimezone:
    """交易时段必须按**市场时区**判断，不看机器本地时区（v1.2.1 CI 红的一手根因）。

    同一时刻在 UTC 机器与 UTC+8 机器上必须判出同一个交易状态——否则限流分档
    会随部署环境漂移（CI 在 UTC 上会把北京时间开盘时段判成休市）。
    """

    def test_aware_utc_input_is_converted_to_market_time(self):
        from datetime import datetime, timezone

        from atst.transport.ratelimit import session_state

        # 2026-10-02（周五）01:45 UTC == 09:45 +08:00 → 集合竞价后的连续交易
        utc_morning = datetime(2026, 10, 2, 1, 45, tzinfo=timezone.utc)
        naive_morning = datetime(2026, 10, 2, 9, 45)
        assert session_state(utc_morning) == session_state(naive_morning)
        assert session_state(utc_morning) == SessionState.CONTINUOUS

    def test_utc_evening_maps_to_after_close(self):
        from datetime import datetime, timezone

        from atst.transport.ratelimit import session_state

        # 16:00 UTC == 24:00 +08:00 → 次日休市
        assert session_state(datetime(2026, 10, 2, 16, 0, tzinfo=timezone.utc)) == (
            SessionState.CLOSED
        )

    def test_default_now_uses_market_timezone(self):
        """不传 now 时取市场时区的现在（不是机器本地 now）。"""
        from atst.domain.calendar import market_now
        from atst.transport.ratelimit import session_state

        assert isinstance(session_state(), str)
        assert market_now().tzinfo is None
