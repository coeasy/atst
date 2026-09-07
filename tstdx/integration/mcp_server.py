# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""MCP (Model Context Protocol) stdio JSON-RPC server for tstdx.

Tier C item C3 — exposes 23 tstdx tools to any MCP client (Claude
Desktop, Cursor, VSCode Copilot, custom agents) over stdio, using
newline-delimited JSON-RPC 2.0.

Zero-dependency
---------------
The server implements the MCP protocol directly and does **not** require
the optional ``mcp`` package.  If a caller has the ``mcp`` package
installed they may compose their own wrapper, but the default path in
this file is self-contained.

.. warning:: Security & trust assumption (P8)
   This server has **no authentication/authorization**.  It is designed to
   run over **stdio** for a single local MCP client — the trust boundary is
   "whoever can spawn the process / attach to its stdio".  Any tool call
   received is executed against the local tstdx data sources.  Do **not**
   expose this process over an untrusted network or an unauthenticated
   socket without adding a guard (token / ACL) in front of it.

Protocol essentials (over stdio, newline-delimited JSON-RPC 2.0)
----------------------------------------------------------------
* Client → Server: ``initialize`` → respond with ``capabilities`` +
  ``serverInfo {name: "tstdx", version}``.
* ``notifications/initialized`` notification → no response.
* ``tools/list`` → ``{"tools": [{name, description, inputSchema}, ...]}``.
* ``tools/call`` with ``params {name, arguments}`` →
  ``{"content": [{"type": "text", "text": "..."}]}``.
* ``ping`` → ``{}`` (empty result).
* Unknown method → JSON-RPC error ``-32601``.
* Notifications (no ``id``) MUST NOT get a response.

Usage
-----
::

    from tstdx.integration.mcp_server import create_mcp_server

    server = create_mcp_server()
    server.serve()

Or run directly::

    python -m tstdx.integration.mcp_server

Layout note (v11 P11-1)
-----------------------
This module is now a compatibility facade re-exporting the implementation
from the :mod:`tstdx.integration.mcp` subpackage (``_common`` constants /
``_tools_impl`` handlers / ``_tools_spec`` manifest / ``_server``
JSON-RPC stdio framework).  All names previously defined here — public
and private — are re-exported unchanged, so both
``tstdx.integration.mcp_server`` and ``tstdx.integration.mcp`` expose the
same API.
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
from .mcp._server import (
    MCPServer,
    _cap_rows,
    _serialize_to_text,
    create_mcp_server,
    logger,
)
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

# --------------------------------------------------------------------------- #
# Module entry point
# --------------------------------------------------------------------------- #
if __name__ == "__main__":  # pragma: no cover - direct execution
    create_mcp_server().serve()
