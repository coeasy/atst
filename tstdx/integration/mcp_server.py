# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""MCP (Model Context Protocol) stdio JSON-RPC server for tstdx.

This module remains the compatibility facade for the historical
``tstdx.integration.mcp_server`` import path. The official server implementation
is now :mod:`tstdx.integration.mcp_app`, which preserves the established tool
surface while returning the canonical :class:`tstdx.error_envelope.ErrorEnvelope`
inside JSON-RPC ``error.data``.
"""

from __future__ import annotations

from ..client import TdxClient
from ..errors import TdxError
from .mcp._common import (
    ERR_INTERNAL,
    ERR_INVALID_PARAMS,
    ERR_INVALID_REQUEST,
    ERR_METHOD_NOT_FOUND,
    ERR_PARSE,
    MAX_BARS_COUNT,
    MAX_HOT_RANK_SIZE,
    MAX_PAGE,
    MAX_ROWS,
    MAX_STOCK_CHANGES_SIZE,
    MAX_TEXT_CHARS,
    PROTOCOL_VERSION,
    SERVER_NAME,
    SERVER_VERSION,
    ToolSpec,
    _int_prop,
    _list_of_strings,
    _str_prop,
    clamp_int,
)
from .mcp._server import _cap_rows, _serialize_to_text, logger
from .mcp._tools_impl import (
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
from .mcp._tools_spec import _TOOLS_BY_NAME, TOOLS
from .mcp_app import MCPServer, create_mcp_server

__all__ = [
    "MCPServer",
    "create_mcp_server",
    "SERVER_NAME",
    "SERVER_VERSION",
    "PROTOCOL_VERSION",
    "TOOLS",
    "ToolSpec",
    "clamp_int",
]

if __name__ == "__main__":  # pragma: no cover - direct execution
    create_mcp_server().serve()
