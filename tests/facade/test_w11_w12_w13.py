# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""Legacy facade regression tests under the v12 Provider contract.

W11 keeps route health/circuit behavior but removes cross-Provider recovery.
W12 keeps explicit route governance while TDX remains the deterministic default.
W13 keeps argument/semantic guards; user-selected Web routes remain valid, but an
error on TDX never triggers a Web request.
"""

from __future__ import annotations

import logging
from typing import Any

import pytest

from tstdx.errors import CommandOffline, SourceUnavailable, TdxError
from tstdx.facade.api import UnifiedQuoteAPI


class _Sentinel(Exception):
    pass


def _capture_try_routes(api: UnifiedQuoteAPI) -> dict:
    captured: dict = {}

    def fake(route, fn):  # noqa: ANN001
        captured["route"] = route
        captured["fn"] = set(fn.keys())
        raise _Sentinel()

    api._try_routes = fake  # type: ignore[assignment, method-assign]
    return captured


class TestW11ErrorAggregation:
    def test_auto_failure_contains_only_selected_provider_path(self) -> None:
        api = UnifiedQuoteAPI()
        calls: list[str] = []

        def boom_tdx() -> list:
            calls.append("tdx")
            raise TdxError("tdx down")

        def boom_web() -> list:
            calls.append("web")
            raise RuntimeError("web down")

        with pytest.raises(TdxError) as ei:
            api._try_routes("auto", {"tdx": boom_tdx, "web": boom_web})
        assert calls == ["tdx"]
        assert set(ei.value.context["route_errors"]) == {"tdx"}

    def test_route_errors_attached_to_non_tdx_exception(self) -> None:
        api = UnifiedQuoteAPI()

        def boom() -> list:
            raise RuntimeError("x")

        with pytest.raises(RuntimeError) as ei:
            api._try_routes("tdx", {"tdx": boom})
        assert ei.value.context["route_errors"]["tdx"].startswith("RuntimeError: ")

    def test_one_warning_for_selected_route_failure(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        api = UnifiedQuoteAPI()

        def boom() -> list:
            raise TdxError("boom")

        with caplog.at_level(logging.WARNING, logger="tstdx.facade"), pytest.raises(TdxError):
            api._try_routes("auto", {"tdx": boom, "web": boom})
        records = [r for r in caplog.records if r.name == "tstdx.facade"]
        assert len(records) == 1
        assert "不会跨 Provider" in records[0].getMessage()


class TestW11CircuitBreaker:
    def test_opens_after_three_failures_without_switching_provider(self) -> None:
        api = UnifiedQuoteAPI()
        calls = {"tdx": 0, "web": 0}

        def boom_tdx() -> list:
            calls["tdx"] += 1
            raise TdxError("tdx down")

        def ok_web() -> list:
            calls["web"] += 1
            return ["web"]

        for _ in range(3):
            with pytest.raises(TdxError):
                api._try_routes("auto", {"tdx": boom_tdx, "web": ok_web})
        assert calls == {"tdx": 3, "web": 0}

        with pytest.raises(SourceUnavailable) as ei:
            api._try_routes("auto", {"tdx": boom_tdx, "web": ok_web})
        assert calls == {"tdx": 3, "web": 0}
        assert ei.value.context["provider_path"] == "tdx"
        assert ei.value.context["fallback"] is False

    def test_success_resets_failure_count(self) -> None:
        api = UnifiedQuoteAPI()
        calls = {"n": 0}

        def flaky() -> list:
            calls["n"] += 1
            if calls["n"] == 3:
                return ["ok"]
            raise TdxError("flaky")

        for _ in range(2):
            with pytest.raises(TdxError):
                api._try_routes("auto", {"tdx": flaky})
        assert api._try_routes("auto", {"tdx": flaky}) == ["ok"]
        assert api._route_fail_counts["tdx"] == 0

    def test_explicit_route_still_attempts_during_auto_cooldown(self) -> None:
        api = UnifiedQuoteAPI()
        calls = {"n": 0}

        def boom() -> list:
            calls["n"] += 1
            raise TdxError("x")

        for _ in range(3):
            with pytest.raises(TdxError):
                api._try_routes("auto", {"tdx": boom})
        with pytest.raises(TdxError):
            api._try_routes("tdx", {"tdx": boom})
        assert calls["n"] == 4

    def test_reset_circuit_reopens_selected_provider(self) -> None:
        api = UnifiedQuoteAPI()
        calls = {"n": 0}

        def boom() -> list:
            calls["n"] += 1
            raise TdxError("x")

        for _ in range(3):
            with pytest.raises(TdxError):
                api._try_routes("auto", {"tdx": boom})
        with pytest.raises(SourceUnavailable):
            api._try_routes("auto", {"tdx": boom})
        api.reset_circuit()
        with pytest.raises(TdxError):
            api._try_routes("auto", {"tdx": boom})
        assert calls["n"] == 4

    def test_close_keeps_circuit_state(self) -> None:
        api = UnifiedQuoteAPI()
        calls = {"n": 0}

        def boom() -> list:
            calls["n"] += 1
            raise TdxError("x")

        for _ in range(3):
            with pytest.raises(TdxError):
                api._try_routes("auto", {"tdx": boom})
        api.close()
        with pytest.raises(SourceUnavailable):
            api._try_routes("auto", {"tdx": boom})
        assert calls["n"] == 3


class TestW12RouteGovernance:
    @pytest.mark.parametrize(
        ("method", "args"),
        [
            ("snapshot", ("sh600519",)),
            ("minute_history", ("sh600519", 20260901)),
            ("security_count", ()),
            ("finance", ("sh600519",)),
            ("capital_changes", ("sh600519",)),
            ("auction", ("sh600519",)),
            ("volume_price", ("sh600519",)),
            ("ex_bars", ("hk00700",)),
            ("goods_quotes", ("m2509",)),
            ("f10", ("sh600519", "gsgk.dat")),
            ("f10_catalog", ("sh600519",)),
            ("ex_market_list", ()),
            ("ex_instruments", (0,)),
        ],
    )
    def test_tdx_only_methods_reject_explicit_web(self, method: str, args: tuple) -> None:
        api = UnifiedQuoteAPI()
        with pytest.raises(ValueError, match="仅支持 tdx"):
            getattr(api, method)(*args, route="web")

    def test_corporate_action_rejects_route_via_delegation(self) -> None:
        api = UnifiedQuoteAPI()
        with pytest.raises(ValueError, match="仅支持 tdx"):
            api.corporate_action("sh600519", route="web")

    def test_f10_catalog_delegates_and_closes(self, monkeypatch: pytest.MonkeyPatch) -> None:
        class FakeF10:
            def __init__(self, **kw: Any) -> None:
                self.closed = False

            def catalog(self, symbol: str) -> list[dict[str, Any]]:
                return [{"title": "公司概况", "filename": "gsgk.dat"}]

            def close(self) -> None:
                self.closed = True

        captured: list[FakeF10] = []

        def factory(**kw: Any) -> FakeF10:
            inst = FakeF10(**kw)
            captured.append(inst)
            return inst

        import tstdx.client as client_mod

        monkeypatch.setattr(client_mod, "F10Client", factory)
        rows = UnifiedQuoteAPI().f10_catalog("sh600519")
        assert rows == [{"title": "公司概况", "filename": "gsgk.dat"}]
        assert captured[0].closed is True

    def test_minute_tdx_failure_does_not_call_web(self) -> None:
        class FakeTdx:
            def minute_today(self, sym: str) -> list:
                raise RuntimeError("tdx down")

        api = UnifiedQuoteAPI()
        api._tdx = FakeTdx()
        web_called: list[str] = []

        def web(sym: str) -> list:
            web_called.append(sym)
            return [{"price": 1.0}]

        api.minute_web = web  # type: ignore[method-assign]
        with pytest.raises(RuntimeError, match="tdx down"):
            api.minute("sh600519")
        assert web_called == []

    def test_minute_prefers_tdx_when_healthy(self) -> None:
        class FakeTdx:
            def minute_today(self, sym: str) -> list:
                return [{"price": 9.9}]

        api = UnifiedQuoteAPI()
        api._tdx = FakeTdx()
        api.minute_web = lambda sym: (_ for _ in ()).throw(AssertionError("web called"))  # type: ignore[method-assign]
        assert api.minute("sh600519") == [{"price": 9.9}]

    def test_minute_count_slice_after_routing(self) -> None:
        class FakeTdx:
            def minute_today(self, sym: str) -> list:
                return [{"i": i} for i in range(10)]

        api = UnifiedQuoteAPI()
        api._tdx = FakeTdx()
        assert len(api.minute("sh600519", count=3)) == 3
        assert len(api.minute("sh600519")) == 10

    def test_trades_tdx_path_and_web_start_guard(self) -> None:
        class FakeTdx:
            def trade_today(self, sym: str, *, start: int = 0, count: int = 0) -> list:
                return [{"price": float(start + i)} for i in range(3)]

        api = UnifiedQuoteAPI()
        api._tdx = FakeTdx()
        rows = api.trades("sh600519", start=1)
        assert [r["price"] for r in rows] == [1.0, 2.0, 3.0]
        with pytest.raises(ValueError, match="start"):
            api.trades("sh600519", start=1, route="web")

    def test_trades_excludes_web_when_start_set(self) -> None:
        api = UnifiedQuoteAPI()
        cap = _capture_try_routes(api)
        with pytest.raises(_Sentinel):
            api.trades("sh600519", start=5)
        assert cap["fn"] == {"tdx"}

    def test_route_without_impl_gets_explicit_message(self) -> None:
        api = UnifiedQuoteAPI()
        with pytest.raises(TdxError) as ei:
            api.minute("sh600519", route="local")
        assert "无对应实现" in str(ei.value)


class TestW13BarsSemantics:
    def test_explicit_web_with_start_raises(self) -> None:
        api = UnifiedQuoteAPI()
        with pytest.raises(ValueError, match="start"):
            api.bars("sh600519", start=5, route="web")

    def test_auto_excludes_web_when_start_set(self) -> None:
        api = UnifiedQuoteAPI()
        cap = _capture_try_routes(api)
        with pytest.raises(_Sentinel):
            api.bars("sh600519", start=5)
        assert "web" not in cap["fn"]
        assert "tdx" in cap["fn"]

    def test_adjust_request_does_not_allow_tdx_raw_price(self) -> None:
        api = UnifiedQuoteAPI()
        cap = _capture_try_routes(api)
        with pytest.raises(_Sentinel):
            api.bars("sh600519", adjust="qfq")
        assert "tdx" not in cap["fn"]

    def test_explicit_tdx_with_adjust_raises(self) -> None:
        api = UnifiedQuoteAPI()
        with pytest.raises(ValueError, match="adjust"):
            api.bars("sh600519", adjust="qfq", route="tdx")

    def test_no_route_satisfies_conflicting_semantics(self) -> None:
        api = UnifiedQuoteAPI()
        with pytest.raises(TdxError, match="口径"):
            api.bars("sh600519", start=5, adjust="qfq")

    def test_default_impl_map_keeps_user_selectable_routes(self) -> None:
        api = UnifiedQuoteAPI()
        cap = _capture_try_routes(api)
        with pytest.raises(_Sentinel):
            api.bars("sh600519")
        assert cap["fn"] == {"local", "tdx", "web"}


class TestBlockQuotesProviderBoundary:
    def test_explicit_web_is_user_selected_provider_path(self) -> None:
        api = UnifiedQuoteAPI()
        cap = _capture_try_routes(api)
        with pytest.raises(_Sentinel):
            api.block_quotes(route="web")
        assert cap["route"] == "web"
        assert "web" in cap["fn"]

    def test_auto_map_can_expose_web_but_selector_stays_tdx(self) -> None:
        api = UnifiedQuoteAPI()
        calls: list[str] = []

        def tdx() -> list:
            calls.append("tdx")
            raise TdxError("tdx unavailable")

        def web() -> list:
            calls.append("web")
            return ["approx"]

        with pytest.raises(TdxError):
            api._try_routes("auto", {"tdx": tdx, "web": web})
        assert calls == ["tdx"]

    def test_explicit_web_rejects_nonzero_start(self) -> None:
        api = UnifiedQuoteAPI()
        with pytest.raises(ValueError, match="start=100"):
            api.block_quotes(start=100, route="web")

    def test_explicit_web_rejects_unmappable_block_type(self) -> None:
        api = UnifiedQuoteAPI()
        with pytest.raises(ValueError, match="block_type=3"):
            api.block_quotes(block_type=3, route="web")

    def test_local_route_still_rejected(self) -> None:
        api = UnifiedQuoteAPI()
        with pytest.raises(ValueError, match="block_quotes"):
            api.block_quotes(route="local")


class TestSourceUnavailableCompatibility:
    def test_source_unavailable_registered_in_retry_advice(self) -> None:
        from tstdx.errors import RETRY_ADVICE, SourceUnavailable

        assert SourceUnavailable in RETRY_ADVICE
        adv = SourceUnavailable.default_advice
        assert adv.retryable is False
        assert SourceUnavailable.code == "E7050"
        assert SourceUnavailable.http_status == 503

    def test_explicit_tdx_command_offline_is_transparent(self) -> None:
        import tstdx.facade.api as api_mod

        api = UnifiedQuoteAPI()
        original = api_mod.UnifiedQuoteAPI.tdx

        class FakeTdx:
            def auction_snapshot(self, sym: str):
                raise CommandOffline("0x056A offline")

        api_mod.UnifiedQuoteAPI.tdx = property(lambda self: FakeTdx())  # type: ignore[method-assign]
        try:
            with pytest.raises(CommandOffline):
                api.auction("sh600519", route="tdx")
        finally:
            api_mod.UnifiedQuoteAPI.tdx = original  # type: ignore[misc]
