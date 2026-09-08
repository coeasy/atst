# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""Freshness contracts for real market-data results.

Freshness is not a generic cache TTL. Different capabilities provide different
kinds of evidence:

* ``DIRECT_SNAPSHOT`` — the Provider endpoint/TDX command itself is a current
  snapshot operation. A verified wall-clock timestamp is useful but not always
  available (for example TDX 0x0530), so direct execution + protocol semantics
  + integrity checks are the freshness proof.
* ``CURRENT_SERIES`` — a direct query for the Provider's current series. The
  returned tail must expose a parseable timestamp/date so callers can audit what
  the Provider considered latest. A broad calendar-age sanity guard may reject
  grossly stale tails, but it is not treated as a trading calendar.
* ``HISTORICAL_CLOSED`` — an explicitly requested historical Provider window.
  Its tail timestamp must be parseable and auditable, but it is not compared with
  wall-clock currentness and can never satisfy a live-only request.
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
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from .errors import FreshnessViolation

__all__ = [
    "FreshnessMode",
    "FreshnessProfile",
    "FreshnessStatus",
    "FreshnessRegistry",
    "FRESHNESS",
    "bar_freshness_profile",
    "validate_freshness",
]


class FreshnessMode(str, Enum):
    DIRECT_SNAPSHOT = "direct_snapshot"
    CURRENT_SERIES = "current_series"
    HISTORICAL_CLOSED = "historical_closed"
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
    max_provider_calendar_age_days: int | None = None


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

    Exact registrations override capability defaults. The registry is static;
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

# Common live contracts. The observed-age guard protects against accidentally
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
        max_provider_calendar_age_days=14,
    ),
)
FRESHNESS.register_capability(
    "trades",
    FreshnessProfile(
        mode=FreshnessMode.CURRENT_SERIES,
        require_provider_timestamp=True,
        max_observation_age_seconds=5.0,
        max_provider_calendar_age_days=14,
    ),
)

# TDX 0x0530 is a verified current-snapshot command but its parsed payload does
# not expose a verified wall-clock field. Explicit exact registration documents
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

# These values are deliberately broad sanity guards, not exchange-session rules.
# Direct Provider execution remains the primary latestness evidence; the guard
# only rejects clearly stale series while avoiding weekend/holiday false alarms.
_BAR_CURRENTNESS_DAYS = {
    "1min": 14,
    "5min": 14,
    "15min": 14,
    "30min": 14,
    "60min": 14,
    "day": 14,
    "week": 21,
    "month": 62,
    "season": 140,
    "year": 400,
}
_HISTORICAL_BAR_PROFILE = FreshnessProfile(
    mode=FreshnessMode.HISTORICAL_CLOSED,
    require_provider_timestamp=True,
    max_observation_age_seconds=30.0,
    description="explicit historical Provider bar window; auditable but not live-current",
)


def bar_freshness_profile(
    provider: str,
    channel: str,
    period: str,
    *,
    historical: bool = False,
) -> FreshnessProfile:
    """Return the canonical bars profile for both fetch and cache paths."""
    if historical:
        return _HISTORICAL_BAR_PROFILE
    base = FRESHNESS.get(provider, channel, "bars")
    return replace(
        base,
        max_provider_calendar_age_days=_BAR_CURRENTNESS_DAYS.get(
            str(period).strip().lower(),
            14,
        ),
    )


def _field(evidence: Any, name: str, default: Any = None) -> Any:
    if isinstance(evidence, Mapping):
        return evidence.get(name, default)
    return getattr(evidence, name, default)


def _parse_provider_date(value: Any) -> datetime | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    normalized = text.replace("/", "-")
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(normalized)
    except ValueError:
        pass
    for fmt in (
        "%Y%m%d",
        "%Y%m%d %H%M%S",
        "%Y%m%d%H%M%S",
        "%Y-%m-%d %H%M%S",
    ):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


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

    if require_live and selected.mode in {
        FreshnessMode.HISTORICAL_CLOSED,
        FreshnessMode.LOCAL_HISTORICAL,
    }:
        raise FreshnessViolation(
            f"请求要求最新实时数据，但结果来自 {selected.mode.value}",
            context={**context, "reason": selected.mode.value},
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

    parsed_provider_time: datetime | None = None
    if selected.mode in {
        FreshnessMode.CURRENT_SERIES,
        FreshnessMode.HISTORICAL_CLOSED,
    }:
        parsed_provider_time = _parse_provider_date(provider_timestamp)
        if parsed_provider_time is None:
            raise FreshnessViolation(
                "Provider tail timestamp 无法解析",
                context={
                    **context,
                    "reason": "provider_timestamp_unparseable",
                    "provider_timestamp": str(provider_timestamp),
                },
            )

    if selected.mode is FreshnessMode.CURRENT_SERIES:
        max_days = selected.max_provider_calendar_age_days
        if max_days is not None and parsed_provider_time is not None:
            now_date = datetime.fromtimestamp(now / 1_000_000_000, tz=timezone.utc).date()
            provider_date = parsed_provider_time.date()
            age_days = (now_date - provider_date).days
            if age_days < -1:
                raise FreshnessViolation(
                    "Provider tail timestamp 明显晚于当前日期",
                    context={
                        **context,
                        "reason": "provider_timestamp_in_future",
                        "provider_timestamp": str(provider_timestamp),
                        "age_days": age_days,
                    },
                )
            if age_days > max_days:
                raise FreshnessViolation(
                    "Provider current-series tail 已超过允许的 calendar age",
                    context={
                        **context,
                        "reason": "provider_timestamp_too_old",
                        "provider_timestamp": str(provider_timestamp),
                        "age_days": age_days,
                        "max_age_days": max_days,
                    },
                )

    if selected.mode is FreshnessMode.DIRECT_SNAPSHOT:
        basis = "direct_current_snapshot"
        currentness_verified = True
    elif selected.mode is FreshnessMode.CURRENT_SERIES:
        basis = "direct_current_series+provider_tail_timestamp"
        currentness_verified = parsed_provider_time is not None
    elif selected.mode is FreshnessMode.HISTORICAL_CLOSED:
        basis = "direct_historical_closed_series"
        currentness_verified = False
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
