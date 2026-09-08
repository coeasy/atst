# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""Dynamic Provider/Channel/Capability health, separate from static capability facts.

Capability answers "can this Provider implement the operation?". Health answers
"is this exact Provider/Channel/Capability currently healthy?". The registry
never chooses another Provider; an open circuit only fails the already-selected
execution plan quickly until its cooldown expires.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, replace

from .errors import ReadTimeout, SourceUnavailable, TdxError, ValidationError
from .providers import PROVIDERS, resolve_provider

__all__ = ["HealthKey", "HealthState", "SourceHealthRegistry"]


@dataclass(frozen=True, slots=True)
class HealthKey:
    provider: str
    channel: str
    capability: str


@dataclass(frozen=True, slots=True)
class HealthState:
    successes: int = 0
    failures: int = 0
    consecutive_failures: int = 0
    last_success_ns: int | None = None
    last_failure_ns: int | None = None
    last_error_code: str | None = None
    cooldown_until_ns: int = 0
    half_open_probe: bool = False

    @property
    def circuit_open(self) -> bool:
        return self.half_open_probe or self.cooldown_until_ns > time.monotonic_ns()


class SourceHealthRegistry:
    """Thread-safe dynamic health registry with a single-probe circuit breaker."""

    def __init__(self, *, failure_threshold: int = 3, cooldown_seconds: float = 30.0) -> None:
        if failure_threshold <= 0:
            raise ValueError("failure_threshold must be > 0")
        if cooldown_seconds <= 0:
            raise ValueError("cooldown_seconds must be > 0")
        self.failure_threshold = int(failure_threshold)
        self.cooldown_ns = int(cooldown_seconds * 1_000_000_000)
        self._lock = threading.RLock()
        self._states: dict[HealthKey, HealthState] = {}

    @staticmethod
    def key(provider: str, channel: str, capability: str) -> HealthKey:
        pid = resolve_provider(provider=provider)
        cid = str(channel).strip().lower()
        cap = str(capability).strip().lower()
        PROVIDERS.require(pid, cap, channel=cid)
        return HealthKey(pid, cid, cap)

    def snapshot(self, provider: str, channel: str, capability: str) -> HealthState:
        key = self.key(provider, channel, capability)
        with self._lock:
            return self._states.get(key, HealthState())

    def before_request(self, provider: str, channel: str, capability: str) -> None:
        key = self.key(provider, channel, capability)
        now = time.monotonic_ns()
        with self._lock:
            state = self._states.get(key)
            if state is None:
                return
            if state.half_open_probe:
                remaining = 0.0
                reason = "half_open_probe_in_flight"
            elif state.cooldown_until_ns > now:
                remaining = (state.cooldown_until_ns - now) / 1_000_000_000
                reason = "cooldown"
            elif state.cooldown_until_ns:
                # Exactly one request becomes the half-open probe. Other callers
                # fail fast until this probe records success/failure.
                self._states[key] = replace(
                    state,
                    cooldown_until_ns=0,
                    half_open_probe=True,
                )
                return
            else:
                return
        raise SourceUnavailable(
            "所选 Provider capability 处于健康门禁状态",
            context={
                "provider": key.provider,
                "channel": key.channel,
                "capability": key.capability,
                "phase": "health_gate",
                "circuit_open": True,
                "health_gate_reason": reason,
                "half_open_probe": state.half_open_probe,
                "cooldown_remaining": remaining,
                "consecutive_failures": state.consecutive_failures,
                "last_error_code": state.last_error_code,
                "fallback": False,
                "provider_switch_allowed": False,
            },
        )

    def record_success(self, provider: str, channel: str, capability: str) -> HealthState:
        key = self.key(provider, channel, capability)
        now = time.monotonic_ns()
        with self._lock:
            current = self._states.get(key, HealthState())
            updated = replace(
                current,
                successes=current.successes + 1,
                consecutive_failures=0,
                last_success_ns=now,
                last_error_code=None,
                cooldown_until_ns=0,
                half_open_probe=False,
            )
            self._states[key] = updated
            return updated

    def record_failure(
        self,
        provider: str,
        channel: str,
        capability: str,
        exc: BaseException,
        *,
        penalize: bool = True,
    ) -> HealthState:
        key = self.key(provider, channel, capability)
        now = time.monotonic_ns()
        error_code = exc.code if isinstance(exc, TdxError) else "E9000"
        with self._lock:
            current = self._states.get(key, HealthState())
            consecutive = current.consecutive_failures + 1 if penalize else current.consecutive_failures
            cooldown_until = current.cooldown_until_ns
            if penalize and consecutive >= self.failure_threshold:
                cooldown_until = now + self.cooldown_ns
            updated = replace(
                current,
                failures=current.failures + 1,
                consecutive_failures=consecutive,
                last_failure_ns=now,
                last_error_code=error_code,
                cooldown_until_ns=cooldown_until,
                half_open_probe=False,
            )
            self._states[key] = updated
            return updated

    @staticmethod
    def should_penalize(exc: BaseException) -> bool:
        """Only failures attributable to the selected Provider affect its circuit."""
        if isinstance(exc, ValidationError):
            return False
        if isinstance(exc, ReadTimeout) and exc.context.get("deadline_scope") == "query":
            return False
        return True

    def reset(self, provider: str | None = None) -> None:
        with self._lock:
            if provider is None:
                self._states.clear()
                return
            pid = resolve_provider(provider=provider)
            for key in [key for key in self._states if key.provider == pid]:
                self._states.pop(key, None)

    def all_states(self) -> dict[HealthKey, HealthState]:
        with self._lock:
            return dict(self._states)
