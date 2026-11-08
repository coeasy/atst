from __future__ import annotations

import importlib

import pytest

import atst.providers as registry
from atst.providers import PROVIDERS, resolve_channel_adapter

WEB_PROVIDER_MODULES = {
    "tencent": registry.tencent,
    "sina": registry.sina,
    "eastmoney": registry.eastmoney,
    "baidu": registry.baidu,
    "jsl": registry.jsl,
    "boc": registry.boc,
    "iwencai": registry.iwencai,
}


def _registry_channels(provider: str) -> set[str]:
    return {channel.id for channel in PROVIDERS.get(provider).channels}


@pytest.mark.parametrize("provider,module", WEB_PROVIDER_MODULES.items())
def test_channel_bindings_provider_id_matches_registry(provider: str, module) -> None:
    assert module.SPEC.id == provider
    assert provider in PROVIDERS.ids()


@pytest.mark.parametrize("provider,module", WEB_PROVIDER_MODULES.items())
def test_all_registry_web_channels_have_channel_binding(provider: str, module) -> None:
    mapped = set(module.CHANNEL_ADAPTERS) | set(module.EXPLICIT_CHANNELS)
    assert mapped == _registry_channels(provider)


@pytest.mark.parametrize("provider,module", WEB_PROVIDER_MODULES.items())
def test_adapter_refs_import_without_instantiation(provider: str, module) -> None:
    for channel, (module_name, class_name) in module.CHANNEL_ADAPTERS.items():
        loaded = importlib.import_module(module_name)
        adapter_cls = getattr(loaded, class_name)
        assert isinstance(adapter_cls, type), (provider, channel, module_name, class_name)


def test_jsl_etf_is_neither_a_registry_nor_a_bound_channel() -> None:
    assert "etf" not in registry.jsl.CHANNEL_ADAPTERS
    with pytest.raises(Exception) as caught:
        resolve_channel_adapter("jsl", "etf")
    assert caught.value.context["provider"] == "jsl"
    assert caught.value.context["channel"] == "etf"
    assert caught.value.context["fallback"] is False
    assert caught.value.context["provider_switch_allowed"] is False


def test_tdx_registry_channels_are_explicit_protocol_or_local_families() -> None:
    # tdx 只暴露在线协议 channel 家族；本地 vipdoc 归属独立 Provider
    # local_vipdoc（v13 canonical design）。
    assert _registry_channels("tdx") == {
        "quotation",
        "extended",
        "goods",
        "f10",
        "mac",
    }
    assert _registry_channels("local_vipdoc") == {"vipdoc"}


def test_provider_ids_do_not_contain_channel_or_market_names() -> None:
    forbidden = {"quote", "kline", "minute", "ticks", "hk", "us", "vipdoc"}
    assert forbidden.isdisjoint(PROVIDERS.ids())
