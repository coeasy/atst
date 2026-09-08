from __future__ import annotations

import threading
import time
from typing import Any

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
