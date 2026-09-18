from __future__ import annotations

from dataclasses import fields

import pytest

import tstdx
from tstdx.client_api import AsyncClient, Client
from tstdx.errors import ValidationError
from tstdx.integration.mcp._tools_spec import TOOLS
from tstdx.providers import PROVIDERS
from tstdx.query import QuerySpec
from tstdx.runtime.executor import DIRECT_BINDINGS, audit_direct_bindings
from tstdx.stream_contract import StreamPlanner, StreamSpec

TIER_A = {
    "quotes",
    "bars",
    "snapshot",
    "minute",
    "trades",
    "security_count",
    "security_list",
}


def test_v13_has_one_supported_business_client_surface() -> None:
    assert tstdx.Client is Client
    assert tstdx.AsyncClient is AsyncClient
    assert "UnifiedQuoteAPI" not in tstdx.__all__
    assert "TdxClient" not in tstdx.__all__
    assert "WebQuoteClient" not in tstdx.__all__


def test_query_ssot_contains_no_legacy_route_source_or_partial_fields() -> None:
    names = {item.name for item in fields(QuerySpec)}
    assert "source" not in names
    assert "route" not in names
    assert "allow_partial" not in names


def test_registry_and_direct_bindings_are_exactly_equal() -> None:
    audit_direct_bindings()
    registered = {
        (provider, channel.id, capability)
        for provider in PROVIDERS.ids()
        for channel in PROVIDERS.get(provider).channels
        for capability in channel.capabilities
    }
    assert registered == {binding.key for binding in DIRECT_BINDINGS}


def test_tdx_tier_a_is_complete_in_registry() -> None:
    quotation = PROVIDERS.get("tdx").channel("quotation")
    # Tier A 必须**完整**包含在注册表中。canonical quotation channel 另外声明：
    # - finance / capital_changes / corporate_action：TDX 行情协议命令
    #   （finance_info / capital_changes），见 TdxQuotationAPI.finance。
    # - auction / block_quotes / minute_history / quotes_concurrent /
    #   security_list_all / volume_price：v13 迁移能力，其唯一执行通道即
    #   canonical quotation（catalog 已按注册表对齐，见 catalog.capability）。
    assert quotation.capabilities >= TIER_A
    assert quotation.capabilities == TIER_A | {
        "finance",
        "capital_changes",
        "corporate_action",
        "auction",
        "block_quotes",
        "minute_history",
        "quotes_concurrent",
        "security_list_all",
        "volume_price",
    }


def test_web_pseudo_provider_is_rejected() -> None:
    with pytest.raises(ValidationError):
        PROVIDERS.get("web")
    with pytest.raises(ValidationError):
        QuerySpec.build("quotes", symbols="sh600519", provider="web").normalized()


def test_streaming_is_explicit_and_fails_closed_for_unbound_provider() -> None:
    plan = StreamPlanner().compile(StreamSpec.build("sh600519", provider="tdx"))
    assert plan.provider == "tdx"
    assert plan.channel == "quotation"
    with pytest.raises(ValidationError):
        StreamPlanner().compile(StreamSpec.build("sh600519", provider="tencent"))


def test_mcp_only_exposes_promoted_canonical_capabilities() -> None:
    names = {tool.name for tool in TOOLS}
    # 规范 manifest = Tier-A dedicated tools + 统一 query_capability 网关
    # （见 integration/mcp/_tools_spec.py 模块 docstring：每个 migrated capability
    # 都经由 query_capability 复用同一条 Client/QuerySpec 执行链）。
    assert names == {
        "get_bars",
        "get_quote",
        "get_quotes",
        "get_snapshot",
        "get_minute_today",
        "get_trades",
        "get_security_count",
        "get_security_list",
        "query_capability",
    }
