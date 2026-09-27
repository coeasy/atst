from __future__ import annotations

import importlib
import threading
import time
from collections.abc import Callable
from typing import Any

import pytest

import atst.transport.pool as pool_module
from atst.errors import ConfigError
from atst.protocol.commands import Family
from atst.transport.async_ import AsyncConnectionPool
from atst.transport.hosts import HostEntry
from atst.transport.pool import ConnectionPool
from atst.transport.speedtest import ProbeResult

# ``atst.transport.speedtest`` is shadowed by a same-named re-exported function
# in ``atst.transport.__init__``, so ``import ... as`` would bind the function.
# Provenance patches must target the real submodule object.
speedtest_module = importlib.import_module("atst.transport.speedtest")


@pytest.mark.asyncio
async def test_async_update_hosts_publishes_fresh_generation_with_old_identity(
    seed_pool_health,
) -> None:
    current = HostEntry(
        host="1.2.3.4",
        family=Family.STANDARD,
        name="verified-primary",
        verified=True,
        rtt_ms=30.0,
        live_rtt_ms=40.0,
        failures=3,
        last_error="current failure",
        circuit="degraded",
        consec_weighted=3.0,
    )
    pool = AsyncConnectionPool([current], slots_per_host=1, heartbeat_interval=0)
    old_slot = pool._slots[0]
    # A directly constructed pool owns a fresh runtime-health generation; the
    # live request health under test is re-established on the pool's own host.
    seed_pool_health(
        pool,
        live_rtt_ms=40.0,
        failures=3,
        last_error="current failure",
        circuit="degraded",
        consec_weighted=3.0,
    )

    published = (
        await pool.update_hosts(
            [
                HostEntry(
                    host="1.2.3.4",
                    family=Family.STANDARD,
                    name="probe-name",
                    verified=False,
                    rtt_ms=2.0,
                    failures=0,
                    circuit="healthy",
                )
            ]
        )
    )[0]

    assert published is not current
    assert pool.hosts[0] is published
    assert pool._slots[0] is old_slot
    assert pool._slots[0].host is published
    assert pool._slots[0].generation == 1
    assert published.name == "verified-primary"
    assert published.verified is True
    assert published.rtt_ms == 2.0
    assert published.live_rtt_ms == 40.0
    assert published.failures == 3
    assert published.last_error == "current failure"
    assert published.circuit == "degraded"
    assert published.consec_weighted == 3.0
    assert current.rtt_ms == 30.0


@pytest.mark.asyncio
async def test_async_update_hosts_rejects_cross_family_and_duplicates() -> None:
    pool = AsyncConnectionPool(
        [HostEntry(host="1.2.3.4", family=Family.STANDARD)],
        slots_per_host=1,
        heartbeat_interval=0,
    )

    with pytest.raises(ConfigError, match="family 不匹配"):
        await pool.update_hosts([HostEntry(host="1.2.3.4", family=Family.F10)])

    duplicate = HostEntry(host="1.2.3.4", family=Family.STANDARD)
    with pytest.raises(ConfigError, match="重复 endpoint"):
        await pool.update_hosts([duplicate, duplicate])


def test_stale_background_probe_cannot_commit_after_generation_change(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    current = HostEntry(
        host="1.2.3.4",
        family=Family.STANDARD,
        rtt_ms=50.0,
    )
    pool = ConnectionPool([current], slots_per_host=1, heartbeat_interval=0)
    captured: list[Callable[[], None]] = []

    class DeferredThread(threading.Thread):
        """登记成线程、但把起跑权交给测试本体：代际复查要跨线程，不能靠调度。

        它继承 :class:`threading.Thread` 而不是鸭子类型：池会把句柄存进
        ``_speedtest_threads`` 并在 ``close()``/下一次起跑时按线程协议问
        ``is_alive()``/``join()``（第 26 轮 F-97），鸭子类型的假线程会当场漏形。
        """

        def __init__(
            self,
            *,
            target: Callable[[], None],
            name: str,
            daemon: bool,
        ) -> None:
            assert name == "atst-speedtest"
            assert daemon is True
            super().__init__(target=target, name=name, daemon=daemon)

        def start(self) -> None:
            captured.append(self._target)

    monkeypatch.setattr(pool_module.threading, "Thread", DeferredThread)
    monkeypatch.setattr(
        speedtest_module,
        "speedtest",
        lambda *_args, **_kwargs: [
            ProbeResult(
                host="1.2.3.4",
                port=7709,
                family=Family.STANDARD,
                ok=True,
                connect_ms=1.0,
                rtt_ms=2.0,
            )
        ],
    )

    class ExplodingStore:
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            raise AssertionError("stale generation must not open persistent ranking")

    monkeypatch.setattr(pool_module, "RankingStore", ExplodingStore)

    pool._trigger_background_speedtest()
    assert pool._speedtest_triggered is True
    assert len(captured) == 1

    published = pool.update_hosts([HostEntry(host="5.6.7.8", family=Family.STANDARD, rtt_ms=10.0)])[
        0
    ]
    assert pool._generation == 1
    assert pool._speedtest_triggered is False

    # Execute the old worker only after the new generation is already public.
    captured.pop()()

    assert published.host == "5.6.7.8"
    assert published.rtt_ms == 10.0
    assert current.rtt_ms == 50.0

    # New-generation failures remain able to schedule a fresh background probe.
    pool._trigger_background_speedtest()
    assert pool._speedtest_triggered is True
    assert len(captured) == 1


def test_closed_pool_never_starts_a_background_probe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """已关闭的池不许再为了"下次启动更快"去拨整表主站（第 26 轮 F-97）。"""
    pool = ConnectionPool(
        [HostEntry(host="1.2.3.4", family=Family.STANDARD)], slots_per_host=1, heartbeat_interval=0
    )
    dials: list[Any] = []
    monkeypatch.setattr(speedtest_module, "speedtest", lambda *a, **k: dials.append(a) or [])

    pool.close()
    # 关掉之后再来一次触发（真实链路：update_hosts 会按代重置这个开关）。
    pool._speedtest_triggered = False
    pool._trigger_background_speedtest()

    assert pool._speedtest_threads == []
    assert dials == []


def test_close_reaps_the_background_probe_handle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """起跑中的测速线程必须留下句柄，让 ``close()`` 追得上、又不被它拖住（F-97）。"""
    current = HostEntry(host="1.2.3.4", family=Family.STANDARD, rtt_ms=50.0)
    pool = ConnectionPool([current], slots_per_host=1, heartbeat_interval=0)
    dial_started = threading.Event()
    dial_release = threading.Event()
    applied: list[Any] = []

    def _blocking_speedtest(*_args: Any, **_kwargs: Any) -> list[ProbeResult]:
        dial_started.set()
        assert dial_release.wait(timeout=5.0) is True
        return []

    monkeypatch.setattr(speedtest_module, "speedtest", _blocking_speedtest)
    monkeypatch.setattr(
        speedtest_module, "_apply_probe_observations", lambda *a, **k: applied.append(a)
    )
    handles: list[threading.Thread] = []
    try:
        pool._trigger_background_speedtest()
        assert dial_started.wait(timeout=2.0) is True
        handles = list(pool._speedtest_threads)
        assert len(handles) == 1
        assert handles[0].is_alive()

        started = time.monotonic()
        pool.close()
        # 停机只借一小段预算：句柄登记在这里，等不到也要说得出谁还在跑。
        assert time.monotonic() - started < 2.0
        assert pool._speedtest_threads == []
    finally:
        dial_release.set()

    for handle in handles:
        handle.join(timeout=5.0)
        assert not handle.is_alive()
    # 线程活到了池关闭之后，也只许把结果咽下去：一处观测写回都不能有。
    assert applied == []
