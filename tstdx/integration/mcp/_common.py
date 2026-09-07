# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""Shared primitives for the tstdx MCP stdio server (pure data / helpers).

Protocol constants, output caps, the :func:`clamp_int` argument guard, the
declarative :class:`ToolSpec` container and the JSON Schema property
builders used by the tool manifest.  No tool handler or transport logic
lives here — see :mod:`._tools_impl` (handlers), :mod:`._tools_spec`
(manifest) and :mod:`._server` (JSON-RPC stdio framework).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ... import __version__

__all__ = [
    "SERVER_NAME",
    "SERVER_VERSION",
    "PROTOCOL_VERSION",
    "ERR_PARSE",
    "ERR_INVALID_REQUEST",
    "ERR_METHOD_NOT_FOUND",
    "ERR_INVALID_PARAMS",
    "ERR_INTERNAL",
    "MAX_ROWS",
    "MAX_TEXT_CHARS",
    "MAX_BARS_COUNT",
    "MAX_STOCK_CHANGES_SIZE",
    "MAX_HOT_RANK_SIZE",
    "MAX_PAGE",
    "clamp_int",
    "ToolSpec",
]

#: Server identity (returned by ``initialize``).
SERVER_NAME = "tstdx"
SERVER_VERSION = __version__

#: MCP protocol version.  Bump only in lockstep with upstream spec releases.
PROTOCOL_VERSION = "2024-11-05"

# --------------------------------------------------------------------------- #
# JSON-RPC error codes (subset)
# --------------------------------------------------------------------------- #
ERR_PARSE = -32700
ERR_INVALID_REQUEST = -32600
ERR_METHOD_NOT_FOUND = -32601
ERR_INVALID_PARAMS = -32602
ERR_INTERNAL = -32603

# --------------------------------------------------------------------------- #
# Output caps — keep single tool responses bounded
# --------------------------------------------------------------------------- #
#: Maximum number of rows in a list-valued tool result before truncation.
MAX_ROWS: int = 500

#: Maximum character length of the serialized text payload.
MAX_TEXT_CHARS: int = 32_000

#: 上限（V6）：schema 声称的 maximum 必须在代码层 enforce。
MAX_BARS_COUNT: int = 2000
MAX_STOCK_CHANGES_SIZE: int = 500
MAX_HOT_RANK_SIZE: int = 100
MAX_PAGE: int = 10_000


def clamp_int(value: Any, default: int, lo: int = 1, hi: int = MAX_BARS_COUNT) -> int:
    """int 参数统一入口（V6）：非数值回退 ``default``，并钳制到 ``[lo, hi]``。

    ``> 65535`` 的 count/size 会在 struct 编码层触发原生异常逃逸——入口
    ``min(max(v, 1), N)`` 钳制是第一道防线，schema 声称的上限在此强制生效。
    """
    try:
        v = int(value)
    except (TypeError, ValueError):
        return default
    return max(lo, min(v, hi))


# --------------------------------------------------------------------------- #
# Tool metadata
# --------------------------------------------------------------------------- #
class ToolSpec:
    """Declarative tool description.

    ``handler`` receives ``(client, args)`` — or ``(facade, args)`` when
    ``use_facade=True`` — and returns an arbitrary JSON-serializable value
    that will be serialized to a single text content block.  Raising
    :class:`TdxError` produces a JSON-RPC error response; other exceptions
    are also wrapped as errors.
    """

    __slots__ = ("name", "description", "inputSchema", "handler", "use_facade")

    def __init__(
        self,
        name: str,
        description: str,
        inputSchema: dict[str, Any],
        handler: Callable[[Any, dict[str, Any]], Any],
        *,
        use_facade: bool = False,
    ) -> None:
        self.name = name
        self.description = description
        self.inputSchema = inputSchema
        self.handler = handler
        #: 跨源 / web 独有能力工具走门面（UnifiedQuoteAPI）而非裸 TdxClient。
        self.use_facade = use_facade

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": self.inputSchema,
        }


def _str_prop(description: str, required: bool = False) -> dict[str, Any]:
    return {"type": "string", "description": description}


def _int_prop(
    description: str,
    default: int | None = None,
    minimum: int | None = None,
    maximum: int | None = None,
) -> dict[str, Any]:
    """构造 integer 属性 schema；minimum/maximum 与代码层 clamp_int 对应。"""
    p: dict[str, Any] = {"type": "integer", "description": description}
    if default is not None:
        p["default"] = default
    if minimum is not None:
        p["minimum"] = minimum
    if maximum is not None:
        p["maximum"] = maximum
    return p


def _list_of_strings(description: str) -> dict[str, Any]:
    return {
        "type": "array",
        "description": description,
        "items": {"type": "string"},
    }
