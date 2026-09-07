"""业绩预告源测试（S4）：RPT_PUBLIC_OP_NEWPREDICT。

含纯函数映射测试（mock 原始 datacenter 行）与网络护栏 live 冒烟。
"""

from __future__ import annotations

import pytest

from tstdx.errors import TdxError
from tstdx.web.corporate import EastmoneyForecastSource
from tstdx.web.facade import WebQuoteSession

pytestmark = pytest.mark.unit

_RAW = [
    {
        "SECURITY_CODE": "600519",
        "SECURITY_NAME_ABBR": "贵州茅台",
        "NOTICE_DATE": "2026-07-15 00:00:00",
        "REPORT_DATE": "2026-06-30 00:00:00",
        "PREDICT_TYPE": "预增",
        "PREDICT_FINANCE": "归属于上市公司股东的净利润",
        "PREDICT_AMT_LOWER": 1000000000.0,
        "PREDICT_AMT_UPPER": 1200000000.0,
        "ADD_AMP_LOWER": 15.0,
        "ADD_AMP_UPPER": 20.0,
        "PREDICT_CONTENT": "预计增长",
        "PREYEAR_SAME_PERIOD": 800000000.0,
        "FORECAST_STATE": "increase",
        "TRADE_MARKET": "上交所主板",
    }
]


def test_fetch_forecast_maps_raw_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    src = EastmoneyForecastSource()
    monkeypatch.setattr(src, "fetch_rows", lambda **kw: list(_RAW))
    rows = src.fetch_forecast(size=10)
    assert len(rows) == 1
    r = rows[0]
    assert r["code"] == "600519"
    assert r["name"] == "贵州茅台"
    assert r["predict_type"] == "预增"
    assert r["profit_lower"] == 1_000_000_000.0
    assert r["profit_upper"] == 1_200_000_000.0
    assert r["amp_lower"] == 15.0 and r["amp_upper"] == 20.0
    assert r["forecast_state"] == "increase"
    assert r["market"] == "上交所主板"


def test_fetch_forecast_report_date_filter_format(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """报告期筛选须补全为 ``YYYY-MM-DD 00:00:00`` 完整字面值。"""
    captured: dict = {}

    def _fake_fetch_rows(**kw: object) -> list[dict]:
        captured.update(kw)
        return []

    src = EastmoneyForecastSource()
    monkeypatch.setattr(src, "fetch_rows", _fake_fetch_rows)
    src.fetch_forecast(report_date="2026-06-30")
    filters = captured.get("filters", [])
    assert any('REPORT_DATE="2026-06-30 00:00:00"' in f for f in filters)


@pytest.mark.network
def test_forecast_live_structure() -> None:
    try:
        rows = WebQuoteSession.forecast(size=3)
    except (TdxError, OSError, TimeoutError) as exc:
        pytest.skip(f"东财网络不可达或受限：{exc}")
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"源调用异常（沙箱可能拦截）：{exc}")
    assert isinstance(rows, list)
    if rows:
        r = rows[0]
        for key in ("code", "name", "predict_type", "notice_date"):
            assert key in r
