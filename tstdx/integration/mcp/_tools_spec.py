# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""Canonical v13 MCP tool manifest.

Tier-A keeps ergonomic dedicated tools.  Every migrated capability is also
reachable through ``query_capability`` and therefore shares the same Client /
QuerySpec / UnifiedRuntime execution chain.
"""

from __future__ import annotations

from ._common import MAX_BARS_COUNT, MAX_PAGE, ToolSpec, _int_prop, _list_of_strings, _str_prop
from ._tools_impl import (
    _h_get_bars,
    _h_get_minute_today,
    _h_get_quote,
    _h_get_quotes,
    _h_get_security_count,
    _h_get_security_list,
    _h_get_snapshot,
    _h_get_trades,
    _h_query_capability,
)

__all__ = ["TOOLS", "_TOOLS_BY_NAME"]

_PROVIDER = _str_prop(
    "Canonical Provider id; defaults to the capability's canonical Provider where omitted."
)

TOOLS: list[ToolSpec] = [
    ToolSpec(
        name="query_capability",
        description="Execute any migrated v13 business capability through the canonical Client runtime.",
        inputSchema={
            "type": "object",
            "properties": {
                "capability": _str_prop("Migrated capability name."),
                "provider": _PROVIDER,
                "channel": _str_prop("Exact canonical channel; normally omitted."),
                "args": {"type": "array", "items": {}},
                "kwargs": {"type": "object", "additionalProperties": True},
                "currentness": _str_prop("auto/live/historical/business."),
            },
            "required": ["capability"],
        },
        handler=_h_query_capability,
    ),
    ToolSpec(
        name="get_bars",
        description="Fetch canonical historical bars through the v13 Client runtime.",
        inputSchema={
            "type": "object",
            "properties": {
                "symbol": _str_prop("Security symbol."),
                "provider": _PROVIDER,
                "period": _str_prop("Canonical bar period."),
                "count": _int_prop(
                    "Number of bars.", default=320, minimum=1, maximum=MAX_BARS_COUNT
                ),
                "start": _int_prop("Pagination offset.", default=0, minimum=0, maximum=MAX_PAGE),
                "adjustment": _str_prop("Adjustment mode when supported."),
            },
            "required": ["symbol"],
        },
        handler=_h_get_bars,
    ),
    ToolSpec(
        name="get_quote",
        description="Fetch one live quote through the v13 Client runtime.",
        inputSchema={
            "type": "object",
            "properties": {"symbol": _str_prop("Security symbol."), "provider": _PROVIDER},
            "required": ["symbol"],
        },
        handler=_h_get_quote,
    ),
    ToolSpec(
        name="get_quotes",
        description="Fetch live quotes through the v13 Client runtime.",
        inputSchema={
            "type": "object",
            "properties": {"symbols": _list_of_strings("Security symbols."), "provider": _PROVIDER},
            "required": ["symbols"],
        },
        handler=_h_get_quotes,
    ),
    ToolSpec(
        name="get_snapshot",
        description="Fetch canonical TDX market snapshot/orderbook data.",
        inputSchema={
            "type": "object",
            "properties": {"symbol": _str_prop("Security symbol."), "provider": _PROVIDER},
            "required": ["symbol"],
        },
        handler=_h_get_snapshot,
    ),
    ToolSpec(
        name="get_minute_today",
        description="Fetch canonical intraday minute data.",
        inputSchema={
            "type": "object",
            "properties": {"symbol": _str_prop("Security symbol."), "provider": _PROVIDER},
            "required": ["symbol"],
        },
        handler=_h_get_minute_today,
    ),
    ToolSpec(
        name="get_trades",
        description="Fetch canonical intraday trades/ticks.",
        inputSchema={
            "type": "object",
            "properties": {
                "symbol": _str_prop("Security symbol."),
                "provider": _PROVIDER,
                "start": _int_prop("Pagination offset.", default=0, minimum=0, maximum=MAX_PAGE),
                "count": _int_prop(
                    "Number of rows; zero means provider default.",
                    default=0,
                    minimum=0,
                    maximum=MAX_BARS_COUNT,
                ),
            },
            "required": ["symbol"],
        },
        handler=_h_get_trades,
    ),
    ToolSpec(
        name="get_security_count",
        description="Fetch canonical security count for a market.",
        inputSchema={
            "type": "object",
            "properties": {"market": _str_prop("Market id/prefix."), "provider": _PROVIDER},
        },
        handler=_h_get_security_count,
    ),
    ToolSpec(
        name="get_security_list",
        description="Fetch canonical security catalog page for a market.",
        inputSchema={
            "type": "object",
            "properties": {
                "market": _str_prop("Market id/prefix."),
                "provider": _PROVIDER,
                "start": _int_prop("Pagination offset.", default=0, minimum=0, maximum=MAX_PAGE),
            },
        },
        handler=_h_get_security_list,
    ),
]

_TOOLS_BY_NAME = {tool.name: tool for tool in TOOLS}
