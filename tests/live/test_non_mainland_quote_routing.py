# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""真机验证：非沪深标的的缺省 quotes 必须落在 live Web 通道（2026-11-24）。"""

from __future__ import annotations

import pytest

from atst import Client

pytestmark = pytest.mark.network

#: 真实代码：港股、美股、北交所各一只；盘中/盘后都应返回带价格的快照。
PROBE_CASES = (
    ("hk00700", "HKD"),
    ("usAAPL", "USD"),
    # 北交所腾讯快照在开盘前会返回价格为昨收、量额为 0、OHLC 为 0；
    # 这是上游口径，不是路由失败，真机断言只钉住价格与通道。
    ("bj833171", "CNY"),
)
PROBE_SYMBOLS = tuple(symbol for symbol, _ in PROBE_CASES)


@pytest.fixture(scope="module")
def live_client() -> Client:
    with Client() as client:
        yield client


@pytest.mark.parametrize(("symbol", "expected_currency"), PROBE_CASES)
def test_default_quotes_are_live_and_non_empty(
    live_client: Client, symbol: str, expected_currency: str
) -> None:
    result = live_client.quotes([symbol])
    assert result.meta.provider == "tencent"
    assert not result.meta.provenance.fallback
    assert result.data, f"{symbol} 缺省 quotes 返回空数据"
    row = result.data[0]
    assert row["code"].lower().endswith(symbol[2:].lower())
    assert isinstance(row["price"], (int, float)) and row["price"] > 0
    # 沪深/北交所快照不显式携带 currency，按原生币种 CNY 处理。
    assert row.get("currency", "CNY") == expected_currency
    # 港美股真机量额口径：[6]=股、[37]=原生币种元。隐含均价必须落在当日高低
    # 区间附近，否则就是又被 A 股 ×100/×10000 缩放了（2026-11-24 真 bug）。
    if symbol.startswith(("hk", "us")):
        assert row["volume"] > 0 and row["amount"] > 0
        implied = row["amount"] / row["volume"]
        assert row["low"] * 0.8 <= implied <= row["high"] * 1.2, (
            f"{symbol} 隐含均价 {implied:.2f} 不在 [{row['low']}, {row['high']}] 附近"
        )
    else:
        # 北交所盘前快照可能量额为 0，不能拿港美股的盘中口径去卡它。
        assert row["volume"] >= 0 and row["amount"] >= 0


def test_mixed_market_batch_served_by_one_live_provider(live_client: Client) -> None:
    result = live_client.quotes(["sh600519", *PROBE_SYMBOLS])
    assert result.meta.provider == "tencent"
    assert len(result.data) == 1 + len(PROBE_SYMBOLS)
    assert all(isinstance(row.get("price"), (int, float)) for row in result.data)
