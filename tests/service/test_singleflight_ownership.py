from __future__ import annotations

import threading
import time
from typing import Any

import pytest

from tstdx.errors import ReadTimeout
from tstdx.execution import SingleFlight


def test_singleflight_follower_gets_independent_mutable_result() -> None:
    singleflight = SingleFlight()
    started = threading.Event()
    release = threading.Event()
    calls = 0
    results: list[list[dict[str, Any]]] = []

    def fetch() -> list[dict[str, Any]]:
        nonlocal calls
        calls += 1
        started.set()
        release.wait(1.0)
        return [{"code": "sh600519", "extra": {"rank": 1}}]

    def run() -> None:
        results.append(singleflight.do("same-query", fetch, timeout=1.0))

    leader = threading.Thread(target=run)
    follower = threading.Thread(target=run)
    leader.start()
    assert started.wait(1.0)
    follower.start()
    time.sleep(0.02)
    release.set()
    leader.join(1.0)
    follower.join(1.0)

    assert calls == 1
    assert len(results) == 2
    assert results[0] is not results[1]
    assert results[0][0] is not results[1][0]
    assert results[0][0]["extra"] is not results[1][0]["extra"]

    results[0][0]["extra"]["rank"] = 99
    assert results[1][0]["extra"]["rank"] == 1


def test_long_deadline_request_bypasses_short_deadline_leader() -> None:
    singleflight = SingleFlight()
    leader_started = threading.Event()
    release_leader = threading.Event()
    leader_errors: list[BaseException] = []
    calls: list[str] = []

    def short_fetch() -> str:
        calls.append("short")
        leader_started.set()
        release_leader.wait(1.0)
        raise ReadTimeout(
            "short leader exhausted its own query budget",
            context={"deadline_scope": "query"},
        )

    def run_short() -> None:
        try:
            singleflight.do("same-query", short_fetch, timeout=0.05)
        except BaseException as exc:
            leader_errors.append(exc)

    leader = threading.Thread(target=run_short)
    leader.start()
    assert leader_started.wait(1.0)

    def long_fetch() -> str:
        calls.append("long")
        return "ok"

    result = singleflight.do("same-query", long_fetch, timeout=1.0)
    assert result == "ok"
    assert calls == ["short", "long"]
    assert singleflight.deadline_bypasses == 1
    assert singleflight.joins == 0

    release_leader.set()
    leader.join(1.0)
    assert len(leader_errors) == 1
    assert isinstance(leader_errors[0], ReadTimeout)


def test_later_long_callers_join_deadline_compatible_second_flight() -> None:
    singleflight = SingleFlight()
    short_started = threading.Event()
    long_started = threading.Event()
    release_short = threading.Event()
    release_long = threading.Event()
    calls: list[str] = []
    long_results: list[str] = []

    def short_fetch() -> str:
        calls.append("short")
        short_started.set()
        release_short.wait(1.0)
        return "short"

    def long_fetch() -> str:
        calls.append("long")
        long_started.set()
        release_long.wait(1.0)
        return "long"

    short = threading.Thread(
        target=lambda: singleflight.do("same-query", short_fetch, timeout=0.05)
    )
    long_leader = threading.Thread(
        target=lambda: long_results.append(
            singleflight.do("same-query", long_fetch, timeout=1.0)
        )
    )
    long_follower = threading.Thread(
        target=lambda: long_results.append(
            singleflight.do("same-query", long_fetch, timeout=0.5)
        )
    )

    short.start()
    assert short_started.wait(1.0)
    long_leader.start()
    assert long_started.wait(1.0)
    long_follower.start()
    time.sleep(0.02)

    assert calls == ["short", "long"]
    assert singleflight.leaders == 2
    assert singleflight.deadline_bypasses == 1
    assert singleflight.joins == 1

    release_long.set()
    long_leader.join(1.0)
    long_follower.join(1.0)
    release_short.set()
    short.join(1.0)

    assert sorted(long_results) == ["long", "long"]
    assert calls.count("long") == 1


def test_short_deadline_follower_can_join_long_deadline_leader() -> None:
    singleflight = SingleFlight()
    started = threading.Event()
    release = threading.Event()
    calls = 0
    results: list[str] = []

    def fetch() -> str:
        nonlocal calls
        calls += 1
        started.set()
        release.wait(1.0)
        return "ok"

    def run_leader() -> None:
        results.append(singleflight.do("same-query", fetch, timeout=1.0))

    def run_follower() -> None:
        results.append(singleflight.do("same-query", fetch, timeout=0.5))

    leader = threading.Thread(target=run_leader)
    follower = threading.Thread(target=run_follower)
    leader.start()
    assert started.wait(1.0)
    follower.start()
    time.sleep(0.02)
    release.set()
    leader.join(1.0)
    follower.join(1.0)

    assert calls == 1
    assert sorted(results) == ["ok", "ok"]
    assert singleflight.joins == 1
    assert singleflight.deadline_bypasses == 0


def test_expired_request_never_becomes_singleflight_leader() -> None:
    singleflight = SingleFlight()
    called = False

    def fetch() -> str:
        nonlocal called
        called = True
        return "unexpected"

    with pytest.raises(ReadTimeout) as caught:
        singleflight.do("expired", fetch, timeout=0.0)

    assert called is False
    assert singleflight.leaders == 0
    assert caught.value.context["phase"] == "singleflight_enter"
    assert caught.value.context["deadline_scope"] == "query"
