from __future__ import annotations

import importlib
from typing import Any

import pytest

import tstdx.provider_api as provider_api
from tstdx.errors import ValidationError
from tstdx.providers import PROVIDERS

#: Composite Providers (``derived`` / ``builtin``) expose their capabilities only
#: through the unified QuerySpec path and deliberately own **no** Direct channel
#: API, so the registry ↔ Direct-channel parity gates do not apply to them.
_COMPOSITE_PROVIDERS = frozenset(
    pid
    for pid in PROVIDERS.ids()
    if provider_api._PROVIDER_API_TYPES[pid].CHANNEL_API_EXEMPT
)

#: Providers that must expose a Direct channel API covering their registry channels.
_WEB_PROVIDERS = tuple(
    pid for pid in PROVIDERS.ids() if pid != "tdx" and pid not in _COMPOSITE_PROVIDERS
)


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
        self.calls: list[tuple[str, str]] = []

    def quotes(
        self,
        symbols: Any,
        *,
        provider: str,
        with_meta: bool = False,
    ) -> tuple[str, str, Any, bool]:
        self.calls.append(("quotes", provider))
        return "quotes", provider, symbols, with_meta

    def bars(
        self,
        symbol: str,
        *,
        period: str,
        count: int,
        start: int,
        adjust: str,
        provider: str,
        with_meta: bool = False,
    ) -> tuple[str, str, str, bool]:
        del period, count, start, adjust
        self.calls.append(("bars", provider))
        return "bars", provider, symbol, with_meta


def _api_type(provider: str) -> type[provider_api.ProviderAPI]:
    mapping = provider_api._PROVIDER_API_TYPES
    assert set(mapping) == set(PROVIDERS.ids())
    return mapping[provider]


@pytest.mark.parametrize("provider", PROVIDERS.ids())
def test_every_registered_provider_has_exactly_one_direct_api_type(provider: str) -> None:
    api_type = _api_type(provider)
    assert api_type.provider_id == provider


@pytest.mark.parametrize("provider", _WEB_PROVIDERS)
def test_web_direct_channel_mapping_is_generated_from_registry(provider: str) -> None:
    api_type = _api_type(provider)
    registered = {
        channel.id for channel in PROVIDERS.get(provider).channels if not channel.local
    }
    mapped = set(api_type.CHANNELS) | set(api_type.EXPLICIT_CHANNELS)
    assert mapped == registered

    for channel, (module_name, class_name) in api_type.CHANNELS.items():
        module = importlib.import_module(module_name)
        assert isinstance(getattr(module, class_name), type), (provider, channel)


@pytest.mark.parametrize("provider", _WEB_PROVIDERS)
def test_every_registered_web_channel_resolves_without_network(provider: str) -> None:
    service = FakeService()
    api = provider_api.build_provider_api(service, provider)
    for channel in PROVIDERS.get(provider).channels:
        if channel.local:
            continue
        resolved = api.channel(channel.id)
        assert resolved is not None


@pytest.mark.parametrize("provider", PROVIDERS.ids())
@pytest.mark.parametrize("capability", ("quotes", "bars"))
def test_unified_direct_methods_are_registry_guarded_before_service_io(
    provider: str,
    capability: str,
) -> None:
    service = FakeService()
    api = provider_api.build_provider_api(service, provider)
    supported = PROVIDERS.get(provider).supports(capability)

    if supported:
        result = api.quotes("sh600519") if capability == "quotes" else api.bars("sh600519")
        assert result[0] == capability
        assert service.calls == [(capability, provider)]
        return

    with pytest.raises(ValidationError) as caught:
        if capability == "quotes":
            api.quotes("sh600519")
        else:
            api.bars("sh600519")
    assert service.calls == []
    assert caught.value.context["provider"] == provider
    assert caught.value.context["capability"] == capability
    assert caught.value.context["phase"] == "direct_contract"
    assert caught.value.context["fallback"] is False
    assert caught.value.context["provider_switch_allowed"] is False


def test_tdx_registered_online_channels_resolve_and_vipdoc_is_explicitly_local_only() -> None:
    service = FakeService()
    api = provider_api.build_provider_api(service, "tdx")
    spec = PROVIDERS.get("tdx")
    online = {channel.id for channel in spec.channels if not channel.local}
    local = {channel.id for channel in spec.channels if channel.local}

    assert set(provider_api.TdxProviderAPI.DIRECT_CHANNELS) == online
    # v13：本地 vipdoc 是独立 Provider local_vipdoc 的 local channel，不再挂在
    # tdx 上；tdx 自身没有任何 local channel，且其 Direct API 显式拒绝 vipdoc。
    assert local == set()
    assert PROVIDERS.get("local_vipdoc").channel("vipdoc").local is True
    for channel in sorted(online):
        assert api.channel(channel) == ("tdx", channel)

    with pytest.raises(ValidationError) as caught:
        api.channel("vipdoc")
    assert caught.value.context["provider"] == "tdx"
    assert caught.value.context["channel"] == "vipdoc"
    assert caught.value.context["phase"] == "direct_contract"


def test_contract_builder_rejects_incomplete_provider_type_set() -> None:
    with pytest.raises(RuntimeError, match="registry mismatch"):
        provider_api._build_provider_api_types((provider_api.TdxProviderAPI,))
