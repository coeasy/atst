from __future__ import annotations

import pytest

import tstdx.transport.hosts as hosts_module
from tstdx.protocol.commands import Family
from tstdx.transport import resolve_hosts
from tstdx.transport.hosts import POOL_BY_FAMILY, HostEntry


@pytest.mark.unit
def test_default_resolutions_never_share_mutable_host_health() -> None:
    first = resolve_hosts(None, family=Family.STANDARD, use_ranking=False, max_hosts=1)
    second = resolve_hosts(None, family=Family.STANDARD, use_ranking=False, max_hosts=1)

    assert first[0].key == second[0].key == POOL_BY_FAMILY[Family.STANDARD][0].key
    assert first[0] is not second[0]
    assert first[0] is not POOL_BY_FAMILY[Family.STANDARD][0]
    assert second[0] is not POOL_BY_FAMILY[Family.STANDARD][0]

    first[0].failures = 9
    first[0].biz_failures = 3
    first[0].live_rtt_ms = 999.0
    first[0].circuit = "open"
    first[0].last_error = "first client failed"

    third = resolve_hosts(None, family=Family.STANDARD, use_ranking=False, max_hosts=1)
    assert third[0].failures == 0
    assert third[0].biz_failures == 0
    assert third[0].live_rtt_ms is None
    assert third[0].circuit == "healthy"
    assert third[0].last_error == ""
    assert POOL_BY_FAMILY[Family.STANDARD][0].failures == 0
    assert POOL_BY_FAMILY[Family.STANDARD][0].circuit == "healthy"


@pytest.mark.unit
def test_explicit_selector_host_is_copied_before_runtime_ownership() -> None:
    explicit = HostEntry(
        host="1.2.3.4",
        family=Family.STANDARD,
        name="caller-owned",
        verified=True,
        rtt_ms=5.0,
    )

    resolved = resolve_hosts(
        [explicit],
        family=Family.STANDARD,
        use_ranking=False,
    )

    assert len(resolved) == 1
    assert resolved[0] is not explicit
    assert resolved[0].to_dict() == explicit.to_dict()

    resolved[0].failures = 4
    resolved[0].circuit = "degraded"
    assert explicit.failures == 0
    assert explicit.circuit == "healthy"


@pytest.mark.unit
def test_public_and_module_resolver_both_use_state_isolation_hardening() -> None:
    assert resolve_hosts.__module__ == "tstdx.transport._host_selector_hardening"
    assert hosts_module.resolve_hosts.__module__ == "tstdx.transport._host_selector_hardening"
