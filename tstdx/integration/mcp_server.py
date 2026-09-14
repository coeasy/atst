# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""Canonical v13 MCP stdio entrypoint.

The implementation lives in :mod:`tstdx.integration.mcp` and is backed only by
the v13 Client runtime. Historical handler/private-symbol compatibility exports
were intentionally removed in the clean-break architecture.
"""

from __future__ import annotations

from .mcp import (
    MCPServer,
    PROTOCOL_VERSION,
    SERVER_NAME,
    SERVER_VERSION,
    TOOLS,
    ToolSpec,
    clamp_int,
    create_mcp_server,
)

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


if __name__ == "__main__":  # pragma: no cover
    create_mcp_server().serve()
