"""Exact Provider/Channel execution bindings for the canonical v13 runtime."""

from __future__ import annotations

import inspect
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import asdict, is_dataclass
from types import MappingProxyType
from typing import Any

from ..config import Config
from ..diagnostics import EMPTY_EXPLAINED_CODES, WarningCode, record_warning, warning_sink
from ..errors import InternalError, TdxError, TruncatedDataError, ValidationError
from ..query import QueryPlan
from ..result import Provenance, QueryResult, ResultWarning
from .audit import audit_runtime
from .bindings import (
    DEDICATED_CAPABILITIES,
    DIRECT_BINDINGS,
    DirectBinding,
    audit_direct_bindings,
)
from .capability_contract import implementation_for
from .composed import _COARSE_DAYLIKE_PERIODS, _day_in_range
from .freshness import verify_currentness

__all__ = [
    "DEDICATED_CAPABILITIES",
    "DirectBinding",
    "DirectProviderExecutor",
    "DIRECT_BINDINGS",
    "audit_direct_bindings",
]


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


def _warn_empty_bars(plan: QueryPlan, collected: list[ResultWarning]) -> None:
    """``bars`` 回来 0 根时必须说破——空结果与"历史耗尽"不是一回事。

    历史耗尽只会表现为**短页**（请求 320 根、回来 87 根），因为分页游标是从最新往回
    数的。所以"0 根"永远是一个需要解释的事实，而此前只有 tdx 那条路解释了它
    （``BARS_EMPTY_FIRST_PAGE``，判据要读服务端声明的记录数）。其余后端——尤其 Web
    源对不支持的标的/周期直接回空数组——一个字都不说，于是三种完全不同的处境在结果里
    长得一模一样：``ok`` + 0 行 + 零告警。

    实测两个形状（2026-10-30）：``bars("bj430047", provider="auto")`` 候选落到腾讯后
    回空、``warnings == ()``；``bars("hk00700")`` 钉死 tdx 则是 ``SymbolError``——同一件
    "取不到"的事，一个响一个不响。这条判据放在执行器出口，因此与后端无关，也不必让每个
    Web 适配器各记一遍。

    去重规则：tdx 已经发过 ``BARS_EMPTY_FIRST_PAGE`` 时不再补第二条——那条的信息更具体
    （带服务端声明数），两条并排只会让读的人以为出了两个问题。
    """
    if any(item.code in EMPTY_EXPLAINED_CODES for item in collected):
        return
    symbol = plan.spec.symbols[0] if plan.spec.symbols else ""
    record_warning(
        WarningCode.BARS_EMPTY_RESULT,
        f"bars({symbol!r}, period={plan.spec.period!r}) 由 {plan.provider}/{plan.channel} "
        "返回 0 根：该源对这个标的+周期没有数据（代码不存在 / 市场不受支持 / 该周期不在"
        "源的覆盖范围内），不是历史耗尽",
    )


def _warn_empty_quotes(plan: QueryPlan, collected: list[ResultWarning]) -> None:
    """``quotes`` 一行都没取回来时必须说破。

    ``QUOTES_PARTIAL_FAILURE`` 只在"取到一部分"时出发；全部为空的三种处境
    （代码不存在 / 市场不受支持 / 该源覆盖不到）在结果里长得一模一样，不能沉默。
    """
    if any(item.code == WarningCode.QUOTES_PARTIAL_FAILURE for item in collected):
        return
    symbols = plan.spec.symbols
    target = ",".join(symbols) if len(symbols) <= 3 else f"{len(symbols)} 只标的"
    record_warning(
        WarningCode.QUOTES_EMPTY_RESULT,
        f"quotes({target}) 由 {plan.provider}/{plan.channel} 返回 0 行：该源对这些标的"
        "都没有给出行情（代码不存在 / 市场不受支持 / 该源覆盖不到），不是部分缺失",
    )


_SEMANTIC_FIELD_RENAMES: Mapping[str, str] = MappingProxyType({"symbols": "symbol"})


_SEMANTIC_CALL_FIELDS: tuple[str, ...] = ("symbols", "period", "count", "start", "adjustment")


_DICT_ROW_CAPABILITIES: frozenset[str] = frozenset({"quotes", "bars"})


def _canonical_rows(data: Any) -> Any:
    """把行数据统一成 canonical dict 行（仅 ``_DICT_ROW_CAPABILITIES`` 内生效）。"""
    if not isinstance(data, list):
        #: 非行数据（如 ``security_count`` 的 int、``sync_daily`` 的汇总 dict）不碰。
        return data
    out: list[Any] = []
    for row in data:
        if isinstance(row, dict):
            out.append(row)
            continue
        to_dict = getattr(row, "to_dict", None)
        if callable(to_dict):
            out.append(to_dict())
        elif is_dataclass(row) and not isinstance(row, type):
            out.append(asdict(row))
        else:
            out.append(row)
    return out


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
                #: 空结果判据量的是 **Provider 原样返回的那一页**，因此必须先于日期区间
                #: 裁剪——区间裁空有它自己的两条告警（``bars_range_uncovered`` /
                #: ``bars_range_interior_empty``），在这里再报一次"0 根"就是重复记账。
                if plan.spec.capability == "bars" and not data:
                    _warn_empty_bars(plan, collected)
                if plan.spec.capability == "quotes" and data is not None and not data:
                    _warn_empty_quotes(plan, collected)
                #: 日期区间在取回的那一页上做过滤，并当场回答"这一页盖不盖得住区间"。
                data = self._apply_bar_range(plan, data)
                #: quotes/bars 的内核出口统一为 canonical dict 行：Provider 之间
                #: 同一种能力只准有一种行形状（``_DICT_ROW_CAPABILITIES``）。
                if plan.spec.capability in _DICT_ROW_CAPABILITIES:
                    data = _canonical_rows(data)
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
        from .execute_capability import _migrated_capability as impl

        return impl(self, plan)

    def _web_adapter_call(
        self, meta: Any, args: list[Any], kwargs: dict[str, Any], *, timeout: float
    ) -> Any:
        from .execute_capability import _web_adapter_call as impl

        return impl(self, meta, args, kwargs, timeout=timeout)

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
        days = [day for day in (day_of(r) for r in data) if day]
        #: 判据是"这一页有没有取到区间起点**之前**的 bar"，不是"最早一根是否等于
        #: 起点"——后者会把起点落在休市日上的正常请求误报成截断（2026-01-01 没有
        #: bar，但 2025-12-31 有，区间就是完整的）。
        #:
        #: 这里**不能**再要求 ``kept`` 非空：区间完全落在取回窗口之前时（如
        #: ``count=30`` 却要 1999 年那段），``kept`` 为空、``days`` 却是真日期，
        #: 该页显然没触及区间起点——恰恰是最需要告警的一格。旧实现把告警挂在
        #: ``kept`` 上，于是这种"空结果 + 零解释"的请求静默通过，调用方无从
        #: 分辨"这些日期没有行情"与"这一段根本没取回来"。
        if start_date and days:
            reached_before_start = any(day < start_date for day in days)
            if not reached_before_start:
                record_warning(
                    WarningCode.BARS_RANGE_UNCOVERED,
                    f"请求区间自 {start_date} 起，但本次取回的 {len(data)} 根 K 线最早只到 "
                    f"{min(days)}，没有触及区间起点之前："
                    "结果是区间的一段而不是全部。请加大 count 或用 start 偏移继续往回取。",
                    stacklevel=2,
                )
            elif (
                not kept
                and plan.spec.period in _COARSE_DAYLIKE_PERIODS
                and any(day > (end_date or start_date) for day in days)
            ):
                #: 页已触及区间之前（上一条不触发），区间两侧都有 bar，却一根都没命中。
                #: 粗周期的 bar 以「周期末交易日」为 ``datetime``（未走完的周期取最新交易日），
                #: 于是"落在周期内部"的窗口按日期裁剪必然为空。这与"这些日期没有行情"是
                #: 两回事（上一条注释里的未来区间就是后者），必须说破——否则调用方拿到的是
                #: 一个无从解释的空结果。实测形状：``year`` 周期取 2026-09 区间回空，
                #: 因为 2026 年的 bar 挂着最新交易日 2026-10-20，而 2025 年及更早的 bar 都在
                #: 区间起点之前。
                record_warning(
                    WarningCode.BARS_RANGE_INTERIOR_EMPTY,
                    f"请求区间 {start_date}..{end_date or '最新'} 落在取回 K 线的内部"
                    f"（最早 {min(days)} / 最晚 {max(days)}）却没有任何一根命中："
                    f"{plan.spec.period} 周期的 bar 以「周期末交易日」为 datetime，"
                    "区间落在该周期内部时按日期裁剪必然为空——请把区间对齐到周期边界"
                    "（整周 / 整月 / 整季 / 整年），或改用 day 周期。",
                    stacklevel=2,
                )
        return kept

    def _adjusted_raw_bars(
        self, symbol: str, *, period: str, count: int, start: int, raw_provider: str, timeout: float
    ) -> list[Any]:
        from .composed import _adjusted_raw_bars as impl

        return impl(
            self,
            symbol,
            period=period,
            count=count,
            start=start,
            raw_provider=raw_provider,
            timeout=timeout,
        )

    def _adjusted_events(self, symbol: str, event_source: str, *, timeout: float) -> list[Any]:
        from .composed import _adjusted_events as impl

        return impl(self, symbol, event_source, timeout=timeout)

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
        from .composed import _extend_window_to_events as impl

        return impl(
            self,
            bars,
            events,
            symbol=symbol,
            period=period,
            start=start,
            target=target,
            raw_provider=raw_provider,
            timeout=timeout,
        )

    def _composed_call(
        self,
        plan: QueryPlan,
        capability: str,
        args: list[Any],
        kwargs: dict[str, Any],
        *,
        timeout: float,
    ) -> Any:
        from .execute_composed import _composed_call as impl

        return impl(self, plan, capability, args, kwargs, timeout=timeout)

    def _tdx_quotes(self, plan: QueryPlan) -> Any:
        from .execute_channel import _tdx_quotes as impl

        return impl(self, plan)

    def _tdx_bars(self, plan: QueryPlan) -> Any:
        from .execute_channel import _tdx_bars as impl

        return impl(self, plan)

    @staticmethod
    def _index_bit(plan: QueryPlan) -> bool:
        from .execute_channel import _index_bit as impl

        return impl(plan)

    def _tdx_snapshot(self, plan: QueryPlan) -> Any:
        from .execute_channel import _tdx_snapshot as impl

        return impl(self, plan)

    def _tdx_minute(self, plan: QueryPlan) -> Any:
        from .execute_channel import _tdx_minute as impl

        return impl(self, plan)

    def _tdx_trades(self, plan: QueryPlan) -> Any:
        from .execute_channel import _tdx_trades as impl

        return impl(self, plan)

    def _tdx_security_count(self, plan: QueryPlan) -> Any:
        from .execute_channel import _tdx_security_count as impl

        return impl(self, plan)

    def _tdx_security_list(self, plan: QueryPlan) -> Any:
        from .execute_channel import _tdx_security_list as impl

        return impl(self, plan)

    def _local_bars(self, plan: QueryPlan) -> Any:
        from .execute_channel import _local_bars as impl

        return impl(self, plan)

    def _web_quotes(self, plan: QueryPlan) -> Any:
        from .execute_channel import _web_quotes as impl

        return impl(self, plan)

    def _tencent_bars(self, plan: QueryPlan) -> Any:
        from .execute_channel import _tencent_bars as impl

        return impl(self, plan)

    def _sina_bars(self, plan: QueryPlan) -> Any:
        from .execute_channel import _sina_bars as impl

        return impl(self, plan)

    def _eastmoney_bars(self, plan: QueryPlan) -> Any:
        from .execute_channel import _eastmoney_bars as impl

        return impl(self, plan)

    def _baidu_bars(self, plan: QueryPlan) -> Any:
        from .execute_channel import _baidu_bars as impl

        return impl(self, plan)
