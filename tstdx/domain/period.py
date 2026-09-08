# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""Canonical bar-period normalization shared by planning and execution.

The unified API accepts common aliases, but Provider adapters should receive one
stable spelling so route selection, fingerprints, cache keys and execution do
not disagree about equivalent windows.
"""

from __future__ import annotations

__all__ = ["normalize_bar_period"]


_PERIOD_ALIASES = {
    "min": "1min",
    "1m": "1min",
    "m1": "1min",
    "5m": "5min",
    "m5": "5min",
    "15m": "15min",
    "m15": "15min",
    "30m": "30min",
    "m30": "30min",
    "60m": "60min",
    "m60": "60min",
    "1h": "60min",
    "1hour": "60min",
    "d": "day",
    "daily": "day",
    "1d": "day",
    "w": "week",
    "weekly": "week",
    "1w": "week",
    "m": "month",
    "mo": "month",
    "monthly": "month",
    "1mo": "month",
    "q": "season",
    "quarter": "season",
    "quarterly": "season",
    "y": "year",
    "1y": "year",
    "yearly": "year",
}


def normalize_bar_period(period: str | None) -> str:
    """Return the canonical spelling for a public bar-period alias."""
    key = (period or "day").strip().lower()
    return _PERIOD_ALIASES.get(key, key)
