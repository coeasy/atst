from __future__ import annotations

from dataclasses import fields

import pytest

import tstdx
from tstdx.client.api import AsyncClient, Client
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
    with pytest.raises(ValidationError):
        StreamPlanner().compile(StreamSpec.build("sh600519", provider="tencent"))
    # canonical channel 是编译期事实，compile 对偏离它的一侧 fail-closed。
    with pytest.raises(ValidationError):
        StreamPlanner().compile(StreamSpec.build("sh600519", channel="quote"))


def test_stream_plan_carries_only_the_fields_the_client_reads() -> None:
    """``StreamPlan`` 的每个字段都必须由唯一消费方真正读取（F-55）。

    旧形状里 ``plan.capability`` 与 ``plan.channel`` 由 ``compile`` 写入，而 ``tstdx/``
    对它们的读取点是 0：唯一的"读取"是一条测试断言，而 compile 本身已经对两者
    fail-closed——一条写给自己看的记录。删掉之后判据钉住剩下的形状：plan 字段清单
    必须与 ``tstdx/client/api.py`` 里 ``plan.*`` 的读取集合逐字相等，新增字段没有接线
    或读取幻影字段都当场变红。
    """

    import ast
    from pathlib import Path

    from tstdx.stream_contract import StreamPlan

    root = Path(__file__).resolve().parents[2]
    source = (root / "tstdx" / "client" / "api.py").read_text(encoding="utf-8")
    reads = {
        node.attr
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "plan"
    }
    plan_fields = {item.name for item in fields(StreamPlan)}
    assert plan_fields, "StreamPlan 已经没有字段了，判据自身失效"
    assert reads, "api.py 里读不到任何 plan.*，判据自身失效"
    assert plan_fields == reads, f"plan 字段与消费方读取不一致：{sorted(plan_fields ^ reads)}"


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


def test_binding_audit_fails_closed_instead_of_warning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """可达三元组缺 catalog 元数据 ⇒ 一次失败（R-2）。

    原先是 ``warnings.warn``：pytest 会把告警收进报告却不因它失败，于是这条判据在
    CI 上等于不存在——同文件的注册表对账却是 raise，一软一硬。
    """
    import warnings

    from tstdx.runtime import executor as ex

    target = next(
        item
        for item in ex.DIRECT_BINDINGS
        if item.executor_name == "_migrated_capability" and ex._is_unified_reachable(*item.key)
    )
    real = ex.binding_for

    def fake_binding_for(provider: str, channel: str, capability: str) -> object:
        if (provider, channel, capability) == target.key:
            raise KeyError(target.key)
        return real(provider, channel, capability)

    monkeypatch.setattr(ex, "binding_for", fake_binding_for)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        with pytest.raises(RuntimeError, match="执行元数据"):
            ex.audit_direct_bindings()
    assert [str(item.message) for item in caught] == [], "审计退回告警即为本条判据失效"
