from __future__ import annotations

import importlib
from typing import Any

import pytest

import tstdx.provider_api as provider_api
from tstdx.errors import ValidationError
from tstdx.providers import PROVIDERS


class FakeManager:
    def web_adapter(
        self,
        provider: str,
        channel: str,
        adapter_cls: type[Any],
        *,
        resource_key: str | None = None,
        **kwargs: Any,
    ) -> tuple[str, str, type[Any], str | None]:
        return provider, channel, adapter_cls, resource_key

    def tdx_channel(self, channel: str) -> tuple[str, str]:
        return "tdx", channel


class FakeService:
    def __init__(self) -> None:
        self.manager = FakeManager()


def _api_type(provider: str) -> type[provider_api.ProviderAPI]:
    mapping = provider_api._PROVIDER_API_TYPES
    assert set(mapping) == set(PROVIDERS.ids())
    return mapping[provider]


@pytest.mark.parametrize("provider", PROVIDERS.ids())
def test_every_registered_provider_has_exactly_one_direct_api_type(provider: str) -> None:
    api_type = _api_type(provider)
    assert api_type.provider_id == provider


@pytest.mark.parametrize("provider", tuple(pid for pid in PROVIDERS.ids() if pid != "tdx"))
def test_web_direct_channel_mapping_is_generated_from_registry(provider: str) -> None:
    api_type = _api_type(provider)
    registered = {channel.id for channel in PROVIDERS.get(provider).channels}
    mapped = set(api_type.CHANNELS)
    if provider == "eastmoney":
        mapped.add("corporate")
    assert mapped == registered

    for channel, (module_name, class_name) in api_type.CHANNELS.items():
        module = importlib.import_module(module_name)
        assert isinstance(getattr(module, class_name), type), (provider, channel)


@pytest.mark.parametrize("provider", tuple(pid for pid in PROVIDERS.ids() if pid != "tdx"))
def test_every_registered_web_channel_resolves_without_network(provider: str) -> None:
    service = FakeService()
    api = provider_api.build_provider_api(service, provider)
    for channel in PROVIDERS.get(provider).channels:
        resolved = api.channel(channel.id)
        assert resolved is not None


def test_tdx_registered_online_channels_resolve_and_vipdoc_is_explicitly_local_only() -> None:
    service = FakeService()
    api = provider_api.build_provider_api(service, "tdx")
    registered = {channel.id for channel in PROVIDERS.get("tdx").channels}
    assert registered == {"quotation", "extended", "goods", "f10", "mac", "vipdoc"}

    for channel in sorted(registered - {"vipdoc"}):
        assert api.channel(channel) == ("tdx", channel)

    with pytest.raises(ValidationError) as caught:
        api.channel("vipdoc")
    assert caught.value.context["provider"] == "tdx"
    assert caught.value.context["channel"] == "vipdoc"
