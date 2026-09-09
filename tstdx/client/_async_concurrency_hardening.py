# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""Async concurrent-quote parity with the canonical synchronous contract."""

from __future__ import annotations

import asyncio
from typing import Any

from ..client_core import _emit, _normalize_symbols, _require_int, _require_output_format
from . import async_ as _impl


async def _quotes_concurrent(
    self: _impl.AsyncTdxClient,
    symbols: Any,
    *,
    workers: int = 8,
    as_format: _impl.OutputFormat = "dict",
) -> Any:
    """Fetch canonical dict rows concurrently, then convert output exactly once."""

    output_format = _require_output_format(as_format)
    syms = _normalize_symbols(symbols)
    worker_count = _require_int("workers", workers, minimum=1, maximum=64)
    if not syms:
        self.last_errors = []
        return _emit([], output_format)

    semaphore = asyncio.Semaphore(min(worker_count, len(syms)))

    async def _one(
        pair: tuple[int, str],
    ) -> tuple[int, dict[str, Any] | None, list[tuple[str, BaseException]]]:
        index, symbol = pair
        local_errors: list[tuple[str, BaseException]] = []
        async with semaphore:
            try:
                rows = await self.quotes(
                    [symbol],
                    as_format="dict",
                    _collect=local_errors,
                )
                row = rows[0] if rows else None
            except Exception as exc:  # noqa: BLE001 - isolate one-symbol failures
                local_errors.append((symbol, exc))
                row = None
        return index, row, local_errors

    completed = await asyncio.gather(*(_one(pair) for pair in enumerate(syms)))
    out: list[dict[str, Any] | None] = [None] * len(syms)
    collected: list[tuple[str, BaseException]] = []
    for index, row, local_errors in completed:
        out[index] = row
        collected.extend(local_errors)

    self.last_errors = collected
    canonical = [row for row in out if row is not None]
    return _emit(canonical, output_format)


setattr(_impl.AsyncTdxClient, "quotes_concurrent", _quotes_concurrent)
