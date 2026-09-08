from __future__ import annotations

import time

import pytest

from tstdx.errors import CommandOffline, SourceUnavailable
from tstdx.execution import SingleFlight


def test_stable_terminal_error_is_coalesced_by_exact_key() -> None:
    calls = 0
    sf = SingleFlight(negative_ttl=0.2)

    def fail() -> None:
        nonlocal calls
        calls += 1
        raise CommandOffline("0x054C offline", context={"command": "0x054C"})

    with pytest.raises(CommandOffline) as first:
        sf.do("q1:tdx-snapshot", fail)
    with pytest.raises(CommandOffline) as second:
        sf.do("q1:tdx-snapshot", fail)

    assert calls == 1
    assert sf.negative_stores == 1
    assert sf.negative_hits == 1
    assert first.value is not second.value
    assert second.value.context["command"] == "0x054C"


def test_negative_cache_never_crosses_query_fingerprint() -> None:
    calls = 0
    sf = SingleFlight(negative_ttl=0.2)

    def fail() -> None:
        nonlocal calls
        calls += 1
        raise CommandOffline("offline")

    with pytest.raises(CommandOffline):
        sf.do("provider=tdx/channel=quotation/cap=snapshot", fail)
    with pytest.raises(CommandOffline):
        sf.do("provider=tdx/channel=quotation/cap=bars", fail)

    assert calls == 2


def test_transient_source_unavailable_is_not_negative_cached() -> None:
    calls = 0
    sf = SingleFlight(negative_ttl=1.0)

    def fail() -> None:
        nonlocal calls
        calls += 1
        raise SourceUnavailable("temporary transport outage")

    for _ in range(2):
        with pytest.raises(SourceUnavailable):
            sf.do("q1:temporary", fail)

    assert calls == 2
    assert sf.negative_stores == 0
    assert sf.negative_hits == 0


def test_expired_negative_entry_reaches_provider_again() -> None:
    calls = 0
    sf = SingleFlight(negative_ttl=0.01)

    def fail() -> None:
        nonlocal calls
        calls += 1
        raise CommandOffline("offline")

    with pytest.raises(CommandOffline):
        sf.do("q1:offline", fail)
    time.sleep(0.02)
    with pytest.raises(CommandOffline):
        sf.do("q1:offline", fail)

    assert calls == 2


def test_negative_cache_can_be_disabled_without_changing_api() -> None:
    calls = 0
    sf = SingleFlight(negative_ttl=0)

    def fail() -> None:
        nonlocal calls
        calls += 1
        raise CommandOffline("offline")

    for _ in range(2):
        with pytest.raises(CommandOffline):
            sf.do("q1:offline", fail)

    assert calls == 2
