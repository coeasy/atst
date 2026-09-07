# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""Tool handler implementations for the tstdx MCP server.

Pure functions of ``(client, args)`` — or ``(facade, args)`` for
``use_facade=True`` tools — one per entry in the tool manifest
(:data:`tstdx.integration.mcp._tools_spec.TOOLS`).  Heavyweight imports
(domain models, web session, transport speedtest, ...) are performed
lazily inside the handlers so that importing this module and listing
tools stays network-free.
"""

from __future__ import annotations

from typing import Any

from ...client import TdxClient
from ...errors import TdxError
from ._common import (
    MAX_BARS_COUNT,
    MAX_HOT_RANK_SIZE,
    MAX_PAGE,
    MAX_STOCK_CHANGES_SIZE,
    clamp_int,
)

__all__ = [
    "_h_get_bars",
    "_h_get_quote",
    "_h_get_quotes",
    "_h_get_minute_today",
    "_h_get_trades",
    "_h_get_finance_info",
    "_h_get_security_count",
    "_h_get_capital_changes",
    "_h_get_f10_catalog",
    "_h_list_servers",
    "_h_server_speedtest",
    "_h_get_stock_changes",
    "_h_get_hot_rank",
    "_h_get_adjusted_bars",
    "_h_get_all_market",
    "_h_get_minute_klines",
    "_h_get_board_quotes",
    "_h_get_board_members",
    "_h_get_ex_bars",
    "_h_get_goods_bars",
    "_h_get_index_list",
    "_h_get_security_list",
    "_h_search_symbols",
]


# --------------------------------------------------------------------------- #
# Tool handlers — pure functions of (client, args)
# --------------------------------------------------------------------------- #
def _h_get_bars(client: TdxClient, args: dict[str, Any]) -> list[dict[str, Any]]:
    period = args.get("period", "day")
    count = clamp_int(args.get("count", 320), 320, 1, MAX_BARS_COUNT)
    return client.bars(args["symbol"], period=period, count=count, as_format="dict")


def _h_get_quote(client: TdxClient, args: dict[str, Any]) -> dict[str, Any]:
    rows = client.quotes([args["symbol"]], as_format="dict")
    return rows[0] if rows else {}


def _h_get_quotes(client: TdxClient, args: dict[str, Any]) -> list[dict[str, Any]]:
    return client.quotes(args["symbols"], as_format="dict")


def _h_get_minute_today(client: TdxClient, args: dict[str, Any]) -> list[Any]:
    return client.minute_today(args["symbol"])


def _h_get_trades(client: TdxClient, args: dict[str, Any]) -> list[dict[str, Any]]:
    return client.trade_today(args["symbol"])


def _h_get_finance_info(client: TdxClient, args: dict[str, Any]) -> dict[str, Any]:
    return client.finance_info(args["symbol"])


def _h_get_security_count(client: TdxClient, args: dict[str, Any]) -> dict[str, Any]:
    count = client.security_count(args["market"])
    return {"market": args["market"], "count": count}


def _h_get_capital_changes(client: TdxClient, args: dict[str, Any]) -> list[dict[str, Any]]:
    from ...domain.models import to_dicts

    return to_dicts(client.capital_changes(args["symbol"]))


def _h_get_f10_catalog(client: TdxClient, args: dict[str, Any]) -> list[dict[str, Any]]:
    """F10 栏目目录（0x0001；F10 族 7615 文件型）。

    注入的客户端具备 ``catalog`` 时直接复用（fake/测试或 F10Client）；
    否则惰性创建 :class:`~tstdx.client.F10Client` 并在调用后释放。
    """
    fn = getattr(client, "catalog", None)
    if callable(fn):
        return fn(args["symbol"])
    from ...client import get_client

    f10 = get_client("f10")
    try:
        return f10.catalog(args["symbol"])
    finally:
        f10.close()


def _h_list_servers(_client: TdxClient, _args: dict[str, Any]) -> list[dict[str, Any]]:
    from ...transport.hosts import DEFAULT_HOST_POOL

    return [e.to_dict() for e in DEFAULT_HOST_POOL]


def _h_server_speedtest(_client: TdxClient, _args: dict[str, Any]) -> list[dict[str, Any]]:
    from ...transport.speedtest import speedtest

    return [r.to_dict() for r in speedtest(progress=False)]


def _h_get_stock_changes(_client: TdxClient, args: dict[str, Any]) -> list[dict[str, Any]]:
    """盘中异动池（Web 源，不需要 TDX 主站连接）。"""
    from ...web.facade import WebQuoteSession

    types = tuple(int(t) for t in args.get("types") or () if str(t).strip())
    page = clamp_int(args.get("page", 1), 1, 1, MAX_PAGE)
    size = clamp_int(args.get("size", 50), 50, 1, MAX_STOCK_CHANGES_SIZE)
    return WebQuoteSession.stock_changes(types, page=page, size=size)


def _h_get_hot_rank(_client: TdxClient, args: dict[str, Any]) -> list[dict[str, Any]]:
    """股吧人气榜（Web 源，不需要 TDX 主站连接）。"""
    from ...web.facade import WebQuoteSession

    page = clamp_int(args.get("page", 1), 1, 1, MAX_PAGE)
    size = clamp_int(args.get("size", 100), 100, 1, MAX_HOT_RANK_SIZE)
    return WebQuoteSession.hot_rank(page=page, size=size)


# -- N3 门面工具（跨源 / web 独有能力统一走 UnifiedQuoteAPI） ------------------- #
def _h_get_adjusted_bars(_facade: Any, args: dict[str, Any]) -> list[dict[str, Any]]:
    """复权 K 线（门面：原始 K 线 + 除权除息事件 → 前/后/定点复权）。"""
    from ...domain.models import to_dicts

    method = args.get("method", "qfq")
    if method not in ("qfq", "hfq", "fixed", "none"):
        raise TdxError(f"复权方法必须是 qfq|hfq|fixed|none，收到 {method!r}")
    period = args.get("period", "day")
    count = clamp_int(args.get("count", 320), 320, 1, MAX_BARS_COUNT)
    return to_dicts(
        _facade.adjusted_bars(args["symbol"], method=method, period=period, count=count)
    )


def _h_get_all_market(_facade: Any, args: dict[str, Any]) -> list[dict[str, Any]]:
    """全市场行情摘要（门面 web 路由：sina/tencent）。"""
    from ...domain.models import to_dicts

    source = args.get("source", "sina")
    if source not in ("sina", "tencent"):
        raise TdxError(f"全市场 source 必须是 sina|tencent，收到 {source!r}")
    page_size = clamp_int(args.get("page_size", 80), 80, 1, 500)
    max_pages = args.get("max_pages")
    if max_pages is not None:
        max_pages = clamp_int(max_pages, 1, 1, MAX_PAGE)
    return to_dicts(
        _facade.all_market(
            node=args.get("node", "hs_a"),
            page_size=page_size,
            max_pages=max_pages,
            source=source,
        )
    )


def _h_get_minute_klines(_facade: Any, args: dict[str, Any]) -> list[dict[str, Any]]:
    """分钟 K 线（门面 web 路由：A 股腾讯 mkline；港美东财 push2his）。"""
    from ...domain.models import to_dicts

    period = args.get("period", "5min")
    count = clamp_int(args.get("count", 240), 240, 1, MAX_BARS_COUNT)
    return to_dicts(_facade.minute_klines(args["symbol"], period=period, count=count))


def _h_get_board_quotes(client: TdxClient, args: dict[str, Any]) -> list[dict[str, Any]]:
    """板块行情（0x07E5；block_type: 0 概念 / 1 行业 / 2 地区 / 3 指数）。"""
    block_type = clamp_int(args.get("block_type", 0), 0, 0, 3)
    start = clamp_int(args.get("start", 0), 0, 0, MAX_PAGE)
    return list(client.block_quotes(block_type=block_type, start=start))


def _h_get_board_members(_facade: Any, args: dict[str, Any]) -> list[dict[str, Any]]:
    """板块成分行情（新浪 node 分页）。"""
    from ...domain.models import to_dicts

    page_size = clamp_int(args.get("page_size", 100), 100, 1, 500)
    max_pages = args.get("max_pages")
    if max_pages is not None:
        max_pages = clamp_int(max_pages, 1, 1, MAX_PAGE)
    return to_dicts(_facade.board_members(args["node"], page_size=page_size, max_pages=max_pages))


def _h_get_ex_bars(_facade: Any, args: dict[str, Any]) -> list[dict[str, Any]]:
    """扩展市场 K 线（7727：港股 / 美股 / 期货 / 外汇）。"""
    period = args.get("period", "day")
    count = clamp_int(args.get("count", 320), 320, 1, MAX_BARS_COUNT)
    return list(_facade.ex_bars(args["symbol"], period=period, count=count))


def _h_get_goods_bars(_facade: Any, args: dict[str, Any]) -> list[dict[str, Any]]:
    """商品 / 期权 K 线（7727）。"""
    period = args.get("period", "day")
    count = clamp_int(args.get("count", 320), 320, 1, MAX_BARS_COUNT)
    return list(_facade.goods_bars(args["symbol"], period=period, count=count))


def _h_get_index_list(_facade: Any, _args: dict[str, Any]) -> list[dict[str, str]]:
    """常用指数目录（离线静态：名称 + 带市场代码）。"""
    return list(_facade.index_list())


def _h_get_security_list(client: TdxClient, args: dict[str, Any]) -> list[dict[str, Any]]:
    """证券代码表（0x044D 分页，1000/页）。market 可传 "sh"/"sz" 或编号。"""
    start = clamp_int(args.get("start", 0), 0, 0, MAX_PAGE)
    return list(client.security_list(args.get("market", 0), start=start))


def _h_search_symbols(_facade: Any, args: dict[str, Any]) -> list[dict[str, str]]:
    """统一证券搜索（名称/拼音/代码 → 市场归属明确的候选）。"""
    limit = clamp_int(args.get("limit", 10), 10, 1, 100)
    return list(_facade.search_symbols(args["pattern"], limit=limit, market=args.get("market")))
