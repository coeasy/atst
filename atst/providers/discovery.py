# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""离线能力发现：按名字 / 中文别名 / Provider / 状态检索能力。

v13 裁决把 ``Client.capabilities()`` / ``GET /v13/capabilities`` / WS
``runtime.capabilities`` 三处发现面钉成**纯名字**（形状上没有 ``available``
字段）。本模块是**独立**的检索与描述辅助：``search_capabilities`` 只回名字，
``describe_capability`` 才给出 Provider / Channel / 状态这些健康度事实，因此
不会改变那三处出口的形状。
"""

from __future__ import annotations

from types import MappingProxyType
from typing import Any

from ..errors import ValidationError
from . import (
    CAPABILITY_STATUSES,
    MIGRATED_CAPABILITIES,
    PROVIDERS,
    capability_status,
    default_provider_for,
)

__all__ = [
    "CAPABILITY_KEYWORDS",
    "capability_catalog",
    "describe_capability",
    "search_capabilities",
]

#: 常见能力的中文别名。发现面本身只有英文能力名；没有这张表，中文用户
#: 搜"龙虎榜""资金流""财报"会得到零结果（对标 akshare 的 ``ak.search``）。
#: 值是空格分隔的关键词串，``search_capabilities`` 对整串做大小写不敏感的子串匹配。
CAPABILITY_KEYWORDS: MappingProxyType[str, str] = MappingProxyType(
    {
        "bars": "K线 日线 历史行情 蜡烛图 行情",
        "history": "K线 日线 历史行情 蜡烛图",
        "klines": "K线 日线 历史行情 蜡烛图",
        "minute": "分时 分钟 分时图 日内",
        "minute_klines": "分钟K线 分钟线 分钟K",
        "minute_history": "分钟K线 分钟线 历史分时",
        "intraday": "分时 日内 盘中",
        "quotes": "行情 实时行情 报价 快照 盘口",
        "snapshot": "盘口 快照 行情",
        "quotes_concurrent": "批量行情 并发行情",
        "all_market": "全市场 全市场行情 市场快照",
        "trades": "逐笔 成交明细 分时成交 明细",
        "ticks": "逐笔 分笔 高频 成交明细",
        "auction": "竞价 集合竞价",
        "fund_flow": "资金流 资金流向 主力资金",
        "fund_flow_history": "历史资金流 资金流",
        "big_order_flow": "大单 大单资金流",
        "sector_flow": "板块资金流 行业资金流",
        "sina_fund_flow": "新浪资金流",
        "sina_board_fund_flow": "新浪板块资金流",
        "longhu": "龙虎榜 席位 上榜",
        "org_seat_trades": "机构席位 机构买卖",
        "northbound": "北向 北向资金 外资 沪深股通",
        "northbound_hold": "北向持仓 外资持股",
        "southbound_ranks": "南向 港股通 南向资金",
        "margin": "两融 融资融券",
        "margin_daily": "两融 融资融券 每日两融",
        "block_trades": "大宗交易",
        "block_trade_stats": "大宗交易统计",
        "dividend_history": "分红 送配 除权除息",
        "corporate_action": "公司行为 除权除息",
        "capital_changes": "股本变动 分红送配",
        "rights_issue": "配股 增发",
        "convertible_bond": "可转债 转债",
        "convertible_bonds": "可转债 转债 列表",
        "bond_kline": "债券K线 债券行情",
        "bond_realtime": "债券行情 债券实时",
        "bond_base_info": "债券基本信息",
        "options_list": "期权 期权列表",
        "options_snapshot": "期权 期权行情",
        "options_trends": "期权 期权走势",
        "options_position_rank": "期权持仓 期权排名",
        "futures_kline": "期货K线 期货行情",
        "futures_realtime": "期货行情 期货实时",
        "futures_position_rank": "期货持仓 期货排名",
        "fund_nav_history": "基金净值 净值",
        "fund_estimate": "基金估值 盘中估值",
        "fund_rank": "基金排行 基金排名",
        "fund_holdings": "基金持仓 重仓",
        "fund_base_info": "基金基本信息",
        "fund_list": "基金列表",
        "fund_companies": "基金公司",
        "etf_shares": "ETF份额 ETF",
        "balance_sheet": "财报 资产负债表",
        "income_sheet": "财报 利润表 损益表",
        "cash_flow": "财报 现金流量表",
        "financial_abstract": "财务摘要 财报摘要",
        "finance": "财务 财报",
        "fin_report": "财报 财务报表",
        "shareholders": "股东 十大股东",
        "top_holders": "十大股东",
        "free_holders": "流通股东 十大流通股东",
        "holder_num": "股东户数",
        "holder_changes": "股东变动 股东增减持",
        "shareholder_changes": "股东变动 股东增减持",
        "equity_structure": "股本结构 股权结构",
        "equity_pledge": "股权质押",
        "goodwill": "商誉",
        "goodwill_market": "商誉 全市场商誉",
        "ipo_calendar": "新股 打新 新股日历 IPO",
        "ipo_review": "新股审核 IPO审核",
        "unlocks": "解禁 限售解禁",
        "unlock_stocks": "解禁 限售解禁",
        "chip_distribution": "筹码 筹码分布",
        "chip_distributions": "筹码 筹码分布",
        "concept_members": "概念 概念股 概念成分",
        "board_members": "板块成分 板块成员",
        "board_member": "板块成分 板块成员",
        "em_board_members": "东财板块成分",
        "industry_boards": "行业板块",
        "index_constituents": "指数成分 指数成分股",
        "limit_pool": "涨停 跌停 涨跌停",
        "limit_up_ladder": "连板 涨停梯队",
        "st_list": "ST 风险警示",
        "suspension": "停牌 复牌 停复牌",
        "news": "新闻 资讯 快讯",
        "news_financial": "财经新闻 资讯",
        "breaking_news": "快讯 突发新闻",
        "research_reports": "研报 研究报告",
        "reports": "研报 报告",
        "rating_consensus": "评级 一致预期",
        "rating_forecast": "评级 盈利预测",
        "earnings_preview": "业绩预告 业绩快报",
        "forecast": "业绩预告 预测",
        "wencai": "问财 选股 条件选股",
        "screening": "选股 筛选",
        "search_symbols": "代码表 证券搜索 搜索代码",
        "trade_calendar": "交易日历 日历",
        "trading_calendar": "交易日历 日历",
        "macro_gdp": "宏观 GDP 国内生产总值",
        "macro_cpi": "宏观 CPI 物价",
        "macro_ppi": "宏观 PPI 工业品价格",
        "macro_pmi": "宏观 PMI 采购经理",
        "macro_lpr": "宏观 LPR 利率",
        "macro_money_supply": "宏观 货币供应 M0 M1 M2",
        "macro_social_financing": "宏观 社融",
        "macro_bond_yield": "宏观 国债收益率",
        "hk_quotes": "港股 港股行情",
        "us_quotes": "美股 美股行情",
        "global_quotes": "全球行情 外盘",
        "fx_rates": "汇率 外汇",
        "esg_rating": "ESG 评级",
        "esg_ratings_all": "ESG 评级 全市场",
        "industry_index": "行业指数",
        "concept_index": "概念指数",
        "market_breadth": "市场宽度 涨跌家数",
        "hot_rank": "热度 人气榜",
        "hot_boards": "热门板块",
        "stock_changes": "盘中异动 异动",
        "risk_scan": "风险扫描 风险",
        "performance": "业绩 表现",
        "stock_valuation": "估值",
        "index_valuation": "指数估值",
        "valuation_history": "估值历史",
    }
)


def _names() -> tuple[str, ...]:
    """发现面交付的那份名字（与三处出口同源）。"""
    from ..client.api import Client

    return tuple(sorted(Client.capabilities()))


def _channels(capability: str, provider: str) -> list[dict[str, Any]]:
    spec = PROVIDERS.get(provider)
    out: list[dict[str, Any]] = []
    for channel in spec.channels_for(capability):
        out.append(
            {
                "provider": provider,
                "channel": channel.id,
                "operational": channel.operationally_supports(capability),
                "periods": sorted(channel.periods),
            }
        )
    return out


def _record(capability: str) -> dict[str, Any]:
    cap = capability.strip().lower()
    names = _names()
    if cap not in names:
        raise ValidationError(
            f"未知 capability {capability!r}",
            context={"capability": capability, "known_capabilities": len(names)},
        )
    migrated = cap in MIGRATED_CAPABILITIES
    declared = tuple(
        provider for provider in PROVIDERS.ids() if PROVIDERS.get(provider).supports(cap)
    )
    operational = PROVIDERS.available_providers(cap)
    if migrated:
        status = capability_status(cap)
        try:
            default_provider = default_provider_for(cap)
        except KeyError:  # pragma: no cover - 迁移能力必然有绑定
            default_provider = None
    else:
        status = "alive"
        default_provider = PROVIDERS.default_available_provider(cap)
    channels: list[dict[str, Any]] = []
    for provider in declared:
        channels.extend(_channels(cap, provider))
    return {
        "name": cap,
        "status": status,
        "core": not migrated,
        "migrated": migrated,
        "providers": list(operational),
        "declared_providers": list(declared),
        "channels": channels,
        "default_provider": default_provider,
        "keywords": CAPABILITY_KEYWORDS.get(cap, ""),
    }


def capability_catalog() -> tuple[dict[str, Any], ...]:
    """全部能力的描述记录；纯离线、确定性排序。"""
    return tuple(_record(name) for name in _names())


def describe_capability(capability: str) -> dict[str, Any]:
    """单能力描述：状态、Provider / Channel、默认源与中文别名。"""
    return _record(capability)


def search_capabilities(
    query: str = "",
    *,
    status: str | None = None,
    provider: str | None = None,
    limit: int = 50,
) -> tuple[dict[str, Any], ...]:
    """按名字 / 中文别名 / Provider 检索能力，返回描述记录。

    ``query`` 的多个空白分隔词之间是 **AND** 关系；空串返回全部（按名字排序）。
    ``status`` 只接受 ``alive`` / ``degraded`` / ``offline`` / ``needs_verify``；
    ``provider`` 必须是注册表里的 Provider id（``auto`` 不是 Provider，会被拒绝）。
    """
    if status is not None and status not in CAPABILITY_STATUSES:
        raise ValidationError(
            f"未知能力状态 {status!r}",
            context={"status": status, "allowed": list(CAPABILITY_STATUSES)},
        )
    pid = None
    if provider is not None:
        pid = PROVIDERS.get(provider).id
    tokens = tuple(token for token in str(query).strip().lower().split() if token)
    scored: list[tuple[int, str, dict[str, Any]]] = []
    for record in capability_catalog():
        if status is not None and record["status"] != status:
            continue
        if pid is not None and pid not in record["declared_providers"]:
            continue
        name = record["name"]
        haystack = " ".join(
            (
                name,
                record["keywords"],
                " ".join(record["declared_providers"]),
                " ".join(item["channel"] for item in record["channels"]),
            )
        )
        if tokens and not all(token in haystack for token in tokens):
            continue
        score = 0
        if tokens:
            joined = " ".join(tokens)
            score = (
                4
                if name == joined
                else 3
                if name.startswith(joined)
                else 2
                if joined in name
                else 1
            )
        scored.append((score, name, record))
    scored.sort(key=lambda item: (-item[0], item[1]))
    capped = max(1, int(limit))
    return tuple(item[2] for item in scored[:capped])
