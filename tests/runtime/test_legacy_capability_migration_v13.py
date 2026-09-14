# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

from __future__ import annotations

import pytest

import tstdx
from tstdx.capability_catalog import (
    MIGRATED_BINDINGS,
    MIGRATED_CAPABILITIES,
    default_provider_for,
)
from tstdx.client_api import Client
from tstdx.direct_provider import DIRECT_BINDINGS, audit_direct_bindings
from tstdx.errors import ValidationError
from tstdx.providers import PROVIDERS
from tstdx.query import QueryPlanner, QuerySpec


# Business abilities that existed on the retired UnifiedQuoteAPI surface.
# Route/query wrapper mechanics are intentionally excluded: Client.call and
# FallbackPolicy replace them instead of preserving the old router kernel.
LEGACY_CAPABILITY_CONTRACT = frozenset(
    {
        "quotes_concurrent",
        "minute_history",
        "finance",
        "capital_changes",
        "corporate_action",
        "adjusted_bars",
        "block_quotes",
        "auction",
        "volume_price",
        "f10",
        "f10_catalog",
        "security_list_all",
        "sync_daily",
        "all_market",
        "rates",
        "ex_market_list",
        "ex_instruments",
        "ex_bars",
        "ex_quotes",
        "goods_bars",
        "goods_quotes",
        "suggest",
        "search_symbols",
        "wencai",
        "index_list",
        "minute_web",
        "minute_klines",
        "history",
        "stock_base_info",
        "stock_all_performance",
        "stock_report_dates",
        "ipo_review",
        "fund_base_info",
        "fund_manager",
        "fund_holdings",
        "fund_period_change",
        "fund_asset_allocation",
        "fund_industry_distribution",
        "fund_public_dates",
        "futures_base_info",
        "futures_realtime",
        "futures_kline",
        "futures_trades",
        "bond_realtime",
        "bond_base_info",
        "bond_kline",
        "bond_history_bill",
        "bond_today_bill",
        "bond_trades",
        "bond_all_base_info",
        "options_list",
        "options_snapshot",
        "options_trends",
        "dividend_history",
        "stock_valuation",
        "holder_changes",
        "financial_abstract",
        "announcements",
        "balance_sheet",
        "income_sheet",
        "cash_flow",
        "fin_report",
        "executive_holds",
        "shareholder_changes",
        "org_profile",
        "org_profiles",
        "rating_forecast",
        "rating_consensus",
        "esg_rating",
        "esg_history",
        "esg_ratings_all",
        "chip_distribution",
        "chip_distributions",
        "industry_index",
        "concept_index",
        "macro_cpi",
        "macro_ppi",
        "macro_gdp",
        "convertible_bonds",
        "northbound_hold",
        "top_holders",
        "unlock_stocks",
        "earnings_preview",
        "fund_rank",
        "fund_snapshot",
        "fund_nav_history_mob",
        "fund_detail",
        "fund_rating",
        "fund_yield_curve",
        "fund_rank_trend",
        "fund_manager_list",
        "fund_manager_profile",
        "fund_manager_yield",
        "fund_manager_eval",
        "fund_manager_style",
        "fund_companies",
        "fund_company_archives",
        "fund_company_funds",
        "fund_company_scale",
        "fund_company_base_info",
        "fund_search",
        "news_financial",
        "research_reports",
        "research_visits",
        "free_holders",
        "holder_num",
        "fund_base_info_multi",
        "industry_boards",
        "board_list",
        "board_members",
        "board_rank",
        "em_boards",
        "em_board_members",
        "stock_boards",
        "ipo_calendar",
        "big_order_flow",
        "stock_changes",
        "hot_rank",
        "sector_flow",
        "globals",
        "market_stat",
        "index",
        "hk_quotes",
        "us_quotes",
        "klines",
        "ticks",
        "fund_nav_history",
        "fund_estimate",
        "fund_list",
        "index_constituents",
        "margin",
        "dc_reports",
        "dc_query",
    }
)


def test_every_legacy_business_ability_is_promoted() -> None:
    missing = sorted(LEGACY_CAPABILITY_CONTRACT - MIGRATED_CAPABILITIES)
    assert missing == []


def test_registry_and_direct_bindings_are_bijective_for_migrated_catalog() -> None:
    direct = {item.key for item in DIRECT_BINDINGS}
    migrated = {item.key for item in MIGRATED_BINDINGS}
    assert migrated <= direct
    for item in MIGRATED_BINDINGS:
        PROVIDERS.require(item.provider, item.capability, channel=item.channel)
    audit_direct_bindings()


def test_every_migrated_capability_has_a_deterministic_default_binding() -> None:
    direct = {item.key for item in DIRECT_BINDINGS}
    for capability in MIGRATED_CAPABILITIES:
        provider = default_provider_for(capability)
        candidates = [
            item
            for item in MIGRATED_BINDINGS
            if item.capability == capability and item.provider == provider
        ]
        assert len(candidates) == 1
        assert candidates[0].key in direct


def test_migrated_signature_validation_happens_during_planning() -> None:
    planner = QueryPlanner()
    provider = default_provider_for("balance_sheet")
    with pytest.raises(ValidationError):
        planner.compile(QuerySpec.build("balance_sheet", provider=provider))

    plan = planner.compile(
        QuerySpec.build(
            "balance_sheet",
            provider=provider,
            options={"args": ["sh600519"], "kwargs": {}},
        )
    )
    assert plan.spec.capability == "balance_sheet"


def test_fingerprint_hashes_secrets_instead_of_storing_plaintext() -> None:
    planner = QueryPlanner()
    provider = default_provider_for("wencai")
    plan = planner.compile(
        QuerySpec.build(
            "wencai",
            provider=provider,
            options={
                "args": ["高股息"],
                "kwargs": {"cookie": "super-secret-cookie"},
            },
        )
    )
    assert "super-secret-cookie" not in plan.fingerprint.canonical
    assert "__secret_sha256__" in plan.fingerprint.canonical


def test_composite_capabilities_are_honestly_derived() -> None:
    assert default_provider_for("adjusted_bars") == "derived"
    assert default_provider_for("sync_daily") == "derived"


def test_retired_facade_is_not_reintroduced() -> None:
    assert not hasattr(tstdx, "UnifiedQuoteAPI")
    assert not hasattr(tstdx, "AsyncUnifiedQuoteAPI")
    assert hasattr(Client, "call")


def test_representative_domains_are_available_on_new_client() -> None:
    required = {
        "finance",
        "f10",
        "fund_rank",
        "futures_kline",
        "bond_kline",
        "options_snapshot",
        "balance_sheet",
        "macro_cpi",
        "northbound_hold",
        "news_financial",
        "research_reports",
        "board_rank",
        "wencai",
        "rates",
        "ex_bars",
        "goods_bars",
        "adjusted_bars",
        "sync_daily",
    }
    assert required <= MIGRATED_CAPABILITIES
