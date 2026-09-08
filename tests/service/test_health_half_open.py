from __future__ import annotations

import time
from dataclasses import replace

import pytest

from tstdx.errors import SourceUnavailable
from tstdx.health import SourceHealthRegistry


def _expire_cooldown(health: SourceHealthRegistry) -> None:
    key = health.key("tencent", "quote", "quotes")
    state = health.snapshot("tencent", "quote", "quotes")
    health._states[key] = replace(state, cooldown_until_ns=time.monotonic_ns() - 1)


def test_half_open_allows_exactly_one_probe_until_success() -> None:
    health = SourceHealthRegistry(failure_threshold=1, cooldown_seconds=60)
    health.record_failure("tencent", "quote", "quotes", SourceUnavailable("down"))
    _expire_cooldown(health)

    health.before_request("tencent", "quote", "quotes")
    probing = health.snapshot("tencent", "quote", "quotes")
    assert probing.half_open_probe is True
    assert probing.circuit_open is True

    with pytest.raises(SourceUnavailable) as caught:
        health.before_request("tencent", "quote", "quotes")
    assert caught.value.context["health_gate_reason"] == "half_open_probe_in_flight"
    assert caught.value.context["provider_switch_allowed"] is False

    health.record_success("tencent", "quote", "quotes")
    recovered = health.snapshot("tencent", "quote", "quotes")
    assert recovered.half_open_probe is False
    assert recovered.circuit_open is False
    health.before_request("tencent", "quote", "quotes")


def test_failed_half_open_probe_reopens_cooldown() -> None:
    health = SourceHealthRegistry(failure_threshold=1, cooldown_seconds=60)
    health.record_failure("tencent", "quote", "quotes", SourceUnavailable("down"))
    _expire_cooldown(health)

    health.before_request("tencent", "quote", "quotes")
    health.record_failure("tencent", "quote", "quotes", SourceUnavailable("still down"))

    failed = health.snapshot("tencent", "quote", "quotes")
    assert failed.half_open_probe is False
    assert failed.circuit_open is True
    with pytest.raises(SourceUnavailable) as caught:
        health.before_request("tencent", "quote", "quotes")
    assert caught.value.context["health_gate_reason"] == "cooldown"
