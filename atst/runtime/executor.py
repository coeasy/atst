# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""Exact Provider/Channel execution bindings for the canonical v13 runtime."""

from __future__ import annotations

import inspect
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any

from ..catalog.capability import binding_for, call_kwargs_for, implementation_for, validate_call
from ..config import Config
from ..diagnostics import WarningCode, record_warning, warning_sink
from ..domain.symbol import normalize_symbol
from ..errors import InternalError, TdxError, TruncatedDataError, ValidationError
from ..providers import PROVIDERS
from ..query import QueryPlan
from ..result import Provenance, QueryResult
from .audit import audit_runtime
from .freshness import verify_currentness

__all__ = [
    "DEDICATED_CAPABILITIES",
    "DirectBinding",
    "DirectProviderExecutor",
    "DIRECT_BINDINGS",
    "audit_direct_bindings",
]


@dataclass(frozen=True, slots=True)
class DirectBinding:
    provider: str
    channel: str
    capability: str
    executor_name: str

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.provider, self.channel, self.capability)


#: Dedicated executors follow three stable rules — no hard-coded registry
#: tuple required. See Phase 2a of the V20 refactor.
_WEB_DEDICATED_PROVIDERS = frozenset({"tencent", "sina", "eastmoney", "baidu"})


def _executor_for(provider: str, channel: str, capability: str) -> str:
    """Determine which executor method handles a (provider, channel, capability) triple.

    Rules cover all 17 dedicated bindings:
      - ``tdx/quotation`` → ``_tdx_<capability>`` for the 7 dedicated quote-capabilities
      - ``local_vipdoc/vipdoc/bars`` → ``_local_bars``
      - web providers' quote channel → ``_web_quotes``
      - web providers' kline/history_kline/minute_kline channels → ``_<provider>_bars``
    Everything else falls through to ``_migrated_capability``.
    """
    if provider == "tdx" and channel == "quotation":
        if capability in {
            "quotes",
            "bars",
            "snapshot",
            "minute",
            "trades",
            "security_count",
            "security_list",
        }:
            return f"_tdx_{capability}"
    elif provider == "local_vipdoc" and channel == "vipdoc":
        if capability == "bars":
            return "_local_bars"
    elif provider in _WEB_DEDICATED_PROVIDERS:
        if capability == "quotes" and channel == "quote":
            return "_web_quotes"
        if capability == "bars" and channel in {"kline", "history_kline", "minute_kline"}:
            return f"_{provider}_bars"
    return "_migrated_capability"


def _registry_triples() -> tuple[tuple[str, str, str], ...]:
    return tuple(
        (provider, channel.id, capability)
        for provider in PROVIDERS.ids()
        for channel in PROVIDERS.get(provider).channels
        for capability in sorted(channel.capabilities)
    )


DIRECT_BINDINGS = tuple(
    DirectBinding(provider, channel, capability, _executor_for(provider, channel, capability))
    for provider, channel, capability in _registry_triples()
)

#: 有内核专属执行体的 capability——「这条能力走专用路径」的唯一判据。
#: ``Client`` 的便捷方法面、``Client.call`` 的核心分派、三张 wire 面的专用入口以前
#: 各自抄一份同名清单；抄到第四份时，清单与执行体表已经可以各说一套而无人报警。
DEDICATED_CAPABILITIES: frozenset[str] = frozenset(
    binding.capability
    for binding in DIRECT_BINDINGS
    if binding.executor_name != "_migrated_capability"
)


def _is_unified_reachable(provider: str, channel: str, capability: str) -> bool:
    """这个三元组会不会真的被统一执行面派发。

    判据只取注册表自己的数据：``bars`` 的 canonical channel 随周期分家，而这条
    事实的唯一持有者是 :func:`atst.query._canonical_unified_channel`。所以"可达"
    = 这个 channel **声明的任一周期** 的 canonical 就是它本身。早先这里写死过
    ``tencent → {kline, minute_kline}``，等于把同一个业务事实在第二个地方抄一遍
    （F-27/F-49 那一族"影子规则"的现存样本）。
    """
    from ..query import _canonical_unified_channel

    declared = next((item for item in PROVIDERS.get(provider).channels if item.id == channel), None)
    periods = sorted(declared.periods) if declared is not None and declared.periods else [""]
    canonical = {_canonical_unified_channel(provider, capability, period) for period in periods}
    if channel in canonical:
        return True
    if canonical != {None}:
        return False
    owners = [item.id for item in PROVIDERS.get(provider).channels_for(capability)]
    return len(owners) == 1 and channel in owners


def audit_direct_bindings() -> tuple[DirectBinding, ...]:
    """绑定表自查：重复即红，"统一可达却没有执行元数据"也即红。

    后者原先只 ``warnings.warn``（同文件的注册表对账却是 raise）。一软一硬的差别
    不是分寸而是成败：pytest 收集告警却不因它失败，于是这条缺陷在 CI 上永远看不见，
    只有读日志的人能发现。判据既然写得出来，就该按它决定要不要放行。
    """
    seen: set[tuple[str, str, str]] = set()
    unroutable: list[tuple[str, str, str]] = []
    for binding in DIRECT_BINDINGS:
        if binding.key in seen:
            raise RuntimeError(f"duplicate Direct binding: {binding.key!r}")
        seen.add(binding.key)
        if binding.executor_name != "_migrated_capability":
            continue
        if not _is_unified_reachable(*binding.key):
            continue
        try:
            binding_for(*binding.key)
        except KeyError:
            unroutable.append(binding.key)
    if unroutable:
        raise RuntimeError(
            f"{len(unroutable)} 个统一可达的 Direct binding 缺少 migrated-capability "
            f"执行元数据（例如 {unroutable[0]!r}）：运行时按 binding 表派发，"
            f"capability catalog 却不认这个三元组，两侧必须补齐而不是带着缺口上线"
        )
    return DIRECT_BINDINGS


def _strict_requested(plan: QueryPlan) -> bool:
    """``options["strict"]``：把"结果带瑕疵"从一条告警升级为一次失败。

    它刻意读执行器收集到的告警，而不是逐个 Provider 转发各自的 ``strict`` 形参：
    一个开关对六种 bars 后端、对全部瑕疵类别同时成立，不存在"某个后端收下却无人执行"
    的第三种下场（F-43/F-46 用的是同一条判据）。
    """
    strict = plan.spec.options.get("strict", False)
    if not isinstance(strict, bool):
        raise ValidationError(
            "options['strict'] 必须是 bool",
            context={"strict_type": type(strict).__name__},
        )
    return strict


#: 语义字段 → 实现形参的唯一改名表。``QuerySpec`` 用复数存"一批代码"，单次实现的
#: 形参按单数收；除这一格外两边同名，所以这座桥只需要一条改名。加一格之前必须先有
#: 实现真的收那个名字，否则 ``tests/architecture/test_semantic_payload_bridge.py`` 即红。
_SEMANTIC_FIELD_RENAMES: Mapping[str, str] = MappingProxyType({"symbols": "symbol"})

#: 会替调用方说话、因而必须抵达实现的 spec 字段。其余字段（``capability`` /
#: ``provider`` / ``channel`` / ``currentness`` / ``deadline_ms`` /
#: ``schema_version`` / ``options``）是**规划**输入，不是实现入参。
_SEMANTIC_CALL_FIELDS: tuple[str, ...] = ("symbols", "period", "count", "start", "adjustment")

#: 复权因子是按**事件日**定位的（:mod:`atst.domain.adjust` 的累乘链以日期为索引），
#: 所以只有"每根 bar 都能落到一个明确日历日"的周期才有复权语义。分钟级周期在同一天里
#: 有多根 bar、无法区分事件前后，因此不支持——宁可当场拒绝，也不返回看起来复过权的假数据。
_ADJUSTABLE_PERIODS: frozenset[str] = frozenset({"day", "week", "month"})

#: 复权事件的两个来源。**默认走东财**：``0x000F``（TDX 除权除息）的记录布局至今没有
#: 真机 golden 锁定，用它复权只会得到错位因子；它是显式 opt-in，且要过
#: :func:`atst.domain.finance.reject_implausible_changes` 那道闸。
_ADJUST_EVENT_SOURCES: frozenset[str] = frozenset({"eastmoney", "tdx"})

#: 复权事件分页。``dividend_history`` 的 ``size`` 默认是 **20**，而长历史标的实有
#: 更多（600519 实测 28 条）。过去 ``_adjusted_events`` 不传 size，早期除权事件被
#: 静默截断——因子少乘一段却不报错，是这条链路里最危险的一类错。这里显式翻页到尽。
_ADJUST_EVENT_PAGE_SIZE: int = 100
#: 1000 条已是 A 股单只标的除权事件数的量级上界（1990 年至今每年最多 1~2 次），
#: 触顶说明上游在异常翻页，宁可告警也不无限翻。
_ADJUST_EVENT_MAX_PAGES: int = 10

#: 后复权/定点复权的因子是从**史上第一个事件**起累乘的。若最早的事件早于 K 线窗口
#: 起点，那批事件拿不到各自的前收盘价，只能走 ``1/(1+S+R)`` 的降级口径——因子于是
#: **随请求的 ``count`` 变化**：同一根 bar 在短窗口和长窗口下复权结果不同。这既不可
#: 复现，也是静默错（拿 short 窗口做回测，早期因子的偏差会被当成"真实价格"）。
#: 这里把取数窗口向后延伸，直到盖住最早的事件或触到历史尽头。
#: 增长用倍数（不是线性 +N 步进）是为了把请求数压在对数量级：320 → 1280 → 5120 → 20480。
_ADJUST_WINDOW_GROWTH: int = 4
#: 1990 年开市至今单只标的的日线根数量级上界（约 8600 个交易日），留一倍余量作硬顶。
_ADJUST_HISTORY_MAX: int = 20_000

#: ``daily_enriched`` 的复权方式。定点复权要 anchor_date，宽表是"一段区间"的产物，
#: 没有单一锚点语义，因此只开放三种。
_ENRICH_ADJUSTMENTS: frozenset[str] = frozenset({"none", "qfq", "hfq"})
#: 估值序列（``RPT_VALUEANALYSIS_DET``）的单页硬上限：**500 个交易日**。
#: 超过就得用 ``start`` 偏移分段取——静默截断会让老行的估值列变成 None，
#: 调用方看不出那是"没有"还是"没取到"，所以这里直接拒绝。
_ENRICH_MAX_COUNT: int = 500


def _day_in_range(day: str, start_date: str, end_date: str) -> bool:
    """闭区间判定。``day`` 取不到时**保留**该行：裁不掉的行宁可留着让调用方看见，
    也不要静默丢掉一条它没法解释的数据。"""
    if not day:
        return True
    if start_date and day < start_date:
        return False
    return not (end_date and day > end_date)


def _semantic_call_payload(plan: QueryPlan, meta: Any) -> tuple[list[Any], dict[str, Any]]:
    """把 QuerySpec 的语义字段绑定成实现自己的关键字入参。

    核心能力的便捷路径不做 raw payload 约定：``Client.minute("000001",
    provider="tencent")`` 把代码写在 ``spec.symbols`` 里，执行器在这里同时握着执行
    元数据与实现签名，一绑就是调用方的入参。缺了这一步，声明了该能力的 Web Provider
    格子会在规划通过之后、任何 I/O 之前撞上 ``missing a required argument:
    'symbol'``——契约写在文档里、链路却是断的（第 30 轮实测）。
    """
    impl = implementation_for(plan.provider, plan.channel, plan.spec.capability)
    if impl is None:
        #: ``web_adapter`` / ``composed`` 按 capability 在执行体内部岔开，没有一份签名
        #: 可绑，所以只认 raw payload。空 payload 会由 ``validate_call`` 报成契约错误，
        #: 不会静默执行。
        return [], {}
    params = inspect.signature(impl).parameters
    accepts_kwargs = any(item.kind is inspect.Parameter.VAR_KEYWORD for item in params.values())
    kwargs: dict[str, Any] = {}
    for name in _SEMANTIC_CALL_FIELDS:
        value = getattr(plan.spec, name)
        if not value:
            #: 留空与写默认值在这一层不可区分，两者都不进 kwargs：实现自己的默认值
            #: 才是默认值，这里替调用方抄一份就是第二份口径。
            continue
        target = _SEMANTIC_FIELD_RENAMES.get(name, name)
        if name == "symbols" and target == "symbol":
            if len(value) != 1:
                raise ValidationError(
                    f"provider {plan.provider!r} channel {plan.channel!r} 的 "
                    f"{plan.spec.capability!r} 按单只代码执行，本次查询给了 {len(value)} 只",
                    context={
                        "provider": plan.provider,
                        "channel": plan.channel,
                        "capability": plan.spec.capability,
                        "phase": "semantic_payload",
                        "symbols": len(value),
                    },
                )
            kwargs[target] = str(value[0])
            continue
        if target not in params and not accepts_kwargs:
            #: 设了却没有落脚点的字段必须当场失败：一个"看起来生效"的开关比没有开关
            #: 更糟（``max_age`` 的同一条判据，见 ``REJECTED_OPTIONS``）。
            raise ValidationError(
                f"语义字段 {name!r} 在 {plan.provider!r}/{plan.channel!r} 的 "
                f"{plan.spec.capability!r} 实现里没有落脚点",
                context={
                    "provider": plan.provider,
                    "channel": plan.channel,
                    "capability": plan.spec.capability,
                    "phase": "semantic_payload",
                    "implementation": getattr(impl, "__qualname__", repr(impl)),
                    "accepted": sorted(params),
                },
            )
        kwargs[target] = list(value) if name == "symbols" else value
    return [], kwargs


class DirectProviderExecutor:
    def __init__(
        self,
        *,
        timeout: float = 5.0,
        hosts: Sequence[Any] | None = None,
        vipdoc_root: str | None = None,
        config: Config | None = None,
    ) -> None:
        self.timeout = float(timeout)
        self.hosts = hosts
        self.vipdoc_root = vipdoc_root
        #: 单一内核解析后的配置；仅用于把 timeout/重试/槽位/限流/TLS
        #: 贯通到传输层，不引入任何缓存或降级语义。
        self.config = config
        audit_runtime()
        self._bindings = {item.key: item for item in DIRECT_BINDINGS}

    def execute(self, plan: QueryPlan) -> QueryResult[Any]:
        key = (plan.provider, plan.channel, plan.spec.capability)
        try:
            binding = self._bindings[key]
        except KeyError as exc:
            raise ValidationError(
                "QueryPlan 没有可执行 Direct Provider binding",
                context={
                    "provider": plan.provider,
                    "channel": plan.channel,
                    "capability": plan.spec.capability,
                    "fallback": False,
                    "provider_switch_allowed": False,
                },
            ) from exc

        fn = getattr(self, binding.executor_name)
        #: 非法的 strict 必须在任何 I/O 之前拒绝，而不是等一次成功的查询再报错。
        strict = _strict_requested(plan)
        #: 一次查询一份告警收集器：Provider 侧记录的任何数据完整性瑕疵都在这个块里
        #: 落进 ``collected``，随后随结果出发（F-45）。
        with warning_sink() as collected:
            #: currentness 的判据先于 I/O：无法证明的契约不该先用一次请求去换一条告警。
            verify_currentness(plan, strict=strict)
            try:
                data = fn(plan)
                #: 日期区间在取回的那一页上做过滤，并当场回答"这一页盖不盖得住区间"。
                data = self._apply_bar_range(plan, data)
            except TdxError as exc:
                exc.context.setdefault("provider", plan.provider)
                exc.context.setdefault("channel", plan.channel)
                exc.context.setdefault("capability", plan.spec.capability)
                exc.context.setdefault("fallback", False)
                exc.context.setdefault("provider_switch_allowed", False)
                raise
            except Exception as exc:
                raise InternalError(
                    "Direct Provider executor 未处理异常",
                    context={
                        "provider": plan.provider,
                        "channel": plan.channel,
                        "capability": plan.spec.capability,
                        "fallback": False,
                        "provider_switch_allowed": False,
                        "cause_type": type(exc).__name__,
                    },
                    cause=exc,
                ) from exc

        caveats = tuple(collected)
        if caveats and strict:
            raise TruncatedDataError(
                "strict=True 且本次结果携带数据完整性瑕疵：拒绝返回带瑕疵的数据",
                context={
                    "provider": plan.provider,
                    "channel": plan.channel,
                    "capability": plan.spec.capability,
                    "codes": sorted({item.code.value for item in caveats}),
                    "messages": [item.message for item in caveats],
                    "fallback": False,
                    "provider_switch_allowed": False,
                },
            )

        return QueryResult.from_plan(
            data,
            plan=plan,
            provenance=Provenance.direct(plan),
            warnings=caveats,
        )

    def _hop_timeout(self, plan: QueryPlan) -> float:
        """本次请求的 socket 超时上界：配置值与总 deadline **剩余预算**取小。

        ``deadline_ms`` 由 ``QueryPlanner`` 折算成 ``plan.budget``。在接上这一跳之前，
        预算只有构造处、没有读取处——调用方设的超时上界在执行面上不存在（与已删除的
        ``max_age`` 同形）。这里取小而非替换：预算耗尽时按 ``deadline_ms`` 立刻失败，
        预算宽于配置时仍按配置，默认两者同为 5 秒，故默认路径逐字节不变。
        """
        budget = plan.budget
        budget.ensure_remaining("provider_request")
        return min(self.timeout, budget.remaining_s())

    def _pool_client(self, cls: Any, timeout: float) -> Any:
        """``TdxClient`` 家族的唯一构造处：主站与传输旋钮同源。

        执行器手里的 ``hosts`` 是本仓库唯一的「只碰调用方点名的主站」手段，``config`` 是
        ``[hosts] / [core] / [rate_limit] / [security]`` 翻译成传输层的唯一一份读数。两者都
        必须抵达家族里的**每一个**客户端：第 31 轮之前 F10 / 商品 / 扩展行情 / MAC 四条分支
        只传 ``timeout``，同一份配置在行情主链路生效、在那四条路上不存在（判据
        ``tests/architecture/test_client_family_transport.py``）。
        """

        from ..transport.pool import pool_settings_from_config

        #: 主站选择已由内核解析完毕；配置在此只提供传输层构造参数，
        #: 避免同一个键在两处解释。
        settings = pool_settings_from_config(self.config)
        settings["timeout"] = timeout
        return cls(self.hosts, **settings)

    def _tdx_client(self, timeout: float) -> Any:
        from ..client import TdxClient

        return self._pool_client(TdxClient, timeout)

    @contextmanager
    def _client_session(self, client: Any) -> Iterator[Any]:
        """执行器交出可关闭对象时唯一那道保护区：块体怎么下场都 ``close()``。

        31-B4 把 ``with client`` 换成 try/finally 时只收了四条迁移分支，核心链路当时还留着
        九处 ``with self._tdx_client(...) as client:``——同一份文件对同一个风险给两种答案，
        本轮第 3 遍按族清点后一次收口（外加 ``web_session`` 那条本就在保护区里的分支，一并
        折进来，全执行器 13 处使用点只剩一种形状）。区别只在 ``__enter__`` 落在哪里：
        ``_pool_client`` 交回来的对象**已经**带着连接池与心跳线程（同步池在 ``__init__``
        起跑），``with client`` 还要再走一次 ``client.__enter__()``（即 ``open()``），而那一步
        不归 try 管。今天 ``open()`` 在 ``bestip=False`` 下只 ``return self``，所以那九处并非
        当场在漏；但一个叫"建连"的方法哪天真握手一次，九处就同时变成九个漏点。判据：
        ``tests/architecture/test_client_family_transport.py`` 的结构尺——执行器里 ``with``
        直接包 ``_tdx_client``/``_pool_client``/``WebQuoteSession`` 即为红，保护区只有这一处。
        """
        try:
            yield client
        finally:
            client.close()

    @staticmethod
    def _call_payload(plan: QueryPlan, meta: Any) -> tuple[list[Any], dict[str, Any]]:
        options = plan.spec.options
        if "args" not in options and "kwargs" not in options:
            #: 没有 raw payload 约定 = 这条查询走的是语义路径（``Client.minute`` 一类
            #: 便捷方法、typed query），请求写在 spec 的语义字段里。
            return _semantic_call_payload(plan, meta)
        args = options.get("args", [])
        kwargs = options.get("kwargs", {})
        if not isinstance(args, list) or not isinstance(kwargs, dict):
            raise ValidationError("migrated capability options 必须包含 args:list / kwargs:object")
        return args, dict(kwargs)

    def _migrated_capability(self, plan: QueryPlan) -> Any:
        meta = binding_for(plan.provider, plan.channel, plan.spec.capability)
        args, kwargs = self._call_payload(plan, meta)
        validate_call(
            plan.provider,
            plan.channel,
            plan.spec.capability,
            tuple(args),
            kwargs,
        )
        #: 一次逻辑请求只有一份预算读数：下面每个后端的 socket 超时都取它。
        hop = self._hop_timeout(plan)

        if meta.backend == "web_session":
            from ..web.session import WebQuoteSession

            with self._client_session(
                WebQuoteSession(meta.source or "sina", timeout=hop)
            ) as session:
                return getattr(session, meta.method)(
                    *args, **call_kwargs_for(meta, plan.provider, kwargs)
                )

        if meta.backend == "tdx_client":
            with self._client_session(self._tdx_client(hop)) as client:
                if meta.capability == "quotes_concurrent":
                    kwargs.setdefault("as_format", "obj")
                return getattr(client, meta.method)(*args, **kwargs)

        if meta.backend == "f10_client":
            from ..client import F10Client

            with self._client_session(self._pool_client(F10Client, hop)) as client:
                if meta.capability == "f10":
                    return client.parse_text(client.download(*args, **kwargs))
                return list(client.catalog(*args, **kwargs))

        if meta.backend in {"ex_client", "goods_client", "mac_client"}:
            from ..client import ExMarketClient, GoodsClient, MacClient

            family_cls = {
                "ex_client": ExMarketClient,
                "goods_client": GoodsClient,
                "mac_client": MacClient,
            }[meta.backend]
            with self._client_session(self._pool_client(family_cls, hop)) as client:
                if meta.capability in {
                    "ex_bars",
                    "ex_quotes",
                    "goods_bars",
                    "goods_quotes",
                    "mac_quotes",
                }:
                    kwargs.setdefault("as_format", "dict")
                return getattr(client, meta.method)(*args, **kwargs)

        if meta.backend == "direct_adapter":
            # The adapter class comes from the v14 ``CHANNELS`` table (single
            # home) rather than a second module/class table in the catalog.
            from ..catalog.provider_bindings import resolve_channel_adapter

            adapter_cls = resolve_channel_adapter(plan.provider, meta.channel)
            adapter = adapter_cls(timeout=hop)
            try:
                return getattr(adapter, meta.method)(*args, **kwargs)
            finally:
                close = getattr(adapter, "close", None)
                if callable(close):
                    close()

        if meta.backend == "web_adapter":
            return self._web_adapter_call(meta, args, kwargs, timeout=hop)
        if meta.backend == "composed":
            return self._composed_call(plan, meta.capability, args, kwargs, timeout=hop)

        raise ValidationError(
            "unknown migrated backend",
            context={"backend": meta.backend},
        )

    def _web_adapter_call(
        self,
        meta: Any,
        args: list[Any],
        kwargs: dict[str, Any],
        *,
        timeout: float,
    ) -> Any:
        # Provider-specific adapter selection was moved into catalog binding
        # metadata (``MigratedCapabilityBinding.factory``). If the factory
        # path is missing we treat it as a registry gap — every web_adapter
        # binding above ships one, and this executor no longer hard-codes
        # eastmoney-vs-sina branching.
        if not getattr(meta, "factory", ""):
            raise ValidationError(
                "web_adapter binding missing factory metadata",
                context={
                    "provider": getattr(meta, "provider", None),
                    "capability": getattr(meta, "capability", None),
                },
            )

        import importlib

        module_path, _, class_name = meta.factory.partition(":")
        adapter_cls = getattr(importlib.import_module(module_path), class_name)

        # 分派口径：``MigratedCapabilityBinding.method`` 对 ``web_adapter`` 后端
        # 就是**要调用的适配器方法名**（与 ``web_session`` / ``tdx_client`` 后端
        # 同一语义）。旧实现在此硬编码 ``fetch_bars``，导致任何非 K 线语义的
        # web_adapter 绑定（公告 / 快讯 / 人气榜）一调就 AttributeError——
        # 绑定表里明明写了入口，执行面却不去读它，是典型的「声明与执行分叉」。
        method_name = getattr(meta, "method", "") or "fetch_bars"
        adapter = adapter_cls(timeout=timeout)
        try:
            entry = getattr(adapter, method_name, None)
            if entry is None or not callable(entry):
                raise ValidationError(
                    "web_adapter 绑定指向的适配器方法不存在",
                    context={
                        "provider": getattr(meta, "provider", None),
                        "capability": getattr(meta, "capability", None),
                        "factory": getattr(meta, "factory", None),
                        "method": method_name,
                    },
                )
            #: 全市场类能力（快讯等）不传标的；旧实现裸取 ``args[0]`` 在无参调用时
            #: IndexError。``symbol`` 为空时整个位置参数都不传——那些方法的签名上
            #: 根本没有 ``symbol`` 形参（快讯无从按标的过滤），传 None 会 TypeError。
            symbol = args[0] if args else None
            if symbol is None:
                return entry(**kwargs)
            return entry(symbol, **kwargs)
        finally:
            close = getattr(adapter, "close", None)
            if callable(close):
                close()

    def _apply_bar_range(self, plan: QueryPlan, data: Any) -> Any:
        """按 ``start_date``/``end_date`` 裁一段 K 线，并说清楚这一页盖不盖得住区间。

        协议侧只有"从最新往回数 ``count`` 根"这一种游标，没有服务端日期区间参数。所以
        区间是在结果上裁的：请求了 ``start_date`` 而取回的最早一根仍晚于它，说明**区间
        的更早那一段根本没被取回来**——此时结果是一段被截断的区间，而不是"这些日期没有
        行情"。这两种说法对调用方完全是两回事，因此记一条
        :data:`~atst.diagnostics.WarningCode.BARS_RANGE_UNCOVERED`（``strict=True`` 时它
        是一次失败），让"看起来正常的部分结果"不再能冒充完整区间。
        """
        start_date = plan.spec.start_date
        end_date = plan.spec.end_date
        if not start_date and not end_date:
            return data
        if not isinstance(data, list) or not data:
            return data

        def day_of(row: Any) -> str:
            if isinstance(row, dict):
                return str(row.get("date") or row.get("datetime") or "")[:10]
            for name in ("date", "datetime"):
                value = getattr(row, name, None)
                if value:
                    return str(value)[:10]
            return ""

        kept = [row for row in data if _day_in_range(day_of(row), start_date, end_date)]
        if start_date and kept:
            days = [day for day in (day_of(r) for r in data) if day]
            #: 判据是"这一页有没有取到区间起点**之前**的 bar"，不是"最早一根是否等于
            #: 起点"——后者会把起点落在休市日上的正常请求误报成截断（2026-01-01 没有
            #: bar，但 2025-12-31 有，区间就是完整的）。
            reached_before_start = any(day < start_date for day in days)
            if not reached_before_start:
                record_warning(
                    WarningCode.BARS_RANGE_UNCOVERED,
                    f"请求区间自 {start_date} 起，但本次取回的 {len(data)} 根 K 线最早只到 "
                    f"{min(days) if days else '（无日期）'}，没有触及区间起点之前："
                    "结果是区间的一段而不是全部。请加大 count 或用 start 偏移继续往回取。",
                    stacklevel=2,
                )
        return kept

    def _adjusted_raw_bars(
        self,
        symbol: str,
        *,
        period: str,
        count: int,
        start: int,
        raw_provider: str,
        timeout: float,
    ) -> list[Any]:
        """取复权用的**未复权**原始 K 线：本地 vipdoc 优先，没有就走 Provider。

        过去的 ``adjusted_bars`` 只认 ``vipdoc_root``，没配本地数据的调用方拿到的就是一句
        ``requires vipdoc_root``——复权因此事实上只是个离线功能，TDX 在线用户用不了。
        这里把"原始 bar 从哪来"变成一次正常的单内核查询：

        * 日线且本地 vipdoc 有这份文件 → 读本地（不产生网络 I/O，口径与离线一致）；
        * 其余情况 → 用 :class:`~atst.query.QueryPlanner` 编一条 ``bars`` 计划并交给
          :meth:`execute`。**复用同一条主链路**而不是在这里另写一份 Provider 分支，
          于是复权自动跟随 Provider 的能力面（TDX 的 10 个周期、Web 三家的 ``adjust``…
          这里刻意不传），也不会出现"复权能取、bars 取不到"的分叉。
        """
        from ..domain.models import Bar
        from ..domain.symbol import split_symbol
        from ..query import QueryPlanner, QuerySpec
        from ..reader import DayBarReader

        if period == "day" and self.vipdoc_root:
            market, code = split_symbol(symbol)
            path = Path(self.vipdoc_root) / market / "lday" / f"{market}{code}.day"
            if path.exists():
                bars = DayBarReader().read(path, output="model")
                end = max(0, len(bars) - start)
                begin = max(0, end - count)
                return list(bars[begin:end])

        spec = QuerySpec.build(
            "bars",
            symbols=symbol,
            provider=raw_provider,
            period=period,
            count=count,
            start=start,
            currentness="historical",
        )
        rows = self.execute(QueryPlanner().compile(spec)).data
        return [row if isinstance(row, Bar) else Bar.from_dict(row) for row in rows]

    def _adjusted_events(
        self,
        symbol: str,
        event_source: str,
        *,
        timeout: float,
    ) -> list[Any]:
        """取复权事件。默认东财（已对拍），``tdx`` 需显式点名并过布局闸。"""
        from ..domain.finance import (
            reject_implausible_changes,
            to_capital_changes,
        )

        if event_source == "eastmoney":
            from ..diagnostics import WarningCode, record_warning
            from ..domain.finance import capital_changes_from_dividends_and_rights
            from ..query import QueryPlanner, QuerySpec

            def page_all(capability: str) -> list[Any]:
                """翻页到尽。``dividend_history`` 的 ``size`` 默认只有 20，
                长历史标的的早期事件会被静默截断（600519 实有 28 条）。"""
                collected: list[Any] = []
                for page in range(1, _ADJUST_EVENT_MAX_PAGES + 1):
                    batch = self.execute(
                        QueryPlanner().compile(
                            QuerySpec.build(
                                capability,
                                symbols=symbol,
                                provider="eastmoney",
                                currentness="historical",
                                options={
                                    "args": [str(symbol)],
                                    "kwargs": {
                                        "page": page,
                                        "size": _ADJUST_EVENT_PAGE_SIZE,
                                    },
                                },
                            )
                        )
                    ).data
                    batch = list(batch or [])
                    collected.extend(batch)
                    if len(batch) < _ADJUST_EVENT_PAGE_SIZE:
                        return collected
                #: 触顶不是"取完了"：最后一页仍是满页，说明还有没取到的事件。
                record_warning(
                    WarningCode.WEB_EASTMONEY_PAGE_LIMIT,
                    f"{symbol} 的 {capability} 在 {_ADJUST_EVENT_MAX_PAGES} 页内未取尽"
                    f"（每页 {_ADJUST_EVENT_PAGE_SIZE} 条）：已取 {len(collected)} 条，"
                    "更早期事件可能缺失。",
                    stacklevel=2,
                )
                return collected

            #: 两路合成：分红送转（RPT_SHAREBONUS_DET）+ 配股（RPT_IPO_ALLOTMENT）。
            #: 配股不补，除权价公式里的 ``Pr·R`` 恒为 0，配股标的的因子系统性偏小。
            return capital_changes_from_dividends_and_rights(
                page_all("dividend_history"),
                page_all("rights_issue"),
                code=str(symbol),
            )

        from ..domain.models import CapitalChange

        with self._client_session(self._tdx_client(timeout)) as client:
            raw = list(client.capital_changes(symbol))
        #: ``TdxClient.capital_changes`` 已经返回 :class:`CapitalChange` 模型（解析行在
        #: 客户端里就转过了），而 :func:`to_capital_changes` 吃的是**解析行 dict**。
        #: 过去这里无条件套一层转换，于是 ``event_source="tdx"`` 每次都炸在
        #: ``row.get(...)`` 上、被兜成 E9000 InternalError——一条只对内部说得通的错，
        #: 调用方根本读不出"这条路不通，换 eastmoney"。这里按类型分派，让错误回到
        #: 它该在的地方：布局闸给出的 ValidationError。
        events = list(raw) if raw and isinstance(raw[0], CapitalChange) else to_capital_changes(raw)
        #: 布局未锁定 → 错位记录不许进引擎（错位的复权比不复权危险得多）。
        reject_implausible_changes(events)
        return events

    def _extend_window_to_events(
        self,
        bars: list[Any],
        events: Sequence[Any],
        *,
        symbol: str,
        period: str,
        start: int,
        target: int,
        raw_provider: str,
        timeout: float,
    ) -> list[Any]:
        """把 K 线窗口向后延伸，直到盖住最早的复权事件（或触到历史尽头）。

        为什么必须有这一步：后复权因子是 ``∏ 1/k_d``，``d`` 遍历**全部**事件日——
        包括窗口之前的那些。而每个 ``k_d`` 都需要该事件日的**前收盘价**，它只能从
        该事件日之前那根 K 线拿到。窗口盖不住那些事件时，引擎只能走 ``1/(1+S+R)``
        的降级口径，于是**同一根 bar 在 ``count=5`` 与 ``count=2000`` 下复权结果不同**：
        实测某标的 hfq 因子 1.045 与 1.642 并存。这既不可复现，也是静默错——拿短窗口
        做回测，早期因子的偏差会被当成真实价格。

        延伸用**倍数增长**（``_ADJUST_WINDOW_GROWTH``）而非线性步进，把请求数压在
        对数量级；一旦某次取回的根数不再增加，说明已经到历史尽头，就此收手——剩下的
        事件真的没有更早的 K 线可依，引擎会照旧记 ``ADJUST_PREV_CLOSE_MISSING``。
        ``_ADJUST_HISTORY_MAX`` 是防御上限：真到那儿说明上游在异常应答，不再无限翻。
        """

        def first_day(rows: Sequence[Any]) -> str:
            if not rows:
                return ""
            return str(getattr(rows[0], "datetime", "") or "")[:10]

        event_days = [str(getattr(ev, "date", "") or "")[:10] for ev in events]
        event_days = [d for d in event_days if d]
        if not event_days:
            return bars
        earliest_event = min(event_days)

        def uncovered(rows: Sequence[Any]) -> bool:
            day = first_day(rows)
            return bool(day) and earliest_event < day

        if not uncovered(bars):
            return bars

        grown = target
        while grown < _ADJUST_HISTORY_MAX:
            grown = min(grown * _ADJUST_WINDOW_GROWTH, _ADJUST_HISTORY_MAX)
            try:
                candidate = self._adjusted_raw_bars(
                    symbol,
                    period=period,
                    count=grown,
                    start=start,
                    raw_provider=raw_provider,
                    timeout=timeout,
                )
            except Exception as exc:  # noqa: BLE001 - 延伸失败退回原窗口，但要留话
                #: 不能静默退回去：退回原窗口意味着早期事件拿不到前收盘价，因子会随
                #: 请求的 ``count`` 漂移（同一根 bar 短窗口/长窗口两个值）。调用方看到
                #: 的必须是"这次因子可能是漂移值"，而不是一个看着正常的数。
                record_warning(
                    WarningCode.ADJUST_WINDOW_EXTEND_FAILED,
                    f"{symbol} 的复权窗口向后延伸取数失败（{type(exc).__name__}: {exc}）；"
                    f"已退回最近 {len(bars)} 根。若最早事件（{earliest_event}）早于窗口起点，"
                    "它的前收盘价取不到，早期因子会随 count 变化。",
                    stacklevel=2,
                )
                return bars
            #: 根数不再增加 = 历史取完了。再往下翻只是重复同一段。
            if len(candidate) <= len(bars):
                return candidate or bars
            bars = list(candidate)
            if not uncovered(bars):
                break
        return bars

    def _composed_call(
        self,
        plan: QueryPlan,
        capability: str,
        args: list[Any],
        kwargs: dict[str, Any],
        *,
        timeout: float,
    ) -> Any:
        if capability == "adjusted_bars":
            from ..domain.adjust import AdjustEngine, AdjustMethod
            from ..domain.finance import to_capital_changes
            from ..domain.models import CapitalChange

            symbol = str(args[0])
            method = str(kwargs.pop("method", "qfq"))
            period = str(kwargs.pop("period", "day"))
            count = int(kwargs.pop("count", 320))
            start = int(kwargs.pop("start", 0))
            events = kwargs.pop("events", None)
            anchor_date = kwargs.pop("anchor_date", None)
            raw_provider = str(kwargs.pop("provider", "") or "").strip().lower() or "tdx"
            event_source = str(kwargs.pop("event_source", "") or "").strip().lower() or "eastmoney"
            if event_source not in _ADJUST_EVENT_SOURCES:
                raise ValidationError(
                    "adjusted_bars 不认识的 event_source",
                    context={
                        "event_source": event_source,
                        "supported": sorted(_ADJUST_EVENT_SOURCES),
                    },
                )
            if period not in _ADJUSTABLE_PERIODS:
                raise ValidationError(
                    "adjusted_bars 只支持按日期定位复权因子的周期",
                    context={"period": period, "supported": sorted(_ADJUSTABLE_PERIODS)},
                )

            #: 多取一根（窗口**前**一根）再裁掉：复权引擎拿它当最早那批除权事件的
            #: 「前收盘价」，否则每股现金红利被整项忽略（ADJUST_PREV_CLOSE_MISSING）。
            #: 调用方看到的根数不变。
            lead = 1 if count > 0 else 0
            want = count + lead
            bars = self._adjusted_raw_bars(
                symbol,
                period=period,
                count=want,
                start=start,
                raw_provider=raw_provider,
                timeout=timeout,
            )

            #: ``events`` 的三态必须分清：
            #:
            #: * ``None``（没给）→ 按 ``event_source`` 去取事件；
            #: * ``[]``（显式给了空表）→ **就是不调整**，不要偷偷去拉一份回来。
            #:   这里过去写的是 ``if not events:``，把 ``[]`` 和 ``None`` 混成一类，
            #:   于是 "我把事件集清空了" 被静默换成 "我去东财拉一份"——同一句话两种
            #:   结果，而且没有任何地方会说。
            #: * 非空 → 用调用方给的事件集（``CapitalChange`` 或等价 dict 行）。
            if events is None:
                events = self._adjusted_events(symbol, event_source, timeout=timeout)
            event_list = list(events)
            if event_list and not isinstance(event_list[0], CapitalChange):
                event_list = to_capital_changes(event_list)

            #: 后复权/定点复权要把**窗口之前**的事件也累乘进去，而那批事件需要各自的
            #: 前收盘价。窗口盖不住它们时只能走降级口径，因子随 count 漂移（不可复现）。
            #: 这里按倍数向后延伸取数窗口，直到盖住最早的事件或触到历史尽头；返回给
            #: 调用方的仍然只有它要的那 ``count`` 根。
            if method in (AdjustMethod.HFQ, AdjustMethod.FIXED) and event_list and bars:
                bars = self._extend_window_to_events(
                    bars,
                    event_list,
                    symbol=symbol,
                    period=period,
                    start=start,
                    target=want,
                    raw_provider=raw_provider,
                    timeout=timeout,
                )

            adjusted = AdjustEngine().apply(
                list(bars),
                event_list,
                method,
                anchor_date=anchor_date,
            )
            if count > 0 and len(adjusted) > count:
                return adjusted[len(adjusted) - count :]
            return adjusted

        if capability == "sync_daily":
            from ..sink import LocalDaySink

            symbols = args[0]
            root = kwargs.pop("root", None) or self.vipdoc_root
            profile = kwargs.pop("profile", "a_share_day")
            #: 默认值取自 sink 自己的常量，不在这里抄第二份字面量——"同一事实只存在
            #: 一处"，否则改常量不改执行体，两处口径会静默分叉。
            from ..sink.local_day import MAX_CHUNK, MAX_WINDOWS

            chunk = int(kwargs.pop("chunk", MAX_CHUNK))
            max_windows = int(kwargs.pop("max_windows", MAX_WINDOWS))
            if not root:
                raise ValidationError("sync_daily requires root or vipdoc_root")
            sink = LocalDaySink(root, profile=profile)
            out: dict[str, Any] = {}
            with self._client_session(self._tdx_client(timeout)) as client:
                for symbol in symbols:
                    requested = str(symbol)
                    normalized = normalize_symbol(requested)

                    def fetch(
                        offset: int,
                        count: int,
                        _symbol: str = normalized,
                    ) -> list[Any]:
                        return client.bars(
                            _symbol,
                            period="day",
                            count=count,
                            start=offset,
                            as_format="dict",
                        )

                    result = sink.sync(
                        normalized,
                        fetch,
                        chunk=chunk,
                        max_windows=max_windows,
                    )
                    out[requested] = {
                        "added": result.added,
                        "existed": result.existed,
                        "path": result.path,
                        "last_date": sink.last_date(normalized),
                    }
            return out

        if capability == "daily_enriched":
            from ..domain.enrich import enrich_daily_bars
            from ..query import QueryPlanner, QuerySpec

            symbol = str(args[0])
            count = int(kwargs.pop("count", 250))
            start = int(kwargs.pop("start", 0))
            adjust = str(kwargs.pop("adjust", "none") or "none").strip().lower()
            bar_provider = str(kwargs.pop("provider", "") or "").strip().lower() or "tdx"
            #: 与 ``adjusted_bars`` 同款双旋钮：``provider`` 管未复权 K 线从哪来，
            #: ``event_source`` 管除权除息事件从哪来。默认东财（已对拍）。
            event_source = str(kwargs.pop("event_source", "") or "").strip().lower() or "eastmoney"
            if event_source not in _ADJUST_EVENT_SOURCES:
                raise ValidationError(
                    "daily_enriched 不认识的 event_source",
                    context={
                        "event_source": event_source,
                        "supported": sorted(_ADJUST_EVENT_SOURCES),
                    },
                )
            if adjust not in _ENRICH_ADJUSTMENTS:
                raise ValidationError(
                    "daily_enriched 不认识的 adjust",
                    context={"adjust": adjust, "supported": sorted(_ENRICH_ADJUSTMENTS)},
                )
            if count < 1:
                raise ValidationError("daily_enriched 的 count 必须 >= 1", context={"count": count})
            if count > _ENRICH_MAX_COUNT:
                #: 不静默截断：老行的估值列会变成 None，调用方分不清"没有"和"没取到"。
                raise ValidationError(
                    "daily_enriched 单次最多覆盖估值序列的单页上限 "
                    f"{_ENRICH_MAX_COUNT} 个交易日；更长的区间请用 start 偏移分段",
                    context={"count": count, "limit": _ENRICH_MAX_COUNT},
                )

            #: 多取一根（窗口前一根）让首行也拿到真实 pre_close，算完裁掉。
            lead = 1
            want = count + lead
            bars = self._adjusted_raw_bars(
                symbol,
                period="day",
                count=want,
                start=start,
                raw_provider=bar_provider,
                timeout=timeout,
            )
            if adjust != "none":
                from ..domain.adjust import AdjustEngine, AdjustMethod

                events = self._adjusted_events(symbol, event_source, timeout=timeout)
                if adjust == AdjustMethod.HFQ and events and bars:
                    #: 与 ``adjusted_bars`` 同一条理由：hfq 的因子从史上第一个事件起
                    #: 累乘，窗口盖不住早期事件时因子会随 ``count`` 漂移。宽表是批量
                    #: 产物，同一根 bar 在不同批次的值不一致会让下游对不上账。
                    bars = self._extend_window_to_events(
                        bars,
                        events,
                        symbol=symbol,
                        period="day",
                        start=start,
                        target=want,
                        raw_provider=bar_provider,
                        timeout=timeout,
                    )
                bars = AdjustEngine().apply(list(bars), events, adjust)
            valuation = self.execute(
                QueryPlanner().compile(
                    QuerySpec.build(
                        "valuation_history",
                        symbols=symbol,
                        provider="eastmoney",
                        currentness="historical",
                        options={
                            "args": [symbol],
                            "kwargs": {"count": min(_ENRICH_MAX_COUNT, count + lead)},
                        },
                    )
                )
            ).data
            rows = enrich_daily_bars(list(bars), list(valuation or []))
            if len(rows) > count:
                return rows[len(rows) - count :]
            return rows

        raise ValidationError(
            "unknown composed capability",
            context={"capability": capability},
        )

    def _tdx_quotes(self, plan: QueryPlan) -> Any:
        """``quotes`` 的失败必须显形：全败即抛，部分失败随结果携带。

        子客户端对逐只失败做了隔离，默认把它们只写进 ``client.last_errors``——
        一个每次调用都被整体覆盖的实例属性。内核既不读那个属性也不另作声明，
        于是"所有主机都连不上"在 wire 上的形状与"这只代码没有行情"完全相同
        （``data=[]`` + ``warnings=()`` + HTTP 200）。失败袋在此显式取走，
        整体与部分按 :meth:`_tdx_bars` 的同一条界线分家：连不上就抛，缺几只需报。
        """
        symbols = list(plan.spec.symbols)
        errors: list[tuple[str, BaseException]] = []
        with self._client_session(self._tdx_client(self._hop_timeout(plan))) as client:
            rows = client.quotes(symbols, _collect=errors)
        if errors and not rows:
            raise errors[0][1]
        if errors:
            record_warning(
                WarningCode.QUOTES_PARTIAL_FAILURE,
                f"quotes 有 {len(errors)}/{len(symbols)} 只标的未取得行情："
                + "；".join(f"{sym} → {type(exc).__name__}({exc})" for sym, exc in errors[:3]),
            )
        return rows

    def _tdx_bars(self, plan: QueryPlan) -> Any:
        if plan.spec.adjustment:
            raise ValidationError(
                "TDX Direct bars 不支持在原始 bars 调用中静默复权",
                context={
                    "provider": "tdx",
                    "channel": "quotation",
                    "adjustment": plan.spec.adjustment,
                },
            )
        with self._client_session(self._tdx_client(self._hop_timeout(plan))) as client:
            return client.bars(
                plan.spec.symbols[0],
                period=plan.spec.period,
                count=plan.spec.count,
                start=plan.spec.start,
            )

    def _tdx_snapshot(self, plan: QueryPlan) -> Any:
        with self._client_session(self._tdx_client(self._hop_timeout(plan))) as client:
            return client.snapshot(plan.spec.symbols[0], as_format="dict")

    def _tdx_minute(self, plan: QueryPlan) -> Any:
        with self._client_session(self._tdx_client(self._hop_timeout(plan))) as client:
            return client.minute_today(plan.spec.symbols[0])

    def _tdx_trades(self, plan: QueryPlan) -> Any:
        with self._client_session(self._tdx_client(self._hop_timeout(plan))) as client:
            return client.trade_today(
                plan.spec.symbols[0],
                start=plan.spec.start,
                count=plan.spec.count,
            )

    def _tdx_security_count(self, plan: QueryPlan) -> Any:
        with self._client_session(self._tdx_client(self._hop_timeout(plan))) as client:
            return client.security_count(plan.spec.options.get("market", 0))

    def _tdx_security_list(self, plan: QueryPlan) -> Any:
        with self._client_session(self._tdx_client(self._hop_timeout(plan))) as client:
            return client.security_list(plan.spec.options.get("market", 0), plan.spec.start)

    def _local_bars(self, plan: QueryPlan) -> Any:
        if not self.vipdoc_root:
            raise ValidationError(
                "local_vipdoc Provider 需要显式 vipdoc_root",
                context={"provider": "local_vipdoc", "channel": "vipdoc"},
            )
        if plan.spec.adjustment:
            raise ValidationError(
                "local_vipdoc Direct bars 不支持静默复权",
                context={
                    "provider": "local_vipdoc",
                    "channel": "vipdoc",
                    "adjustment": plan.spec.adjustment,
                },
            )

        from ..domain.symbol import split_symbol
        from ..reader import DayBarReader, MinBarReader

        market, code = split_symbol(plan.spec.symbols[0])
        if market not in {"sh", "sz", "bj"}:
            raise ValidationError("local_vipdoc Direct bars 仅支持沪深北本地目录")

        root = Path(self.vipdoc_root)
        canonical = f"{market}{code}"
        if plan.spec.period == "day":
            rows = DayBarReader().read(
                root / market / "lday" / f"{canonical}.day",
                output="model",
            )
        elif plan.spec.period == "1min":
            rows = MinBarReader(profile="a_share_min", interval=1).read(
                root / market / "minline" / f"{canonical}.lc1",
                output="model",
            )
        elif plan.spec.period == "5min":
            rows = MinBarReader(profile="a_share_min", interval=5).read(
                root / market / "fzline" / f"{canonical}.lc5",
                output="model",
            )
        else:
            raise ValidationError("local_vipdoc Direct bars 仅支持 day/1min/5min")

        if plan.spec.start:
            end = max(0, len(rows) - plan.spec.start)
            begin = max(0, end - plan.spec.count) if plan.spec.count else 0
            return rows[begin:end]
        return rows[-plan.spec.count :] if plan.spec.count else rows

    def _web_quotes(self, plan: QueryPlan) -> Any:
        """Web 报价一跳：源名取 `plan.provider`，构造即单源，不存在换源下一步。

        刻意不复用公开便捷函数 `atst.web.get_quotes`——它在 `source` 缺省时会 new 一个
        `WebQuoteClient`，按 `web.enabled_sources` 顺序降级，而有序降级是内核禁止的形状。
        """
        from ..web import create_source, normalize_symbol

        src = create_source(plan.provider, timeout=self._hop_timeout(plan))
        try:
            return src.fetch([normalize_symbol(s) for s in plan.spec.symbols])
        finally:
            src.close()

    def _tencent_bars(self, plan: QueryPlan) -> Any:
        from ..web import create_source

        source_name = "minute_kline" if plan.channel == "minute_kline" else "kline"
        src = create_source(source_name, timeout=self._hop_timeout(plan))
        try:
            if plan.channel == "minute_kline":
                if plan.spec.adjustment:
                    raise ValidationError("Tencent minute_kline 不支持复权参数")
                return src.fetch_bars(  # type: ignore[attr-defined]
                    plan.spec.symbols[0],
                    period=plan.spec.period,
                    count=plan.spec.count,
                )
            return src.fetch_bars(  # type: ignore[attr-defined]
                plan.spec.symbols[0],
                period=plan.spec.period,
                count=plan.spec.count,
                adjust=plan.spec.adjustment,
            )
        finally:
            src.close()

    def _sina_bars(self, plan: QueryPlan) -> Any:
        from ..web.sina.adapters import SinaHistoryKlineSource

        src = SinaHistoryKlineSource(timeout=self._hop_timeout(plan))
        try:
            return src.fetch_bars(
                plan.spec.symbols[0],
                period=plan.spec.period,
                count=plan.spec.count,
                adjust=plan.spec.adjustment,
            )
        finally:
            src.close()

    def _eastmoney_bars(self, plan: QueryPlan) -> Any:
        from ..web.eastmoney.adapters import EastmoneyHistoryKlineSource

        src = EastmoneyHistoryKlineSource(timeout=self._hop_timeout(plan))
        try:
            return src.fetch_bars(
                plan.spec.symbols[0],
                period=plan.spec.period,
                count=plan.spec.count,
                adjust=plan.spec.adjustment,
            )
        finally:
            src.close()

    def _baidu_bars(self, plan: QueryPlan) -> Any:
        from ..web.baidu.adapters import BaiduSource

        if plan.spec.adjustment:
            raise ValidationError("Baidu Direct bars 不支持复权参数")
        src = BaiduSource(timeout=self._hop_timeout(plan))
        try:
            return src.fetch_kline(
                plan.spec.symbols[0],
                period=plan.spec.period,
                count=plan.spec.count,
            )
        finally:
            src.close()
