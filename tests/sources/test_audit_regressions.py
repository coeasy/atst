# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""Regressions for sources-layer audit findings (CSV positional mapping, S-4/S-6/S-7/S-8/S-9).

All offline: fake HTTP / fake clients only; no network access.
"""

from __future__ import annotations

import math
from typing import Any

import pytest

from atst.diagnostics import WarningCode, warning_sink
from atst.errors import AllSourcesExhausted, CompatibilityError, SourceDeprecated, WebSourceError
from atst.sources._base_http import HttpResponse
from atst.sources._session_research import ResearchSessionMixin
from atst.sources.sina.adapters import SinaSource
from atst.sources.tencent.adapters import TencentSource

pytestmark = pytest.mark.unit


class _CsvHttp:
    """Minimal client returning one canned GET response."""

    def __init__(self, body: str) -> None:
        self.body = body

    def get(self, url: str, **kwargs: Any) -> HttpResponse:  # noqa: ARG002
        return HttpResponse(200, self.body.encode(), {})


def _feed_csv(monkeypatch: pytest.MonkeyPatch, body: str) -> None:
    monkeypatch.setattr("atst.sources._session_research._shared_http", lambda: _CsvHttp(body))


class TestMacroRepoRatePositionalParsing:
    def test_leading_empty_cell_keeps_positional_mapping(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`2026-09-30,,1.85,2.10` → FR001=None, FR007=1.85, FR014=2.10（不再整行丢弃）。"""
        _feed_csv(monkeypatch, "date,FR001,FR007,FR014\r\n2026-09-30,,1.85,2.10\r\n")
        assert ResearchSessionMixin.macro_repo_rate() == [
            {"date": "2026-09-30", "FR001": None, "FR007": "1.85", "FR014": "2.10"}
        ]

    def test_middle_empty_cell_does_not_shift_later_columns(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`...,1.50,,2.10,9.99` → FR007=None、FR014=2.10（不再错位成 2.10/9.99）。"""
        _feed_csv(monkeypatch, "date,FR001,FR007,FR014,x\r\n2026-09-30,1.50,,2.10,9.99\r\n")
        assert ResearchSessionMixin.macro_repo_rate() == [
            {"date": "2026-09-30", "FR001": "1.50", "FR007": None, "FR014": "2.10"}
        ]


class TestRiskScanPartialFailureWarning:
    def test_one_kind_fails_warns_and_keeps_other_kind(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def fake_rows(report: str, **kwargs: Any) -> list[dict[str, Any]]:  # noqa: ARG001
            if report == "RPT_GOODWILL_STOCKDETAILS":
                return [{"NOTICE_DATE": "2026-01-01"}]
            raise WebSourceError("pledge upstream down", context={"source": "eastmoney"})

        monkeypatch.setattr("atst.sources._session_research._eastmoney_rows", fake_rows)
        with warning_sink() as caveats:
            rows = ResearchSessionMixin.risk_scan("600519")
        assert rows == [{"kind": "goodwill", "NOTICE_DATE": "2026-01-01"}]
        assert [w.code for w in caveats] == [WarningCode.QUOTES_PARTIAL_FAILURE]
        assert "pledge" in caveats[0].message and "1/2" in caveats[0].message


class TestTencentMissingHighLow:
    def test_primary_parse_missing_high_low_is_nan_with_warning(self) -> None:
        """A 股 30 字段响应缺 high/low：置 NaN 并告警，不用现价冒充。"""
        fields = [""] * 30
        fields[1] = "demo"
        fields[3] = "10.00"
        payload = "~".join(fields)
        src = TencentSource(max_retries=0)
        with warning_sink() as caveats:
            quote = src.parse(f'v_sh600519="{payload}";', ["sh600519"])[0]
        assert quote.price == 10.0
        assert math.isnan(quote.high) and math.isnan(quote.low)
        assert len(caveats) == 2
        assert all(w.code is WarningCode.DECODE_CAVEAT for w in caveats)
        assert any("high" in w.message for w in caveats)
        assert any("low" in w.message for w in caveats)


class TestSinaPerRowTolerance:
    @staticmethod
    def _full_row(code: str = "sh600519") -> str:
        f = [""] * 32
        f[0] = "demo"
        f[1] = f[3] = "10.00"
        f[2] = "9.90"
        f[4] = "10.50"
        f[5] = "9.80"
        f[8] = "100"
        f[9] = "1000.0"
        f[30] = "2026-09-30"
        f[31] = "15:00:00"
        return f'var hq_str_{code}="{",".join(f)}";'

    def test_short_row_is_skipped_without_aborting_good_rows(self) -> None:
        body = self._full_row() + '\nvar hq_str_sh999999="a,b,c";\n'
        src = SinaSource(max_retries=0)
        with warning_sink() as caveats:
            quotes = src.parse(body, ["sh600519", "sh999999"])
        assert [q.code for q in quotes] == ["sh600519"]
        assert len(caveats) == 1
        assert caveats[0].code is WarningCode.WEB_SINA_MARKET_FIELD_MISSING
        assert "sh999999" in caveats[0].message

    def test_no_such_code_distinguished_from_deprecated_source(self) -> None:
        src = SinaSource(max_retries=0)
        with warning_sink() as caveats, pytest.raises(SourceDeprecated) as exc:
            src.parse('var hq_str_sh999999="";\n', ["sh999999"])
        assert exc.value.context["reason"] == "no_such_code"
        assert caveats and "空报价" in caveats[0].message

        with pytest.raises(SourceDeprecated) as exc2:
            src.parse("no recognizable data", ["sh600519"])
        assert exc2.value.context["reason"] == "empty_response"


class TestKlinesRespectsConfiguredSources:
    def test_no_kline_in_config_fails_without_injecting_source(self) -> None:
        from atst.sources import WebQuoteClient

        client = WebQuoteClient(sources=["sina"])
        try:
            with pytest.raises(AllSourcesExhausted, match="未包含"):
                client.klines("sh600519")
        finally:
            client.close()

    def test_configured_kline_source_is_used(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from atst.sources import WebQuoteClient

        class _Fake:
            def fetch_bars(self, symbol: str, **kwargs: Any) -> list[dict[str, Any]]:
                return [{"symbol": symbol, **kwargs}]

        client = WebQuoteClient(sources=["kline"])
        monkeypatch.setattr(client, "_instance", lambda name: _Fake())
        assert client.klines("sh600519", count=1)[0] == {
            "symbol": "sh600519",
            "period": "day",
            "count": 1,
            "adjust": "qfq",
        }


class TestQuotesTransportExceptionNarrowing:
    @staticmethod
    def _session_with(fake: Any) -> Any:
        from atst.sources.session import WebQuoteSession

        session = WebQuoteSession(source="sina")
        session._client = fake  # noqa: SLF001 —— 离线 seam
        return session

    def test_value_error_propagates_unchanged(self) -> None:
        class _Fake:
            def fetch(self, symbols: Any) -> list[Any]:
                raise ValueError("bad period")

        with pytest.raises(ValueError, match="bad period"):
            self._session_with(_Fake()).quotes(["600519"])

    def test_os_error_is_wrapped_as_compatibility_error(self) -> None:
        class _Fake:
            def fetch(self, symbols: Any) -> list[Any]:
                raise ConnectionError("network down")

        with pytest.raises(CompatibilityError, match="network down"):
            self._session_with(_Fake()).quotes(["600519"])
