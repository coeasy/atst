from __future__ import annotations

import threading
import time
from typing import Any

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
