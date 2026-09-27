"""Offline-hours guard regression: the prober must agree with ``session_state``.

``only_offline_hours()`` used to compare against a ``SessionState.IN_SESSION``
member that never existed, so every real (non-stubbed) call raised
``AttributeError`` and the trading-hours protection was silently dead.
"""

from datetime import datetime

import pytest

from atst.errors import TdxError
from atst.protocol.prober import Prober

# 2026-09-14 is a Monday, 2026-09-19 is a Saturday.
TRADING_DAY = datetime(2026, 9, 14)
WEEKEND = datetime(2026, 9, 19)


@pytest.fixture()
def prober() -> Prober:
    return Prober()


@pytest.mark.parametrize(
    ("hour", "minute", "allowed"),
    [
        (9, 14, True),  # 盘前
        (9, 15, False),  # 集合竞价
        (9, 29, False),
        (11, 29, False),  # 连续竞价
        (11, 30, True),  # 午休
        (12, 30, True),
        (13, 0, False),  # 下午连续竞价
        (14, 59, False),
        (15, 0, True),  # 盘后
    ],
)
def test_only_offline_hours_matches_trading_session(
    prober: Prober, hour: int, minute: int, allowed: bool
) -> None:
    now = TRADING_DAY.replace(hour=hour, minute=minute)

    assert prober.only_offline_hours(now=now) is allowed


def test_only_offline_hours_allows_weekend(prober: Prober) -> None:
    assert prober.only_offline_hours(now=WEEKEND.replace(hour=10)) is True


def test_guard_offline_raises_only_during_trading_session(prober: Prober) -> None:
    prober.block_offline_only = True
    prober.only_offline_hours = lambda **kwargs: False

    with pytest.raises(TdxError):
        prober._guard_offline()
