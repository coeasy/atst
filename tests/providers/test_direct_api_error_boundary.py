from __future__ import annotations

import pytest

from tstdx.errors import InternalError, ValidationError
from tstdx.provider_api import ProviderAPI


class Service:
    pass


def test_existing_tdx_error_is_preserved_and_enriched() -> None:
    api = ProviderAPI(Service(), provider="tencent")
    original = ValidationError("bad input")

    def fail() -> None:
        raise original

    with pytest.raises(ValidationError) as caught:
        api._invoke("quote", fail)

    assert caught.value is original
    assert caught.value.context["provider"] == "tencent"
    assert caught.value.context["channel"] == "quote"
    assert caught.value.context["fallback"] is False


def test_native_exception_is_wrapped_as_internal_error_with_cause() -> None:
    api = ProviderAPI(Service(), provider="tencent")
    original = ValueError("unexpected adapter shape")

    def fail() -> None:
        raise original

    with pytest.raises(InternalError) as caught:
        api._invoke("quote", fail)

    assert caught.value.code == "E9000"
    assert caught.value.context == {
        "provider": "tencent",
        "channel": "quote",
        "fallback": False,
        "provider_switch_allowed": False,
        "cause_type": "ValueError",
    }
    assert caught.value.cause is original
    assert caught.value.__cause__ is original
