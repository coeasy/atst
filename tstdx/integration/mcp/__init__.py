# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""Canonical v13 MCP stdio JSON-RPC adapter.

The MCP package is transport-only: all market-data tools delegate to the same
Client runtime used by Python/CLI/HTTP/WS. The manifest exposes only capabilities
promoted into the v13 Provider Registry and DirectBinding contract.

Current tool surface:

- get_bars
- get_quote / get_quotes
- get_snapshot
- get_minute_today
- get_trades
- get_security_count / get_security_list

No legacy facade/TdxClient dual target, `use_facade`, route semantics or
unpromoted historical helper tools are supported.
"""

from __future__ import annotations

from ._common import (
    PROTOCOL_VERSION,
    SERVER_NAME,
    SERVER_VERSION,
    ToolSpec,
    clamp_int,
)
from ._server import MCPServer, create_mcp_server
from ._tools_spec import TOOLS

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
