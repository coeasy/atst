# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""v13 public Client API: the sole high-level business surface."""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, is_dataclass, replace
from enum import Enum
from pathlib import Path
from typing import Any, cast

from ..batch import BatchItem, BatchResult
from ..diagnostics import ResultWarning, WarningCode
from ..domain.symbol import normalize_symbol
from ..error_envelope import ErrorEnvelope, to_error_envelope
from ..errors import ValidationError
from ..providers import (
    COMPOSED_CAPABILITIES,
    MIGRATED_CAPABILITIES,
    PROVIDERS,
    default_provider_for,
    is_migrated_capability,
    resolve_capability_provider,
)
from ..query import QuerySpec
from ..result import QueryResult
from ..runtime.autoselect import is_auto_provider, provider_health
from ..runtime.executor import DEDICATED_CAPABILITIES as _CORE_CAPABILITIES
from ..runtime.kernel import UnifiedRuntime
from ..runtime.orchestration import (
    AutoPolicy,
    FallbackPolicy,
    OrchestratedResult,
    ProviderOrchestrator,
)
from ..stream_contract import StreamPlanner, StreamSpec
from ..streaming.stateful import AsyncStatefulQuoteStream, StatefulQuoteStream

__all__ = ["Client", "AsyncClient"]


def _json_contract(value: Any) -> Any:
    """Convert deterministic domain values into QuerySpec-safe JSON values."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Enum):
        return _json_contract(value.value)
    if isinstance(value, Path):
        return str(value)
    if is_dataclass(value) and not isinstance(value, type):
        return _json_contract(asdict(value))
    if isinstance(value, Mapping):
        return {str(key): _json_contract(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_contract(item) for item in value]
    raise ValidationError(
        "capability 参数必须可转换为确定性 JSON contract",
        context={"value_type": type(value).__name__},
    )


def _symbol_hint(args: Sequence[Any], kwargs: Mapping[str, Any]) -> str | None:
    """从 capability 的入参里取第一个符号串，供按市场分叉的默认源使用。

    ``minute_klines`` / ``history`` 这类能力的第一个位置参数就是 symbol；HTTP / WS /
    MCP 面也可以把 ``symbol`` 写在 ``kwargs`` 里（``{"kwargs": {"symbol": ...}}``），
    两种写法必须给出同一个默认源。取不到（全市场能力、符号写成非字符串）返回
    ``None``，默认源退回与符号无关的那一档。
    """
    for value in args:
        if isinstance(value, str) and value.strip():
            return value
    for key in ("symbol", "symbols", "code"):
        value = kwargs.get(key)
        if isinstance(value, str) and value.strip():
            return value
        if isinstance(value, (list, tuple)) and value and isinstance(value[0], str):
            return value[0]
    return None


def _reject_provider_with_policy(provider: str | None, policy: FallbackPolicy) -> None:
    """``provider`` 与 ``fallback`` 同时给出＝同时要求「只走这家」和「这家失败就换」。

    这里必须是 :class:`ValidationError`（E1010 / HTTP 422 / JSON-RPC -32602 / CLI 退出码 2），
    而不是原来那个裸 ``ValueError``：入参冲突的归类发生在**抛出点**，四面只是转述它，所以
    一次选错类型的 raise 会让四张面**一致地**把它说成 ``E9000`` 服务器故障、把调用方写错的
    那两个键从消息里抹掉（实测四面同读数）。四面各自加转换只会把这第七份抄件再抄四遍。
    """
    if provider is None:
        return
    raise ValidationError(
        "provider 与 fallback 互斥：指定 provider 就是钉死一跳，fallback 名单要求换跳执行",
        context={
            "phase": "wire_validation",
            "provider": provider,
            "fallback": list(policy.providers),
        },
    )


class Client:
    """Canonical synchronous v13 client.

    Tier-A uses strongly typed QuerySpec fields. Retired-facade abilities use the
    migrated capability catalog, but still execute through the same QueryPlanner
    and UnifiedRuntime. No facade/router compatibility kernel is reintroduced.
    """

    def __init__(
        self,
        runtime: UnifiedRuntime | None = None,
        **runtime_kwargs: Any,
    ) -> None:
        if runtime is not None and runtime_kwargs:
            raise ValueError("runtime and runtime kwargs are mutually exclusive")
        self.runtime = runtime or UnifiedRuntime(**runtime_kwargs)
        self.orchestrator = ProviderOrchestrator(self.runtime)
        self.stream_planner = StreamPlanner()
        self._owns_runtime = runtime is None

    def close(self) -> None:
        """收尾本实例**自建**的内核；``runtime=`` 借来的那份归调用方。

        它关不掉"连接池"，因为默认内核根本不跨调用持有连接（每次取数现场构造家族客户端、
        ``finally`` 释放）。这条链今天兑现的是所有权口径与注入执行器的收尾口子，三张服务面
        （HTTP lifespan、WS ``RuntimeJsonRpcHandler.close()``、MCP ``stop()``）都落在这里。
        实测口径与判据见 ``tests/runtime/test_close_chain_ownership.py``。

        关过一次就把所有权让出去（``_owns_runtime`` 置回 ``False``）：注入的执行器不必
        自己防重复释放，``with`` 出口和显式 ``close()`` 抢着关也只关一次——与 MCP
        ``stop()``、WS ``RuntimeJsonRpcHandler.close()`` 同一写法。
        """
        if self._owns_runtime:
            self._owns_runtime = False
            self.runtime.close()

    def __enter__(self) -> Client:
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    @staticmethod
    def capabilities() -> tuple[str, ...]:
        return tuple(sorted(_CORE_CAPABILITIES | MIGRATED_CAPABILITIES))

    def core_capability_statuses(self) -> dict[str, dict[str, Any]]:
        """Return operational status for this Client's canonical core surface.

        Omitted-provider routing is evaluated against this instance's effective
        planner default, so health metadata cannot diverge from real execution.
        """

        configured_default = self.runtime.planner.default_provider
        values: dict[str, dict[str, Any]] = {}
        for capability in sorted(_CORE_CAPABILITIES):
            declared = tuple(
                provider
                for provider in PROVIDERS.ids()
                if PROVIDERS.get(provider).supports(capability)
            )
            available = PROVIDERS.available_providers(capability)
            effective_default: str | None = None
            if available:
                selected = resolve_capability_provider(
                    capability,
                    default=configured_default,
                )
                if selected in available:
                    effective_default = selected
            values[capability] = {
                "available": bool(available),
                "default_available": effective_default is not None,
                "declared_providers": list(declared),
                "operational_providers": list(available),
                "default_provider": effective_default,
            }
        return values

    def provider_health_status(self) -> dict[str, dict[str, Any]]:
        """``provider="auto"`` 当前降级名单（观测面用）：``{provider: {code, age_s}}``。

        只列**仍处 TTL 窗口内**的失败标记——与 ``auto`` 排序时用的是同一份名单
        （:meth:`ProviderHealthStore.degraded` 顺手复位过期项），所以这个字段不会
        报出一批"其实已经不算数"的 Provider。

        ``age_s`` 是失败发生到现在的秒数，不是时间戳：``monotonic_ns`` 只在同一进程内
        可比，贴到线上接口没有意义。健康反馈关闭（``auto_health_ttl=0``）时恒为空——
        那时没有任何 Provider 会被降级，报一份名单等于凭空造出一个不存在的机制。
        """
        return provider_health().degraded(ttl_ns=self._auto_ttl_ns())

    @staticmethod
    def capability_statuses() -> dict[str, str]:
        """迁移能力状态映射（``{capability: status}``，G-16）。

        ``status`` 取值见 :data:`atst.providers.CAPABILITY_STATUSES`；
        未显式标注的能力默认为 ``"alive"``。四面出口（CLI/HTTP/WS/MCP）据此
        渲染能力健康度，``offline`` 不进可用集。
        """
        from ..providers import capability_statuses

        return capability_statuses()

    def execute(self, spec: QuerySpec) -> QueryResult[Any]:
        if self._wants_auto(spec):
            return self._execute_auto(spec)
        return self.runtime.execute(spec)

    # ------------------------------------------------------------------ #
    # 显式自动选源（provider='auto' / core.default_provider='auto'）
    # ------------------------------------------------------------------ #
    def _wants_auto(self, spec: QuerySpec) -> bool:
        """这次请求是否走自动选源。

        两种显式形态：请求自己写了 ``provider='auto'``，或缺省 Provider 就是
        ``auto``（``core.default_provider='auto'``）且本次没钉死 Provider。
        钉死 Provider 的请求永远不进这里——provider-first 不变量不受影响。
        """
        if is_auto_provider(spec.provider):
            return True
        if spec.provider is None:
            return is_auto_provider(self.runtime.planner.default_provider)
        return False

    def _auto_policy(self) -> AutoPolicy:
        routing = getattr(self.runtime.config, "routing", None)
        prefer = tuple(getattr(routing, "auto_providers", ()) or ())
        return AutoPolicy.build(prefer=prefer)

    def _auto_ttl_ns(self) -> int:
        routing = getattr(self.runtime.config, "routing", None)
        ttl = float(getattr(routing, "auto_health_ttl", 0.0) or 0.0)
        return int(ttl * 1_000_000_000)

    def _execute_auto(self, spec: QuerySpec) -> QueryResult[Any]:
        """按能力面 + 运行时健康反馈逐跳执行，首次成功即返回。

        与显式 ``FallbackPolicy`` 的关系：候选名单不是调用方手写的，而是每次
        请求现排的；跨源之所以合法，是因为调用方显式选择了 ``auto``。每个结果
        仍带 Provenance（``requested_provider='auto'`` + ``fallback``），跨源
        出数时另记一条 ``auto_provider_fallback`` 告警——不静默换源。
        """
        #: 未配 vipdoc_root 时 local_vipdoc 排进来只会得到一次配置错误，没有
        #: 第二次机会，因此不进候选。
        exclude: frozenset[str] = frozenset({"local_vipdoc"})
        if getattr(self.runtime.executor, "vipdoc_root", None):
            exclude = frozenset()
        planner_default = self.runtime.planner.default_provider
        orchestrated = self.orchestrator.execute_auto(
            spec,
            policy=self._auto_policy(),
            exclude=exclude,
            ttl_ns=self._auto_ttl_ns(),
            default=None if is_auto_provider(planner_default) else planner_default,
        )
        result = orchestrated.result
        if len(orchestrated.attempts) > 1:
            served = orchestrated.attempts[-1]
            failed = [
                f"{item.provider}({item.code or 'error'})"
                for item in orchestrated.attempts
                if item.status == "failed"
            ]
            result = replace(
                result,
                meta=replace(
                    result.meta,
                    warnings=result.meta.warnings
                    + (
                        ResultWarning(
                            code=WarningCode.AUTO_PROVIDER_FALLBACK,
                            message=(
                                f"provider='auto'：候选 {failed} 失败，"
                                f"实际由 {served.provider} 出数"
                            ),
                        ),
                    ),
                ),
            )
        return result

    def _call_core(
        self,
        capability: str,
        args: tuple[Any, ...],
        *,
        provider: str | None,
        currentness: str,
        kwargs: dict[str, Any],
    ) -> QueryResult[Any]:
        """核心集的分派不抄表：目标方法自己就是那份表。

        这里原本有七段 ``if capability == ...``，每段各自抄三件事——收几个位置参、
        ``provider`` 缺省是谁、``currentness`` 转不转。抄件的代价量得过：多给一个签名里没有的
        关键字（``call("snapshot", "600519", count=5)``）会穿过 ``**kwargs`` 撞到方法本体，抛出来的是
        裸 ``TypeError``，于是四张面**一致地**把调用方写错的键说成 E9000/HTTP 500；而整数代码只在
        写了 ``str(args[0])`` 的那一格侥幸能用，换任何一条路都是同一个裸 ``TypeError``。
        现在绑定交给 :func:`inspect.signature`，缺省交给方法自己的签名，本段只留三件事：
        核心集的闭合点、"入参不合签名"归 ``ValidationError``、以及 fallback 结果不许从这条路出来。
        """
        target = getattr(type(self), capability, None)
        if not callable(target):
            #: 闭合点。核心集由执行体表派生，派生结果长出一格而便捷方法面没有对应方法时，
            #: 绝不能借邻格去跑——借用的下场是返回一份不相干的能力的结果。
            raise ValidationError(
                f"capability {capability!r} 在核心集内但 Client 上没有对应方法",
                context={
                    "capability": capability,
                    "known_core": sorted(_CORE_CAPABILITIES),
                    "phase": "wire_validation",
                },
            )
        method = getattr(self, capability)
        forwarded = dict(kwargs)
        if provider is not None:
            forwarded["provider"] = provider
        if currentness != "business":
            #: ``business`` 的含义写在各能力自己的缺省里（``Client.<cap>`` 的签名，判据
            #: ``test_the_business_default_is_not_a_second_table`` 盯住它与内核那一侧一致），
            #: 这里不再抄第二份"哪些能力算盘中"的名单。
            forwarded["currentness"] = currentness
        try:
            bound = inspect.signature(method).bind(*args, **forwarded)
        except TypeError as exc:
            raise ValidationError(
                f"Client.call({capability!r}) 的入参不合它自己的签名：{exc}",
                context={
                    "capability": capability,
                    "phase": "wire_validation",
                    "received": sorted(forwarded),
                    "positional": len(args),
                },
            ) from exc
        result = method(*bound.args, **bound.kwargs)
        if isinstance(result, OrchestratedResult):
            raise ValidationError("Client.call core path does not accept hidden fallback policy")
        return result

    def call(
        self,
        capability: str,
        *args: Any,
        provider: str | None = None,
        channel: str | None = None,
        currentness: str = "business",
        **kwargs: Any,
    ) -> QueryResult[Any]:
        """Execute a core or migrated capability through the canonical runtime."""
        cap = str(capability).strip().lower()
        if cap in _CORE_CAPABILITIES:
            if channel is not None:
                raise ValidationError("Tier-A Client.call does not accept an arbitrary channel")
            return self._call_core(
                cap,
                args,
                provider=provider,
                currentness=currentness,
                kwargs=dict(kwargs),
            )
        if not is_migrated_capability(cap):
            raise ValidationError(
                f"未知 capability {capability!r}",
                context={"capability": cap},
            )
        selected = provider or default_provider_for(cap, _symbol_hint(args, kwargs))
        if provider and cap in COMPOSED_CAPABILITIES:
            #: 组合能力（``adjusted_bars`` / ``daily_enriched`` / ``sync_daily``）没有自己的
            #: 传输层：它名字里的 ``provider`` 指的是**取原始 K 线的那条腿**，不是"谁来执行"。
            #: 过去这个值被当成路由字段递进 planner，于是
            #: ``Client.adjusted_bars(..., provider="tdx")`` 在规划期就 E1010
            #: （"provider 'tdx' 不支持 capability 'adjusted_bars'"）——而执行体里
            #: ``_composed_call`` 读 ``kwargs["provider"]`` 的那一行永远读不到东西，
            #: 变成一条谁都用不上的参数。这里把它归位到 ``options.kwargs``，执行体才拿得到。
            #: 与之相对，``web_adapter`` 一类的 ``provider`` 仍然是路由字段，语义不变。
            kwargs["provider"] = provider
            selected = default_provider_for(cap)
        spec = QuerySpec.build(
            cap,
            provider=selected,
            channel=channel,
            currentness=currentness,
            options={
                "args": _json_contract(list(args)),
                "kwargs": _json_contract(kwargs),
            },
        )
        return self.execute(spec)

    def execute_with_policy(
        self,
        spec: QuerySpec,
        *,
        policy: FallbackPolicy,
    ) -> OrchestratedResult:
        return self.orchestrator.execute(spec, policy=policy)

    def typed(self, query: Any, **kwargs: Any) -> Any:
        """Execute one typed CapabilityQuery end-to-end through the kernel.

        Compilation is fail-closed: unregistered capabilities raise before any
        Provider request, and the normalized Domain Record payload is returned.
        """
        from ..typed_query import TypedQueryResult, call_payload_from_typed, records_from_data

        payload = call_payload_from_typed(query)
        payload.update(kwargs)
        result = self.call(query.capability, provider=query.provider, **payload)
        if isinstance(result, OrchestratedResult):
            raise ValidationError("Client.typed does not produce fallback-policy results")
        return TypedQueryResult(
            data=records_from_data(query.capability, result.data),
            capability=query.capability,
        )

    def quotes(
        self,
        symbols: str | Sequence[str],
        *,
        provider: str | None = None,
        policy: FallbackPolicy | None = None,
        currentness: str = "live",
        strict: bool = False,
    ) -> QueryResult[Any] | OrchestratedResult:
        """实时行情快照。

        ``strict=True``（仅 tdx 路径生效）：解析器自校验（哨兵 / 成交额值域）
        发现字段漂移的标的直接失败，不返回"帧合法、值可疑"的行情——不存在代码
        的 TDX 主站响应就长这样（回声匹配、载荷齐全、量能荒谬）。Web 源没有
        同型自校验判据，该开关对它们是空操作。
        """
        spec = QuerySpec.build(
            "quotes",
            symbols=symbols,
            provider=provider,
            currentness=currentness,
            options={"strict": True} if strict else None,
        )
        if policy is not None:
            _reject_provider_with_policy(provider, policy)
            return self.execute_with_policy(spec, policy=policy)
        return self.execute(spec)

    def quotes_batch(
        self,
        symbols: Sequence[str],
        *,
        provider: str | None = None,
        currentness: str = "live",
    ) -> BatchResult[QueryResult[Any]]:
        """逐 symbol 三态审计的批量行情。

        ``provider="auto"``（或 ``core.default_provider="auto"``）必须与
        :meth:`quotes` 同语义：每只标的各自走完整候选链，而不是让内核把它解析成
        "第一跳"后按单跳执行——那样同一个 ``auto`` 在单只与批量两条路上会给出
        两种行为（批量那只失败时直接进 ``errors``，永远不会换源）。批次结构仍按
        ``BatchItem`` / ``errors`` / ``requested`` 三件套给，逐项 provenance 各自
        记录实际出数的 Provider。
        """
        spec = QuerySpec.build(
            "quotes",
            symbols=symbols,
            provider=provider,
            currentness=currentness,
        )
        auto = self._wants_auto(spec)
        unique: list[str] = []
        seen: set[str] = set()
        for raw_symbol in symbols:
            symbol = normalize_symbol(raw_symbol)
            if symbol in seen:
                continue
            seen.add(symbol)
            unique.append(symbol)

        def one(symbol: str) -> QueryResult[Any]:
            if auto:
                return self.execute(replace(spec, symbols=(symbol,)))
            return cast(
                QueryResult[Any], self.quotes(symbol, provider=provider, currentness=currentness)
            )

        def run(symbol: str) -> QueryResult[Any] | Exception:
            """逐项审计：单只失败只落该项，不打断同批其余标的。"""
            try:
                return one(symbol)
            except Exception as exc:  # noqa: BLE001 - 见 BatchItem 的三态契约
                return exc

        workers = self._batch_workers()
        if workers > 1 and len(unique) > 1:
            with ThreadPoolExecutor(max_workers=min(workers, len(unique))) as pool:
                # map 自带按提交顺序回传，省掉一份 futures 字典与逐键查找。
                outcomes = list(pool.map(run, unique))
        else:
            outcomes = [run(symbol) for symbol in unique]

        items: dict[str, BatchItem[QueryResult[Any]]] = {}
        errors: dict[str, ErrorEnvelope] = {}
        for symbol, outcome in zip(unique, outcomes, strict=True):
            if isinstance(outcome, Exception):
                items[symbol] = BatchItem("failed", error=outcome)
                errors[symbol] = to_error_envelope(outcome)
            elif outcome.data:
                items[symbol] = BatchItem("ok", value=outcome)
            else:
                items[symbol] = BatchItem("missing")
        return BatchResult.build(items, errors=errors, requested=tuple(unique))

    def _batch_workers(self) -> int:
        """``core.batch_workers``：0（或缺失）表示串行，保持 v1 的逐只顺序。"""
        core = getattr(self.runtime.config, "core", None)
        return max(0, int(getattr(core, "batch_workers", 0) or 0))

    def bars(
        self,
        symbol: str,
        *,
        provider: str | None = None,
        policy: FallbackPolicy | None = None,
        period: str = "day",
        count: int = 320,
        start: int = 0,
        adjustment: str = "",
        adjust: str = "",
        currentness: str = "historical",
        strict: bool = False,
        start_date: str = "",
        end_date: str = "",
        index: bool | None = None,
    ) -> QueryResult[Any] | OrchestratedResult:
        """K 线。``strict=True`` 时"结果带瑕疵"直接失败，而不是返回后靠调用方自查。

        ``provider`` 缺省是 **``tdx``**（``PROVIDERS.default_provider``）：核心能力的缺省源
        在 ``QuerySpec.normalized()`` 里解析，不走 ``catalog.capability`` 那张迁移能力表
        （对 ``bars`` 调 ``default_provider_for`` 会 ``KeyError``，别被它误导）。tdx 只认
        SZ/SH/BJ 三个二进制市场；港美股代码在 provider 缺省时由 ``QuerySpec.normalized()``
        按能力面自动路由到 operational Web 源（eastmoney/tencent，结果 provenance 显示真实
        出数源），显式指定 provider 则完全尊重调用方选择。缺省源也不会因为 tdx 主机全挂而
        悄悄改由别的 Provider 出数。

        回来 **0 根**不是"正常空结果"：分页游标从最新往回数，历史耗尽只会表现为短页，所以
        0 根一定带一条告警（``bars_empty_result``；tdx 的空桩换成 ``bars_empty_first_page``，
        带服务端声明的记录数）。``strict=True`` 时它是一次失败。

        执行器把一次查询里记录的全部数据完整性瑕疵装进
        :attr:`~atst.result.ResultMeta.warnings`（空桩首页、锚点漂移截断等）；
        ``strict`` 就是那份名单的开关，任何 Provider、任何瑕疵类别同一条判据。

        ``adjust`` 是 ``adjustment`` 的别名：canonical 的 ``adjustment`` 非空时胜出，
        否则用 ``adjust`` 填充。推荐统一用 ``adjust``（与 ``adjusted_bars`` 的同名参数一致）。
        **复权只有 Web 源支持**：缺省 Provider 是 ``tdx``，它会显式拒绝带复权的原始
        ``bars``（E1010，不静默返回不复权数据）。要复权请显式指定
        ``provider="eastmoney"`` / ``"tencent"``，或改用 :meth:`adjusted_bars`
        （由本地除权事件自算，Provider 仍可走 tdx）。

        ``start_date`` / ``end_date``（``YYYY-MM-DD``，闭区间）给"取哪一段"一个比
        ``count``/``start`` 更自然的说法。协议侧只有"从最新往回数 ``count`` 根"这一种
        游标，所以区间是在取回的那一页上裁出来的——**那一页盖不住区间起点时会记一条
        ``bars_range_uncovered`` 告警**而不是假装完整，``strict=True`` 时它是一次失败。
        要更早的历史就加大 ``count``（或用 ``start`` 继续往回翻页）。

        ``index``：``0x052D`` 的指数位，**缺省按代码段自动推断**（``sh000001`` /
        ``sz399001`` 这类判指数）。指数 K 线每条记录尾部多 4 字节涨跌家数，这个位不开
        整条记录流就错位（日期变成 ``10112-28-10``、成交量变成 1e40），所以正常情况下
        不用管它；只有"按段表判错了"时才需要显式覆盖（例如某只代码段被重新划类）。
        """
        options: dict[str, Any] = {}
        if index is not None:
            options["index"] = index
        if strict:
            options["strict"] = True
        spec = QuerySpec.build(
            "bars",
            symbols=symbol,
            provider=provider,
            period=period,
            count=count,
            start=start,
            adjustment=adjustment,
            adjust=adjust,
            currentness=currentness,
            start_date=start_date,
            end_date=end_date,
            options=options or None,
        )
        if policy is not None:
            _reject_provider_with_policy(provider, policy)
            return self.execute_with_policy(spec, policy=policy)
        return self.execute(spec)

    def snapshot(
        self,
        symbol: str,
        *,
        provider: str = "tdx",
        currentness: str = "live",
    ) -> QueryResult[Any]:
        return self.runtime.snapshot(symbol, provider=provider, currentness=currentness)

    def minute(
        self,
        symbol: str,
        *,
        provider: str | None = None,
        currentness: str = "live",
    ) -> QueryResult[Any]:
        """当日分时。

        未指定 Provider 时由 Provider Registry 选择 operational provider（当前首选
        ``tencent``）。显式指定 ``provider="tdx"`` 走 ``0x0537``：请求体
        ``<H market><6s code><I 0>`` 与 leb128 差分布局已于 2026-10-21 由双市场
        golden 样本锁定（账本 L1 / verified），价格「基准 + 增量」与同日 ``0x052D``
        一分钟 K 线 240/240 对齐。也可用 Web Provider：``tencent`` /
        ``eastmoney`` / ``baidu``。一格 = 一次 HTTP 请求、一只代码；上游反爬时抛
        :class:`AntiSpiderBlocked` 或 :class:`WebSourceError`，不会返回空序列冒充成功。
        """
        return self.runtime.minute(
            symbol,
            provider=provider,
            currentness=currentness,
        )

    def trades(
        self,
        symbol: str,
        *,
        provider: str | None = None,
        start: int = 0,
        count: int = 0,
        currentness: str = "live",
    ) -> QueryResult[Any]:
        """当日逐笔成交。

        未指定 Provider 时由 Provider Registry 选择 operational provider（当前首选
        ``tencent``）。显式指定 ``provider="tdx"`` 走 ``0x0FC5``：请求体
        ``<H market><6s code><H start><H count>``（``start`` 自最新一笔往回数）
        与 leb128 逐笔累加布局已于 2026-10-21 由双市场 golden 样本锁定（账本
        L1 / verified），全日逐笔按分钟汇总与同日 ``0x052D`` 一分钟 K 线逐桶
        对账 237/240（余下 3 桶为竞价并入）。Web Provider 为 ``tencent`` /
        ``baidu``。
        """
        return self.runtime.trades(
            symbol,
            provider=provider,
            start=start,
            count=count,
            currentness=currentness,
        )

    def security_count(
        self,
        *,
        market: int | str = 0,
        provider: str = "tdx",
        currentness: str = "business",
    ) -> QueryResult[Any]:
        return self.runtime.security_count(
            market=market,
            provider=provider,
            currentness=currentness,
        )

    def security_list(
        self,
        *,
        market: int | str = 0,
        start: int = 0,
        provider: str = "tdx",
        currentness: str = "business",
    ) -> QueryResult[Any]:
        """代码表分页（tdx Provider 的 ``0x044D``）。

        **该面当前已下线**：``0x044D`` 在命令账本登记为 offline（多主站实测无响应），
        客户端在发包前 fail-fast，所以本调用总是抛 :class:`CommandOffline`（context 里带
        provider/channel/capability）而不是返回空页；保留 API 面是为了参数校正后接回。
        可用判据与推导见 ``docs/providers/tdx.md`` 的 quotation 能力清单。
        """
        return self.runtime.security_list(
            market=market,
            start=start,
            provider=provider,
            currentness=currentness,
        )

    def stream(
        self,
        symbols: str | Sequence[str],
        *,
        provider: str = "tdx",
        interval: float = 1.0,
        diff_only: bool = False,
        max_queue: int = 1024,
        on_quote: Any | None = None,
        on_error: Any | None = None,
    ) -> StatefulQuoteStream:
        plan = self.stream_planner.compile(
            StreamSpec.build(
                symbols,
                provider=provider,
                interval=interval,
                diff_only=diff_only,
                max_queue=max_queue,
            )
        )
        stream = StatefulQuoteStream(runtime=self.runtime, provider=plan.provider)
        stream.subscribe(
            plan.symbols,
            interval=plan.interval,
            diff_only=plan.diff_only,
            max_queue=plan.max_queue,
            on_quote=on_quote,
            on_error=on_error,
        )
        return stream

    # -- 一等公民便捷方法（只翻译为 call()，不引入第二条执行路径）------------ #
    # 这些能力此前只能经 execute/call/typed 到达，接口可发现性差。这里补的只是
    # "命名 + 转发"，参数语义、provider 选择、provenance 全部沿用 call()，架构
    # 守卫 tests/architecture/ 不因此出现新内核。

    def adjusted_bars(
        self,
        symbol: str,
        *,
        adjust: str = "qfq",
        method: str = "",
        period: str = "day",
        count: int = 320,
        start: int = 0,
        provider: str = "tdx",
        event_source: str = "eastmoney",
        **kwargs: Any,
    ) -> QueryResult[Any]:
        """复权 K 线（前/后/定点），一等公民入口；内部委托 ``call('adjusted_bars')``。

        ``adjust`` 与 ``method`` 是同一复权方式的两种拼法，``method`` 非空时胜出；
        二者都给不同值时以 ``method`` 为准（与内核 ``execute_composed`` 一致）。
        """
        chosen = method or adjust
        return self.call(
            "adjusted_bars",
            symbol,
            method=chosen,
            period=period,
            count=count,
            start=start,
            provider=provider,
            event_source=event_source,
            **kwargs,
        )

    def capital_changes(self, symbol: str, **kwargs: Any) -> QueryResult[Any]:
        """除权除息 / 股本变迁（0x000F），一等公民入口。"""
        return self.call("capital_changes", symbol, **kwargs)

    def finance(self, symbol: str, **kwargs: Any) -> QueryResult[Any]:
        """财务基础信息（0x0010），一等公民入口。"""
        return self.call("finance", symbol, **kwargs)

    def f10(self, symbol: str, filename: str = "", **kwargs: Any) -> QueryResult[Any]:
        """F10 资料目录（TDX F10 协议族），一等公民入口。"""
        return self.call("f10", symbol, filename=filename, **kwargs)

    def ex_bars(self, code: str, **kwargs: Any) -> QueryResult[Any]:
        """扩展市场（7727）K 线，一等公民入口。"""
        return self.call("ex_bars", code, **kwargs)

    def goods_bars(self, code: str, **kwargs: Any) -> QueryResult[Any]:
        """商品市场（7727 goods）K 线，一等公民入口。"""
        return self.call("goods_bars", code, **kwargs)

    def mac_quotes(self, symbol: str, **kwargs: Any) -> QueryResult[Any]:
        """MAC 专属族行情，一等公民入口。"""
        return self.call("mac_quotes", symbol, **kwargs)

    def local_finance(
        self,
        symbol: str,
        *,
        root: str | None = None,
        fields_per_record: int | None = None,
    ) -> list[dict[str, Any]]:
        """读本地 vipdoc ``gpcw*.dat`` 财务（离线，非查询内核路径）。

        与 :meth:`finance`（TDX 0x0010 在线财务）不同，这里直接消费本地财务文件，
        因此不返回 :class:`~atst.result.QueryResult`/provenance——它是纯本地文件读取，
        不是一次 Provider 查询。返回 ``list[dict]``，字段见
        :data:`atst.domain.finance.GPCW_FIELD_NAMES`。

        ``root`` 省略时用本 Client 的 ``vipdoc_root``（构造参数）；都没有则显式报错，
        不猜默认安装路径。gpcw 文件位于 ``<root>/<market>/gpcw*.dat``。
        """
        from ..domain.symbol import split_symbol
        from ..errors import ValidationError
        from ..reader import FinanceReader

        base = root or getattr(self.runtime.executor, "vipdoc_root", None)
        if not base:
            raise ValidationError(
                "local_finance 需要 root 或 Client(vipdoc_root=...)",
                context={"symbol": symbol},
            )
        market, code = split_symbol(symbol)
        if market not in {"sh", "sz", "bj"}:
            raise ValidationError("local_finance 仅支持沪深北本地目录", context={"symbol": symbol})
        from pathlib import Path

        candidates = sorted(Path(base).glob(f"{market}/gpcw*.dat"))
        if not candidates:
            return []
        reader = FinanceReader(fields_per_record=fields_per_record)
        rows = reader.read_indicators(candidates[-1], output="dict")
        return [dict(r, code=f"{market}{code}") for r in rows]


class AsyncClient:
    """Async facade over the exact same v13 semantic/runtime contracts."""

    def __init__(self, client: Client | None = None, **runtime_kwargs: Any) -> None:
        if client is not None and runtime_kwargs:
            raise ValueError("client and runtime kwargs are mutually exclusive")
        self.client = client or Client(**runtime_kwargs)
        self._owns_client = client is None
        self.stream_planner = StreamPlanner()

    async def close(self) -> None:
        """与 :meth:`Client.close` 同一条口径：只关自己造的那份，而且只关一次。"""
        if self._owns_client:
            self._owns_client = False
            await asyncio.to_thread(self.client.close)

    async def __aenter__(self) -> AsyncClient:
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.close()

    @staticmethod
    def capabilities() -> tuple[str, ...]:
        return Client.capabilities()

    def core_capability_statuses(self) -> dict[str, dict[str, Any]]:
        return self.client.core_capability_statuses()

    def provider_health_status(self) -> dict[str, dict[str, Any]]:
        return self.client.provider_health_status()

    @staticmethod
    def capability_statuses() -> dict[str, str]:
        return Client.capability_statuses()

    async def execute(
        self,
        spec: QuerySpec,
    ) -> QueryResult[Any]:
        return await asyncio.to_thread(self.client.execute, spec)

    async def call(
        self,
        capability: str,
        *args: Any,
        **kwargs: Any,
    ) -> QueryResult[Any]:
        return await asyncio.to_thread(self.client.call, capability, *args, **kwargs)

    async def execute_with_policy(
        self,
        spec: QuerySpec,
        *,
        policy: FallbackPolicy,
    ) -> OrchestratedResult:
        return await asyncio.to_thread(
            self.client.execute_with_policy,
            spec,
            policy=policy,
        )

    async def typed(self, query: Any, **kwargs: Any) -> Any:
        return await asyncio.to_thread(self.client.typed, query, **kwargs)

    async def quotes(self, symbols: str | Sequence[str], **kwargs: Any) -> Any:
        return await asyncio.to_thread(self.client.quotes, symbols, **kwargs)

    async def quotes_batch(self, symbols: Sequence[str], **kwargs: Any) -> Any:
        return await asyncio.to_thread(self.client.quotes_batch, symbols, **kwargs)

    async def bars(self, symbol: str, **kwargs: Any) -> Any:
        return await asyncio.to_thread(self.client.bars, symbol, **kwargs)

    async def snapshot(self, symbol: str, **kwargs: Any) -> Any:
        return await asyncio.to_thread(self.client.snapshot, symbol, **kwargs)

    async def minute(self, symbol: str, **kwargs: Any) -> Any:
        return await asyncio.to_thread(self.client.minute, symbol, **kwargs)

    async def trades(self, symbol: str, **kwargs: Any) -> Any:
        return await asyncio.to_thread(self.client.trades, symbol, **kwargs)

    async def security_count(self, **kwargs: Any) -> Any:
        return await asyncio.to_thread(self.client.security_count, **kwargs)

    async def security_list(self, **kwargs: Any) -> Any:
        return await asyncio.to_thread(self.client.security_list, **kwargs)

    def stream(
        self,
        symbols: str | Sequence[str],
        *,
        provider: str = "tdx",
        interval: float = 1.0,
        diff_only: bool = False,
        max_queue: int = 1024,
        on_quote: Any | None = None,
        on_error: Any | None = None,
    ) -> AsyncStatefulQuoteStream:
        plan = self.stream_planner.compile(
            StreamSpec.build(
                symbols,
                provider=provider,
                interval=interval,
                diff_only=diff_only,
                max_queue=max_queue,
            )
        )
        stream = AsyncStatefulQuoteStream(
            runtime=self.client.runtime,
            provider=plan.provider,
        )
        stream.subscribe(
            plan.symbols,
            interval=plan.interval,
            diff_only=plan.diff_only,
            max_queue=plan.max_queue,
            on_quote=on_quote,
            on_error=on_error,
        )
        return stream

    # -- 同步 facade 的一等公民方法镜像（全部 to_thread 转调，语义同源）-------- #
    async def adjusted_bars(self, symbol: str, **kwargs: Any) -> Any:
        return await asyncio.to_thread(self.client.adjusted_bars, symbol, **kwargs)

    async def capital_changes(self, symbol: str, **kwargs: Any) -> Any:
        return await asyncio.to_thread(self.client.capital_changes, symbol, **kwargs)

    async def finance(self, symbol: str, **kwargs: Any) -> Any:
        return await asyncio.to_thread(self.client.finance, symbol, **kwargs)

    async def f10(self, symbol: str, filename: str = "", **kwargs: Any) -> Any:
        return await asyncio.to_thread(self.client.f10, symbol, filename=filename, **kwargs)

    async def ex_bars(self, code: str, **kwargs: Any) -> Any:
        return await asyncio.to_thread(self.client.ex_bars, code, **kwargs)

    async def goods_bars(self, code: str, **kwargs: Any) -> Any:
        return await asyncio.to_thread(self.client.goods_bars, code, **kwargs)

    async def mac_quotes(self, symbol: str, **kwargs: Any) -> Any:
        return await asyncio.to_thread(self.client.mac_quotes, symbol, **kwargs)

    async def local_finance(
        self,
        symbol: str,
        *,
        root: str | None = None,
        fields_per_record: int | None = None,
    ) -> Any:
        return await asyncio.to_thread(
            self.client.local_finance,
            symbol,
            root=root,
            fields_per_record=fields_per_record,
        )
