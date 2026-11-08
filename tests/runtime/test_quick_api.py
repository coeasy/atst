# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""零配置快捷入口的离线回放：真 Client + 假执行器，不碰网络。"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import atst
from atst.client import quick
from atst.client.api import Client
from atst.result import Provenance, QueryResult
from atst.runtime import UnifiedRuntime


class _RecordingExecutor:
    """只记录计划、返回预置数据的执行器。"""

    def __init__(self, data: Any) -> None:
        self.data = data
        self.plans: list[Any] = []

    def execute(self, plan: Any) -> QueryResult[Any]:
        self.plans.append(plan)
        return QueryResult.from_plan(
            self.data,
            plan=plan,
            provenance=Provenance.direct(plan),
        )

    def close(self) -> None:
        return None


def _patch_client(monkeypatch: Any, data: Any) -> tuple[Client, _RecordingExecutor]:
    executor = _RecordingExecutor(data)
    client = Client(runtime=UnifiedRuntime(executor=executor))
    monkeypatch.setattr(quick, "_client", lambda: client)
    return client, executor


def test_history_returns_dict_rows(monkeypatch: Any) -> None:
    _patch_client(
        monkeypatch, [SimpleNamespace(to_dict=lambda: {"symbol": "600519", "close": 1.0})]
    )

    rows = quick.history("600519", provider="tdx", count=5)

    assert rows == [{"symbol": "600519", "close": 1.0}]


def test_history_passes_symbol_period_and_range(monkeypatch: Any) -> None:
    _, executor = _patch_client(monkeypatch, [])

    quick.history(
        "600519",
        provider="tdx",
        period="day",
        count=7,
        start_date="2026-01-01",
        end_date="2026-02-01",
    )

    plan = executor.plans[0]
    assert plan.spec.capability == "bars"
    assert plan.spec.symbols == ("sh600519",)
    assert plan.spec.period == "day"
    assert plan.spec.count == 7
    assert plan.spec.start_date == "2026-01-01"
    assert plan.spec.end_date == "2026-02-01"


def test_default_provider_is_auto(monkeypatch: Any) -> None:
    _, executor = _patch_client(monkeypatch, [{"symbol": "sh600519", "last_price": 1.0}])

    quick.history("600519", count=5)

    # auto 会把计划落到某个具体候选上，但 provenance 记住请求方要的是 auto。
    assert executor.plans
    assert executor.plans[0].provider not in (None, "auto")


def test_quotes_and_quote(monkeypatch: Any) -> None:
    _patch_client(monkeypatch, [{"symbol": "sh600519", "last_price": 1.0}])

    assert quick.quotes("600519") == [{"symbol": "sh600519", "last_price": 1.0}]
    assert quick.quote("600519") == {"symbol": "sh600519", "last_price": 1.0}


def test_quote_returns_none_on_empty(monkeypatch: Any) -> None:
    _patch_client(monkeypatch, [])

    assert quick.quote("600519") is None


def test_minute_and_trades_shape(monkeypatch: Any) -> None:
    _patch_client(monkeypatch, [{"symbol": "sh600519", "time": "09:31"}])

    assert quick.minute("600519") == [{"symbol": "sh600519", "time": "09:31"}]
    assert quick.trades("600519", start=0, count=10) == [{"symbol": "sh600519", "time": "09:31"}]


def test_top_level_reexports_are_the_same_functions() -> None:
    assert atst.history is quick.history
    assert atst.quote is quick.quote
    assert atst.quotes is quick.quotes
    assert atst.minute is quick.minute
    assert atst.trades is quick.trades
