# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""Bridge between v14 orchestration Runtime and v13 UnifiedRuntime execution engine.

The :class:`LegacyRuntimeBridge` wraps a v13 :class:`UnifiedRuntime` instance,
providing the full L1/L2/negative-cache/SingleFlight execution stack that v14's
:class:`~tstdx.execution.semantic.SemanticExecutionAdapter` delegates to.

Design principles:

- **No duplicate logic** — the bridge is a thin delegation layer.
- **No new execution path** — all cache, negative-cache, singleflight, and
  provider execution live in ``UnifiedRuntime``.
- **Backwards compatible** — when no bridge is injected, ``SemanticExecutionAdapter``
  falls back to its own L1-only path via ``router.query()``.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from ..batch import BatchResult
from ..query import QuerySpec
from ..result import QueryResult
from ..runtime_v13 import UnifiedRuntime

__all__ = ["LegacyRuntimeBridge"]

_CORE_CAPABILITIES = frozenset(
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


class LegacyRuntimeBridge:
    """Execution engine bridge — v14 Runtime delegates here for real work.

    Holds a v13 :class:`UnifiedRuntime` that owns the full cache / negative-cache
    / SingleFlight / DirectProviderExecutor stack.  v14's
    :class:`SemanticExecutionAdapter` calls :meth:`execute_spec` when a bridge
    is injected, gaining L2/negative/singleflight for free.
    """

    def __init__(
        self,
        runtime: UnifiedRuntime | None = None,
        **runtime_kwargs: Any,
    ) -> None:
        self.runtime = runtime or UnifiedRuntime(**runtime_kwargs)
        self._owns_runtime = runtime is None

    def close(self) -> None:
        if self._owns_runtime:
            self.runtime.close()

    # -- Spec-level execution (used by SemanticExecutionAdapter) ----------- #

    def execute_spec(
        self,
        spec: QuerySpec,
    ) -> QueryResult[Any]:
        """Execute a compiled :class:`QuerySpec` through v13's full pipeline."""
        return self.runtime.execute(spec)

    # -- Business methods (mirrors UnifiedRuntime API) --------------------- #

    def quotes(
        self,
        symbols: str | Sequence[str],
        *,
        provider: str | None = None,
        currentness: str = "live",
        max_age: float | None = None,
    ) -> QueryResult[Any]:
        return self.runtime.quotes(
            symbols,
            provider=provider,
            currentness=currentness,
            max_age=max_age,
        )

    def quotes_batch(
        self,
        symbols: Sequence[str],
        *,
        provider: str | None = None,
        currentness: str = "live",
        max_age: float | None = None,
    ) -> BatchResult[QueryResult[Any]]:
        return self.runtime.quotes_batch(
            symbols,
            provider=provider,
            currentness=currentness,
            max_age=max_age,
        )

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
        return self.runtime.bars(
            symbol,
            provider=provider,
            period=period,
            count=count,
            start=start,
            adjustment=adjustment,
            currentness=currentness,
            max_age=max_age,
        )

    def snapshot(
        self,
        symbol: str,
        *,
        provider: str | None = None,
        currentness: str = "live",
        max_age: float | None = None,
    ) -> QueryResult[Any]:
        return self.runtime.snapshot(
            symbol,
            provider=provider,
            currentness=currentness,
            max_age=max_age,
        )

    def minute(
        self,
        symbol: str,
        *,
        provider: str | None = None,
        currentness: str = "live",
        max_age: float | None = None,
    ) -> QueryResult[Any]:
        return self.runtime.minute(
            symbol,
            provider=provider,
            currentness=currentness,
            max_age=max_age,
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
        return self.runtime.trades(
            symbol,
            provider=provider,
            start=start,
            count=count,
            currentness=currentness,
            max_age=max_age,
        )

    def security_count(
        self,
        *,
        market: int | str = 0,
        provider: str | None = None,
        currentness: str = "business",
        max_age: float | None = None,
    ) -> QueryResult[Any]:
        return self.runtime.security_count(
            market=market,
            provider=provider,
            currentness=currentness,
            max_age=max_age,
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
        return self.runtime.security_list(
            market=market,
            start=start,
            provider=provider,
            currentness=currentness,
            max_age=max_age,
        )

    # -- Capability dispatch ----------------------------------------------- #

    def call(
        self,
        capability: str,
        *args: Any,
        provider: str | None = None,
        currentness: str = "business",
        max_age: float | None = None,
        **kwargs: Any,
    ) -> QueryResult[Any]:
        """Dispatch a capability by name through the v13 UnifiedRuntime."""
        cap = str(capability).strip().lower()
        if cap not in _CORE_CAPABILITIES:
            from ..errors import ValidationError

            raise ValidationError(
                f"LegacyRuntimeBridge does not support capability {capability!r}",
                context={"capability": cap},
            )

        if cap == "quotes":
            if len(args) != 1:
                raise ValueError("quotes requires exactly one symbols argument")
            return self.quotes(
                args[0],
                provider=provider,
                currentness=currentness,
                max_age=max_age,
                **kwargs,
            )
        if cap == "bars":
            if len(args) != 1:
                raise ValueError("bars requires exactly one symbol argument")
            return self.bars(
                str(args[0]),
                provider=provider,
                currentness=currentness,
                max_age=max_age,
                **kwargs,
            )
        if cap == "snapshot":
            if len(args) != 1:
                raise ValueError("snapshot requires exactly one symbol argument")
            return self.snapshot(
                str(args[0]),
                provider=provider or "tdx",
                **kwargs,
            )
        if cap == "minute":
            if len(args) != 1:
                raise ValueError("minute requires exactly one symbol argument")
            return self.minute(
                str(args[0]),
                provider=provider or "tdx",
                **kwargs,
            )
        if cap == "trades":
            if len(args) != 1:
                raise ValueError("trades requires exactly one symbol argument")
            return self.trades(
                str(args[0]),
                provider=provider or "tdx",
                start=kwargs.pop("start", 0),
                count=kwargs.pop("count", 0),
                **kwargs,
            )
        if cap == "security_count":
            return self.security_count(
                provider=provider or "tdx",
                **kwargs,
            )
        if cap == "security_list":
            return self.security_list(
                provider=provider or "tdx",
                **kwargs,
            )
        raise RuntimeError(f"unreachable: {cap}")
