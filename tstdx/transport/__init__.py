# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""传输层（§12 / §13）：连接、连接池、限流、测速。

    tstdx.transport
     ├── hosts       主站候选池 + 排名持久化
     ├── ratelimit   按交易状态分档的本地限流
     ├── base        同步 TcpConnection
     ├── pool        同步 ConnectionPool（槽位 / 故障转移 / 多帧合并）
     ├── speedtest   测速与排名
     └── async_      asyncio 版连接与连接池

关键设计：**故障转移策略由异常的 RetryAdvice 驱动**，不在传输层硬编码。
新增一种错误只需在 ``errors.py`` 声明建议，传输层自动获得正确行为。
"""

from __future__ import annotations

# Side-effect imports install the v1.0 generation/lease + circuit hardening onto
# the canonical pool classes before callers can receive either public class.
from . import _async_pool_hardening, _pool_hardening
from .base import DEFAULT_HEARTBEAT_CMD, ConnectionStats, TcpConnection
from .hosts import DEFAULT_HOST_POOL, POOL_BY_FAMILY, HostEntry, RankingStore, resolve_hosts
# Ranking hardening is deliberately loaded after hosts.py exists and before
# speedtest imports RankingStore. Disk ranking must remain probe-only across
# process restarts while the public hosts import path stays unchanged.
from . import _ranking_hardening
from .pool import ConnectionPool, PoolStats, Slot
from .ratelimit import SessionRateLimiter, SessionState, TokenBucket, session_state
from .speedtest import ProbeResult, probe, rank_hosts, speedtest, speedtest_and_save

del _async_pool_hardening, _pool_hardening, _ranking_hardening

__all__ = [
    "TcpConnection",
    "ConnectionStats",
    "DEFAULT_HEARTBEAT_CMD",
    "HostEntry",
    "DEFAULT_HOST_POOL",
    "POOL_BY_FAMILY",
    "RankingStore",
    "resolve_hosts",
    "ConnectionPool",
    "PoolStats",
    "Slot",
    "SessionRateLimiter",
    "SessionState",
    "TokenBucket",
    "session_state",
    "ProbeResult",
    "probe",
    "rank_hosts",
    "speedtest",
    "speedtest_and_save",
]


def __getattr__(name: str):
    if name in ("AsyncTcpConnection", "AsyncConnectionPool", "AsyncSlot"):
        from . import async_ as _a

        return getattr(_a, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
