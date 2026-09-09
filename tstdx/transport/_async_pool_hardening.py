# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""AsyncConnectionPool lifecycle hardening installed onto the legacy module.

``transport.async_`` is a long-lived public import path. Keep that module and
class identity while installing the missing async equivalents of the synchronous
circuit/request-health state machine. This module is imported once by
``tstdx.transport`` before callers can receive ``transport.async_.AsyncConnectionPool``.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from typing import Any

from ..errors import (
    ALL_HOSTS_UNREACHABLE_NEXT_STEPS,
    AllHostsUnreachable,
    ConnectionFailed,
    TdxError,
)
from ..observability import metrics
from . import async_ as _impl
from .pool import (
    BIZ_FAILURE_WEIGHT,
    CIRCUIT_COOLDOWN_SECONDS,
    CIRCUIT_DEGRADED_AT,
    CIRCUIT_OPEN_AT,
)

_LOG = logging.getLogger("tstdx.transport")


async def _circuit_allows(self: _impl.AsyncConnectionPool, host: Any) -> bool:
    """Async mirror of the synchronous single-probe circuit admission gate."""

    async with self._lock:
        if host.circuit == "open":
            if time.time() - host.circuit_opened_at < CIRCUIT_COOLDOWN_SECONDS:
                return False
            if host.circuit_probe_inflight:
                return False
            host.circuit = "half_open"
            host.circuit_probe_inflight = True
            _LOG.info("异步主站 %s 熔断冷却到期，转 HALF_OPEN 放行单次探测", host.key)
            return True
        if host.circuit == "half_open":
            if host.circuit_probe_inflight:
                return False
            host.circuit_probe_inflight = True
            return True
        return True


async def _mark_failure(
    self: _impl.AsyncConnectionPool,
    slot: _impl.AsyncSlot,
    exc: BaseException,
    *,
    generation: int | None = None,
    conn: _impl.AsyncTcpConnection | None = None,
) -> None:
    """Advance async live-health only when the producing generation is current."""

    current = generation is None or await self._slot_is_current(slot, generation)
    if current:
        async with self._lock:
            host = slot.host
            was_half_open = host.circuit == "half_open" or host.circuit_probe_inflight
            host.failures += 1
            if not isinstance(exc, ConnectionFailed):
                host.biz_failures += 1
            host.consec_weighted += (
                1.0 if isinstance(exc, ConnectionFailed) else BIZ_FAILURE_WEIGHT
            )
            host.circuit_probe_inflight = False
            if was_half_open or host.consec_weighted >= CIRCUIT_OPEN_AT:
                if host.circuit != "open" or host.circuit_opened_at == 0.0:
                    host.circuit_opened_at = time.time()
                host.circuit = "open"
            elif host.consec_weighted >= CIRCUIT_DEGRADED_AT:
                host.circuit = "degraded"
            host.last_error = f"{type(exc).__name__}: {exc}"
    metrics.record_error(type(exc).__name__)
    await self._drop(slot, expected=conn)


async def _mark_success(
    self: _impl.AsyncConnectionPool,
    slot: _impl.AsyncSlot,
    *,
    generation: int | None = None,
    rtt_ms: float | None = None,
) -> None:
    """Reset async live-health/circuit only for the current generation."""

    if generation is not None and not await self._slot_is_current(slot, generation):
        return
    async with self._lock:
        host = slot.host
        host.failures = 0
        host.biz_failures = 0
        host.last_error = ""
        host.last_ok = time.time()
        if rtt_ms is not None:
            host.live_rtt_ms = max(0.0, float(rtt_ms))
            host.live_ok_at = host.last_ok
        host.circuit = "healthy"
        host.circuit_probe_inflight = False
        host.consec_weighted = 0.0
        host.circuit_opened_at = 0.0


async def _request(
    self: _impl.AsyncConnectionPool,
    method: int,
    body: bytes = b"",
    *,
    timeout: float | None = None,
) -> _impl.ResponseFrame:
    """Async request path with sync-equivalent host coverage and circuit semantics."""

    self._ensure_open()
    max_attempts = self.max_retries + 1
    distinct_hosts = len({slot.host.key for slot in self._slots})
    max_attempts = max(max_attempts, distinct_hosts)
    last_exc: BaseException | None = None
    tried: list[str] = []
    started = time.perf_counter()

    for attempt in range(max_attempts):
        self._ensure_open()
        await self._acquire_rate()
        slots = self._ordered()
        if not slots:
            break
        candidates = [slot for slot in slots if slot.host.key not in tried] or slots
        slot: _impl.AsyncSlot | None = None
        for candidate in candidates:
            if await _circuit_allows(self, candidate.host):
                slot = candidate
                break
        if slot is None:
            last_exc = ConnectionFailed("所有候选主站均处于熔断门禁")
            break
        tried.append(slot.host.key)

        conn: _impl.AsyncTcpConnection | None = None
        generation: int | None = None
        attempt_started = time.perf_counter()
        try:
            conn, generation = await self._acquire_lease(slot)
            try:
                frame = await conn.request(method, body, timeout=timeout)
            finally:
                await self._release_lease(slot, conn)
        except TdxError as exc:
            last_exc = exc
            await _mark_failure(self, slot, exc, generation=generation, conn=conn)
            advice = exc.advice
            if attempt + 1 >= max_attempts or not advice.retryable:
                break
            if advice.switch_host:
                _LOG.info("异步换主站：离开 %s（attempt=%d）", slot.host.key, attempt)
                self._rotate_away(slot.host.key)
            if advice.backoff:
                delay = advice.backoff * (2**attempt) * (0.75 + 0.5 * random.random())
                await asyncio.sleep(delay)
            continue
        except asyncio.CancelledError:
            # Cancellation is caller/process control, never host health evidence.
            raise
        except Exception as exc:
            last_exc = exc
            await _mark_failure(self, slot, exc, generation=generation, conn=conn)
            if attempt + 1 >= max_attempts:
                break
            await asyncio.sleep(0.05 * (attempt + 1))
            continue

        await _mark_success(
            self,
            slot,
            generation=generation,
            rtt_ms=(time.perf_counter() - attempt_started) * 1000.0,
        )
        metrics.record_request(
            command=f"0x{method:04x}", ok=True, duration=time.perf_counter() - started
        )
        return frame

    metrics.record_request(command=f"0x{method:04x}", ok=False)
    raise AllHostsUnreachable(
        f"所有主站均不可达（已尝试 {tried}）。\n{ALL_HOSTS_UNREACHABLE_NEXT_STEPS}",
        context={
            "hosts": tried,
            "method": hex(method),
            "attempts": max_attempts,
            "last_error": str(last_exc) if last_exc else None,
        },
        cause=last_exc,
    )


async def _heartbeat_loop(self: _impl.AsyncConnectionPool) -> None:
    """Heartbeat mirror that feeds the same circuit/live-health state machine."""

    interval = max(1, int(self.heartbeat_interval or 0))
    while not self._closed:
        await asyncio.sleep(interval)
        for slot in list(self._slots):
            if self._closed:
                return
            if not await _circuit_allows(self, slot.host):
                continue
            conn: _impl.AsyncTcpConnection | None = None
            generation: int | None = None
            try:
                conn, generation = await self._acquire_lease(slot)
                idle = time.time() - (conn.stats.last_used or conn.stats.created_at)
                if idle < interval:
                    await self._release_lease(slot, conn)
                    # A HALF_OPEN token must not be consumed by an idle check.
                    if slot.host.circuit == "half_open":
                        async with self._lock:
                            slot.host.circuit_probe_inflight = False
                    continue
                try:
                    rtt = await conn.ping(self.heartbeat_cmd)
                finally:
                    await self._release_lease(slot, conn)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                await _mark_failure(self, slot, exc, generation=generation, conn=conn)
            else:
                await _mark_success(self, slot, generation=generation, rtt_ms=rtt)


# Install onto the canonical public class rather than publishing a second class.
# ``setattr`` keeps this compatibility bridge independent of mypy's method-assign
# diagnostic code names, so --warn-unused-ignores remains meaningful.
setattr(_impl.AsyncConnectionPool, "_circuit_allows", _circuit_allows)
setattr(_impl.AsyncConnectionPool, "_mark_failure", _mark_failure)
setattr(_impl.AsyncConnectionPool, "_mark_success", _mark_success)
setattr(_impl.AsyncConnectionPool, "request", _request)
setattr(_impl.AsyncConnectionPool, "_heartbeat_loop", _heartbeat_loop)
