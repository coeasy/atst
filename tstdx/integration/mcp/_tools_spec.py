# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""Tool manifest (schema definitions) for the tstdx MCP server — pure data.

One :class:`~tstdx.integration.mcp._common.ToolSpec` per exposed tool:
name / description / JSON Schema ``inputSchema`` / handler binding.  The
canonical ordering below is what ``tools/list`` returns; it is part of the
MCP client-visible contract and must not change.
"""

from __future__ import annotations

from ._common import (
    MAX_BARS_COUNT,
    MAX_HOT_RANK_SIZE,
    MAX_PAGE,
    MAX_STOCK_CHANGES_SIZE,
    ToolSpec,
    _int_prop,
    _list_of_strings,
    _str_prop,
)
from ._tools_impl import (
    _h_get_adjusted_bars,
    _h_get_all_market,
    _h_get_bars,
    _h_get_board_members,
    _h_get_board_quotes,
    _h_get_capital_changes,
    _h_get_ex_bars,
    _h_get_f10_catalog,
    _h_get_finance_info,
    _h_get_goods_bars,
    _h_get_hot_rank,
    _h_get_index_list,
    _h_get_minute_klines,
    _h_get_minute_today,
    _h_get_quote,
    _h_get_quotes,
    _h_get_security_count,
    _h_get_security_list,
    _h_get_stock_changes,
    _h_get_trades,
    _h_list_servers,
    _h_search_symbols,
    _h_server_speedtest,
)

__all__ = ["TOOLS"]

#: Canonical list of all tools.  Order is stable and exposed via ``tools/list``.
TOOLS: list[ToolSpec] = [
    ToolSpec(
        name="get_bars",
        description=(
            "Fetch K-line / candlestick bars for a symbol. "
            "Returns a list of OHLCV dicts ordered oldest → newest."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "symbol": _str_prop("Security symbol, e.g. 'sh600519' or '600519'."),
                "period": _str_prop(
                    "Bar period: 'day' | 'week' | 'month' | '5min' | '15min' "
                    "| '30min' | '60min' | '1min'. Defaults to 'day'."
                ),
                "count": _int_prop(
                    "Number of bars to fetch.", default=320, minimum=1, maximum=MAX_BARS_COUNT
                ),
            },
            "required": ["symbol"],
        },
        handler=_h_get_bars,
    ),
    ToolSpec(
        name="get_quote",
        description="Fetch the realtime quote snapshot for a single symbol.",
        inputSchema={
            "type": "object",
            "properties": {
                "symbol": _str_prop("Security symbol, e.g. 'sh600519'."),
            },
            "required": ["symbol"],
        },
        handler=_h_get_quote,
    ),
    ToolSpec(
        name="get_quotes",
        description=(
            "Fetch realtime quotes for a list of symbols (batched, "
            "per-symbol fallback on protocol limits)."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "symbols": _list_of_strings(
                    "List of security symbols, e.g. ['sh600519', 'sz000001']."
                ),
            },
            "required": ["symbols"],
        },
        handler=_h_get_quotes,
    ),
    ToolSpec(
        name="get_minute_today",
        description="Fetch today's minute-by-minute (1-min) bars for a symbol.",
        inputSchema={
            "type": "object",
            "properties": {
                "symbol": _str_prop("Security symbol."),
            },
            "required": ["symbol"],
        },
        handler=_h_get_minute_today,
    ),
    ToolSpec(
        name="get_trades",
        description="Fetch today's tick-by-tick trades for a symbol.",
        inputSchema={
            "type": "object",
            "properties": {
                "symbol": _str_prop("Security symbol."),
            },
            "required": ["symbol"],
        },
        handler=_h_get_trades,
    ),
    ToolSpec(
        name="get_finance_info",
        description="Fetch financial fundamentals (shares, market cap, etc.).",
        inputSchema={
            "type": "object",
            "properties": {
                "symbol": _str_prop("Security symbol."),
            },
            "required": ["symbol"],
        },
        handler=_h_get_finance_info,
    ),
    ToolSpec(
        name="get_security_count",
        description="Fetch the total number of securities in a market.",
        inputSchema={
            "type": "object",
            "properties": {
                "market": _str_prop("Market id: 0 | 1 | 'sh' | 'sz' | 'bj' (int or prefix)."),
            },
            "required": ["market"],
        },
        handler=_h_get_security_count,
    ),
    ToolSpec(
        name="get_capital_changes",
        description=(
            "Fetch capital structure changes / ex-dividend history "
            "(除权除息 / 股本变迁) for a symbol."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "symbol": _str_prop("Security symbol."),
            },
            "required": ["symbol"],
        },
        handler=_h_get_capital_changes,
    ),
    ToolSpec(
        name="get_f10_catalog",
        description=(
            "Fetch the F10 section catalog (栏目目录) for a symbol — the list "
            "of F10 report sections (公司概况 / 财务分析 / ...) with their "
            "downloadable filenames. Requires an F10-capable connection "
            "(F10Client or an injected client exposing 'catalog')."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "symbol": _str_prop("Security symbol, e.g. 'sh600519'."),
            },
            "required": ["symbol"],
        },
        handler=_h_get_f10_catalog,
    ),
    ToolSpec(
        name="list_servers",
        description=(
            "List the built-in TDX host candidate pool (no network I/O). "
            "Use server_speedtest to measure actual reachability."
        ),
        inputSchema={"type": "object", "properties": {}},
        handler=_h_list_servers,
    ),
    ToolSpec(
        name="server_speedtest",
        description=(
            "Run a concurrent speedtest against all built-in TDX hosts "
            "and return per-host connect / rtt timings."
        ),
        inputSchema={"type": "object", "properties": {}},
        handler=_h_server_speedtest,
    ),
    ToolSpec(
        name="get_stock_changes",
        description=(
            "Fetch the intraday stock changes pool (Eastmoney push2ex, "
            "16 change categories: rocket-launch, big-buy, 60d-high, ...). "
            "Live during trading hours; empty list off-hours is normal."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "types": _list_of_strings(
                    "Change type codes as strings (e.g. ['8201','8193']); "
                    "empty = all 16 categories."
                ),
                "page": _int_prop("1-based page number.", default=1, minimum=1, maximum=MAX_PAGE),
                "size": _int_prop(
                    "Rows per page (max 500).",
                    default=50,
                    minimum=1,
                    maximum=MAX_STOCK_CHANGES_SIZE,
                ),
            },
        },
        handler=_h_get_stock_changes,
    ),
    ToolSpec(
        name="get_hot_rank",
        description=(
            "Fetch the Guba stock popularity ranking (Eastmoney emappdata). "
            "Returns rank/symbol/rank-change; fetch quotes separately for prices."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "page": _int_prop("1-based page number.", default=1, minimum=1, maximum=MAX_PAGE),
                "size": _int_prop(
                    "Rows per page (max 100).",
                    default=100,
                    minimum=1,
                    maximum=MAX_HOT_RANK_SIZE,
                ),
            },
        },
        handler=_h_get_hot_rank,
    ),
    # -- N3 工具面扩展（13 → 23；跨源 / web 独有能力走门面） ---------------------- #
    ToolSpec(
        name="get_adjusted_bars",
        description=(
            "Fetch price-adjusted K-line bars (前/后复权) for a symbol. "
            "Combines raw K-lines (local vipdoc day bars) with ex-dividend "
            "events via the adjust engine."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "symbol": _str_prop("Security symbol, e.g. 'sh600519'."),
                "method": _str_prop(
                    "Adjust method: 'qfq' (前复权, default) | 'hfq' (后复权) "
                    "| 'fixed' (定点, needs anchor_date via API) | 'none'."
                ),
                "period": _str_prop("Bar period, default 'day'."),
                "count": _int_prop(
                    "Number of bars to fetch.", default=320, minimum=1, maximum=MAX_BARS_COUNT
                ),
            },
            "required": ["symbol"],
        },
        handler=_h_get_adjusted_bars,
        use_facade=True,
    ),
    ToolSpec(
        name="get_all_market",
        description=(
            "Fetch a full-market quote summary (全市场行情) via web sources "
            "(sina default / tencent), paged by node."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "node": _str_prop(
                    "Market node: 'hs_a' (沪深A股, default) | 'cyb' (创业板) | "
                    "'hk' | 'us' (tencent source)."
                ),
                "page_size": _int_prop("Rows per page.", default=80, minimum=1, maximum=500),
                "max_pages": _int_prop(
                    "Max pages to fetch (default: all).", minimum=1, maximum=MAX_PAGE
                ),
                "source": _str_prop("Web source: 'sina' (default) | 'tencent'."),
            },
        },
        handler=_h_get_all_market,
        use_facade=True,
    ),
    ToolSpec(
        name="get_minute_klines",
        description=(
            "Fetch minute-level K-line bars (分钟K线) via web sources "
            "(A-share Tencent mkline; HK/US Eastmoney push2his)."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "symbol": _str_prop("Security symbol."),
                "period": _str_prop(
                    "Minute period: '1min' | '5min' (default) | '15min' | '30min' | '60min'."
                ),
                "count": _int_prop(
                    "Number of minute bars.", default=240, minimum=1, maximum=MAX_BARS_COUNT
                ),
            },
            "required": ["symbol"],
        },
        handler=_h_get_minute_klines,
        use_facade=True,
    ),
    ToolSpec(
        name="get_board_quotes",
        description=(
            "Fetch sector/board quotes (板块行情, 0x07E5). "
            "block_type: 0 concept (概念) | 1 industry (行业) | 2 region (地区) | 3 index."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "block_type": _int_prop(
                    "Board type: 0 concept | 1 industry | 2 region | 3 index.",
                    default=0,
                    minimum=0,
                    maximum=3,
                ),
                "start": _int_prop("Pagination offset.", default=0, minimum=0, maximum=MAX_PAGE),
            },
        },
        handler=_h_get_board_quotes,
    ),
    ToolSpec(
        name="get_board_members",
        description=(
            "Fetch board member quotes (板块成分行情) by Sina node "
            "(e.g. 'new_blhy' industry nodes), paged."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "node": _str_prop("Sina board node id."),
                "page_size": _int_prop("Rows per page.", default=100, minimum=1, maximum=500),
                "max_pages": _int_prop(
                    "Max pages to fetch (default: all).", minimum=1, maximum=MAX_PAGE
                ),
            },
            "required": ["node"],
        },
        handler=_h_get_board_members,
        use_facade=True,
    ),
    ToolSpec(
        name="get_ex_bars",
        description=(
            "Fetch extended-market K-line bars (扩展市场 K 线, 7727 protocol): "
            "HK / US stocks, futures, forex."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "symbol": _str_prop("Extended-market symbol, e.g. 'hk00700'."),
                "period": _str_prop("Bar period, default 'day'."),
                "count": _int_prop(
                    "Number of bars to fetch.", default=320, minimum=1, maximum=MAX_BARS_COUNT
                ),
            },
            "required": ["symbol"],
        },
        handler=_h_get_ex_bars,
        use_facade=True,
    ),
    ToolSpec(
        name="get_goods_bars",
        description=("Fetch commodities / options K-line bars (商品/期权 K 线, 7727 protocol)."),
        inputSchema={
            "type": "object",
            "properties": {
                "symbol": _str_prop("Commodity/option symbol."),
                "period": _str_prop("Bar period, default 'day'."),
                "count": _int_prop(
                    "Number of bars to fetch.", default=320, minimum=1, maximum=MAX_BARS_COUNT
                ),
            },
            "required": ["symbol"],
        },
        handler=_h_get_goods_bars,
        use_facade=True,
    ),
    ToolSpec(
        name="get_index_list",
        description=(
            "Fetch the common index directory (常用指数目录) — static offline "
            "list of index names with market-prefixed codes."
        ),
        inputSchema={"type": "object", "properties": {}},
        handler=_h_get_index_list,
        use_facade=True,
    ),
    ToolSpec(
        name="get_security_list",
        description=(
            "Fetch the security code list (证券代码表, 0x044D) paged 1000/page for a market."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "market": _str_prop("Market id: 0 | 1 | 'sh' | 'sz' (int or prefix)."),
                "start": _int_prop("Pagination offset.", default=0, minimum=0, maximum=MAX_PAGE),
            },
        },
        handler=_h_get_security_list,
    ),
    ToolSpec(
        name="search_symbols",
        description=(
            "Unified security search (统一证券搜索) by name / pinyin / code → "
            "market-qualified candidates with display names."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "pattern": _str_prop("Search keyword: name, pinyin or code."),
                "limit": _int_prop("Max candidates.", default=10, minimum=1, maximum=100),
                "market": _str_prop("Optional market filter: 'sh' | 'sz' | 'bj' | 'hk' | 'us'."),
            },
            "required": ["pattern"],
        },
        handler=_h_search_symbols,
        use_facade=True,
    ),
]

_TOOLS_BY_NAME: dict[str, ToolSpec] = {t.name: t for t in TOOLS}
