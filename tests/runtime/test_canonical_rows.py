# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""canonical dict 行契约：``quotes`` / ``bars`` 的行出口统一是 dict。

``result.data[0]["price"]`` 写在 quickstart / cookbook 里，是调用方第一眼看到的
形状。TDX 路径本来就是 canonical dict，Web 路径却返回 ``Quote`` / ``Bar`` 数据类，
同一个能力两种行类型——按文档写 ``["price"]`` 的代码在 Web 源上会撞
``TypeError: 'Quote' object is not subscriptable``。

执行器在 ``quotes`` / ``bars`` 两个能力上把行统一成 dict；其余能力（``security_count``
的 int、``sync_daily`` 的汇总 dict）一律不碰。域模型仍是 typed 层
（``Client.typed()``）的契约，两个面各就各位。
"""

from __future__ import annotations

import pytest

from atst.domain.models import Bar, Quote
from atst.query import QueryPlanner, QuerySpec
from atst.runtime.executor import DirectProviderExecutor, _canonical_rows


class _StubSource:
    """最小 Web 源替身：只实现执行器用到的出口。"""

    def __init__(self, rows):
        self._rows = rows
        self.closed = False

    def fetch_quotes(self, symbols):  # noqa: ARG002 - 替身不校验入参
        return list(self._rows)

    def fetch_bars(self, symbol, **kwargs):  # noqa: ARG002
        return list(self._rows)

    def close(self):
        self.closed = True


def _patch_source(monkeypatch: pytest.MonkeyPatch, rows) -> _StubSource:
    source = _StubSource(rows)
    monkeypatch.setattr("atst.web.create_source", lambda *a, **k: source)
    return source


class TestCanonicalRowsHelper:
    def test_dataclass_rows_become_dicts(self) -> None:
        rows = _canonical_rows([Quote(code="sh600519", price=1.5), Bar(datetime="2026-10-20")])
        assert all(isinstance(row, dict) for row in rows)
        assert rows[0]["code"] == "sh600519" and rows[0]["price"] == 1.5
        assert rows[1]["datetime"] == "2026-10-20"

    def test_existing_dicts_pass_through(self) -> None:
        row = {"code": "sh600519", "price": 1.5}
        assert _canonical_rows([row])[0] is row

    def test_non_row_payloads_are_untouched(self) -> None:
        """``security_count`` 的 int / ``sync_daily`` 的汇总 dict 不在行契约里。"""

        assert _canonical_rows(7) == 7
        assert _canonical_rows({"written": 3}) == {"written": 3}
        assert _canonical_rows(None) is None


class TestQuotesExit:
    def test_web_quotes_return_canonical_dict_rows(self, monkeypatch: pytest.MonkeyPatch) -> None:
        source = _patch_source(
            monkeypatch,
            [Quote(code="sh600519", datetime="2026-10-20 15:00:00", price=1324.97)],
        )
        plan = QueryPlanner().compile(
            QuerySpec.build("quotes", symbols="sh600519", provider="tencent")
        )
        result = DirectProviderExecutor(timeout=1.0).execute(plan)

        row = result.data[0]
        assert isinstance(row, dict)
        assert row["price"] == 1324.97
        assert row["datetime"] == "2026-10-20 15:00:00"
        assert source.closed is True

    def test_web_quotes_route_through_fetch_quotes_not_fetch(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """回归：``quotes`` 直连路径必须走 ``fetch_quotes``（百度默认分组是 K 线）。"""

        calls: list[str] = []

        class Source(_StubSource):
            def fetch(self, *a, **k):  # noqa: ARG002 - 不该被调用
                calls.append("fetch")
                return [Bar(datetime="2026-10-20")]

            def fetch_quotes(self, symbols):
                calls.append("fetch_quotes")
                return [Quote(code="sh600519", price=1.0)]

        source = Source([])
        monkeypatch.setattr("atst.web.create_source", lambda *a, **k: source)
        plan = QueryPlanner().compile(
            QuerySpec.build("quotes", symbols="sh600519", provider="baidu")
        )
        DirectProviderExecutor(timeout=1.0).execute(plan)
        assert calls == ["fetch_quotes"]


class TestBarsExit:
    def test_web_bars_return_canonical_dict_rows(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_source(
            monkeypatch,
            [Bar(datetime="2026-10-20", open=1.0, high=2.0, low=0.5, close=1.5, volume=100)],
        )
        plan = QueryPlanner().compile(
            QuerySpec.build("bars", symbols="sh600519", provider="tencent", period="day", count=1)
        )
        result = DirectProviderExecutor(timeout=1.0).execute(plan)

        row = result.data[0]
        assert isinstance(row, dict)
        assert row["datetime"] == "2026-10-20"
        assert row["close"] == 1.5
        assert row["volume"] == 100
