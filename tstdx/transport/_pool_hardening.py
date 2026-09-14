# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""Synchronous ConnectionPool circuit/lifecycle hardening.

The v1.0 generation/lease model is preserved. This module closes the remaining
admission holes without changing the public ``tstdx.transport.pool`` import path:
only one HALF_OPEN candidate may claim a probe token, process-control signals
release that token without becoming host failures, multi-frame paths respect the
same circuit gate, and caller truncation never returns a dirty socket to the pool.
"""

from __future__ import annotations

import logging
import math
import random
import threading
import time
from collections.abc import Iterator
from typing import Any

from ..errors import (
    ALL_HOSTS_UNREACHABLE_NEXT_STEPS,
    AllHostsUnreachable,
    ConfigError,
    ConnectionFailed,
    FramingError,
    TdxError,
)
from ..observability import metrics
from . import pool as _impl

_LOG = logging.getLogger("tstdx.transport")


def _select_allowed_slot(
    self: _impl.ConnectionPool,
    *,
    exclude_hosts: set[str] | None = None,
) -> _impl.Slot | None:
    """Sequentially claim at most one circuit admission token."""

    slots = self._ordered_slots()
    if exclude_hosts:
        preferred = [slot for slot in slots if slot.host.key not in exclude_hosts]
        candidates = preferred or slots
    else:
        candidates = slots
    for candidate in candidates:
        if self._circuit_allows(candidate.host):
            return candidate
    return None


def _release_probe_token(
    self: _impl.ConnectionPool,
    slot: _impl.Slot,
    generation: int | None,
) -> None:
    """Release one HALF_OPEN token without fabricating health evidence."""

    if generation is not None and not self._slot_is_current(slot, generation):
        return
    with self._lock:
        if slot.host.circuit == "half_open":
            slot.host.circuit_probe_inflight = False


def _require_positive_frame_limit(value: Any, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ConfigError(f"{field} 必须是正整数，收到 {value!r}")
    return value


def _require_bool_option(value: Any, *, field: str) -> bool:
    if not isinstance(value, bool):
        raise ConfigError(f"{field} 必须是 bool，收到 {value!r}")
    return value


def _require_optional_bool_option(value: Any, *, field: str) -> bool | None:
    if value is not None and not isinstance(value, bool):
        raise ConfigError(f"{field} 必须是 bool 或 None，收到 {value!r}")
    return value


def _require_request_timeout(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigError(f"timeout 必须是正有限数值或 None，收到 {value!r}")
    timeout = float(value)
    if not math.isfinite(timeout) or timeout <= 0:
        raise ConfigError(f"timeout 必须是正有限数值或 None，收到 {value!r}")
    return timeout


def _request(
    self: _impl.ConnectionPool,
    method: int,
    body: bytes = b"",
    *,
    timeout: float | None = None,
    retry: bool | None = None,
    compress: bool = False,
) -> _impl.ResponseFrame:
    """Single-frame request with one-at-a-time HALF_OPEN admission."""

    retry_enabled = _require_optional_bool_option(retry, field="retry")
    compress_enabled = _require_bool_option(compress, field="compress")
    request_timeout = _require_request_timeout(timeout)
    self._ensure_open()
    max_attempts = (
        (self.max_retries + 1) if (retry_enabled is None or retry_enabled) else 1
    )
    if retry_enabled is None or retry_enabled:
        distinct_hosts = len({slot.host.key for slot in self._slots})
        max_attempts = max(max_attempts, distinct_hosts)
    last_exc: BaseException | None = None
    tried_hosts: list[str] = []
    started = time.perf_counter()

    for attempt in range(max_attempts):
        self._ensure_open()
        if self.rate_limiter is not None:
            self.rate_limiter.acquire()
        slot = _select_allowed_slot(self, exclude_hosts=set(tried_hosts))
        if slot is None:
            self.stats.circuit_skips += 1
            last_exc = ConnectionFailed("所有候选主站均处于熔断门禁")
            break
        tried_hosts.append(slot.host.key)

        leased_conn: _impl.TcpConnection | None = None
        leased_generation: int | None = None
        attempt_started = time.perf_counter()
        try:
            with self._lease(slot) as (conn, generation):
                leased_conn = conn
                leased_generation = generation
                frame = conn.request(
                    method,
                    body,
                    timeout=request_timeout,
                    compress=compress_enabled,
                )
        except TdxError as exc:
            last_exc = exc
            self.stats.failures += 1
            self._mark_failure(slot, exc, generation=leased_generation, conn=leased_conn)
            advice = exc.advice
            if attempt + 1 >= max_attempts or not advice.retryable:
                break
            if advice.switch_host:
                self.stats.host_switches += 1
                _LOG.info("换主站：离开 %s（attempt=%d）", slot.host.key, attempt)
                self._rotate_away(slot.host.key)
            self.stats.retries += 1
            if advice.backoff:
                delay = advice.backoff * (2**attempt) * (0.75 + 0.5 * random.random())
                _LOG.debug("退避 %.3fs 后重试（%s）", delay, type(exc).__name__)
                time.sleep(delay)
            continue
        except Exception as exc:
            last_exc = exc
            self._mark_failure(slot, exc, generation=leased_generation, conn=leased_conn)
            if attempt + 1 >= max_attempts:
                break
            self.stats.retries += 1
            time.sleep(0.05 * (attempt + 1))
            continue
        except BaseException:
            # KeyboardInterrupt/SystemExit remain control flow. The connection
            # lease has already been returned by the context manager; release
            # only our HALF_OPEN admission token and propagate unchanged.
            _release_probe_token(self, slot, leased_generation)
            raise

        self.stats.requests += 1
        self.stats.frames += 1
        self._mark_success(
            slot,
            generation=leased_generation,
            rtt_ms=(time.perf_counter() - attempt_started) * 1000.0,
        )
        metrics.record_request(
            command=f"0x{method:04x}", ok=True, duration=time.perf_counter() - started
        )
        return frame

    metrics.record_request(command=f"0x{method:04x}", ok=False)
    raise AllHostsUnreachable(
        f"所有主站均不可达（已尝试 {tried_hosts}）。\n{ALL_HOSTS_UNREACHABLE_NEXT_STEPS}",
        context={
            "hosts": tried_hosts,
            "method": hex(method),
            "attempts": max_attempts,
            "last_error": str(last_exc) if last_exc else None,
        },
        cause=last_exc,
    )


def _request_multi(
    self: _impl.ConnectionPool,
    method: int,
    body: bytes = b"",
    *,
    record_size: int | None = None,
    expect_count: bool = True,
    max_frames: int = 512,
    timeout: float | None = None,
) -> _impl.ResponseFrame:
    """Multi-frame request that never bypasses circuit or reuses truncated sockets."""

    frame_limit = _require_positive_frame_limit(max_frames, field="max_frames")
    if record_size is not None:
        _require_positive_frame_limit(record_size, field="record_size")
    expect_count_enabled = _require_bool_option(expect_count, field="expect_count")
    request_timeout = _require_request_timeout(timeout)
    self._ensure_open()
    if self.rate_limiter is not None:
        self.rate_limiter.acquire()
    slot = _select_allowed_slot(self)
    if slot is None:
        self.stats.circuit_skips += 1
        raise AllHostsUnreachable(
            f"所有主站均处于熔断门禁。\n{ALL_HOSTS_UNREACHABLE_NEXT_STEPS}",
            context={"hosts": [], "method": hex(method), "attempts": 0},
            cause=ConnectionFailed("所有候选主站均处于熔断门禁"),
        )

    conn: _impl.TcpConnection | None = None
    generation: int | None = None
    try:
        conn, generation = self._acquire_lease(slot)
    except TdxError as exc:
        self._mark_failure(slot, exc, generation=generation, conn=conn)
        raise
    except BaseException:
        _release_probe_token(self, slot, generation)
        raise

    first_exc: TdxError | None = None
    stream_exc: TdxError | None = None
    merged: _impl.ResponseFrame | None = None
    started = time.perf_counter()
    try:
        with conn._lock:
            try:
                first = conn.request(method, body, timeout=request_timeout)
            except TdxError as exc:
                first_exc = exc
            else:
                self.stats.frames += 1
                payload = first.payload
                if not expect_count_enabled or len(payload) < 2:
                    merged = first
                else:
                    count = int.from_bytes(payload[:2], "little")
                    chunks = [payload[2:]]
                    got = len(chunks[0])
                    if count > 0 and record_size is None and got > 0:
                        record_size = max(1, got // count)
                    need = count * (record_size or 1) if record_size else None
                    frames_read = 1
                    while count > 0 and need is not None and got < need and frames_read < frame_limit:
                        try:
                            nxt = conn.read_frame()
                        except TdxError as exc:
                            stream_exc = exc
                            break
                        frames_read += 1
                        self.stats.frames += 1
                        if not nxt.payload:
                            break
                        chunks.append(nxt.payload)
                        got += len(nxt.payload)
                    joined = b"".join(chunks)
                    merged = _impl.ResponseFrame(
                        magic=first.magic,
                        zip_flag=first.zip_flag,
                        seq=first.seq,
                        method=first.method,
                        zip_size=len(joined),
                        unzip_size=len(joined),
                        body=b"",
                        payload=joined,
                        header_raw=first.header_raw,
                    )
                    if need is not None and got < need and stream_exc is None:
                        stream_exc = FramingError(
                            f"request_multi 响应截断: need={need} got={got} max_frames={frame_limit}",
                            context={
                                "method": hex(method),
                                "need": need,
                                "got": got,
                                "max_frames": frame_limit,
                            },
                        )
    except BaseException:
        self._release_lease(slot, conn)
        _release_probe_token(self, slot, generation)
        self._drop(slot, expected=conn)
        raise

    self._release_lease(slot, conn)
    if first_exc is not None:
        self._mark_failure(slot, first_exc, generation=generation, conn=conn)
        metrics.record_request(command=f"0x{method:04x}", ok=False)
        raise first_exc

    assert merged is not None
    self.stats.requests += 1
    if stream_exc is not None:
        _LOG.warning("request_multi 数据不完整，返回已收到前缀并弃连: %s", stream_exc)
        self._mark_failure(slot, stream_exc, generation=generation, conn=conn)
        metrics.record_request(
            command=f"0x{method:04x}",
            ok=False,
            duration=time.perf_counter() - started,
        )
        return merged

    self._mark_success(
        slot,
        generation=generation,
        rtt_ms=(time.perf_counter() - started) * 1000.0,
    )
    metrics.record_request(
        command=f"0x{method:04x}", ok=True, duration=time.perf_counter() - started
    )
    return merged


def _iter_frames(
    self: _impl.ConnectionPool,
    method: int,
    body: bytes = b"",
    *,
    max_frames: int = 512,
) -> Iterator[_impl.ResponseFrame]:
    """Iterate frames with circuit admission and conservative socket reuse."""

    frame_limit = _require_positive_frame_limit(max_frames, field="max_frames")
    self._ensure_open()
    if self.rate_limiter is not None:
        self.rate_limiter.acquire()
    slot = _select_allowed_slot(self)
    if slot is None:
        self.stats.circuit_skips += 1
        raise AllHostsUnreachable(
            f"所有主站均处于熔断门禁。\n{ALL_HOSTS_UNREACHABLE_NEXT_STEPS}",
            context={"hosts": [], "method": hex(method), "attempts": 0},
            cause=ConnectionFailed("所有候选主站均处于熔断门禁"),
        )

    conn: _impl.TcpConnection | None = None
    generation: int | None = None
    try:
        conn, generation = self._acquire_lease(slot)
    except TdxError as exc:
        self._mark_failure(slot, exc, generation=generation, conn=conn)
        raise
    except BaseException:
        _release_probe_token(self, slot, generation)
        raise

    failed: TdxError | None = None
    frames_read = 0
    try:
        with conn._lock:
            frame_bytes, _ = _impl.build_request(
                method,
                body,
                seq=conn.next_seq(),
                spec=conn.spec,
            )
            conn._sendall(frame_bytes)
            while frames_read < frame_limit:
                try:
                    frame = conn.read_frame()
                except TdxError as exc:
                    failed = exc
                    break
                frames_read += 1
                self.stats.frames += 1
                conn.stats.last_used = time.time()
                yield frame
    except BaseException:
        self._release_lease(slot, conn)
        _release_probe_token(self, slot, generation)
        self._drop(slot, expected=conn)
        raise

    self._release_lease(slot, conn)
    if failed is not None:
        self._mark_failure(slot, failed, generation=generation, conn=conn)
        return

    self._mark_success(slot, generation=generation)
    # Reaching the caller's cap does not prove the server stream ended. Reusing
    # this socket could expose an unread old frame to the next request, so close
    # it without counting a host failure.
    if frames_read >= frame_limit:
        self._drop(slot, expected=conn)


def _start_heartbeat(self: _impl.ConnectionPool) -> None:
    """Heartbeat that obeys the same OPEN/HALF_OPEN circuit admission."""

    interval = max(1, int(self.heartbeat_interval or 0))

    def loop() -> None:
        self._sweep_idle()
        while not self._closed:
            time.sleep(interval)
            if self._closed:
                break
            self._sweep_idle()
            for slot in list(self._slots):
                if self._closed:
                    break
                if not self._circuit_allows(slot.host):
                    continue
                failed: BaseException | None = None
                rtt: float | None = None
                generation = slot.generation
                try:
                    with slot.lock:
                        conn = slot.conn
                        if conn is None or not conn.connected:
                            _release_probe_token(self, slot, generation)
                            continue
                        idle = time.time() - (conn.stats.last_used or conn.stats.created_at)
                        if idle < interval:
                            _release_probe_token(self, slot, generation)
                            continue
                        try:
                            rtt = conn.ping(self.heartbeat_cmd)
                        except Exception as exc:
                            failed = exc
                except BaseException:
                    _release_probe_token(self, slot, generation)
                    raise
                if failed is not None:
                    self._mark_failure(slot, failed, generation=generation, conn=conn)
                elif rtt is not None:
                    self._mark_success(slot, generation=generation, rtt_ms=rtt)

    self._hb = threading.Thread(target=loop, name="tstdx-heartbeat", daemon=True)
    self._hb.start()


setattr(_impl.ConnectionPool, "_select_allowed_slot", _select_allowed_slot)
setattr(_impl.ConnectionPool, "_release_probe_token", _release_probe_token)
setattr(_impl.ConnectionPool, "request", _request)
setattr(_impl.ConnectionPool, "request_multi", _request_multi)
setattr(_impl.ConnectionPool, "iter_frames", _iter_frames)
setattr(_impl.ConnectionPool, "_start_heartbeat", _start_heartbeat)
