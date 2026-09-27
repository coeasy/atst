# Copyright (c) 2026 atst contributors
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
#: 与 HTTP 查询串、WS ``params``、CLI ``--fallback`` 同一个旋钮、同一条解析
#: （:meth:`atst.runtime.orchestration.FallbackPolicy.from_wire`）。此前只有 MCP 没有它，
#: 所以同一个「这家失败就换」的请求在三张面上能提、在面向模型的那张面上提不出来。
_FALLBACK = _str_prop("Comma-separated ordered Provider list for cross-Provider fallback.")

TOOLS: list[ToolSpec] = [
    ToolSpec(
        name="query_capability",
        description="Execute any migrated v13 business capability through the canonical Client runtime.",
        inputSchema={
            "type": "object",
            "additionalProperties": False,
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
            "additionalProperties": False,
            "properties": {
                "symbol": _str_prop("Security symbol."),
                "provider": _PROVIDER,
                "fallback": _FALLBACK,
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
            "additionalProperties": False,
            "properties": {
                "symbol": _str_prop("Security symbol."),
                "provider": _PROVIDER,
                "fallback": _FALLBACK,
            },
            "required": ["symbol"],
        },
        handler=_h_get_quote,
    ),
    ToolSpec(
        name="get_quotes",
        description="Fetch live quotes through the v13 Client runtime.",
        inputSchema={
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "symbols": _list_of_strings("Security symbols."),
                "provider": _PROVIDER,
                "fallback": _FALLBACK,
            },
            "required": ["symbols"],
        },
        handler=_h_get_quotes,
    ),
    ToolSpec(
        name="get_snapshot",
        description="Fetch canonical TDX market snapshot/orderbook data.",
        inputSchema={
            "type": "object",
            "additionalProperties": False,
            "properties": {"symbol": _str_prop("Security symbol."), "provider": _PROVIDER},
            "required": ["symbol"],
        },
        handler=_h_get_snapshot,
    ),
    ToolSpec(
        name="get_minute_today",
        description=(
            "Unavailable on the tdx provider: its 0x0537 request/parser is still inferred, "
            "so the structured client refuses to send it (NotImplementedFeature). "
            "Only web providers (e.g. tencent) declare this capability today."
        ),
        inputSchema={
            "type": "object",
            "additionalProperties": False,
            "properties": {"symbol": _str_prop("Security symbol."), "provider": _PROVIDER},
            "required": ["symbol"],
        },
        handler=_h_get_minute_today,
    ),
    ToolSpec(
        name="get_trades",
        description=(
            "Unavailable on the tdx provider: its 0x0FC5 request/parser is still inferred, "
            "so the structured client refuses to send it (NotImplementedFeature). "
            "Intraday ticks are only declared by web providers (e.g. baidu/tencent)."
        ),
        inputSchema={
            "type": "object",
            "additionalProperties": False,
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
            "additionalProperties": False,
            "properties": {"market": _str_prop("Market id/prefix."), "provider": _PROVIDER},
        },
        handler=_h_get_security_count,
    ),
    ToolSpec(
        name="get_security_list",
        description=(
            "Offline: 0x044D is registered offline (multi-host measured, no response), so "
            "the client fail-fasts with CommandOffline before sending; no other provider "
            "declares a security catalog page."
        ),
        inputSchema={
            "type": "object",
            "additionalProperties": False,
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
