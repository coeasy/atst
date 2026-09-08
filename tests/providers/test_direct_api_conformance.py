from __future__ import annotations

import importlib

import pytest

from tstdx.errors import ValidationError
from tstdx.provider_api import (
    BaiduProviderAPI,
    BocProviderAPI,
    EastmoneyProviderAPI,
    IwencaiProviderAPI,
    JslProviderAPI,
    SinaProviderAPI,
    TencentProviderAPI,
)
from tstdx.providers import PROVIDERS


WEB_PROVIDER_APIS = {
    "tencent": TencentProviderAPI,
    "sina": SinaProviderAPI,
    "eastmoney": EastmoneyProviderAPI,
    "baidu": BaiduProviderAPI,
    "jsl": JslProviderAPI,
    "boc": BocProviderAPI,
    "iwencai": IwencaiProviderAPI,
}

# Full cleanup requires replacing the large provider_api.py file. The stale map
# is deliberately tracked here and is unreachable because WebProviderAPI.channel
# validates the authoritative Provider Registry before consulting CHANNELS.
LEGACY_UNREACHABLE_MAPPINGS = {("jsl", "etf")}


def _registry_channels(provider: str) -> set[str]:
    return {channel.id for channel in PROVIDERS.get(provider).channels}


@pytest.mark.parametrize("provider,api_cls", WEB_PROVIDER_APIS.items())
def test_direct_api_provider_id_matches_registry(provider: str, api_cls: type) -> None:
    assert api_cls.provider_id == provider
    assert provider in PROVIDERS.ids()


@pytest.mark.parametrize("provider,api_cls", WEB_PROVIDER_APIS.items())
def test_all_registry_web_channels_have_direct_api_mapping(provider: str, api_cls: type) -> None:
    mapped = set(api_cls.CHANNELS)
    if provider == "eastmoney":
        mapped.add("corporate")  # composite provider-specific namespace
    registered = _registry_channels(provider)
    expected_unreachable = {
        channel
        for mapped_provider, channel in LEGACY_UNREACHABLE_MAPPINGS
        if mapped_provider == provider
    }
    assert registered.issubset(mapped)
    assert mapped - registered == expected_unreachable


@pytest.mark.parametrize("provider,api_cls", WEB_PROVIDER_APIS.items())
def test_direct_adapter_refs_import_without_instantiation(provider: str, api_cls: type) -> None:
    for channel, (module_name, class_name) in api_cls.CHANNELS.items():
        module = importlib.import_module(module_name)
        adapter_cls = getattr(module, class_name)
        assert isinstance(adapter_cls, type), (provider, channel, module_name, class_name)


def test_jsl_etf_legacy_mapping_is_rejected_before_adapter_resolution() -> None:
    api = JslProviderAPI(object())  # type: ignore[arg-type]
    with pytest.raises(ValidationError) as caught:
        api.channel("etf")
    assert caught.value.context == {"provider": "jsl", "channel": "etf"}


def test_tdx_registry_channels_are_explicit_protocol_or_local_families() -> None:
    assert _registry_channels("tdx") == {
        "quotation",
        "extended",
        "goods",
        "f10",
        "mac",
        "vipdoc",
    }


def test_provider_ids_do_not_contain_channel_or_market_names() -> None:
    forbidden = {"quote", "kline", "minute", "ticks", "hk", "us", "vipdoc"}
    assert forbidden.isdisjoint(PROVIDERS.ids())
