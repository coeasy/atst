from __future__ import annotations

import pytest

from tstdx.errors import TdxError
from tstdx.facade.routing import RouteSelector


class Selector(RouteSelector):
    default_route = "auto"


def test_auto_prefers_tdx_once_and_never_calls_web_after_failure() -> None:
    selector = Selector()
    calls: list[str] = []

    def tdx():
        calls.append("tdx")
        raise TdxError("tdx failed")

    def web():
        calls.append("web")
        return "wrong-provider"

    with pytest.raises(TdxError):
        selector._try_routes(None, {"tdx": tdx, "web": web})

    assert calls == ["tdx"]


def test_auto_uses_only_implementation_when_tdx_does_not_exist() -> None:
    selector = Selector()
    assert selector._try_routes(None, {"web": lambda: "ok"}) == "ok"


def test_explicit_web_remains_explicit_compatibility_path() -> None:
    selector = Selector()
    assert selector._try_routes("web", {"tdx": lambda: "tdx", "web": lambda: "web"}) == "web"
