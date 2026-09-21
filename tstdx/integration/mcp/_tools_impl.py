# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""Canonical v13 MCP tool handlers backed exclusively by Client."""

from __future__ import annotations

from typing import Any

from ...client.api import Client
from ...errors import ValidationError
from ...runtime.orchestration import FallbackPolicy
from ..serialization import serialize_result
from ._common import MAX_BARS_COUNT, MAX_PAGE, clamp_int

__all__ = [
    "_h_get_bars",
    "_h_get_quote",
    "_h_get_quotes",
    "_h_get_snapshot",
    "_h_get_minute_today",
    "_h_get_trades",
    "_h_get_security_count",
    "_h_get_security_list",
    "_h_query_capability",
]


def _h_query_capability(client: Client, args: dict[str, Any]) -> dict[str, Any]:
    capability = args.get("capability")
    if not isinstance(capability, str) or not capability.strip():
        raise ValidationError("capability is required")
    call_args = args.get("args", [])
    call_kwargs = args.get("kwargs", {})
    if not isinstance(call_args, list):
        raise ValidationError("args must be an array")
    if not isinstance(call_kwargs, dict):
        raise ValidationError("kwargs must be an object")
    return serialize_result(
        client.call(
            capability,
            *call_args,
            provider=args.get("provider"),
            channel=args.get("channel"),
            currentness=str(args.get("currentness", "business")),
            **call_kwargs,
        )
    )


def _policy_and_provider(
    fallback: Any, provider: Any, *, default_provider: str | None
) -> tuple[FallbackPolicy | None, str | None]:
    """``(policy, provider)`` 的合成：要了 fallback 就不能再把 provider 钉成默认那一家。

    各工具的 ``provider`` 缺省本来就不同（``get_bars`` 交给内核选 canonical，其余三格写死
    ``"tdx"``），所以缺省留在调用处；这里只处理一件对所有工具同样的事：内核把「同时点名
    一家 + 要求换跳」判成互斥（:func:`tstdx.client.api._reject_provider_with_policy`），
    因此一旦递了 fallback，MCP 自己的 provider 缺省必须让位，否则每一个用 fallback 的
    MCP 调用都会撞在互斥上。
    """
    policy = FallbackPolicy.from_wire(fallback)
    if policy is not None:
        return policy, provider
    return None, provider or default_provider


def _h_get_bars(client: Client, args: dict[str, Any]) -> dict[str, Any]:
    policy, provider = _policy_and_provider(
        args.get("fallback"), args.get("provider"), default_provider=None
    )
    return serialize_result(
        client.bars(
            args["symbol"],
            provider=provider,
            policy=policy,
            period=str(args.get("period", "day")),
            count=clamp_int(args.get("count", 320), 320, 1, MAX_BARS_COUNT),
            start=clamp_int(args.get("start", 0), 0, 0, MAX_PAGE),
            adjustment=str(args.get("adjustment", "")),
        )
    )


def _h_get_quote(client: Client, args: dict[str, Any]) -> dict[str, Any]:
    policy, provider = _policy_and_provider(
        args.get("fallback"), args.get("provider"), default_provider="tdx"
    )
    return serialize_result(client.quotes(args["symbol"], provider=provider, policy=policy))


def _h_get_quotes(client: Client, args: dict[str, Any]) -> dict[str, Any]:
    policy, provider = _policy_and_provider(
        args.get("fallback"), args.get("provider"), default_provider="tdx"
    )
    return serialize_result(client.quotes(args["symbols"], provider=provider, policy=policy))


def _h_get_snapshot(client: Client, args: dict[str, Any]) -> dict[str, Any]:
    return serialize_result(client.snapshot(args["symbol"], provider=args.get("provider") or "tdx"))


def _h_get_minute_today(client: Client, args: dict[str, Any]) -> dict[str, Any]:
    return serialize_result(client.minute(args["symbol"], provider=args.get("provider") or "tdx"))


def _h_get_trades(client: Client, args: dict[str, Any]) -> dict[str, Any]:
    return serialize_result(
        client.trades(
            args["symbol"],
            provider=args.get("provider") or "tdx",
            start=clamp_int(args.get("start", 0), 0, 0, MAX_PAGE),
            count=clamp_int(args.get("count", 0), 0, 0, MAX_BARS_COUNT),
        )
    )


def _h_get_security_count(client: Client, args: dict[str, Any]) -> dict[str, Any]:
    return serialize_result(
        client.security_count(market=args.get("market", 0), provider=args.get("provider") or "tdx")
    )


def _h_get_security_list(client: Client, args: dict[str, Any]) -> dict[str, Any]:
    return serialize_result(
        client.security_list(
            market=args.get("market", 0),
            start=clamp_int(args.get("start", 0), 0, 0, MAX_PAGE),
            provider=args.get("provider") or "tdx",
        )
    )
