# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""Shared pure-data primitives for the v13 MCP adapter."""

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
    "MAX_PAGE",
    "clamp_int",
    "ToolSpec",
    "_str_prop",
    "_int_prop",
    "_list_of_strings",
]

SERVER_NAME = "tstdx"
SERVER_VERSION = __version__
PROTOCOL_VERSION = "2024-11-05"

ERR_PARSE = -32700
ERR_INVALID_REQUEST = -32600
ERR_METHOD_NOT_FOUND = -32601
ERR_INVALID_PARAMS = -32602
ERR_INTERNAL = -32603

MAX_ROWS = 500
MAX_TEXT_CHARS = 32_000
MAX_BARS_COUNT = 2000
MAX_PAGE = 10_000


def clamp_int(value: Any, default: int, lo: int = 1, hi: int = MAX_BARS_COUNT) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(lo, min(parsed, hi))


class ToolSpec:
    """Declarative canonical tool description; every handler receives Client."""

    __slots__ = ("name", "description", "inputSchema", "handler")

    def __init__(
        self,
        name: str,
        description: str,
        inputSchema: dict[str, Any],
        handler: Callable[[Any, dict[str, Any]], Any],
    ) -> None:
        self.name = name
        self.description = description
        self.inputSchema = inputSchema
        self.handler = handler

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": self.inputSchema,
        }


def _str_prop(description: str) -> dict[str, Any]:
    return {"type": "string", "description": description}


def _int_prop(
    description: str,
    default: int | None = None,
    minimum: int | None = None,
    maximum: int | None = None,
) -> dict[str, Any]:
    value: dict[str, Any] = {"type": "integer", "description": description}
    if default is not None:
        value["default"] = default
    if minimum is not None:
        value["minimum"] = minimum
    if maximum is not None:
        value["maximum"] = maximum
    return value


def _list_of_strings(description: str) -> dict[str, Any]:
    return {
        "type": "array",
        "description": description,
        "items": {"type": "string"},
    }
