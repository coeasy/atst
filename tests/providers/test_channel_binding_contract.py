"""Registry ↔ channel-binding parity gates（导入期契约，离线）。

``tstdx/catalog/provider_bindings.py`` 只做一件事：声明 ``(provider, channel) -> adapter``。
本文件锁定它与 Provider 注册表的一一对应关系，并锁定 v16 clean break——
Direct API 对象层（依赖已删除的 v12 service）不再存在。
"""

from __future__ import annotations

import importlib

import pytest

import tstdx.catalog.provider_bindings as provider_api
from tstdx.catalog.provider_bindings import TdxProviderAPI, resolve_channel_adapter
from tstdx.providers import PROVIDERS

#: Composite Providers (``derived`` / ``builtin``) expose their capabilities only
#: through the unified QuerySpec path and deliberately own **no** channel
#: adapter, so the parity gates do not apply to them.
_COMPOSITE_PROVIDERS = frozenset(
    pid for pid in PROVIDERS.ids() if provider_api._CHANNEL_BINDINGS_BY_PROVIDER[pid].CHANNEL_API_EXEMPT
)

_WEB_PROVIDERS = tuple(
    pid for pid in PROVIDERS.ids() if pid != "tdx" and pid not in _COMPOSITE_PROVIDERS
)


def _binding_type(provider: str) -> type[provider_api.ChannelBindings]:
    mapping = provider_api._CHANNEL_BINDINGS_BY_PROVIDER
    assert set(mapping) == set(PROVIDERS.ids())
    return mapping[provider]


@pytest.mark.parametrize("provider", PROVIDERS.ids())
def test_every_registered_provider_has_exactly_one_binding_type(provider: str) -> None:
    assert _binding_type(provider).provider_id == provider


@pytest.mark.parametrize("provider", _WEB_PROVIDERS)
def test_web_channel_bindings_are_generated_from_registry(provider: str) -> None:
    api_type = _binding_type(provider)
    registered = {channel.id for channel in PROVIDERS.get(provider).channels if not channel.local}
    mapped = set(api_type.CHANNELS) | set(api_type.EXPLICIT_CHANNELS)
    assert mapped == registered

    for channel, (module_name, class_name) in api_type.CHANNELS.items():
        module = importlib.import_module(module_name)
        assert isinstance(getattr(module, class_name), type), (provider, channel)


@pytest.mark.parametrize("provider", _WEB_PROVIDERS)
def test_every_registered_web_channel_resolves_without_network(provider: str) -> None:
    for channel in PROVIDERS.get(provider).channels:
        if channel.local or channel.id in _binding_type(provider).EXPLICIT_CHANNELS:
            continue
        assert resolve_channel_adapter(provider, channel.id) is not None


def test_tdx_online_channels_are_declared_and_vipdoc_stays_local_only() -> None:
    """tdx 的 Direct channel 表为空：本地 vipdoc 归属 ``local_vipdoc`` Provider。"""
    spec = PROVIDERS.get("tdx")
    online = {channel.id for channel in spec.channels if not channel.local}

    assert set(TdxProviderAPI.DIRECT_CHANNELS) == online
    assert TdxProviderAPI.CHANNELS == {}
    assert {channel.id for channel in spec.channels if channel.local} == set()
    assert PROVIDERS.get("local_vipdoc").channel("vipdoc").local is True


def test_binding_builder_rejects_incomplete_provider_type_set() -> None:
    with pytest.raises(RuntimeError, match="registry mismatch"):
        provider_api._build_channel_bindings((provider_api.TdxProviderAPI,))


def test_direct_api_object_layer_is_physically_removed() -> None:
    """v16 clean break：不再有绑定 v12 service 的 ``md.<provider>`` 对象层。"""
    assert not hasattr(provider_api, "build_provider_api")
    assert not hasattr(provider_api, "ProviderAPI")
    assert not hasattr(provider_api, "WebProviderAPI")
