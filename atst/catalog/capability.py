# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""v13 migrated capability catalog.

The retired UnifiedQuoteAPI is not restored. Every migrated business ability is
assigned to one explicit Provider/Channel and one low-level implementation.
Historical helpers that internally select or aggregate upstream services are
honestly represented by the explicit ``derived`` Provider.
"""

from __future__ import annotations

import functools
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
    "capability_status",
    "capability_statuses",
    "MIGRATED_CAPABILITY_STATUS",
    "CAPABILITY_STATUSES",
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
    #: ``"atst.web.tencent.adapters:MinuteSource"``. Only that backend reads it,
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
    # 缺口补全能力（G-01~G-13）的 Provider 归属
    "trade_calendar": "builtin",
    "st_list": "eastmoney",
    "equity_pledge": "eastmoney",
    "interactive_qa": "cninfo",
    "news_broadcast": "eastmoney",
    "valuation_history": "eastmoney",
    "sw_industry": "eastmoney",
    "sw_industry_history": "eastmoney",
    "etf_shares": "eastmoney",
    "risk_scan": "eastmoney",
    "index_valuation": "eastmoney",
    "futures_position_rank": "eastmoney",
    "options_position_rank": "eastmoney",
    "macro_social_financing": "eastmoney",
    "macro_pmi": "eastmoney",
    "macro_lpr": "eastmoney",
    "macro_bond_yield": "eastmoney",
    "macro_repo_rate": "eastmoney",
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
    # cninfo 同 baidu/iwencai/builtin 惯例：该系能力（interactive_qa）是
    # web_session 后端的**无源 @staticmethod**（内部直连 irm.cninfo.com.cn），
    # 会话源只是名义值，必须落在 SOURCE_ALIASES 之内，否则 executor 以
    # source='cninfo' 构造 WebQuoteSession 时直接 CompatibilityError（E8000）。
    "cninfo": "sina",
    "builtin": "sina",
}
#: Capabilities whose canonical registry channel is *not* the generic ``catalog``
#: channel — the registry is the single source of truth for
#: ``(provider, channel, capability)``, so the catalog must bind to the same home
#: or the plan compiled by :class:`~atst.query.QueryPlanner` could never be
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
#:   它的权威在 :data:`atst.runtime.executor.DEDICATED_CAPABILITIES`（从执行体表派生）；catalog
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
        "fetch_minute",
        factory="atst.web.tencent.adapters:MinuteSource",
    ),
    MigratedCapabilityBinding(
        "minute_klines",
        "tencent",
        "catalog",
        "web_adapter",
        "fetch_bars",
        factory="atst.web.tencent.adapters:MinuteKlineSource",
    ),
    MigratedCapabilityBinding(
        "minute_klines",
        "eastmoney",
        "catalog",
        "web_adapter",
        "fetch_bars",
        factory="atst.web.eastmoney.adapters:EastmoneyHistoryKlineSource",
    ),
    MigratedCapabilityBinding(
        "history",
        "sina",
        "catalog",
        "web_adapter",
        "fetch_bars",
        factory="atst.web.sina.adapters:SinaHistoryKlineSource",
    ),
    MigratedCapabilityBinding(
        "history",
        "eastmoney",
        "catalog",
        "web_adapter",
        "fetch_bars",
        factory="atst.web.eastmoney.adapters:EastmoneyHistoryKlineSource",
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
    # V6 L3 新增 3 个 Web Provider（cninfo / ths / wallstreet）
    #
    # 收录纪律：这里每一条都必须是**真机抓包验证过**的端点。交易所（sse/szse）与
    # 中债（chinamoney）两个源在 2026-10 实测全部不可达（404 / 500 / data=null），
    # 因此**没有**登记——宁可少一个 Provider，也不留「有名字、发不出数据」的死能力。
    # cninfo 巨潮：法定披露公告（POST 表单，column 决定板块）
    MigratedCapabilityBinding(
        "announcements",
        "cninfo",
        "catalog",
        "web_adapter",
        "fetch_announcements",
        factory="atst.web.cninfo.adapters:CninfoSource",
    ),
    MigratedCapabilityBinding(
        "hk_announcements",
        "cninfo",
        "catalog",
        "web_adapter",
        "fetch_hk_announcements",
        factory="atst.web.cninfo.adapters:CninfoSource",
    ),
    # ths 同花顺：涨停池 / 板块归属 / 概念成分 / 人气榜
    MigratedCapabilityBinding(
        "limit_pool",
        "ths",
        "catalog",
        "web_adapter",
        "fetch_limit_pool",
        factory="atst.web.ths.adapters:ThsSource",
    ),
    MigratedCapabilityBinding(
        "theme_attribution",
        "ths",
        "catalog",
        "web_adapter",
        "fetch_theme_attribution",
        factory="atst.web.ths.adapters:ThsSource",
    ),
    MigratedCapabilityBinding(
        "concept_members",
        "ths",
        "catalog",
        "web_adapter",
        "fetch_concept_members",
        factory="atst.web.ths.adapters:ThsSource",
    ),
    MigratedCapabilityBinding(
        "hot_rank",
        "ths",
        "catalog",
        "web_adapter",
        "fetch_hot_rank",
        factory="atst.web.ths.adapters:ThsSource",
    ),
    # wallstreet 华尔街见闻：全球快讯流（仅 global-channel 稳定返回）
    MigratedCapabilityBinding(
        "breaking_news",
        "wallstreet",
        "catalog",
        "web_adapter",
        "fetch_breaking_news",
        factory="atst.web.wallstreet.adapters:WallstreetSource",
    ),
)


#: v14 registry channels whose capabilities map 1:1 onto a same-named
#: :class:`~atst.web.session.WebQuoteSession` method. These are the *semantic
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
#: single :data:`atst.catalog.provider_bindings` ``CHANNELS`` table at dispatch time, so this
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

#: 能力健康度的**显式叠加**（G-16）。只登记注册表表达不出的健康度：
#:
#: * ``offline``：tdx 命令账本判定的死名字（``auction`` / ``block_quotes`` /
#:   ``minute_history`` / ``volume_price``，见 ``docs/api/interfaces.md``
#:   「能力发现面」）。注册表的 ``unavailable_capabilities`` 只标了
#:   ``security_list_all``，覆盖不到这四个，故在此显式登记。
#: * ``needs_verify``：缺口补全能力（G-01~G-13）的在线端点待真机抓包校准。
#:
#: 「声明了、但每个归属 channel 都下线」的能力由
#: :func:`_registry_offline_capabilities` **派生**（不在此重复登记）；其余默认 ``alive``。
#: 状态取值见 :data:`CAPABILITY_STATUSES`。
MIGRATED_CAPABILITY_STATUS: dict[str, str] = {
    # G-15：tdx 账本 offline 的 4 个死名字（一调即抛 CommandOffline）
    "auction": "offline",
    "volume_price": "offline",
    "block_quotes": "offline",
    "minute_history": "offline",
    # G-02~G-13 缺口能力：端点已真机验证的标 alive；仍未抓包校准的标
    # needs_verify（端点失效时基座统一抛 SourceDeprecated / WebSourceError，
    # 干净失败，不崩溃、不静默返回空）。
    # -- 已验证（2026-10-02 真机请求实测出数）：
    "st_list": "alive",  # push2 clist b:BK0511（风险警示板成分）
    "equity_pledge": "alive",  # RPT_CSDC_LIST（中证登质押比例明细）
    "valuation_history": "alive",  # RPT_VALUEANALYSIS_DET（个股估值历史）
    "etf_shares": "alive",  # push2 clist f38（ETF 最新份额）
    "risk_scan": "alive",  # RPT_GOODWILL_STOCKDETAILS + RPT_CSDC_LIST 复合
    "macro_pmi": "alive",  # RPT_ECONOMY_PMI
    "macro_lpr": "alive",  # RPTA_WEB_RATE
    "macro_bond_yield": "alive",  # RPTA_WEB_TREASURYYIELD（中/美国债收益率）
    "macro_social_financing": "alive",  # data.mofcom.gov.cn 社融月度
    "macro_repo_rate": "alive",  # chinamoney frr/fdr CSV（回购定盘利率）
    # -- 待校准（端点未找到公开真名或真机不可达，needs_verify）：
    "interactive_qa": "needs_verify",  # irm.cninfo.com.cn（沙箱不可达）
    "news_broadcast": "needs_verify",  # content-api.cctv.com（沙箱不可达）
    "sw_industry": "needs_verify",  # 东财无公开申万分类报表
    "sw_industry_history": "needs_verify",
    "index_valuation": "needs_verify",  # RPT_VALUEANALYSIS_DET 不含指数
    "futures_position_rank": "needs_verify",  # 走 futsseapi 面板，需新适配器
    "options_position_rank": "needs_verify",
}

#: 状态枚举（用于校验与文档生成）。
CAPABILITY_STATUSES = ("alive", "degraded", "offline", "needs_verify")


@functools.lru_cache(maxsize=1)
def _registry_offline_capabilities() -> frozenset[str]:
    """注册表派生：声明了、但**每个**归属 channel 都标 ``unavailable`` 的能力。

    ``ChannelSpec.unavailable_capabilities`` 是「声明了但已下线」的既有事实源
    （``operationally_supports()`` 读它做路由）。能力健康度不另立第二份下线名单，
    而是从这里派生——``minute`` 只在 tdx 下线、在腾讯/百度/东财仍在线，故不是
    ``offline``；``security_list_all`` 只归属 tdx，故判 ``offline``。
    """
    from ..providers import PROVIDERS

    unavailable_by_cap: dict[str, list[bool]] = {}
    for pid in PROVIDERS.ids():
        for channel in PROVIDERS.get(pid).channels:
            for cap in channel.capabilities:
                unavailable_by_cap.setdefault(cap, []).append(
                    cap in channel.unavailable_capabilities
                )
    return frozenset(cap for cap, flags in unavailable_by_cap.items() if flags and all(flags))


def capability_status(capability: str) -> str:
    """返回单个迁移能力的状态；能力不存在抛 ``KeyError``。

    优先级：显式叠加（:data:`MIGRATED_CAPABILITY_STATUS`）→ 注册表派生的
    ``offline``（:func:`_registry_offline_capabilities`）→ ``"alive"``。
    标注值必须落在 :data:`CAPABILITY_STATUSES` 之内（写错状态串在这里当场报，
    而不是把一句错话一路发到四面出口）。
    """
    cap = str(capability).strip().lower()
    if cap not in MIGRATED_CAPABILITIES:
        raise KeyError(capability)
    status = MIGRATED_CAPABILITY_STATUS.get(cap)
    if status is None:
        status = "offline" if cap in _registry_offline_capabilities() else "alive"
    if status not in CAPABILITY_STATUSES:
        raise ValueError(f"迁移能力 {cap!r} 的状态 {status!r} 不在 {CAPABILITY_STATUSES} 之内")
    return status


def capability_statuses() -> dict[str, str]:
    """返回 ``{capability: status}``，覆盖全部迁移能力（默认 ``alive``）。"""
    return {cap: capability_status(cap) for cap in sorted(MIGRATED_CAPABILITIES)}


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
    :class:`~atst.runtime.executor.DirectProviderExecutor` read the host class
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
            "provider",
            "event_source",
        },
        "sync_daily": {"root", "profile", "chunk", "max_windows"},
    }[capability]
    unknown = sorted(set(kwargs) - allowed)
    if unknown:
        raise TypeError(f"unexpected keyword argument(s): {', '.join(unknown)}")


#: 全市场口径的 web_adapter 能力：**不接受**标的。快讯这类数据上游根本没有按标的
#: 分流的频道，传 symbol 只能是调用方误解，在契约层就拒绝而不是静默忽略。
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
