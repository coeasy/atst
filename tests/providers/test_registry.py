from __future__ import annotations

import pytest

from tstdx.errors import ValidationError
from tstdx.providers import PROVIDERS, resolve_provider


def test_tdx_is_only_default_provider() -> None:
    assert PROVIDERS.default_provider == "tdx"
    assert "tdx" in PROVIDERS.ids()
    assert "tencent" in PROVIDERS.ids()
    assert "sina" in PROVIDERS.ids()
    assert "eastmoney" in PROVIDERS.ids()


def test_vipdoc_is_tdx_channel_not_provider() -> None:
    assert "vipdoc" not in PROVIDERS.ids()
    assert PROVIDERS.get("tdx").channel("vipdoc").local is True
    assert PROVIDERS.supports("tdx", "bars", channel="vipdoc") is True


def test_source_is_only_provider_selector_alias() -> None:
    assert resolve_provider(provider="tdx") == "tdx"
    assert resolve_provider(source="tdx") == "tdx"
    assert resolve_provider(provider="tencent", source="qq") == "tencent"


def test_conflicting_provider_and_source_fail_fast() -> None:
    with pytest.raises(ValidationError):
        resolve_provider(provider="tdx", source="sina")


def test_market_and_channel_are_not_provider_ids() -> None:
    with pytest.raises(ValidationError):
        PROVIDERS.get("hk")
    with pytest.raises(ValidationError):
        PROVIDERS.get("kline")


def test_only_verified_batch_limits_are_declared() -> None:
    assert PROVIDERS.get("tdx").channel("quotation").batch_limit == 60
    assert PROVIDERS.get("tencent").channel("quote").batch_limit is None
    assert PROVIDERS.get("sina").channel("quote").batch_limit is None
    assert PROVIDERS.get("eastmoney").channel("quote").batch_limit is None
