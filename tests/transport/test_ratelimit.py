# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""C7 回归：TokenBucket 死等消除（tokens > burst 入口报错）+ SessionRateLimiter 行为保持。"""

from __future__ import annotations

import time

import pytest

from tstdx.errors import RateLimitedLocal
from tstdx.transport.ratelimit import SessionRateLimiter, SessionState, TokenBucket


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
