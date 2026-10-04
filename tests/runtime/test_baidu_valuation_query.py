from __future__ import annotations

from atst.query import QueryPlanner, QuerySpec
from atst.runtime.executor import DirectProviderExecutor
from atst.web.session import WebQuoteSession


def test_baidu_valuation_history_is_planned_and_dispatched(monkeypatch):
    expected = {"市盈率(TTM)": [{"date": "2026-01-02", "value": 12.5}]}

    def fake_history(symbol, *, indicators, period):
        assert symbol == "600519"
        assert indicators == ["市盈率(TTM)"]
        assert period == "近五年"
        return expected

    monkeypatch.setattr(WebQuoteSession, "baidu_valuation_history", staticmethod(fake_history))
    spec = QuerySpec.build(
        "baidu_valuation_history",
        provider="baidu",
        options={
            "args": ["600519"],
            "kwargs": {"indicators": ["市盈率(TTM)"], "period": "近五年"},
        },
    )
    plan = QueryPlanner().compile(spec)
    assert (plan.provider, plan.channel) == ("baidu", "catalog")

    result = DirectProviderExecutor().execute(plan)
    assert result.data == expected
    assert result.meta.provider == "baidu"
    assert result.meta.channel == "catalog"
