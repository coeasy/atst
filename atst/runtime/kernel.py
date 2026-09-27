# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""Canonical zero-cache provider-first execution kernel.

Every :meth:`UnifiedRuntime.execute` compiles one exact single-Provider plan
and requests the bound Provider directly. No result/negative/coalescing cache
exists on this path.

The kernel is also the single consumer of the configuration surface: an
explicit constructor argument wins, otherwise the value comes from
:class:`~atst.config.schema.Config` (defaults → config files → environment →
caller overrides). A key that the kernel does not read does not exist in the
schema.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Protocol

from ..batch import BatchItem, BatchResult
from ..config import Config, get_config
from ..domain.symbol import normalize_symbol
from ..error_envelope import ErrorEnvelope, to_error_envelope
from ..query import QueryPlan, QueryPlanner, QuerySpec
from ..result import QueryResult
from .audit import audit_runtime
from .executor import DirectProviderExecutor
from .identity import execution_identity_from_plan
from .provenance import validate_runtime_provenance

__all__ = ["KernelExecutor", "UnifiedRuntime"]


class KernelExecutor(Protocol):
    """Injection seam for tests: one already-compiled plan in, QueryResult out."""

    def execute(self, plan: QueryPlan) -> QueryResult[Any]: ...


class UnifiedRuntime:
    """Compile one exact Provider plan and execute it against that Provider."""

    def __init__(
        self,
        *,
        config: Config | None = None,
        default_provider: str | None = None,
        timeout: float | None = None,
        hosts: Sequence[Any] | None = None,
        vipdoc_root: str | None = None,
        executor: KernelExecutor | None = None,
    ) -> None:
        audit_runtime()
        cfg = (config if config is not None else get_config()).validate()
        self.config = cfg
        self.planner = QueryPlanner(
            default_provider=(
                cfg.core.default_provider if default_provider is None else default_provider
            )
        )
        configured_hosts: Sequence[Any] | None = (
            list(cfg.hosts.servers) if cfg.hosts.servers else None
        )
        self.executor: KernelExecutor = executor or DirectProviderExecutor(
            timeout=cfg.core.timeout if timeout is None else timeout,
            hosts=hosts if hosts is not None else configured_hosts,
            vipdoc_root=(cfg.core.vipdoc_root if vipdoc_root is None else vipdoc_root),
            config=cfg,
        )

    def close(self) -> None:
        """把收尾递给执行器——**只有它自己定义了** ``close()`` 的时候。

        内置的 ``DirectProviderExecutor`` 不定义这个方法：家族客户端不在它身上，每一跳
        都是构造 → ``try`` → ``finally: client.close()``。所以默认内核上这里是一次空转，
        它服务的是注入执行器（自带连接池、心跳线程或别的外设）那条口子。判据
        ``tests/runtime/test_close_chain_ownership.py``。
        """
        close = getattr(self.executor, "close", None)
        if callable(close):
            close()

    def __enter__(self) -> UnifiedRuntime:
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def execute(self, spec: QuerySpec) -> QueryResult[Any]:
        plan = self.planner.compile(spec)
        result = self.executor.execute(plan)
        validate_runtime_provenance(execution_identity_from_plan(plan), result)
        return result

    def quotes(
        self,
        symbols: str | Sequence[str],
        *,
        provider: str | None = None,
        currentness: str = "live",
    ) -> QueryResult[Any]:
        return self.execute(
            QuerySpec.build(
                "quotes",
                symbols=symbols,
                provider=provider,
                currentness=currentness,
            )
        )

    def quotes_batch(
        self,
        symbols: Sequence[str],
        *,
        provider: str | None = None,
        currentness: str = "live",
    ) -> BatchResult[QueryResult[Any]]:
        """Execute independently auditable quote requests without hidden fallback.

        ``items`` 与 ``errors`` 是同一批符号的两种视角：``items`` 逐项带状态
        （``ok`` / ``missing`` / ``failed``），``errors`` 只给失败的符号挂上可越界的
        :class:`~atst.error_envelope.ErrorEnvelope`；``requested`` 是归一、去重之后
        真正向 Provider 发出的那份名单。三者齐了，``partial`` / ``status_for`` /
        ``to_dict()`` 才有真源——此前这条生产路径只喂 ``items``，于是
        ``to_dict()["requested"]`` 恒为 ``[]``、``errors`` 恒为 ``{}``，而 ``partial``
        在一个确有符号失败的批次上仍然恒为 ``False``（``requested`` 还兼作
        ``errors ⊆ requested`` 那条不变量的一半）。
        """
        items: dict[str, BatchItem[QueryResult[Any]]] = {}
        errors: dict[str, ErrorEnvelope] = {}
        for raw_symbol in symbols:
            symbol = normalize_symbol(raw_symbol)
            if symbol in items:
                continue
            try:
                result = self.quotes(
                    symbol,
                    provider=provider,
                    currentness=currentness,
                )
            except Exception as exc:
                items[symbol] = BatchItem("failed", error=exc)
                errors[symbol] = to_error_envelope(exc)
                continue
            items[symbol] = (
                BatchItem("missing") if not result.data else BatchItem("ok", value=result)
            )
        return BatchResult.build(items, errors=errors, requested=tuple(items))

    def bars(
        self,
        symbol: str,
        *,
        provider: str | None = None,
        period: str = "day",
        count: int = 320,
        start: int = 0,
        adjustment: str = "",
        currentness: str = "historical",
    ) -> QueryResult[Any]:
        return self.execute(
            QuerySpec.build(
                "bars",
                symbols=symbol,
                provider=provider,
                period=period,
                count=count,
                start=start,
                adjustment=adjustment,
                currentness=currentness,
            )
        )

    def snapshot(
        self,
        symbol: str,
        *,
        provider: str | None = None,
        currentness: str = "live",
    ) -> QueryResult[Any]:
        return self.execute(
            QuerySpec.build(
                "snapshot",
                symbols=symbol,
                provider=provider,
                currentness=currentness,
            )
        )

    def minute(
        self,
        symbol: str,
        *,
        provider: str | None = None,
        currentness: str = "live",
    ) -> QueryResult[Any]:
        return self.execute(
            QuerySpec.build(
                "minute",
                symbols=symbol,
                provider=provider,
                currentness=currentness,
            )
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
        return self.execute(
            QuerySpec.build(
                "trades",
                symbols=symbol,
                provider=provider,
                start=start,
                count=count,
                currentness=currentness,
            )
        )

    def security_count(
        self,
        *,
        market: int | str = 0,
        provider: str | None = None,
        currentness: str = "business",
    ) -> QueryResult[Any]:
        return self.execute(
            QuerySpec.build(
                "security_count",
                provider=provider,
                options={"market": market},
                currentness=currentness,
            )
        )

    def security_list(
        self,
        *,
        market: int | str = 0,
        start: int = 0,
        provider: str | None = None,
        currentness: str = "business",
    ) -> QueryResult[Any]:
        return self.execute(
            QuerySpec.build(
                "security_list",
                provider=provider,
                start=start,
                options={"market": market},
                currentness=currentness,
            )
        )
