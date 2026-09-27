# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""v13 migrated capability catalog.

The retired UnifiedQuoteAPI is not restored. Every migrated business ability is
assigned to one explicit Provider/Channel and one low-level implementation.
Historical helpers that internally select or aggregate upstream services are
honestly represented by the explicit ``derived`` Provider.
"""

from __future__ import annotations

import inspect
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from ..errors import ValidationError

__all__ = [
    "MigratedCapabilityBinding",
    "MIGRATED_BINDINGS",
    "MIGRATED_CAPABILITIES",
    "binding_for",
    "call_kwargs_for",
    "bindings_for_provider",
    "default_provider_for",
    "implementation_for",
    "is_migrated_capability",
    "validate_call",
]


@dataclass(frozen=True, slots=True)
class MigratedCapabilityBinding:
    capability: str
    provider: str
    channel: str
    backend: str
    method: str
    source: str = ""
    #: Lazy import path for the ``web_adapter`` backends, e.g.
    #: ``"tstdx.web.tencent.adapters:MinuteSource"``. Only that backend reads it,
    #: and all five of its bindings ship one — a ``web_adapter`` binding without it
    #: is a registry gap the executor reports instead of papering over.
    factory: str = ""

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.provider, self.channel, self.capability)


_PROVIDER_OVERRIDES: dict[str, str] = {
    "industry_boards": "sina",
    "board_list": "sina",
    "board_members": "sina",
    "suggest": "sina",
    "search_symbols": "sina",
    "esg_rating": "sina",
    "esg_history": "sina",
    "esg_ratings_all": "sina",
    "board_rank": "tencent",
    "us_quotes": "tencent",
    "globals": "tencent",
    "market_stat": "tencent",
    "ticks": "tencent",
    "intraday": "eastmoney",
    "em_boards": "eastmoney",
    "em_board_members": "eastmoney",
    "stock_boards": "eastmoney",
    "rates": "boc",
    "wencai": "iwencai",
    "index_list": "builtin",
    "dc_reports": "builtin",
}
_SOURCE_FOR_PROVIDER = {
    "derived": "sina",
    "eastmoney": "eastmoney",
    "sina": "sina",
    "tencent": "tencent",
    "baidu": "sina",
    "boc": "boc",
    "iwencai": "sina",
    "builtin": "sina",
}
#: Capabilities whose canonical registry channel is *not* the generic ``catalog``
#: channel — the registry is the single source of truth for
#: ``(provider, channel, capability)``, so the catalog must bind to the same home
#: or the plan compiled by :class:`~tstdx.query.QueryPlanner` could never be
#: dispatched. Keeping the two lists aligned by hand is exactly the drift this
#: table removes for the remaining special cases.
_CHANNEL_OVERRIDES: dict[str, str] = {
    "board_list": "board_list",
    "suggest": "suggest",
    "market_stat": "market_stat",
    "board_rank": "board_rank",
    "wencai": "screening",
    "rates": "fx",
}
#: 自动发现要跳开的名字分成两件事，因为它们**为不同的说法负责**，混在一个集合里就没人能
#: 核对任何一件：
#:
#: * :data:`_RESERVED_CORE_CAPABILITIES` 是**保留名**——"这条能力归核心分派，web 面哪天长出
#:   同名方法都不许在这里再开一个家"。今天 `WebQuoteSession` 上只有 `quotes` 与 `minute` 两个
#:   同名成员真的会被挡住，其余五格是为将来留的（留着的代价是零，去掉的代价见 §21 的读数）。
#:   它的权威在 :data:`tstdx.runtime.executor.DEDICATED_CAPABILITIES`（从执行体表派生）；catalog
#:   不能直接 import 它——`runtime/executor.py` 顶层就 import 了本模块，方向反过来是硬环。
#:   两份名单的一致性由 `tests/architecture/test_catalog_reserved_names.py` 钉住。
#: * :data:`_NOT_A_QUERY_MEMBER` 是**成员黑名单**——类上真实存在、但不是查询的东西。每一条
#:   都必须真的命中一个可调用成员，否则它就是一句关于这个类的谎话（判据同上一份文件）。
_RESERVED_CORE_CAPABILITIES = frozenset(
    {
        "quotes",
        "bars",
        "snapshot",
        "minute",
        "trades",
        "security_count",
        "security_list",
    }
)
_NOT_A_QUERY_MEMBER = frozenset({"close"})
_SKIP_WEB_METHODS = _RESERVED_CORE_CAPABILITIES | _NOT_A_QUERY_MEMBER


def _discover_web_bindings() -> list[MigratedCapabilityBinding]:
    from ..web.session import WebQuoteSession

    values: list[MigratedCapabilityBinding] = []
    for name, _member in inspect.getmembers(WebQuoteSession, predicate=callable):
        if name.startswith("_") or name in _SKIP_WEB_METHODS:
            continue
        provider = _PROVIDER_OVERRIDES.get(name)
        if provider is None:
            provider = "baidu" if name.startswith("baidu_") else "derived"
        values.append(
            MigratedCapabilityBinding(
                capability=name,
                provider=provider,
                channel=_CHANNEL_OVERRIDES.get(name, "catalog"),
                backend="web_session",
                method=name,
                source=_SOURCE_FOR_PROVIDER[provider],
            )
        )
    return values


_EXPLICIT_BINDINGS: tuple[MigratedCapabilityBinding, ...] = (
    MigratedCapabilityBinding(
        "quotes_concurrent", "tdx", "quotation", "tdx_client", "quotes_concurrent"
    ),
    MigratedCapabilityBinding("minute_history", "tdx", "quotation", "tdx_client", "minute_history"),
    MigratedCapabilityBinding("block_quotes", "tdx", "quotation", "tdx_client", "block_quotes"),
    MigratedCapabilityBinding("auction", "tdx", "quotation", "tdx_client", "auction_snapshot"),
    MigratedCapabilityBinding(
        "volume_price", "tdx", "quotation", "tdx_client", "volume_price_dist"
    ),
    MigratedCapabilityBinding("finance", "tdx", "quotation", "tdx_client", "finance_info"),
    MigratedCapabilityBinding(
        "capital_changes", "tdx", "quotation", "tdx_client", "capital_changes"
    ),
    MigratedCapabilityBinding(
        "corporate_action", "tdx", "quotation", "tdx_client", "capital_changes"
    ),
    #: 分页只有一份实现：绑定到 `TdxClient.export_security_list`（页数上限、短页判据、
    #: 空首页告警都在那里），不再在 composed 面另写一条 start 游标循环。
    MigratedCapabilityBinding(
        "security_list_all", "tdx", "quotation", "tdx_client", "export_security_list"
    ),
    MigratedCapabilityBinding("f10", "tdx", "f10", "f10_client", "f10"),
    MigratedCapabilityBinding("f10_catalog", "tdx", "f10", "f10_client", "catalog"),
    MigratedCapabilityBinding("ex_market_list", "tdx", "extended", "ex_client", "ex_market_list"),
    MigratedCapabilityBinding(
        "ex_instruments", "tdx", "extended", "ex_client", "ex_instrument_list"
    ),
    MigratedCapabilityBinding("ex_bars", "tdx", "extended", "ex_client", "ex_bars"),
    MigratedCapabilityBinding("ex_quotes", "tdx", "extended", "ex_client", "ex_quote"),
    MigratedCapabilityBinding("goods_bars", "tdx", "goods", "goods_client", "goods_bars"),
    MigratedCapabilityBinding("goods_quotes", "tdx", "goods", "goods_client", "goods_quote"),
    MigratedCapabilityBinding(
        "adjusted_bars", "derived", "adjustment", "composed", "adjusted_bars"
    ),
    MigratedCapabilityBinding("sync_daily", "derived", "sync", "composed", "sync_daily"),
    MigratedCapabilityBinding(
        "minute_web",
        "tencent",
        "catalog",
        "web_adapter",
        "minute_web",
        factory="tstdx.web.tencent.adapters:MinuteSource",
    ),
    MigratedCapabilityBinding(
        "minute_klines",
        "tencent",
        "catalog",
        "web_adapter",
        "minute_klines",
        factory="tstdx.web.tencent.adapters:MinuteKlineSource",
    ),
    MigratedCapabilityBinding(
        "minute_klines",
        "eastmoney",
        "catalog",
        "web_adapter",
        "minute_klines",
        factory="tstdx.web.eastmoney.adapters:EastmoneyHistoryKlineSource",
    ),
    MigratedCapabilityBinding(
        "history",
        "sina",
        "catalog",
        "web_adapter",
        "history",
        factory="tstdx.web.sina.adapters:SinaHistoryKlineSource",
    ),
    MigratedCapabilityBinding(
        "history",
        "eastmoney",
        "catalog",
        "web_adapter",
        "history",
        factory="tstdx.web.eastmoney.adapters:EastmoneyHistoryKlineSource",
    ),
    MigratedCapabilityBinding("all_market", "sina", "catalog", "web_session", "all_market", "sina"),
    MigratedCapabilityBinding(
        "all_market", "tencent", "catalog", "web_session", "all_market", "tencent"
    ),
    MigratedCapabilityBinding("hk_quotes", "sina", "catalog", "web_session", "hk_quotes", "sina"),
    MigratedCapabilityBinding(
        "hk_quotes", "tencent", "catalog", "web_session", "hk_quotes", "tencent"
    ),
    # MAC 协议行情（TDX 7727 family）。``mac`` channel 的 ``quotes`` capability
    # 是 v14 Direct API surface（``TdxMacAPI.quote``）；planner 因 canonical
    # channel 规则不会把它选为统一引用，故只在 ``mac_quotes`` 上补一条。
    MigratedCapabilityBinding("mac_quotes", "tdx", "mac", "mac_client", "mac_quote"),
    # 权息资料（公司行为 / 股本变迁）在全仓只有**一个**低层实现：TDX
    # ``TdxClient.capital_changes``。v14 注册表把 ``CorporateActionQuery`` 归到
    # eastmoney ``corporate`` channel，因此该语义 home 指向同一实现，而不是杜撰
    # 一个不存在的东财资源。
    MigratedCapabilityBinding(
        "corporate_action", "eastmoney", "corporate", "tdx_client", "capital_changes"
    ),
)


#: v14 registry channels whose capabilities map 1:1 onto a same-named
#: :class:`~tstdx.web.session.WebQuoteSession` method. These are the *semantic
#: homes* the v14 registry gave migrated business abilities (``datacenter`` /
#: ``derivatives`` / ``fund`` …) next to their aggregate ``derived`` home. The
#: session class is already the single high-level implementation of every one of these
#: capabilities — ``derived``/``catalog`` binds the identical method — so the
#: semantic home is bound to the *same* implementation instead of forking a
#: second one. Capabilities are read from the registry (SSOT), never re-listed
#: here, and a capability that the session class does not expose is skipped so that
#: :data:`_DIRECT_ADAPTER_BINDINGS` can own it instead.
_SEMANTIC_WEB_CHANNELS: tuple[tuple[str, str], ...] = (
    ("eastmoney", "datacenter"),
    ("eastmoney", "derivatives"),
    ("eastmoney", "fund"),
    ("eastmoney", "fund_flow"),
    ("eastmoney", "hot_rank"),
    ("eastmoney", "index_constituents"),
    ("eastmoney", "limit_pool"),
    ("eastmoney", "longhu"),
    ("eastmoney", "margin"),
    ("eastmoney", "news"),
    ("eastmoney", "northbound"),
    ("eastmoney", "options"),
    ("eastmoney", "rank"),
    ("eastmoney", "research"),
    ("eastmoney", "stock_changes"),
    ("sina", "fund_flow"),
    ("sina", "news"),
)

#: Registry triples whose only implementation is a channel adapter class.
#: ``backend="direct_adapter"`` resolves the adapter *class* from the
#: single :data:`tstdx.catalog.provider_bindings` ``CHANNELS`` table at dispatch time, so this
#: table only names the adapter *method* — module/class strings are never
#: duplicated here.
_DIRECT_ADAPTER_BINDINGS: tuple[tuple[str, str, str, str], ...] = (
    # (capability, provider, channel, adapter_method)
    ("minute", "baidu", "minute", "fetch_minute"),
    ("trades", "baidu", "ticks", "fetch_ticks"),
    ("fx_rates", "boc", "fx", "fetch_rates"),
    ("minute", "eastmoney", "trends", "fetch_minutes"),
    ("screening", "iwencai", "screening", "fetch_strategy"),
    ("convertible_bond", "jsl", "bond", "fetch"),
    ("board_member", "sina", "board_member", "fetch_members"),
    ("industry_board", "sina", "industry_board", "fetch_boards"),
    ("global_quotes", "tencent", "global", "fetch"),
    ("minute", "tencent", "minute", "fetch_minute"),
    ("trades", "tencent", "ticks", "fetch_ticks"),
)


def _semantic_web_bindings() -> list[MigratedCapabilityBinding]:
    from ..providers import PROVIDERS
    from ..web.session import WebQuoteSession

    values: list[MigratedCapabilityBinding] = []
    for provider, channel in _SEMANTIC_WEB_CHANNELS:
        spec = PROVIDERS.get(provider).channel(channel)
        for capability in sorted(spec.capabilities):
            if not hasattr(WebQuoteSession, capability):
                continue
            values.append(
                MigratedCapabilityBinding(
                    capability=capability,
                    provider=provider,
                    channel=channel,
                    backend="web_session",
                    method=capability,
                    # 与 :func:`_discover_web_bindings` 同一口径：Provider 没有登记来源就当场
                    # 报错。带默认值的查表会让一个新 Provider 的能力悄悄以别家的量纲单位上线。
                    source=_SOURCE_FOR_PROVIDER[provider],
                )
            )
    return values


def _direct_adapter_bindings() -> list[MigratedCapabilityBinding]:
    return [
        MigratedCapabilityBinding(
            capability=capability,
            provider=provider,
            channel=channel,
            backend="direct_adapter",
            method=method,
        )
        for capability, provider, channel, method in _DIRECT_ADAPTER_BINDINGS
    ]


def _build_bindings() -> tuple[MigratedCapabilityBinding, ...]:
    """Compose the migrated catalog from every declared source of truth.

    Precedence is deliberate: the auto-discovered WebQuoteSession methods give the
    baseline, the derived/registry-driven tables narrow it per Provider home, and
    the hand-written :data:`_EXPLICIT_BINDINGS` win last because they encode the
    session-independent backends (native TDX clients, composed adapters, …).
    """

    by_key: dict[tuple[str, str, str], MigratedCapabilityBinding] = {}
    for item in _discover_web_bindings():
        by_key[item.key] = item
    for item in _semantic_web_bindings():
        by_key[item.key] = item
    for item in _direct_adapter_bindings():
        by_key[item.key] = item
    for item in _EXPLICIT_BINDINGS:
        by_key[item.key] = item
    return tuple(sorted(by_key.values(), key=lambda x: x.key))


MIGRATED_BINDINGS = _build_bindings()
MIGRATED_CAPABILITIES = frozenset(item.capability for item in MIGRATED_BINDINGS)
_BINDINGS_BY_KEY = MappingProxyType({item.key: item for item in MIGRATED_BINDINGS})
_BINDINGS_BY_CAPABILITY = {
    capability: tuple(item for item in MIGRATED_BINDINGS if item.capability == capability)
    for capability in sorted(MIGRATED_CAPABILITIES)
}

_DEFAULT_PROVIDER = {
    "all_market": "derived",
    "hk_quotes": "derived",
    "history": "derived",
    "minute_klines": "derived",
}


def binding_for(provider: str, channel: str, capability: str) -> MigratedCapabilityBinding:
    return _BINDINGS_BY_KEY[(provider, channel, capability)]


def bindings_for_provider(provider: str) -> tuple[MigratedCapabilityBinding, ...]:
    pid = str(provider).strip().lower()
    return tuple(item for item in MIGRATED_BINDINGS if item.provider == pid)


def default_provider_for(capability: str) -> str:
    cap = str(capability).strip().lower()
    values = _BINDINGS_BY_CAPABILITY.get(cap)
    if not values:
        raise KeyError(capability)
    configured = _DEFAULT_PROVIDER.get(cap)
    if configured and any(item.provider == configured for item in values):
        return configured
    for preferred in (
        "derived",
        "tdx",
        "eastmoney",
        "sina",
        "tencent",
        "baidu",
        "boc",
        "iwencai",
        "builtin",
    ):
        for item in values:
            if item.provider == preferred:
                return preferred
    return values[0].provider


def is_migrated_capability(capability: str) -> bool:
    return str(capability).strip().lower() in MIGRATED_CAPABILITIES


#: Backends that pick the method *by capability* inside the executor, so no single
#: signature exists to bind against and the contract stays a manual check.
_MANUAL_BACKENDS = frozenset({"web_adapter", "composed"})


def implementation_for(provider: str, channel: str, capability: str) -> Any | None:
    """Return the callable that executes a migrated binding, or ``None``.

    ``None`` means the binding uses a :data:`_MANUAL_BACKENDS` backend. Both the
    argument check (:func:`validate_call`) and the semantic-payload derivation in
    :class:`~tstdx.runtime.executor.DirectProviderExecutor` read the host class
    from here, so "which implementation owns this capability's parameters" can
    never be answered twice with two different results.
    """
    meta = binding_for(provider, channel, capability)
    backend = meta.backend
    if backend == "web_session":
        from ..web.session import WebQuoteSession

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
        from .provider_bindings import resolve_channel_adapter

        return getattr(resolve_channel_adapter(provider, meta.channel), meta.method)
    if backend in _MANUAL_BACKENDS:
        return None
    raise TypeError(f"unknown migrated backend {backend!r}")


def call_kwargs_for(
    meta: MigratedCapabilityBinding, provider: str, kwargs: dict[str, Any]
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
    }[capability]
    if len(args) < required:
        raise TypeError(f"{capability} requires at least {required} positional argument(s)")
    allowed = {
        "adjusted_bars": {
            "method",
            "period",
            "count",
            "start",
            "events",
            "anchor_date",
        },
        "sync_daily": {"root", "profile", "chunk", "max_windows"},
    }[capability]
    unknown = sorted(set(kwargs) - allowed)
    if unknown:
        raise TypeError(f"unexpected keyword argument(s): {', '.join(unknown)}")


def _validate_web_adapter(
    capability: str,
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
) -> None:
    if len(args) != 1:
        raise TypeError(f"{capability} requires exactly one symbol argument")
    allowed = {
        "minute_web": set(),
        "minute_klines": {"period", "count", "adjust"},
        "history": {"period", "count", "adjust"},
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
        #: 够不到的地方：``cause`` 不进错误信封，实测 ``tstdx all-market`` 拿到 E1010 后
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
