# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""Exact Provider/Channel execution bindings for the canonical v13 runtime."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..catalog.capability import binding_for, validate_call
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
    事实的唯一持有者是 :func:`tstdx.query._canonical_unified_channel`。所以"可达"
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

    def _tdx_client(self, timeout: float) -> Any:
        from ..client import TdxClient
        from ..transport.pool import pool_settings_from_config

        #: 主站选择已由内核解析完毕；配置在此只提供传输层构造参数，
        #: 避免同一个键在两处解释。
        settings = pool_settings_from_config(self.config)
        settings["timeout"] = timeout
        return TdxClient(self.hosts, **settings)

    @staticmethod
    def _call_payload(plan: QueryPlan) -> tuple[list[Any], dict[str, Any]]:
        options = plan.spec.options
        args = options.get("args", [])
        kwargs = options.get("kwargs", {})
        if not isinstance(args, list) or not isinstance(kwargs, dict):
            raise ValidationError("migrated capability options 必须包含 args:list / kwargs:object")
        return args, dict(kwargs)

    def _migrated_capability(self, plan: QueryPlan) -> Any:
        meta = binding_for(plan.provider, plan.channel, plan.spec.capability)
        args, kwargs = self._call_payload(plan)
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

            session = WebQuoteSession(meta.source or "sina", timeout=hop)
            try:
                if meta.capability == "hk_quotes" and plan.provider in {
                    "sina",
                    "tencent",
                }:
                    kwargs.setdefault("provider", plan.provider)
                return getattr(session, meta.method)(*args, **kwargs)
            finally:
                session.close()

        if meta.backend == "tdx_client":
            with self._tdx_client(hop) as client:
                if meta.capability == "quotes_concurrent":
                    kwargs.setdefault("as_format", "obj")
                return getattr(client, meta.method)(*args, **kwargs)

        if meta.backend == "f10_client":
            from ..client import F10Client

            client = F10Client(timeout=hop)
            try:
                if meta.capability == "f10":
                    return client.parse_text(client.download(*args, **kwargs))
                return list(client.catalog(*args, **kwargs))
            finally:
                client.close()

        if meta.backend in {"ex_client", "goods_client", "mac_client"}:
            from ..client import ExMarketClient, GoodsClient, MacClient

            client = {
                "ex_client": ExMarketClient,
                "goods_client": GoodsClient,
                "mac_client": MacClient,
            }[meta.backend](timeout=hop)
            try:
                if meta.capability in {
                    "ex_bars",
                    "ex_quotes",
                    "goods_bars",
                    "goods_quotes",
                    "mac_quotes",
                }:
                    kwargs.setdefault("as_format", "dict")
                return getattr(client, meta.method)(*args, **kwargs)
            finally:
                client.close()

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
            return self._composed_call(meta.capability, args, kwargs, timeout=hop)

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

        adapter = adapter_cls(timeout=timeout)
        try:
            symbol = args[0]
            if meta.capability == "minute_web":
                return adapter.fetch_minute(symbol)
            return adapter.fetch_bars(symbol, **kwargs)
        finally:
            close = getattr(adapter, "close", None)
            if callable(close):
                close()

    def _composed_call(
        self,
        capability: str,
        args: list[Any],
        kwargs: dict[str, Any],
        *,
        timeout: float,
    ) -> Any:
        if capability == "adjusted_bars":
            from ..domain.adjust import AdjustEngine
            from ..domain.finance import to_capital_changes
            from ..domain.models import CapitalChange
            from ..domain.symbol import split_symbol
            from ..reader import DayBarReader

            symbol = str(args[0])
            method = str(kwargs.pop("method", "qfq"))
            period = str(kwargs.pop("period", "day"))
            count = int(kwargs.pop("count", 320))
            start = int(kwargs.pop("start", 0))
            events = kwargs.pop("events", None)
            anchor_date = kwargs.pop("anchor_date", None)
            if period != "day":
                raise ValidationError(
                    "adjusted_bars canonical local raw path currently supports day only",
                    context={"period": period},
                )
            if not self.vipdoc_root:
                raise ValidationError("adjusted_bars requires vipdoc_root for canonical raw bars")

            market, code = split_symbol(symbol)
            path = Path(self.vipdoc_root) / market / "lday" / f"{market}{code}.day"
            bars = DayBarReader().read(path, output="model")
            end = max(0, len(bars) - start)
            begin = max(0, end - count)
            bars = bars[begin:end]

            if not events:
                with self._tdx_client(timeout) as client:
                    events = list(client.capital_changes(symbol))
            event_list = list(events)
            if event_list and not isinstance(event_list[0], CapitalChange):
                event_list = to_capital_changes(event_list)
            return AdjustEngine().apply(
                list(bars),
                event_list,
                method,
                anchor_date=anchor_date,
            )

        if capability == "sync_daily":
            from ..sink import LocalDaySink

            symbols = args[0]
            root = kwargs.pop("root", None) or self.vipdoc_root
            profile = kwargs.pop("profile", "a_share_day")
            chunk = int(kwargs.pop("chunk", 800))
            max_windows = int(kwargs.pop("max_windows", 64))
            if not root:
                raise ValidationError("sync_daily requires root or vipdoc_root")
            sink = LocalDaySink(root, profile=profile)
            out: dict[str, Any] = {}
            with self._tdx_client(timeout) as client:
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
        with self._tdx_client(self._hop_timeout(plan)) as client:
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
        with self._tdx_client(self._hop_timeout(plan)) as client:
            return client.bars(
                plan.spec.symbols[0],
                period=plan.spec.period,
                count=plan.spec.count,
                start=plan.spec.start,
            )

    def _tdx_snapshot(self, plan: QueryPlan) -> Any:
        with self._tdx_client(self._hop_timeout(plan)) as client:
            return client.snapshot(plan.spec.symbols[0], as_format="dict")

    def _tdx_minute(self, plan: QueryPlan) -> Any:
        with self._tdx_client(self._hop_timeout(plan)) as client:
            return client.minute_today(plan.spec.symbols[0])

    def _tdx_trades(self, plan: QueryPlan) -> Any:
        with self._tdx_client(self._hop_timeout(plan)) as client:
            return client.trade_today(
                plan.spec.symbols[0],
                start=plan.spec.start,
                count=plan.spec.count,
            )

    def _tdx_security_count(self, plan: QueryPlan) -> Any:
        with self._tdx_client(self._hop_timeout(plan)) as client:
            return client.security_count(plan.spec.options.get("market", 0))

    def _tdx_security_list(self, plan: QueryPlan) -> Any:
        with self._tdx_client(self._hop_timeout(plan)) as client:
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

        刻意不复用公开便捷函数 `tstdx.web.get_quotes`——它在 `source` 缺省时会 new 一个
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
