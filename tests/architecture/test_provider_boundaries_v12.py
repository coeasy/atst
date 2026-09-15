from __future__ import annotations

import ast
from pathlib import Path

import pytest

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
from tstdx.providers.http import PROVIDER_HTTP_HOST_SUFFIXES

ROOT = Path(__file__).resolve().parents[2]
PKG = ROOT / "tstdx"


WEB_APIS = {
    "tencent": TencentProviderAPI,
    "sina": SinaProviderAPI,
    "eastmoney": EastmoneyProviderAPI,
    "baidu": BaiduProviderAPI,
    "jsl": JslProviderAPI,
    "boc": BocProviderAPI,
    "iwencai": IwencaiProviderAPI,
}


def _python_files() -> list[Path]:
    return sorted(PKG.rglob("*.py"))


def test_production_code_does_not_bypass_provider_http_guard() -> None:
    """Only the guard implementation itself may expose ``raw_client``.

    Tests may inspect the underlying fake client, but production adapters/runtime
    must never call ``.raw_client`` because that would bypass Provider host
    validation.
    """
    offenders: list[str] = []
    allowed = PKG / "providers" / "http.py"
    for path in _python_files():
        if path == allowed:
            continue
        text = path.read_text(encoding="utf-8")
        if ".raw_client" in text:
            offenders.append(str(path.relative_to(ROOT)))
    assert offenders == []


def test_provider_ids_are_organizations_not_markets_or_channels() -> None:
    ids = set(PROVIDERS.ids())
    forbidden = {
        "hk",
        "us",
        "cn_a",
        "quote",
        "kline",
        "minute",
        "ticks",
        "vipdoc",
        "reader",
        "cache",
        "synthetic",
        "replay",
    }
    assert ids.isdisjoint(forbidden)
    assert PROVIDERS.default_provider == "tdx"


@pytest.mark.parametrize("provider,api_cls", WEB_APIS.items())
def test_every_web_provider_has_transport_host_policy(provider: str, api_cls: type) -> None:
    assert api_cls.provider_id == provider
    assert provider in PROVIDER_HTTP_HOST_SUFFIXES
    assert PROVIDER_HTTP_HOST_SUFFIXES[provider]


@pytest.mark.parametrize("provider,api_cls", WEB_APIS.items())
def test_direct_api_channels_are_registry_channels(provider: str, api_cls: type) -> None:
    registry = {channel.id for channel in PROVIDERS.get(provider).channels}
    mapped = set(api_cls.CHANNELS)
    if provider == "eastmoney":
        mapped.add("corporate")
    assert mapped == registry


def test_no_parallel_source_registry_domain_entity_exists() -> None:
    """The formal domain model has ProviderRegistry only.

    Compatibility names such as SourceUnavailable/DataSourceRouter are allowed;
    a new ``class SourceRegistry`` or ``class SourceSpec`` under the provider
    architecture would recreate the terminology split ADR-013 removed.
    """
    offenders: list[str] = []
    for path in _python_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(
                node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
            ) and node.name in {"SourceRegistry", "SourceManager"}:
                offenders.append(f"{path.relative_to(ROOT)}:{node.lineno}:{node.name}")
    assert offenders == []


def test_provider_manager_is_the_only_new_runtime_provider_owner() -> None:
    from tstdx.service import ProviderManager

    assert ProviderManager.__name__ == "ProviderManager"
