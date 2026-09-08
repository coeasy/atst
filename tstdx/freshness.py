# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""Freshness contracts for real market-data results.

Freshness is not a generic cache TTL.  Different capabilities provide different
kinds of evidence:

* ``DIRECT_SNAPSHOT`` — the Provider endpoint/TDX command itself is a current
  snapshot operation. A verified wall-clock timestamp is useful but not always
  available (for example TDX 0x0530), so direct execution + protocol semantics
  + integrity checks are the freshness proof.
* ``CURRENT_SERIES`` — a direct query for the Provider's current series. The
  returned tail must expose a timestamp/date so callers can audit what the
  Provider considered latest.
* ``BUSINESS_DATE`` — reports/rankings/fundamental information with its own
  business-date/update-time semantics. It must not be forced into quote TTLs.
* ``LOCAL_HISTORICAL`` — explicitly historical/local and therefore never a live
  substitute.

The validator deliberately does not use the built-in 2026 A-share calendar as
the sole latestness oracle because that calendar is marked estimated. It also
never invents a timestamp from unknown protocol fields.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping

from .errors import FreshnessViolation

__all__ = [
    "FreshnessMode",
    "FreshnessProfile",
    "FreshnessStatus",
    "FreshnessRegistry",
    "FRESHNESS",
    "validate_freshness",
]


class FreshnessMode(str, Enum):
    DIRECT_SNAPSHOT = "direct_snapshot"
    CURRENT_SERIES = "current_series"
    BUSINESS_DATE = "business_date"
    LOCAL_HISTORICAL = "local_historical"


@dataclass(frozen=True, slots=True)
class FreshnessProfile:
    mode: FreshnessMode
    require_direct: bool = True
    require_provider_timestamp: bool = False
    allow_cache: bool = False
    allow_replay: bool = False
    allow_synthetic: bool = False
    max_observation_age_seconds: float | None = None
    description: str = ""


@dataclass(frozen=True, slots=True)
class FreshnessStatus:
    verified: bool
    mode: FreshnessMode
    basis: str
    provider_timestamp: str | None
    observed_age_seconds: float
    currentness_verified: bool


class FreshnessRegistry:
    """Provider/Channel/Capability freshness profiles.

    Exact registrations override capability defaults.  The registry is static;
    dynamic Provider health belongs to the runtime health registry, not here.
    """

    def __init__(self) -> None:
        self._exact: dict[tuple[str, str, str], FreshnessProfile] = {}
        self._capability: dict[str, FreshnessProfile] = {}

    def register_capability(self, capability: str, profile: FreshnessProfile) -> None:
        self._capability[str(capability).strip().lower()] = profile

    def register(
        self,
        provider: str,
        channel: str,
        capability: str,
        profile: FreshnessProfile,
    ) -> None:
        self._exact[(provider.lower(), channel.lower(), capability.lower())] = profile

    def get(self, provider: str, channel: str, capability: str) -> FreshnessProfile:
        key = (provider.lower(), channel.lower(), capability.lower())
        exact = self._exact.get(key)
        if exact is not None:
            return exact
        try:
            return self._capability[capability.lower()]
        except KeyError as exc:
            raise FreshnessViolation(
                "该 Capability 尚未声明 freshness profile，拒绝把结果标记为最新",
                context={
                    "provider": provider,
                    "channel": channel,
                    "capability": capability,
                    "reason": "profile_missing",
                },
            ) from exc

    def as_dict(self) -> Mapping[tuple[str, str, str], FreshnessProfile]:
        return dict(self._exact)


FRESHNESS = FreshnessRegistry()

# Common live contracts.  The observed-age guard protects against accidentally
# reusing an already-produced result object as if it were a fresh request; it is
# not an upstream quote TTL and does not authorize cache use.
FRESHNESS.register_capability(
    "quotes",
    FreshnessProfile(
        mode=FreshnessMode.DIRECT_SNAPSHOT,
        max_observation_age_seconds=5.0,
        description="current quote/snapshot endpoint fetched directly from selected Provider",
    ),
)
FRESHNESS.register_capability(
    "bars",
    FreshnessProfile(
        mode=FreshnessMode.CURRENT_SERIES,
        require_provider_timestamp=True,
        max_observation_age_seconds=30.0,
        description="direct current-series query; audit latest Provider tail timestamp",
    ),
)
FRESHNESS.register_capability(
    "minute",
    FreshnessProfile(
        mode=FreshnessMode.CURRENT_SERIES,
        require_provider_timestamp=True,
        max_observation_age_seconds=5.0,
    ),
)
FRESHNESS.register_capability(
    "trades",
    FreshnessProfile(
        mode=FreshnessMode.CURRENT_SERIES,
        require_provider_timestamp=True,
        max_observation_age_seconds=5.0,
    ),
)

# TDX 0x0530 is a verified current-snapshot command but its parsed payload does
# not expose a verified wall-clock field.  Explicit exact registration documents
# that the timestamp is not required rather than silently pretending _u4 is time.
FRESHNESS.register(
    "tdx",
    "quotation",
    "quotes",
    FreshnessProfile(
        mode=FreshnessMode.DIRECT_SNAPSHOT,
        require_provider_timestamp=False,
        max_observation_age_seconds=5.0,
        description="TDX 0x0530 current snapshot; direct command semantics are freshness evidence",
    ),
)

# vipdoc is auditable historical data but never live evidence.
FRESHNESS.register(
    "tdx",
    "vipdoc",
    "bars",
    FreshnessProfile(
        mode=FreshnessMode.LOCAL_HISTORICAL,
        require_direct=False,
        require_provider_timestamp=True,
        description="local TDX historical file; never substitutes live Provider data",
    ),
)


def _field(evidence: Any, name: str, default: Any = None) -> Any:
    if isinstance(evidence, Mapping):
        return evidence.get(name, default)
    return getattr(evidence, name, default)


def validate_freshness(
    evidence: Any,
    *,
    provider: str,
    channel: str,
    capability: str,
    profile: FreshnessProfile | None = None,
    now_ns: int | None = None,
    require_live: bool = True,
) -> FreshnessStatus:
    """Validate freshness evidence without switching Provider or guessing time.

    ``evidence`` is intentionally structural so service/result models can evolve
    without a dependency cycle. Required fields are ``origin``,
    ``observed_at_ns``, ``provider_timestamp``, ``cache_hit``, ``replay`` and
    ``synthetic``.
    """

    selected = profile or FRESHNESS.get(provider, channel, capability)
    origin = str(_field(evidence, "origin", ""))
    observed_at_ns = int(_field(evidence, "observed_at_ns", 0) or 0)
    provider_timestamp = _field(evidence, "provider_timestamp")
    cache_hit = bool(_field(evidence, "cache_hit", False))
    replay = bool(_field(evidence, "replay", False))
    synthetic = bool(_field(evidence, "synthetic", False))

    context = {
        "provider": provider,
        "channel": channel,
        "capability": capability,
        "mode": selected.mode.value,
    }

    if require_live and selected.mode is FreshnessMode.LOCAL_HISTORICAL:
        raise FreshnessViolation(
            "请求要求最新实时数据，但结果来自 local_historical Channel",
            context={**context, "reason": "local_historical"},
        )
    if selected.require_direct and origin != "direct":
        raise FreshnessViolation(
            "实时结果不是 selected Provider 的 direct fetch",
            context={**context, "origin": origin, "reason": "not_direct"},
        )
    if cache_hit and not selected.allow_cache:
        raise FreshnessViolation(
            "实时结果命中了不允许的旧缓存",
            context={**context, "reason": "cache_not_allowed"},
        )
    if replay and not selected.allow_replay:
        raise FreshnessViolation(
            "Replay 数据不得冒充最新真实数据",
            context={**context, "reason": "replay_not_allowed"},
        )
    if synthetic and not selected.allow_synthetic:
        raise FreshnessViolation(
            "Synthetic 数据不得冒充最新真实数据",
            context={**context, "reason": "synthetic_not_allowed"},
        )
    if selected.require_provider_timestamp and not provider_timestamp:
        raise FreshnessViolation(
            "时间序列缺少可审计的 Provider tail timestamp",
            context={**context, "reason": "provider_timestamp_missing"},
        )
    if observed_at_ns <= 0:
        raise FreshnessViolation(
            "结果缺少 observed_at 证据",
            context={**context, "reason": "observed_at_missing"},
        )

    now = time.time_ns() if now_ns is None else int(now_ns)
    age = max(0.0, (now - observed_at_ns) / 1_000_000_000)
    if (
        selected.max_observation_age_seconds is not None
        and age > selected.max_observation_age_seconds
    ):
        raise FreshnessViolation(
            "结果在返回前已超过 freshness observation age",
            context={
                **context,
                "reason": "observation_too_old",
                "age_seconds": age,
                "max_age_seconds": selected.max_observation_age_seconds,
            },
        )

    if selected.mode is FreshnessMode.DIRECT_SNAPSHOT:
        basis = "direct_current_snapshot"
        currentness_verified = True
    elif selected.mode is FreshnessMode.CURRENT_SERIES:
        basis = "direct_current_series+provider_tail_timestamp"
        currentness_verified = bool(provider_timestamp)
    elif selected.mode is FreshnessMode.BUSINESS_DATE:
        basis = "provider_business_date"
        currentness_verified = bool(provider_timestamp)
    else:
        basis = "local_historical"
        currentness_verified = False

    return FreshnessStatus(
        verified=True,
        mode=selected.mode,
        basis=basis,
        provider_timestamp=str(provider_timestamp) if provider_timestamp else None,
        observed_age_seconds=age,
        currentness_verified=currentness_verified,
    )
