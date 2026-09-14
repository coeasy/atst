from __future__ import annotations

from dataclasses import fields
from pathlib import Path

from tstdx.batch import BatchSpec
from tstdx.direct_provider import DirectProviderExecutor
from tstdx.domain.models import Bar, Quote
from tstdx.errors import ValidationError
from tstdx.query import QueryPlanner, QuerySpec
from tstdx.result import Provenance, QueryResult
from tstdx.runtime import UnifiedRuntime


def _local_plan(period: str):
    return QueryPlanner().compile(
        QuerySpec.build(
            "bars",
            symbols="sh600519",
            provider="local_vipdoc",
            period=period,
            count=2,
            currentness="historical",
        )
    )


def test_partial_success_exists_only_on_batch_contract() -> None:
    assert "allow_partial" not in {item.name for item in fields(QuerySpec)}
    spec = BatchSpec.quotes(["sh600519", "sh600519", "sz000001"])
    assert spec.symbols == ("sh600519", "sz000001")
    assert spec.provider == "tdx"


def test_quotes_batch_reports_ok_missing_and_failed(monkeypatch) -> None:
    runtime = UnifiedRuntime()

    def fake_quotes(symbol, **kwargs):
        plan = QueryPlanner().compile(
            QuerySpec.build(
                "quotes",
                symbols=symbol,
                provider="tdx",
                currentness="live",
            )
        )
        if symbol == "sh600519":
            return QueryResult.from_plan(
                [Quote(code="600519", price=1.0)],
                plan=plan,
                provenance=Provenance.direct(plan),
            )
        if symbol == "sz000001":
            return QueryResult.from_plan([], plan=plan, provenance=Provenance.direct(plan))
        raise ValidationError("bad symbol")

    monkeypatch.setattr(runtime, "quotes", fake_quotes)
    result = runtime.quotes_batch(["sh600519", "sz000001", "bj430047"])
    assert result.items["sh600519"].status == "ok"
    assert result.items["sz000001"].status == "missing"
    assert result.items["bj430047"].status == "failed"
    assert result.failed == ("bj430047",)
    assert result.missing == ("sz000001",)


def test_local_5min_uses_minute_reader_and_fzline_path(monkeypatch, tmp_path) -> None:
    seen: dict[str, object] = {}

    class FakeMinReader:
        def __init__(self, *, profile, interval):
            seen["profile"] = profile
            seen["interval"] = interval

        def read(self, path, *, output):
            seen["path"] = Path(path)
            seen["output"] = output
            return [Bar(datetime="a"), Bar(datetime="b"), Bar(datetime="c")]

    monkeypatch.setattr("tstdx.reader.MinBarReader", FakeMinReader)
    executor = DirectProviderExecutor(vipdoc_root=str(tmp_path))
    rows = executor._local_bars(_local_plan("5min"))
    assert seen["interval"] == 5
    assert seen["profile"] == "a_share_min"
    assert seen["output"] == "model"
    assert seen["path"] == tmp_path / "sh" / "fzline" / "sh600519.lc5"
    assert [row.datetime for row in rows] == ["b", "c"]


def test_local_1min_uses_minute_reader_and_minline_path(monkeypatch, tmp_path) -> None:
    seen: dict[str, object] = {}

    class FakeMinReader:
        def __init__(self, *, profile, interval):
            seen["profile"] = profile
            seen["interval"] = interval

        def read(self, path, *, output):
            seen["path"] = Path(path)
            return [Bar(datetime="a")]

    monkeypatch.setattr("tstdx.reader.MinBarReader", FakeMinReader)
    executor = DirectProviderExecutor(vipdoc_root=str(tmp_path))
    rows = executor._local_bars(_local_plan("1min"))
    assert seen["interval"] == 1
    assert seen["path"] == tmp_path / "sh" / "minline" / "sh600519.lc1"
    assert len(rows) == 1
