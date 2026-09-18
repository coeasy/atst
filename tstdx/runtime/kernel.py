# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""Canonical zero-cache provider-first execution kernel.

Every :meth:`UnifiedRuntime.execute` compiles one exact single-Provider plan
and requests the bound Provider directly. No result, negative, promotion or
request-coalescing cache exists on this path.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Protocol

from ..batch import BatchItem, BatchResult
from ..direct_provider import DirectProviderExecutor
from ..domain.symbol import normalize_symbol
from ..query import QueryPlan, QueryPlanner, QuerySpec
from ..result import QueryResult
from ..runtime_audit import audit_runtime
from ..runtime_identity import execution_identity_from_plan
from ..runtime_provenance import validate_runtime_provenance

__all__ = ["KernelExecutor", "UnifiedRuntime"]


class KernelExecutor(Protocol):
    """Injection seam for tests: one already-compiled plan in, QueryResult out."""

    def execute(self, plan: QueryPlan) -> QueryResult[Any]: ...


class UnifiedRuntime:
    """Compile one exact Provider plan and execute it against that Provider."""

    def __init__(
        self,
        *,
        default_provider: str = "tdx",
        timeout: float = 5.0,
        hosts: list[str] | None = None,
        vipdoc_root: str | None = None,
        executor: KernelExecutor | None = None,
    ) -> None:
        audit_runtime()
        self.planner = QueryPlanner(default_provider=default_provider)
        self.executor: KernelExecutor = executor or DirectProviderExecutor(
            timeout=timeout,
            hosts=hosts,
            vipdoc_root=vipdoc_root,
        )

    def close(self) -> None:
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
        max_age: float | None = None,
    ) -> QueryResult[Any]:
        return self.execute(
            QuerySpec.build(
                "quotes",
                symbols=symbols,
                provider=provider,
                currentness=currentness,
                max_age=max_age,
            )
        )

    def quotes_batch(
        self,
        symbols: Sequence[str],
        *,
        provider: str | None = None,
        currentness: str = "live",
        max_age: float | None = None,
    ) -> BatchResult[QueryResult[Any]]:
        """Execute independently auditable quote requests without hidden fallback."""
        items: dict[str, BatchItem[QueryResult[Any]]] = {}
        for raw_symbol in symbols:
            symbol = normalize_symbol(raw_symbol)
            if symbol in items:
                continue
            try:
                result = self.quotes(
                    symbol,
                    provider=provider,
                    currentness=currentness,
                    max_age=max_age,
                )
            except Exception as exc:
                items[symbol] = BatchItem("failed", error=exc)
                continue
            items[symbol] = (
                BatchItem("missing")
                if not result.data
                else BatchItem("ok", value=result)
            )
        return BatchResult.build(items)

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
        max_age: float | None = None,
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
                max_age=max_age,
            )
        )

    def snapshot(
        self,
        symbol: str,
        *,
        provider: str | None = None,
        currentness: str = "live",
        max_age: float | None = None,
    ) -> QueryResult[Any]:
        return self.execute(
            QuerySpec.build(
                "snapshot",
                symbols=symbol,
                provider=provider,
                currentness=currentness,
                max_age=max_age,
            )
        )

    def minute(
        self,
        symbol: str,
        *,
        provider: str | None = None,
        currentness: str = "live",
        max_age: float | None = None,
    ) -> QueryResult[Any]:
        return self.execute(
            QuerySpec.build(
                "minute",
                symbols=symbol,
                provider=provider,
                currentness=currentness,
                max_age=max_age,
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
        max_age: float | None = None,
    ) -> QueryResult[Any]:
        return self.execute(
            QuerySpec.build(
                "trades",
                symbols=symbol,
                provider=provider,
                start=start,
                count=count,
                currentness=currentness,
                max_age=max_age,
            )
        )

    def security_count(
        self,
        *,
        market: int | str = 0,
        provider: str | None = None,
        currentness: str = "business",
        max_age: float | None = None,
    ) -> QueryResult[Any]:
        return self.execute(
            QuerySpec.build(
                "security_count",
                provider=provider,
                options={"market": market},
                currentness=currentness,
                max_age=max_age,
            )
        )

    def security_list(
        self,
        *,
        market: int | str = 0,
        start: int = 0,
        provider: str | None = None,
        currentness: str = "business",
        max_age: float | None = None,
    ) -> QueryResult[Any]:
        return self.execute(
            QuerySpec.build(
                "security_list",
                provider=provider,
                start=start,
                options={"market": market},
                currentness=currentness,
                max_age=max_age,
            )
        )
