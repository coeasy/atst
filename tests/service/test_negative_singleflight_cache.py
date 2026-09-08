from __future__ import annotations

import threading
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
    assert "negative_cache" not in first.value.context
    assert second.value.context["command"] == "0x054C"
    assert second.value.context["negative_cache"] is True
    assert second.value.context["fallback"] is False
    assert second.value.context["provider_switch_allowed"] is False

    second.value.context["caller_mutation"] = True
    with pytest.raises(CommandOffline) as third:
        sf.do("q1:tdx-snapshot", fail)
    assert "caller_mutation" not in third.value.context


def test_active_flight_failure_is_cloned_for_each_follower() -> None:
    sf = SingleFlight(negative_ttl=0.2)
    entered = threading.Event()
    release = threading.Event()
    errors: list[CommandOffline] = []
    errors_lock = threading.Lock()

    def fail() -> None:
        entered.set()
        release.wait(timeout=1.0)
        raise CommandOffline("offline", context={"provider": "tdx", "seed": 1})

    def worker() -> None:
        try:
            sf.do("q1:shared-terminal", fail, timeout=1.0)
        except CommandOffline as exc:
            with errors_lock:
                errors.append(exc)

    leader = threading.Thread(target=worker)
    leader.start()
    assert entered.wait(timeout=0.5)

    followers = [threading.Thread(target=worker) for _ in range(2)]
    for thread in followers:
        thread.start()

    deadline = time.monotonic() + 0.5
    while sf.joins < 2 and time.monotonic() < deadline:
        time.sleep(0.005)
    assert sf.joins == 2
    release.set()

    leader.join(timeout=1.0)
    for thread in followers:
        thread.join(timeout=1.0)

    assert len(errors) == 3
    assert len({id(exc) for exc in errors}) == 3
    errors[0].context["mutated"] = True
    assert all("mutated" not in exc.context for exc in errors[1:])


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


def test_negative_cache_never_crosses_provider_identity() -> None:
    calls = 0
    sf = SingleFlight(negative_ttl=0.2)

    def fail() -> None:
        nonlocal calls
        calls += 1
        raise CommandOffline("offline")

    with pytest.raises(CommandOffline):
        sf.do("provider=tdx/channel=quotation/cap=quotes/symbol=sh600519", fail)
    with pytest.raises(CommandOffline):
        sf.do("provider=tencent/channel=quote/cap=quotes/symbol=sh600519", fail)

    assert calls == 2
    assert sf.negative_stores == 2
    assert sf.negative_hits == 0


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
