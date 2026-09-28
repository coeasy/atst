# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""Live proof for the omitted-provider Web defaults of minute/trades.

These assertions run only when the A-share session makes intraday data a valid
expectation. Network/provider failure during that applicable window is a real
failure, not a skip. Outside the session the capability is not being asserted.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from atst import Client
from atst.domain.calendar import TradingSession, is_trading_day

pytestmark = pytest.mark.network

MARKET_TZ = timezone(timedelta(hours=8), "UTC+8")
PROBE_SYMBOL = "sh600519"


def _market_now() -> datetime:
    return datetime.now(MARKET_TZ)


def _in_trading_session(now: datetime) -> bool:
    return is_trading_day(now.date()) and TradingSession.in_session(now.hour * 60 + now.minute)


def _rows(result: Any) -> list[dict[str, Any]]:
    data = result.data
    if isinstance(data, dict):
        return [data]
    return list(data)


@pytest.fixture(scope="module")
def web_default_client() -> Any:
    # Pin the runtime policy to TDX. minute/trades are declared-but-unavailable
    # there, so omitted per-request provider must resolve to the registry's
    # operational default (currently Tencent) through the real QueryPlanner.
    with Client(default_provider="tdx") as client:
        yield client


def test_minute_omitted_provider_reaches_live_operational_default(
    web_default_client: Client,
) -> None:
    now = _market_now()
    if not _in_trading_session(now):
        pytest.skip(f"{now.isoformat()} 不在 A 股交易时段，minute live 断言不适用")

    result = web_default_client.minute(PROBE_SYMBOL)
    assert result.meta.provider == "tencent"
    assert not result.meta.provenance.fallback
    rows = _rows(result)
    assert rows, "交易时段内 Tencent minute 返回空数据"


def test_trades_omitted_provider_reaches_live_operational_default(
    web_default_client: Client,
) -> None:
    now = _market_now()
    if not _in_trading_session(now):
        pytest.skip(f"{now.isoformat()} 不在 A 股交易时段，trades live 断言不适用")

    result = web_default_client.trades(PROBE_SYMBOL)
    assert result.meta.provider == "tencent"
    assert not result.meta.provenance.fallback
    rows = _rows(result)
    assert rows, "交易时段内 Tencent trades 返回空数据"
