from __future__ import annotations

from typing import Any

import pytest

import tstdx.facade as facade_pkg
from tstdx.errors import CommandOffline, ValidationError
from tstdx.facade.api import UnifiedQuoteAPI as LegacyUnifiedQuoteAPI
from tstdx.facade.strict import UnifiedQuoteAPI as StrictUnifiedQuoteAPI


def test_official_facade_export_is_strict_and_legacy_remains_available() -> None:
    assert facade_pkg.UnifiedQuoteAPI is StrictUnifiedQuoteAPI
    assert facade_pkg.LegacyUnifiedQuoteAPI is LegacyUnifiedQuoteAPI
    assert StrictUnifiedQuoteAPI is not LegacyUnifiedQuoteAPI


def test_auto_adjust_does_not_silently_select_web_provider() -> None:
    api = StrictUnifiedQuoteAPI(route="auto")
    try:
        with pytest.raises(ValidationError) as caught:
            api.bars("sh600519", adjust="qfq")
        assert caught.value.context["provider"] == "tdx"
        assert caught.value.context["fallback"] is False
    finally:
        api.close()


def test_explicit_web_adjust_remains_an_explicit_compatibility_choice(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []

    def fake_bars(self, symbol: str, **kwargs: Any):  # noqa: ANN001
        calls.append({"symbol": symbol, **kwargs})
        return []

    monkeypatch.setattr(LegacyUnifiedQuoteAPI, "bars", fake_bars)
    api = StrictUnifiedQuoteAPI(route="auto")
    try:
        assert api.bars("sh600519", adjust="qfq", route="web") == []
    finally:
        api.close()

    assert calls == [
        {
            "symbol": "sh600519",
            "period": "day",
            "count": 320,
            "start": 0,
            "adjust": "qfq",
            "route": "web",
        }
    ]


class _OfflineTdx:
    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []

    def security_list(self, market: Any, start: int = 0):
        self.calls.append(("security_list", (market, start)))
        raise CommandOffline("0x044D offline")

    def security_list_all(self, market: Any):
        self.calls.append(("security_list_all", market))
        raise CommandOffline("0x044D offline")


class _FakeManager:
    def __init__(self, tdx: Any) -> None:
        self.tdx = tdx


class _FakeService:
    def __init__(self, tdx: Any) -> None:
        self.manager = _FakeManager(tdx)

    def close(self) -> None:
        pass


def test_security_list_tdx_offline_does_not_call_eastmoney(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tdx = _OfflineTdx()
    api = StrictUnifiedQuoteAPI(route="auto")
    api._provider_service = _FakeService(tdx)  # type: ignore[assignment]
    web_calls: list[Any] = []
    monkeypatch.setattr(
        api,
        "_security_list_web",
        lambda *args, **kwargs: web_calls.append((args, kwargs)) or [],
    )
    try:
        with pytest.raises(CommandOffline):
            api.security_list(1)
        with pytest.raises(CommandOffline):
            api.security_list_all(1)
    finally:
        api.close()

    assert web_calls == []
    assert tdx.calls == [
        ("security_list", (1, 0)),
        ("security_list_all", 1),
    ]


def test_explicit_web_security_list_is_user_selected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api = StrictUnifiedQuoteAPI(route="auto")
    monkeypatch.setattr(api, "_security_list_web", lambda market, start=0: [{"code": "x"}])
    try:
        assert api.security_list(1, route="web") == [{"code": "x"}]
    finally:
        api.close()


def test_adjusted_bars_explicit_empty_events_does_not_fetch_capital_changes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tstdx.domain.models import Bar

    api = StrictUnifiedQuoteAPI()
    sample = [
        Bar(
            datetime="2026-09-08 15:00",
            open=10.0,
            high=10.0,
            low=10.0,
            close=10.0,
            volume=100,
            amount=1000.0,
        )
    ]
    monkeypatch.setattr(api, "bars", lambda *args, **kwargs: sample)

    def forbidden(*args: Any, **kwargs: Any):
        raise AssertionError("capital_changes must not be fetched for events=[]")

    monkeypatch.setattr(api, "capital_changes", forbidden)
    try:
        rows = api.adjusted_bars("sh600519", events=[])
    finally:
        api.close()

    assert len(rows) == 1
    assert rows[0].close == pytest.approx(10.0)
