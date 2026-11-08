# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""Canonical v13 MCP stdio JSON-RPC adapter.

The MCP package is transport-only: all market-data tools delegate to the same
Client runtime used by Python/CLI/HTTP/WS. The manifest exposes only capabilities
promoted into the v13 Provider Registry and DirectBinding contract.

Current tool surface:

``_tools_spec.TOOLS`` is the single roster, and this docstring deliberately does not
copy it. The list that used to sit here named seven tools and had been missing
``query_capability`` -- the generic entry every capability reaches through -- since
the round that added it. Ask ``tools/list``, or read ``_tools_spec``, not this page.

No legacy facade/TdxClient dual target, `use_facade`, route semantics or
unpromoted historical helper tools are supported.
"""

from __future__ import annotations

from ._common import (
    PROTOCOL_VERSION,
    SERVER_NAME,
    SERVER_VERSION,
    ToolSpec,
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
]
