# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""Provider ``channel -> adapter`` binding tables.

v16 clean break: the ``md.<provider>`` Direct API object layer was deleted
together with the v12 service it was bound to. :class:`~atst.client.api.Client`
is the only business entry point, and every request travels one zero-cache path
(:class:`~atst.runtime.kernel.UnifiedRuntime` -> ``DirectProviderExecutor`` ->
the bound Provider).

What survives here is the single home for ``(provider, channel) -> adapter class``
bindings, validated against the canonical Provider Registry at import time: every
registered Provider owns exactly one table and every non-local registered Channel
declares itself here. The migrated capability catalog resolves through
:func:`resolve_channel_adapter` instead of duplicating module/class strings, so a
channel can never point at a different adapter depending on which layer asks.
"""

from __future__ import annotations

import importlib
from collections.abc import Sequence
from typing import Any, ClassVar

from ..errors import ValidationError
from ..providers import PROVIDERS, resolve_provider

__all__ = [
    "ChannelBindings",
    "TdxProviderAPI",
    "LocalVipdocProviderAPI",
    "TencentProviderAPI",
    "SinaProviderAPI",
    "EastmoneyProviderAPI",
    "BaiduProviderAPI",
    "JslProviderAPI",
    "BocProviderAPI",
    "IwencaiProviderAPI",
    "CompositeProviderAPI",
    "DerivedProviderAPI",
    "BuiltinProviderAPI",
    "resolve_channel_adapter",
]

AdapterRef = tuple[str, str]


class ChannelBindings:
    """One canonical ProviderId's channel declarations."""

    provider_id: ClassVar[str | None] = None

    #: Channels served by exactly one adapter class, imported lazily on dispatch.
    CHANNELS: ClassVar[dict[str, AdapterRef]] = {}

    #: TDX online protocol channel families, served by ``atst.client`` backends.
    DIRECT_CHANNELS: ClassVar[frozenset[str]] = frozenset()

    #: Channels owned by a multi-adapter resource family instead of one class.
    EXPLICIT_CHANNELS: ClassVar[frozenset[str]] = frozenset()

    #: ``True`` for composite Providers (``derived`` / ``builtin``) that expose
    #: capabilities only through the unified QuerySpec path. They own no channel
    #: declaration, so the registry parity check skips them instead of forcing a
    #: fake adapter per channel.
    CHANNEL_API_EXEMPT: ClassVar[bool] = False


class TdxProviderAPI(ChannelBindings):
    provider_id = "tdx"
    DIRECT_CHANNELS: ClassVar[frozenset[str]] = frozenset(
        {"quotation", "extended", "goods", "f10", "mac"}
    )


class LocalVipdocProviderAPI(ChannelBindings):
    """Local TDX vipdoc bindings.

    ``local_vipdoc`` 是显式的本地历史数据 Provider：其唯一 channel ``vipdoc`` 标记为
    ``local=True``，属于本地文件执行器而非在线通道，因此本类型的 channel 表为空。
    读取本地 vipdoc 必须经 ``bars`` 能力走本地执行器，绝不允许以在线 TDX 行情之名冒充。
    """

    provider_id = "local_vipdoc"


class TencentProviderAPI(ChannelBindings):
    provider_id = "tencent"
    CHANNELS: ClassVar[dict[str, AdapterRef]] = {
        "quote": ("atst.web.tencent.adapters", "TencentSource"),
        "kline": ("atst.web.tencent.adapters", "KlineSource"),
        "minute_kline": ("atst.web.tencent.adapters", "MinuteKlineSource"),
        "minute": ("atst.web.tencent.adapters", "MinuteSource"),
        "ticks": ("atst.web.ticks", "TencentTickSource"),
        "global": ("atst.web.global_market", "TencentGlobalSource"),
        "market_stat": ("atst.web.global_market", "TencentMarketStatSource"),
        "board_rank": ("atst.web.boards", "TencentBoardRankSource"),
        "catalog": ("atst.web.session", "WebQuoteSession"),
    }


class SinaProviderAPI(ChannelBindings):
    provider_id = "sina"
    CHANNELS: ClassVar[dict[str, AdapterRef]] = {
        "quote": ("atst.web.sina.adapters", "SinaSource"),
        "history_kline": ("atst.web.sina.adapters", "SinaHistoryKlineSource"),
        "suggest": ("atst.web.sina.adapters", "SuggestSource"),
        "industry_board": ("atst.web.boards", "SinaIndustryBoardSource"),
        "board_list": ("atst.web.boards", "SinaBoardListSource"),
        "board_member": ("atst.web.boards", "SinaBoardMemberSource"),
        "fund_flow": ("atst.web.fundflow", "SinaFundFlowSource"),
        "news": ("atst.web.news", "SinaNewsSource"),
        "catalog": ("atst.web.session", "WebQuoteSession"),
    }


class EastmoneyProviderAPI(ChannelBindings):
    provider_id = "eastmoney"
    #: ``corporate`` 是一族共享东财报表后端的子资源，其能力由
    #: :class:`~atst.web.session.WebQuoteSession` 统一暴露，故无单一 adapter 类。
    EXPLICIT_CHANNELS: ClassVar[frozenset[str]] = frozenset({"corporate"})
    CHANNELS: ClassVar[dict[str, AdapterRef]] = {
        "quote": ("atst.web.eastmoney.adapters", "EastmoneySource"),
        "kline": ("atst.web.eastmoney.adapters", "EastmoneyHistoryKlineSource"),
        "trends": ("atst.web.ticks", "EastmoneyTrendsSource"),
        "rank": ("atst.web.fundflow", "EastmoneyRankSource"),
        "fund_flow": ("atst.web.fundflow", "EastmoneyFundFlowSource"),
        "limit_pool": ("atst.web.fundflow", "EastmoneyLimitPoolSource"),
        "stock_changes": ("atst.web.fundflow", "EastmoneyStockChangesSource"),
        "northbound": ("atst.web.fundflow", "EastmoneyNorthboundSource"),
        "hot_rank": ("atst.web.hot_rank", "EastmoneyHotRankSource"),
        "longhu": ("atst.web.longhu", "EastmoneyTopListSource"),
        "margin": ("atst.web.eastmoney.adapters", "EastmoneyMarginSource"),
        "index_constituents": (
            "atst.web.eastmoney.adapters",
            "EastmoneyIndexConstituentsSource",
        ),
        "fund": ("atst.web.adapters_fund", "FundSource"),
        "derivatives": ("atst.web.efinance_deriv", "EastmoneyFuturesSource"),
        "datacenter": ("atst.web.fin_report", "EastmoneyF10ReportSource"),
        "news": ("atst.web.news", "EastmoneyNewsSource"),
        "research": ("atst.web.news", "EastmoneyResearchVisitSource"),
        "options": ("atst.web.efinance_options", "EastmoneyOptionsSource"),
        "catalog": ("atst.web.session", "WebQuoteSession"),
    }


class BaiduProviderAPI(ChannelBindings):
    provider_id = "baidu"
    CHANNELS: ClassVar[dict[str, AdapterRef]] = {
        "quote": ("atst.web.baidu.adapters", "BaiduSource"),
        "kline": ("atst.web.baidu.adapters", "BaiduSource"),
        "minute": ("atst.web.baidu.adapters", "BaiduSource"),
        "ticks": ("atst.web.baidu.adapters", "BaiduSource"),
        "catalog": ("atst.web.session", "WebQuoteSession"),
    }


class JslProviderAPI(ChannelBindings):
    provider_id = "jsl"
    CHANNELS: ClassVar[dict[str, AdapterRef]] = {"bond": ("atst.web.jsl.adapters", "JslSource")}


class BocProviderAPI(ChannelBindings):
    provider_id = "boc"
    CHANNELS: ClassVar[dict[str, AdapterRef]] = {"fx": ("atst.web.boc.adapters", "BocSource")}


class IwencaiProviderAPI(ChannelBindings):
    provider_id = "iwencai"
    CHANNELS: ClassVar[dict[str, AdapterRef]] = {
        "screening": ("atst.web.wencai", "WencaiSource"),
    }


class CompositeProviderAPI(ChannelBindings):
    """Composite Providers reached only through the unified QuerySpec path.

    ``derived`` (honest aggregates over first-party Providers) and ``builtin``
    (static built-in catalogs) are canonical registry Providers so the migrated
    capability catalog has stable homes, but they own no channel adapter rather
    than a fake one, so the registry parity check skips them.
    """

    CHANNEL_API_EXEMPT: ClassVar[bool] = True


class DerivedProviderAPI(CompositeProviderAPI):
    provider_id = "derived"


class BuiltinProviderAPI(CompositeProviderAPI):
    provider_id = "builtin"


_BINDING_TYPES: tuple[type[ChannelBindings], ...] = (
    TdxProviderAPI,
    LocalVipdocProviderAPI,
    TencentProviderAPI,
    SinaProviderAPI,
    EastmoneyProviderAPI,
    BaiduProviderAPI,
    JslProviderAPI,
    BocProviderAPI,
    IwencaiProviderAPI,
    DerivedProviderAPI,
    BuiltinProviderAPI,
)


def _build_channel_bindings(
    api_types: Sequence[type[ChannelBindings]],
) -> dict[str, type[ChannelBindings]]:
    """Build and validate the channel declaration contract from the Registry."""

    mapping: dict[str, type[ChannelBindings]] = {}
    for api_type in api_types:
        provider_id = api_type.provider_id
        if provider_id is None:
            raise RuntimeError(f"Channel binding type {api_type.__name__} has no provider_id")
        pid = resolve_provider(provider=provider_id)
        if pid in mapping:
            raise RuntimeError(f"duplicate channel binding type for Provider {pid!r}")
        mapping[pid] = api_type

    registered_ids = set(PROVIDERS.ids())
    if set(mapping) != registered_ids:
        missing = sorted(registered_ids - set(mapping))
        extra = sorted(set(mapping) - registered_ids)
        raise RuntimeError(
            "Provider channel binding registry mismatch: "
            f"missing bindings for {missing}, unknown Providers {extra}"
        )

    for pid, api_type in mapping.items():
        if api_type.CHANNEL_API_EXEMPT:
            continue
        spec = PROVIDERS.get(pid)
        expected = {channel.id for channel in spec.channels if not channel.local}
        declared = (
            set(api_type.CHANNELS) | set(api_type.EXPLICIT_CHANNELS) | set(api_type.DIRECT_CHANNELS)
        )
        if declared != expected:
            missing = sorted(expected - declared)
            extra = sorted(declared - expected)
            raise RuntimeError(
                f"Channel binding contract mismatch for Provider {pid!r}: "
                f"missing={missing} undeclared={extra}"
            )
    return mapping


_CHANNEL_BINDINGS_BY_PROVIDER: dict[str, type[ChannelBindings]] = _build_channel_bindings(
    _BINDING_TYPES
)


def resolve_channel_adapter(provider: str, channel: str) -> type[Any]:
    """Return the adapter class bound to one Provider channel."""

    pid = resolve_provider(provider=provider)
    cid = str(channel).strip().lower()
    channels: dict[str, AdapterRef] = _CHANNEL_BINDINGS_BY_PROVIDER[pid].CHANNELS
    try:
        module_name, class_name = channels[cid]
    except KeyError as exc:
        raise ValidationError(
            f"Provider {pid!r} 的 channel {cid!r} 没有 adapter 引用",
            context={
                "provider": pid,
                "channel": cid,
                "phase": "channel_binding",
                "fallback": False,
                "provider_switch_allowed": False,
            },
        ) from exc
    return getattr(importlib.import_module(module_name), class_name)
