from __future__ import annotations

import pytest

from tstdx.facade.api import UnifiedQuoteAPI
from tstdx.web.facade import WebQuoteSession

# These capabilities were added to main after PR #1 diverged. The v11 runtime
# migration must never overwrite them with the older long-lived branch surface.
_LATEST_MAIN_WEB_METHODS = (
    # efinance stock/fund/derivatives
    "stock_base_info",
    "stock_all_performance",
    "stock_report_dates",
    "ipo_review",
    "fund_base_info",
    "fund_base_info_multi",
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
    "bond_all_base_info",
    "bond_kline",
    "bond_history_bill",
    "bond_today_bill",
    "bond_trades",
    # astock/news/shareholder additions
    "dividend_history",
    "stock_valuation",
    "holder_changes",
    "financial_abstract",
    "announcements",
    "research_reports",
    "research_visits",
    "free_holders",
    "holder_num",
    # later Eastmoney option surface
    "options_list",
    "options_snapshot",
    "options_trends",
    # fund-v2 surface
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
    # fundamental/governance depth
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
)


@pytest.mark.parametrize("method_name", _LATEST_MAIN_WEB_METHODS)
def test_latest_main_web_surface_is_not_dropped(method_name: str) -> None:
    assert callable(getattr(WebQuoteSession, method_name, None)), method_name


@pytest.mark.parametrize(
    "method_name",
    (
        "stock_base_info",
        "fund_base_info",
        "futures_realtime",
        "bond_realtime",
        "dividend_history",
        "announcements",
        "research_reports",
        "options_list",
        "options_snapshot",
        "options_trends",
    ),
)
def test_latest_main_unified_surface_is_not_dropped(method_name: str) -> None:
    assert callable(getattr(UnifiedQuoteAPI, method_name, None)), method_name
