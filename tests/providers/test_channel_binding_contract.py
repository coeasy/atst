"""Registry ↔ channel-binding parity gates（导入期契约，离线）。

v2 起 ``(provider, channel) -> adapter`` 声明与 Provider 注册表一起放在各
``atst/providers/<provider>.py`` 声明模块里。本文件锁定声明聚合与注册表的一一对应，
并保留 v16 clean break：Direct API 对象层不再存在。
"""

from __future__ import annotations

import importlib

import pytest

import atst.providers as registry
from atst.errors import ValidationError
from atst.providers import PROVIDERS, resolve_channel_adapter

#: Composite Providers (``derived`` / ``builtin``) expose their capabilities only
#: through the unified QuerySpec path and deliberately own **no** channel adapter.
_COMPOSITE_PROVIDERS = frozenset(
    pid for pid in PROVIDERS.ids() if registry._CHANNEL_DECLARATIONS[pid].CHANNEL_API_EXEMPT
)

_WEB_PROVIDERS = tuple(
    pid for pid in PROVIDERS.ids() if pid != "tdx" and pid not in _COMPOSITE_PROVIDERS
)


def _declaration(provider: str):
    mapping = registry._CHANNEL_DECLARATIONS
    assert set(mapping) == set(PROVIDERS.ids())
    return mapping[provider]


@pytest.mark.parametrize("provider", PROVIDERS.ids())
def test_every_registered_provider_has_exactly_one_declaration_module(provider: str) -> None:
    assert _declaration(provider).SPEC.id == provider


@pytest.mark.parametrize("provider", _WEB_PROVIDERS)
def test_web_channel_bindings_are_declared_from_registry(provider: str) -> None:
    declaration = _declaration(provider)
    registered = {channel.id for channel in PROVIDERS.get(provider).channels if not channel.local}
    mapped = (
        set(declaration.CHANNEL_ADAPTERS)
        | set(declaration.EXPLICIT_CHANNELS)
        | set(declaration.DIRECT_CHANNELS)
    )
    assert mapped == registered

    for channel, (module_name, class_name) in declaration.CHANNEL_ADAPTERS.items():
        module = importlib.import_module(module_name)
        assert isinstance(getattr(module, class_name), type), (provider, channel)


@pytest.mark.parametrize("provider", _WEB_PROVIDERS)
def test_every_registered_web_channel_resolves_without_network(provider: str) -> None:
    declaration = _declaration(provider)
    for channel in PROVIDERS.get(provider).channels:
        if channel.local or channel.id in declaration.EXPLICIT_CHANNELS:
            continue
        if channel.id in declaration.DIRECT_CHANNELS:
            continue
        assert resolve_channel_adapter(provider, channel.id) is not None


def test_tdx_online_channels_are_declared_and_vipdoc_stays_local_only() -> None:
    """tdx 的 Direct channel 表为空：本地 vipdoc 归属 ``local_vipdoc`` Provider。"""
    spec = PROVIDERS.get("tdx")
    online = {channel.id for channel in spec.channels if not channel.local}

    assert set(registry.tdx.DIRECT_CHANNELS) == online
    assert registry.tdx.CHANNEL_ADAPTERS == {}
    assert {channel.id for channel in spec.channels if channel.local} == set()
    assert PROVIDERS.get("local_vipdoc").channel("vipdoc").local is True


def test_declaration_validation_rejects_incomplete_channel_set(monkeypatch) -> None:
    """声明与注册表分叉必须当场红——不许靠运行时 KeyError 才发现。"""
    declaration = registry.tencent
    monkeypatch.setattr(declaration, "CHANNEL_ADAPTERS", {})
    with pytest.raises(RuntimeError, match="Channel declaration mismatch"):
        registry._validate_channel_declarations()


def test_unknown_provider_channel_is_rejected_with_context() -> None:
    with pytest.raises(ValidationError) as caught:
        resolve_channel_adapter("jsl", "etf")
    context = caught.value.context
    assert context["provider"] == "jsl"
    assert context["channel"] == "etf"
    assert context["fallback"] is False
    assert context["provider_switch_allowed"] is False


def test_direct_api_object_layer_is_physically_removed() -> None:
    """v16 clean break：不再有绑定 v12 service 的 ``md.<provider>`` 对象层。"""
    assert not hasattr(registry, "build_provider_api")
    assert not hasattr(registry, "ProviderAPI")
    assert not hasattr(registry, "WebProviderAPI")
