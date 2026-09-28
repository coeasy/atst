# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""v13 public Client API: the sole high-level business surface."""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Mapping, Sequence
from dataclasses import asdict, is_dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from ..batch import BatchResult
from ..catalog.capability import (
    MIGRATED_CAPABILITIES,
    default_provider_for,
    is_migrated_capability,
)
from ..errors import ValidationError
from ..query import QuerySpec
from ..providers import PROVIDERS, resolve_capability_provider
from ..result import QueryResult
from ..runtime.executor import DEDICATED_CAPABILITIES as _CORE_CAPABILITIES
from ..runtime.kernel import UnifiedRuntime
from ..runtime.orchestration import FallbackPolicy, OrchestratedResult, ProviderOrchestrator
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


def _reject_provider_with_policy(provider: str | None, policy: FallbackPolicy) -> None:
    """``provider`` 与 ``fallback`` 同时给出＝同时要求「只走这家」和「这家失败就换」。

    这里必须是 :class:`ValidationError`（E1010 / HTTP 422 / JSON-RPC -32602 / CLI 退出码 2），
    而不是原来那个裸 ``ValueError``：入参冲突的归类发生在**抛出点**，四面只是转述它，所以
    一次选错类型的 raise 会让四张面**一致地**把它说成 ``E9000`` 服务器故障、把调用方写错的
    那两个键从消息里抹掉（实测四面同读数，见 ``docs/REFACTOR_PLAN_V18_RESTRUCTURE.md``
    第 19 节）。四面各自加转换只会把这第七份抄件再抄四遍。
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

    @staticmethod
    def core_capability_statuses() -> dict[str, dict[str, Any]]:
        """Return operational status for the canonical core capability surface.

        Declaration and operational availability are deliberately separate:
        explicit TDX minute/trades/security-list calls still preserve their
        protocol-specific fail-fast errors, while omitted-provider calls may use
        another Provider only when the caller did not pin a Provider.
        """

        values: dict[str, dict[str, Any]] = {}
        for capability in sorted(_CORE_CAPABILITIES):
            declared = tuple(
                provider
                for provider in PROVIDERS.ids()
                if PROVIDERS.get(provider).supports(capability)
            )
            available = PROVIDERS.available_providers(capability)
            values[capability] = {
                "available": bool(available),
                "declared_providers": list(declared),
                "operational_providers": list(available),
                "default_provider": PROVIDERS.default_available_provider(capability),
            }
        return values

    def execute(self, spec: QuerySpec) -> QueryResult[Any]:
        return self.runtime.execute(spec)

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
        selected = provider or default_provider_for(cap)
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

    def __getattr__(self, name: str) -> Any:
        if is_migrated_capability(name):

            def migrated(*args: Any, **kwargs: Any) -> QueryResult[Any]:
                provider = kwargs.pop("provider", None)
                channel = kwargs.pop("channel", None)
                currentness = kwargs.pop("currentness", "business")
                return self.call(
                    name,
                    *args,
                    provider=provider,
                    channel=channel,
                    currentness=currentness,
                    **kwargs,
                )

            migrated.__name__ = name
            migrated.__qualname__ = f"Client.{name}"
            return migrated
        raise AttributeError(name)

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
    ) -> QueryResult[Any] | OrchestratedResult:
        spec = QuerySpec.build(
            "quotes",
            symbols=symbols,
            provider=provider,
            currentness=currentness,
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
        return self.runtime.quotes_batch(
            symbols,
            provider=provider,
            currentness=currentness,
        )

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
        currentness: str = "historical",
        strict: bool = False,
    ) -> QueryResult[Any] | OrchestratedResult:
        """K 线。``strict=True`` 时"结果带瑕疵"直接失败，而不是返回后靠调用方自查。

        执行器把一次查询里记录的全部数据完整性瑕疵装进
        :attr:`~atst.result.ResultMeta.warnings`（空桩首页、锚点漂移截断等）；
        ``strict`` 就是那份名单的开关，任何 Provider、任何瑕疵类别同一条判据。
        """
        spec = QuerySpec.build(
            "bars",
            symbols=symbol,
            provider=provider,
            period=period,
            count=count,
            start=start,
            adjustment=adjustment,
            currentness=currentness,
            options={"strict": True} if strict else None,
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
        ``tencent``）。显式指定 ``provider="tdx"`` 仍保持协议事实：TDX 的 ``0x0537``
        request/parser 仍为 inferred，客户端在发包前抛 :class:`NotImplementedFeature`
        （真机 golden 锁定前不通过结构化 API 发包）。可用 Web Provider 为 ``tencent`` /
        ``eastmoney`` / ``baidu``。一格 = 一次 HTTP 请求、一只代码；上游反爬时抛
        :class:`AntiSpiderBlocked` 或 :class:`WebSourceError`，不会返回空序列冒充成功。
        """
        return self.runtime.minute(
            symbol,
            provider=resolve_capability_provider("minute", provider),
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
        ``tencent``）。显式 ``provider="tdx"`` 仍走 ``0x0FC5``，其 request/parser
        仍为 inferred，发包前即抛 :class:`NotImplementedFeature`；可用 Web Provider
        包括 ``tencent`` / ``baidu``。
        """
        return self.runtime.trades(
            symbol,
            provider=resolve_capability_provider("trades", provider),
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

    def __getattr__(self, name: str) -> Any:
        if is_migrated_capability(name):

            async def migrated(*args: Any, **kwargs: Any) -> QueryResult[Any]:
                return await asyncio.to_thread(
                    getattr(self.client, name),
                    *args,
                    **kwargs,
                )

            migrated.__name__ = name
            migrated.__qualname__ = f"AsyncClient.{name}"
            return migrated
        raise AttributeError(name)

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
