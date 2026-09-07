# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""Integration adapters — bridge tstdx to third-party protocols.

This package hosts standalone adapters that expose tstdx capabilities to
external systems.  The current member is :mod:`.mcp_server`, a zero-
dependency stdio JSON-RPC 2.0 server implementing the MCP (Model Context
Protocol) with 10 tstdx tools.

Design principle
----------------
Every adapter in this package is **zero-dependency**: it must work without
the optional ``mcp`` / ``fastapi`` / … extras installed.  Optional
dependencies may be detected at runtime for enhanced features, but the
default code path never imports them.
"""

from __future__ import annotations

__all__ = []
