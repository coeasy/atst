# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""UnifiedQuoteAPI legacy route compatibility under the v12 Provider contract.

The old tests asserted exception-driven ``local -> tdx -> web`` fallback.  That
behavior is intentionally removed: one production request chooses one Provider,
and provider failure is returned without trying a different Provider.
"""

from __future__ import annotations

import pytest

from tstdx.errors import TdxError
from tstdx.facade.api import UnifiedQuoteAPI


class _Sentinel(Exception):
    """Used to capture ``_try_routes`` inputs without executing I/O."""


def _capture(api: UnifiedQuoteAPI) -> dict:
    captured: dict = {}

    def fake(route, fn):  # noqa: ANN001
        captured["route"] = route
        captured["fn"] = set(fn.keys())
        raise _Sentinel()

    api._try_routes = fake  # type: ignore[assignment]
    return captured


class TestTryRoutes:
    def test_empty_list_is_success(self) -> None:
        api = UnifiedQuoteAPI()
        assert api._try_routes("auto", {"tdx": lambda: []}) == []

    def test_tdx_failure_does_not_fallback_to_web(self) -> None:
        api = UnifiedQuoteAPI()
        calls: list[str] = []

        def boom() -> list:
            calls.append("tdx")
            raise TdxError("boom")

        def web() -> list:
            calls.append("web")
            return [1, 2]

        with pytest.raises(TdxError, match="boom"):
            api._try_routes("auto", {"tdx": boom, "web": web})
        assert calls == ["tdx"]

    def test_auto_uses_only_available_web_implementation(self) -> None:
        api = UnifiedQuoteAPI()
        assert api._try_routes("auto", {"web": lambda: [1, 2]}) == [1, 2]

    def test_explicit_route_single(self) -> None:
        api = UnifiedQuoteAPI()
        assert api._try_routes("tdx", {"tdx": lambda: [42]}) == [42]

    def test_import_error_not_caught(self) -> None:
        api = UnifiedQuoteAPI()

        def boom() -> list:
            raise ImportError("no module")

        with pytest.raises(ImportError):
            api._try_routes("auto", {"tdx": boom})


class TestDefaultRouteRespected:
    def test_snapshot_uses_default_route_auto(self) -> None:
        api = UnifiedQuoteAPI(route="auto")
        cap = _capture(api)
        with pytest.raises(_Sentinel):
            api.snapshot("sh600519")
        assert cap["route"] == "auto"

    def test_snapshot_uses_explicit_default_route(self) -> None:
        api = UnifiedQuoteAPI(route="web")
        cap = _capture(api)
        with pytest.raises(_Sentinel):
            api.snapshot("sh600519")
        assert cap["route"] == "web"


class TestBarsProviderSelection:
    def test_bars_legacy_impl_map_can_contain_multiple_paths(self) -> None:
        """Compatibility map may expose choices; ``auto`` still executes TDX once."""
        api = UnifiedQuoteAPI(route="auto")
        cap = _capture(api)
        with pytest.raises(_Sentinel):
            api.bars("sh600519")
        assert cap["fn"] == {"local", "tdx", "web"}

    def test_bars_explicit_web_remains_user_choice(self) -> None:
        api = UnifiedQuoteAPI(route="auto")
        cap = _capture(api)
        with pytest.raises(_Sentinel):
            api.bars("sh600519", route="web")
        assert cap["route"] == "web"
        assert cap["fn"] == {"local", "tdx", "web"}
