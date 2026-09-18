# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""本地交易时段感知限流（§12.4）。

设计目标：
* 不依赖网络/服务端状态；
* 交易时段分档（call auction / continuous / noon / closed）；
* token-bucket，可阻塞或 fail-fast；
* 所有数值/布尔配置严格校验，拒绝隐式 coercion；
* 自定义状态函数异常/未知返回值按 CLOSED fail-safe。
"""

from __future__ import annotations

import datetime as _dt
import math
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from ..errors import RateLimitedLocal

__all__ = [
    "SessionState",
    "TokenBucket",
    "SessionRateLimiter",
    "session_state",
]


class SessionState:
    CALL_AUCTION = "call_auction"
    CONTINUOUS = "continuous"
    NOON_BREAK = "noon_break"
    CLOSED = "closed"


_SESSION_STATES = frozenset(
    {
        SessionState.CALL_AUCTION,
        SessionState.CONTINUOUS,
        SessionState.NOON_BREAK,
        SessionState.CLOSED,
    }
)


DEFAULT_RATES: dict[str, float] = {
    SessionState.CALL_AUCTION: 80.0,
    SessionState.CONTINUOUS: 120.0,
    SessionState.NOON_BREAK: 25.0,
    SessionState.CLOSED: 15.0,
}


def _require_positive_number(name: str, value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} 必须是正有限数值，收到 {value!r}")
    normalized = float(value)
    if not math.isfinite(normalized) or normalized <= 0:
        raise ValueError(f"{name} 必须是正有限数值，收到 {value!r}")
    return normalized


def _require_nonnegative_timeout(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"timeout 必须是非负有限数值或 None，收到 {value!r}")
    normalized = float(value)
    if not math.isfinite(normalized) or normalized < 0:
        raise ValueError(f"timeout 必须是非负有限数值或 None，收到 {value!r}")
    return normalized


def _require_bool(name: str, value: Any) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{name} 必须是 bool，收到 {value!r}")
    return value


def _validated_rates(rates: Mapping[str, Any] | None) -> dict[str, float]:
    if rates is None:
        return {}
    if not isinstance(rates, Mapping):
        raise ValueError(f"rates 必须是 Mapping，收到 {type(rates).__name__}")
    unknown = sorted(str(state) for state in rates if state not in _SESSION_STATES)
    if unknown:
        raise ValueError(f"rates 包含未知交易状态: {unknown!r}")
    return {
        state: _require_positive_number(f"rates[{state!r}]", value)
        for state, value in rates.items()
    }


def session_state(now: _dt.datetime | None = None) -> str:
    """返回中国 A 股当前交易状态（仅按本地时间段，不判断节假日）。"""

    now = now or _dt.datetime.now()
    if now.weekday() >= 5:
        return SessionState.CLOSED
    t = now.time()
    if _dt.time(9, 15) <= t < _dt.time(9, 30):
        return SessionState.CALL_AUCTION
    if _dt.time(9, 30) <= t < _dt.time(11, 30):
        return SessionState.CONTINUOUS
    if _dt.time(11, 30) <= t < _dt.time(13, 0):
        return SessionState.NOON_BREAK
    if _dt.time(13, 0) <= t < _dt.time(15, 0):
        return SessionState.CONTINUOUS
    return SessionState.CLOSED


class TokenBucket:
    """线程安全 token-bucket。``rate`` 单位 tokens/sec。"""

    def __init__(self, rate: float, *, burst: float | None = None) -> None:
        normalized_rate = _require_positive_number("rate", rate)
        normalized_burst = (
            max(1.0, normalized_rate) if burst is None else _require_positive_number("burst", burst)
        )
        self.rate = normalized_rate
        self.burst = normalized_burst
        self._tokens = normalized_burst
        self._last = time.monotonic()
        self._lock = threading.Lock()

    def _refill(self) -> None:
        now = time.monotonic()
        elapsed = now - self._last
        if elapsed > 0:
            self._tokens = min(self.burst, self._tokens + elapsed * self.rate)
            self._last = now

    def acquire(
        self,
        tokens: float = 1.0,
        *,
        blocking: bool = True,
        timeout: float | None = None,
    ) -> bool:
        """获取 token；``blocking=False`` 时不足立即返回 False。"""

        requested = _require_positive_number("tokens", tokens)
        blocking_enabled = _require_bool("blocking", blocking)
        wait_timeout = _require_nonnegative_timeout(timeout)
        if requested > self.burst:
            # 桶容量永远达不到该请求量；旧实现会在 blocking=True 时死循环。
            raise ValueError(f"请求 tokens={requested:g} 超过桶容量 burst={self.burst:g}")

        deadline = None if wait_timeout is None else time.monotonic() + wait_timeout
        while True:
            with self._lock:
                self._refill()
                if self._tokens >= requested:
                    self._tokens -= requested
                    return True
                missing = requested - self._tokens
                wait_for = missing / self.rate
            if not blocking_enabled:
                return False
            if deadline is not None and time.monotonic() + wait_for > deadline:
                return False
            time.sleep(min(wait_for, 0.05))

    @property
    def available(self) -> float:
        with self._lock:
            self._refill()
            return self._tokens

    def set_rate(self, rate: float) -> None:
        normalized_rate = _require_positive_number("rate", rate)
        with self._lock:
            self._refill()
            self.rate = normalized_rate
            self.burst = max(1.0, normalized_rate)
            self._tokens = min(self._tokens, self.burst)


@dataclass
class RateSnapshot:
    state: str
    rate: float
    available: float
    waited: float = 0.0


class SessionRateLimiter:
    """按交易状态自动切换速率的本地限流器。

    Parameters
    ----------
    rates:
        状态 -> requests/sec；缺省使用 :data:`DEFAULT_RATES`。
    state_fn:
        可注入自定义交易状态函数；异常或未知状态 fail-safe 为 ``closed``。
    strict:
        True 时 token 不足抛 :class:`RateLimitedLocal`，False 时阻塞等待。
    """

    def __init__(
        self,
        rates: Mapping[str, float] | None = None,
        *,
        state_fn: Callable[[], str] | None = None,
        strict: bool = False,
    ) -> None:
        overrides = _validated_rates(rates)
        if state_fn is not None and not callable(state_fn):
            raise ValueError(f"state_fn 必须是 callable 或 None，收到 {type(state_fn).__name__}")
        self._rates = {
            state: overrides.get(state, default) for state, default in DEFAULT_RATES.items()
        }
        self._buckets = {state: TokenBucket(rate) for state, rate in self._rates.items()}
        self._state_fn = state_fn or session_state
        self.strict = _require_bool("strict", strict)
        self._lock = threading.Lock()

    @property
    def state(self) -> str:
        try:
            state = self._state_fn()
        except Exception:
            return SessionState.CLOSED
        return state if state in _SESSION_STATES else SessionState.CLOSED

    @property
    def rate(self) -> float:
        return self._rates[self.state]

    def acquire(self, tokens: float = 1.0) -> RateSnapshot:
        state = self.state
        bucket = self._buckets[state]
        started = time.perf_counter()
        ok = bucket.acquire(tokens, blocking=not self.strict, timeout=0.0 if self.strict else None)
        waited = time.perf_counter() - started
        if not ok:
            raise RateLimitedLocal(
                f"本地限流：{state} 时段速率 {bucket.rate:.0f}/s",
                context={
                    "state": state,
                    "rate": bucket.rate,
                    "available": bucket.available,
                },
            )
        return RateSnapshot(
            state=state,
            rate=bucket.rate,
            available=bucket.available,
            waited=waited,
        )

    def try_acquire(self, tokens: float = 1.0) -> bool:
        state = self.state
        return self._buckets[state].acquire(tokens, blocking=False)

    def set_rates(self, **rates: float) -> None:
        """运行时更新速率；未知状态/非法值整体失败，不产生部分更新。"""

        validated = _validated_rates(rates)
        with self._lock:
            for state, rate in validated.items():
                self._rates[state] = rate
                self._buckets[state].set_rate(rate)

    def snapshot(self) -> dict[str, RateSnapshot]:
        return {
            state: RateSnapshot(state, self._rates[state], bucket.available)
            for state, bucket in self._buckets.items()
        }

    @classmethod
    def from_config(cls, config: Any) -> SessionRateLimiter:
        """从 :class:`~tstdx.config.schema.RateLimitConfig` 构建（避免顶层循环导入）。

        属性直接访问而非 ``getattr(..., 默认)``：读不到的键名就是配置面的谎话，
        必须报错而不是悄悄退回内置速率。
        """

        rates = {
            SessionState.CALL_AUCTION: config.call_auction,
            SessionState.CONTINUOUS: config.continuous,
            SessionState.NOON_BREAK: config.noon_break,
            SessionState.CLOSED: config.closed,
        }
        return cls(rates, strict=config.strict)
