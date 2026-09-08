from __future__ import annotations

from pathlib import Path

import pytest

import tstdx.reader as reader_mod
from tstdx import errors
from tstdx.errors import ValidationError
from tstdx.sources import DataSourceRouter, SourceUnavailable


def test_sources_reexports_canonical_source_unavailable() -> None:
    assert SourceUnavailable is errors.SourceUnavailable
    assert SourceUnavailable.code == "E7050"


def test_legacy_multi_order_is_rejected_instead_of_fallback() -> None:
    router = DataSourceRouter(order=["tdx", "web"])
    with pytest.raises(ValidationError):
        router._selection(provider=None, source=None, order=None)


def test_default_selection_is_tdx() -> None:
    router = DataSourceRouter()
    assert router._selection(provider=None, source=None, order=None) == ("tdx", None, None)


def test_legacy_web_selects_one_provider_only() -> None:
    router = DataSourceRouter(web_sources=["sina", "tencent"])
    assert router._selection(provider=None, source=None, order=["web"]) == (
        "sina",
        None,
        None,
    )


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
    rows = router._reader_kline("sh600519", "5m", 1)

    assert rows[0]["close"] == 1.0
    assert calls == ["min"]
