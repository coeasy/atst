from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

import tstdx.reader as reader_mod
from tstdx import errors
from tstdx.errors import ValidationError
from tstdx.planned_service import UnifiedMarketDataService as PlannedService
from tstdx.sources import DataSourceRouter, SourceUnavailable


def test_sources_reexports_canonical_source_unavailable() -> None:
    assert SourceUnavailable is errors.SourceUnavailable
    assert SourceUnavailable.code == "E7050"


def test_default_router_uses_the_same_planned_service_as_public_runtime() -> None:
    router = DataSourceRouter()
    try:
        assert isinstance(router._service, PlannedService)
    finally:
        router.close()


def test_legacy_multi_order_is_rejected_instead_of_fallback() -> None:
    router = DataSourceRouter(order=["tdx", "web"])
    try:
        with pytest.raises(ValidationError):
            router._selection(provider=None, source=None, order=None)
    finally:
        router.close()


def test_default_selection_is_tdx() -> None:
    router = DataSourceRouter()
    try:
        assert router._selection(provider=None, source=None, order=None) == (
            "tdx",
            None,
            None,
        )
    finally:
        router.close()


def test_legacy_web_selects_one_provider_only() -> None:
    router = DataSourceRouter(web_sources=["sina", "tencent"])
    try:
        assert router._selection(provider=None, source=None, order=["web"]) == (
            "sina",
            None,
            None,
        )
    finally:
        router.close()


class FakeSinaService:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def bars(
        self,
        symbol: str,
        *,
        period: str,
        count: int,
        start: int,
        adjust: str,
        provider: str,
    ) -> list[dict[str, Any]]:
        self.calls.append(
            {
                "symbol": symbol,
                "period": period,
                "count": count,
                "start": start,
                "adjust": adjust,
                "provider": provider,
            }
        )
        return [{"datetime": "2026-09-08 10:00", "close": 1.0}]

    def close(self) -> None:
        return None


def test_sina_specific_period_does_not_pass_through_tdx_category_mapping() -> None:
    service = FakeSinaService()
    router = DataSourceRouter(service=service)  # type: ignore[arg-type]

    rows = router.kline(
        "sh600519",
        provider="sina",
        period="120min",
        count=1,
    )

    assert rows == [{"datetime": "2026-09-08 10:00", "close": 1.0}]
    assert service.calls == [
        {
            "symbol": "sh600519",
            "period": "120min",
            "count": 1,
            "start": 0,
            "adjust": "",
            "provider": "sina",
        }
    ]


def test_m5_vipdoc_uses_minute_reader(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    fake_file = tmp_path / "fake.lc5"
    fake_file.write_bytes(b"x")
    calls: list[str] = []

    class FakeMinReader:
        def read(self, path, output="dict"):
            calls.append("min")
            return [{"datetime": "2026-09-08 09:35", "close": 1.0}]

    class FakeDayReader:
        def read(self, path, output="dict"):
            calls.append("day")
            raise AssertionError("M5 must never be parsed by DayBarReader")

    monkeypatch.setattr(reader_mod, "MinBarReader", FakeMinReader)
    monkeypatch.setattr(reader_mod, "DayBarReader", FakeDayReader)
    monkeypatch.setattr(reader_mod, "resolve_vipdoc_path", lambda *args, **kwargs: fake_file)

    router = DataSourceRouter(vipdoc_root=tmp_path)
    try:
        rows = router._reader_kline("sh600519", "5m", 1)
        assert rows[0]["close"] == 1.0
        assert calls == ["min"]
    finally:
        router.close()
