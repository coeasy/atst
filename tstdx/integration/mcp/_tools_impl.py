# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""Canonical v13 MCP tool handlers backed exclusively by Client."""

from __future__ import annotations

from typing import Any

from ...client.api import Client
from ...errors import ValidationError
from ...runtime.orchestration import FallbackPolicy
from ..serialization import serialize_result
from ..wire_fields import as_request_int, reject_reserved_kwargs

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


def _int_arg(tool: str, field: str, value: Any) -> int:
    """整数格由**该工具自己的 ``inputSchema``** 裁定缺省与边界（第 25 轮 G34）。

    此前这里调一个已删除的整数钳位函数，把不合要求的值**静默换成**另一个数：``count="abc"`` 悄悄
    变 320、``count=0`` 悄悄变 1、``count=99999999`` 悄悄夹到 2000——于是同一张 schema 上
    声明的 ``minimum``/``maximum`` 成了一张没人按它行事的假告示，而调用方以为自己报的那个数
    生效了。改成"缺省才用 default，越界/坏类型当场拒"之后，边界只有 schema 这一个来源，
    不再需要第二份抄来的数，也不可能与对外声明分叉。

    形参收的是**已取出的值**而不是整个 ``args``：调用处必须自己写 ``args.get("count")``，
    这样 ``test_declared_knobs`` 那份 AST 才看得见"声明的键有人读"——键名一旦变成变量，
    那道判据就当场失明。
    """
    from ._tools_spec import _TOOLS_BY_NAME

    prop = _TOOLS_BY_NAME[tool].inputSchema["properties"][field]
    return as_request_int(
        face="mcp_arguments",
        where=f"MCP tool {tool} arguments",
        name=field,
        value=value,
        default=prop.get("default", 0),
        lo=prop.get("minimum"),
        hi=prop.get("maximum"),
    )


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
    reject_reserved_kwargs(
        face="mcp_arguments",
        where="MCP tool query_capability 的 kwargs",
        kwargs=call_kwargs,
    )
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
            count=_int_arg("get_bars", "count", args.get("count")),
            start=_int_arg("get_bars", "start", args.get("start")),
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
            start=_int_arg("get_trades", "start", args.get("start")),
            count=_int_arg("get_trades", "count", args.get("count")),
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
            start=_int_arg("get_security_list", "start", args.get("start")),
            provider=args.get("provider") or "tdx",
        )
    )
