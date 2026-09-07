# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""MCP (Model Context Protocol) stdio JSON-RPC server package for tstdx.

Zero-dependency: the MCP protocol is implemented directly over stdio and
the optional ``mcp`` package is never imported.  Split out of
:mod:`tstdx.integration.mcp_server` (v11 P11-1); the historical module
path remains the public facade and re-exports the full surface below.

Layout
------
* :mod:`._common` — protocol constants, output caps, ``clamp_int``,
  :class:`ToolSpec`, JSON Schema property builders (pure data);
* :mod:`._tools_impl` — the 23 tool handler functions;
* :mod:`._tools_spec` — the tool manifest (name/description/inputSchema);
* :mod:`._server` — JSON-RPC 2.0 stdio loop, :class:`MCPServer`,
  :func:`create_mcp_server`.

.. warning:: Security & trust assumption (P8)
   The server has **no authentication/authorization** and is designed to
   run over **stdio** for a single local MCP client.  Do **not** expose
   it over an untrusted network or socket without adding a guard
   (token / ACL) in front of it.
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
