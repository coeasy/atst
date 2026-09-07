# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""按交易状态分档的本地请求限流（§12.4）。

为什么要分档
------------
通达信主站对高频请求敏感：盘中并发过猛容易被静默断连甚至短暂封 IP。
因此本地侧按交易状态给出三档 **建议速率**（req/s）：

===========  ==============================  ==========
状态          时间范围                         默认 req/s
===========  ==============================  ==========
in_session   交易日 09:15–11:30 / 13:00–15:00    15
pre_post     交易日其余时段                      30
closed       非交易日（含周末 / 法定节假日）      60
===========  ==============================  ==========

限流器是**纯本地**的：它不感知服务端到底限了多少，只保证本地不超速。
真正的 429/断连由 :mod:`tstdx.transport.pool` 的故障转移处理。
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from ..domain.calendar import is_trading_day

__all__ = [
    "SessionState",
    "session_state",
    "TokenBucket",
    "SessionRateLimiter",
]

_CST = timezone(timedelta(hours=8))

#: 盘中（含集合竞价）
_IN_SESSION_RANGES = ((9 * 60 + 15, 11 * 60 + 30), (13 * 60, 15 * 60))


class SessionState:
    IN_SESSION = "in_session"
    PRE_POST = "pre_post"
    CLOSED = "closed"


def session_state(now: datetime | None = None) -> str:
    """判定给定时刻所属的交易状态档位。

    ``now`` 为 naive datetime 时按 Asia/Shanghai 处理；aware datetime 会先转成本地时区。
    """
    if now is None:
        now = datetime.now(_CST)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=_CST)
    else:
        now = now.astimezone(_CST)

    if not is_trading_day(now.date()):
        return SessionState.CLOSED
    minutes = now.hour * 60 + now.minute
    for lo, hi in _IN_SESSION_RANGES:
        if lo <= minutes < hi:
            return SessionState.IN_SESSION
    return SessionState.PRE_POST


# --------------------------------------------------------------------------- #
# 令牌桶
# --------------------------------------------------------------------------- #
class TokenBucket:
    """线程安全令牌桶。

    ``rate`` 为每秒补充速率，``burst`` 为桶容量（允许的瞬时突发）。
    """

    __slots__ = ("rate", "burst", "_tokens", "_last", "_lock")

    def __init__(self, rate: float, burst: float | None = None) -> None:
        if rate <= 0:
            raise ValueError("rate 必须为正数")
        self.rate = float(rate)
        self.burst = float(burst if burst is not None else max(1.0, rate))
        self._tokens = self.burst
        self._last = time.monotonic()
        self._lock = threading.Lock()

    def _refill(self, now: float) -> None:
        elapsed = now - self._last
        if elapsed > 0:
            self._tokens = min(self.burst, self._tokens + elapsed * self.rate)
            self._last = now

    def acquire(
        self, tokens: float = 1.0, *, blocking: bool = True, timeout: float | None = None
    ) -> bool:
        """取走 ``tokens`` 个令牌。

        Returns
        -------
        True 表示取得；非阻塞模式下 False 表示当前无足够令牌。

        Raises
        ------
        ValueError
            ``tokens`` 超过桶容量 ``burst``（C7）。此类请求在数学上**永不
            满足**（``_refill`` 上限就是 burst），旧实现会陷入 ``while True``
            死等——改为入口显式报错。
        """
        if tokens > self.burst:
            raise ValueError(f"请求令牌数 {tokens} 超过桶容量 {self.burst}（该请求永远不会被满足）")
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            with self._lock:
                now = time.monotonic()
                self._refill(now)
                if self._tokens >= tokens:
                    self._tokens -= tokens
                    return True
                wait = (tokens - self._tokens) / self.rate
            if not blocking:
                return False
            if deadline is not None and now + wait > deadline:
                return False
            time.sleep(min(wait, 0.25))

    def set_rate(self, rate: float) -> None:
        with self._lock:
            self.rate = float(rate)
            self.burst = max(1.0, self.rate)
            self._tokens = min(self._tokens, self.burst)

    @property
    def tokens(self) -> float:
        with self._lock:
            self._refill(time.monotonic())
            return self._tokens


# --------------------------------------------------------------------------- #
# 分档限流器
# --------------------------------------------------------------------------- #
@dataclass
class RateSnapshot:
    """一次限流决策的快照（便于埋点与调试）。"""

    state: str
    rate: float
    waited: float

    def to_dict(self) -> dict[str, object]:
        return {"state": self.state, "rate": round(self.rate, 3), "waited": round(self.waited, 4)}


class SessionRateLimiter:
    """按交易状态自动切换速率的限流器。

    行为说明（保持既有语义）
    ----------------------
    * 每次 :meth:`acquire` 先判定当前交易状态，再从该状态的桶里取令牌；
      桶按状态独立（``_buckets``），互不共享余量。
    * ``strict=True``：非阻塞取令牌，取不到立刻抛 :class:`RateLimitedLocal`；
      ``strict=False``（默认）：阻塞等待至令牌充足。

    已知局限（§2.9，行为保持不变，仅在此留痕）
    ----------------------------------------
    * **等待期间时段切换不重选档**：``acquire`` 阻塞等待时固定使用进入时
      判定的状态桶，盘中→盘后切换后仍按旧档速率等待，直至本次取到令牌。
    * 令牌桶的 ``tokens > burst`` 死等已由 :meth:`TokenBucket.acquire`
      的入口校验（C7）显式报错消除。
    * ``HostEntry.failures`` 的读改写非原子（见 hosts.py），与限流无关。

    Parameters
    ----------
    rates:
        三档速率（req/s）。缺省取 ``RateLimitConfig`` 的默认值。
    state_fn:
        状态判定函数，便于测试注入。
    strict:
        True 时超速直接抛 :class:`~tstdx.errors.RateLimitedLocal`；
        False 时阻塞等待。
    """

    __slots__ = ("_rates", "_buckets", "_state_fn", "strict", "_clock")

    def __init__(
        self,
        rates: dict[str, float] | None = None,
        *,
        state_fn: Callable[[], str] | None = None,
        strict: bool = False,
    ) -> None:
        rates = rates or {}
        self._rates: dict[str, float] = {
            SessionState.IN_SESSION: float(rates.get(SessionState.IN_SESSION, 15)),
            SessionState.PRE_POST: float(rates.get(SessionState.PRE_POST, 30)),
            SessionState.CLOSED: float(rates.get(SessionState.CLOSED, 60)),
        }
        self._buckets = {state: TokenBucket(rate) for state, rate in self._rates.items()}
        self._state_fn = state_fn or (lambda: session_state())
        self.strict = strict
        self._clock = time.monotonic

    # -- 配置 --------------------------------------------------------------- #
    @classmethod
    def from_config(cls, rate_cfg: object, *, strict: bool = False) -> SessionRateLimiter:
        """从 :class:`~tstdx.config.schema.RateLimitConfig` 构造。"""
        g = getattr
        return cls(
            {
                SessionState.IN_SESSION: float(g(rate_cfg, "in_session", 15)),
                SessionState.PRE_POST: float(g(rate_cfg, "pre_post", 30)),
                SessionState.CLOSED: float(g(rate_cfg, "closed", 60)),
            },
            strict=strict,
        )

    def set_rates(self, rates: dict[str, float]) -> None:
        for state, rate in rates.items():
            if state in self._buckets:
                self._rates[state] = float(rate)
                self._buckets[state].set_rate(float(rate))

    # -- 使用 --------------------------------------------------------------- #
    @property
    def state(self) -> str:
        try:
            return self._state_fn()
        except Exception:
            return SessionState.CLOSED

    @property
    def rate(self) -> float:
        return self._rates.get(self.state, 15.0)

    def acquire(self, tokens: float = 1.0) -> RateSnapshot:
        """阻塞式取令牌（strict 模式下超速则抛错）。"""
        state = self.state
        bucket = self._buckets[state]
        started = self._clock()
        if self.strict:
            ok = bucket.acquire(tokens, blocking=False)
            if not ok:
                from ..errors import RateLimitedLocal

                raise RateLimitedLocal(
                    f"本地限流：当前处于 {state}，限速 {self._rates[state]:.0f} req/s",
                    context={"state": state, "rate": self._rates[state]},
                )
        else:
            bucket.acquire(tokens, blocking=True)
        return RateSnapshot(state=state, rate=self._rates[state], waited=self._clock() - started)

    def try_acquire(self, tokens: float = 1.0) -> bool:
        return self._buckets[self.state].acquire(tokens, blocking=False)
