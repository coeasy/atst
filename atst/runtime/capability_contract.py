# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""能力调用契约（参数校验 / 实现解析）。

v1 的 ``atst.catalog.capability`` 把「声明」与「校验」混在一个模块里。v2 收敛后：

* 声明（binding 表、默认源、健康度）在 :mod:`atst.providers`；
* 校验（``validate_call``、实现解析、Provider 注入规则）在本模块。

两者读取同一份 :class:`atst.providers.CapabilityBinding`，因此「哪个实现拥有
这个能力的参数」永远只有一个答案。
"""

from __future__ import annotations

import inspect
from typing import Any

from ..errors import ValidationError
from ..providers import (
    CapabilityBinding,
    binding_for,
    resolve_channel_adapter,
)

#: Backends that pick the method *by capability* inside the executor, so no single
#: signature exists to bind against and the contract stays a manual check.
_MANUAL_BACKENDS = frozenset({"web_adapter", "composed"})

#: 全市场口径的 web_adapter 能力：**不接受**标的。
_MARKET_WIDE_WEB_ADAPTER_CAPS = frozenset({"breaking_news"})

#: 标的可选：给了就按标的过滤（本地筛，不是服务端过滤），不给就取全市场。
_SYMBOL_OPTIONAL_WEB_ADAPTER_CAPS = frozenset(
    {
        "announcements",
        "hk_announcements",
        "theme_attribution",
        "concept_members",
        "limit_pool",
        "hot_rank",
    }
)


def implementation_for(provider: str, channel: str, capability: str) -> Any | None:
    """Return the callable that executes a migrated binding, or ``None``.

    ``None`` means the binding uses a :data:`_MANUAL_BACKENDS` backend. Both the
    argument check (:func:`validate_call`) and the semantic-payload derivation in
    :class:`~atst.runtime.executor.DirectProviderExecutor` read the host class
    from here, so "which implementation owns this capability's parameters" can
    never be answered twice with two different results.
    """

    meta = binding_for(provider, channel, capability)
    backend = meta.backend
    if backend == "web_session":
        from ..sources.session import WebQuoteSession

        return getattr(WebQuoteSession, meta.method)
    if backend == "tdx_client":
        from ..client import TdxClient

        return getattr(TdxClient, meta.method)
    if backend == "f10_client":
        from ..client import F10Client

        return getattr(F10Client, "download" if capability == "f10" else "catalog")
    if backend in {"ex_client", "goods_client", "mac_client"}:
        from ..client import ExMarketClient, GoodsClient, MacClient

        cls = {
            "ex_client": ExMarketClient,
            "goods_client": GoodsClient,
            "mac_client": MacClient,
        }[backend]
        return getattr(cls, meta.method)
    if backend == "direct_adapter":
        return getattr(resolve_channel_adapter(provider, meta.channel), meta.method)
    if backend in _MANUAL_BACKENDS:
        return None
    raise TypeError(f"unknown migrated backend {backend!r}")


def call_kwargs_for(
    meta: CapabilityBinding, provider: str, kwargs: dict[str, Any]
) -> dict[str, Any]:
    """真正递交给实现的关键字入参——Provider 注入规则只有这一处。

    ``WebQuoteSession.hk_quotes`` 的 ``provider`` 缺省是 sina，让这一格"意味着 tencent"
    的是**绑定的 Provider**，所以它必须由同时认识后端与 Provider 的目录层填进去。校验
    （:func:`validate_call`）与执行（``DirectProviderExecutor._migrated_capability``）读的
    是同一份规则：在此之前两处各抄一遍，改一处即分叉。
    """

    call_kwargs = dict(kwargs)
    if meta.backend == "web_session" and meta.capability == "hk_quotes":
        call_kwargs.setdefault("provider", provider)
    return call_kwargs


def _bind_signature(target: Any, args: tuple[Any, ...], kwargs: dict[str, Any]) -> None:
    signature = inspect.signature(target)
    parameters = tuple(signature.parameters.values())
    if parameters and parameters[0].name in {"self", "cls"}:
        signature.bind(None, *args, **kwargs)
    else:
        signature.bind(*args, **kwargs)


def _validate_composed(
    capability: str,
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
) -> None:
    required = {
        "adjusted_bars": 1,
        "sync_daily": 1,
        "daily_enriched": 1,
    }[capability]
    if len(args) < required:
        raise TypeError(f"{capability} requires at least {required} positional argument(s)")
    allowed = {
        "adjusted_bars": {
            "method",
            "adjustment",
            "period",
            "count",
            "start",
            "events",
            "anchor_date",
            "provider",
            "event_source",
        },
        "sync_daily": {"root", "profile", "chunk", "max_windows"},
        "daily_enriched": {"count", "start", "adjust", "provider", "event_source"},
    }[capability]
    unknown = sorted(set(kwargs) - allowed)
    if unknown:
        raise TypeError(f"unexpected keyword argument(s): {', '.join(unknown)}")


def _validate_web_adapter(
    capability: str,
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
) -> None:
    if capability in _MARKET_WIDE_WEB_ADAPTER_CAPS:
        if args:
            raise TypeError(
                f"{capability} 是全市场能力，不接受 symbol 参数（它按市场整体返回，"
                f"传标的只会误导；请用 limit/date 控制返回量）"
            )
    elif capability in _SYMBOL_OPTIONAL_WEB_ADAPTER_CAPS:
        if len(args) > 1:
            raise TypeError(f"{capability} 最多接受一个 symbol 参数")
    elif len(args) != 1:
        raise TypeError(f"{capability} requires exactly one symbol argument")
    allowed = {
        "minute_web": set(),
        "minute_klines": {"period", "count", "adjust"},
        "history": {"period", "count", "adjust"},
        "limit_pool": {"date", "limit"},
        "hot_rank": {"limit"},
        "breaking_news": {"limit", "channel"},
        "announcements": {"limit", "start_date", "end_date"},
        "hk_announcements": {"limit", "start_date", "end_date"},
        "theme_attribution": {"date", "limit"},
        "concept_members": {"date", "limit"},
    }[capability]
    unknown = sorted(set(kwargs) - allowed)
    if unknown:
        raise TypeError(f"unexpected keyword argument(s): {', '.join(unknown)}")


def validate_call(
    provider: str,
    channel: str,
    capability: str,
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
) -> None:
    """Validate migrated arguments before any Provider I/O.

    The catalog is generic at the transport boundary, but caller arguments are
    bound against the real implementation signature (or an explicit manual
    contract for composed adapters) before execution.
    """
    try:
        meta = binding_for(provider, channel, capability)
        impl = implementation_for(provider, channel, capability)
        if impl is None:
            if meta.backend == "web_adapter":
                _validate_web_adapter(capability, args, kwargs)
                return
            _validate_composed(capability, args, kwargs)
            return
        call_kwargs = call_kwargs_for(meta, provider, kwargs)
        _bind_signature(impl, args, call_kwargs)
    except AttributeError as exc:
        # 绑定表指向的实现取不到：这是表与代码分叉，不是调用方的参数错了。
        # 让它穿过这里，HTTP/WS 两张面会把内部漂移报成 E9000/500——一张过期条目
        # 对调用方是可诊断的契约问题，对本仓库是必须当场红的账。
        raise ValidationError(
            f"capability {capability!r} 的绑定指向了不存在的实现：{exc}",
            context={
                "provider": provider,
                "channel": channel,
                "capability": capability,
                "phase": "binding_resolution",
            },
            cause=exc,
        ) from exc
    except (KeyError, TypeError, ValueError) as exc:
        #: 收敛成 ValidationError 是对的，但只报「不符合 contract」等于把判据丢在调用方
        #: 够不到的地方：``cause`` 不进错误信封，实测 ``atst all-market`` 拿到 E1010 后
        #: 无人能说出是哪几个键越了界（第 23 轮）。被拒的键名必须出现在消息里。
        raise ValidationError(
            f"capability {capability!r} 参数不符合 v13 contract：{exc}",
            context={
                "provider": provider,
                "channel": channel,
                "capability": capability,
                "phase": "query_validation",
                "reason": str(exc),
            },
            cause=exc,
        ) from exc


__all__ = [
    "call_kwargs_for",
    "implementation_for",
    "validate_call",
]
