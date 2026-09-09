from __future__ import annotations

import pytest

from tstdx.protocol.commands import Family
from tstdx.transport import AsyncConnectionPool
from tstdx.transport.hosts import HostEntry


@pytest.mark.asyncio
async def test_async_update_hosts_canonicalizes_identity_before_generation_matching() -> None:
    current = HostEntry(
        host="1.2.3.4",
        port=7709,
        family=Family.STANDARD,
        name="canonical-primary",
        verified=True,
        live_rtt_ms=9.0,
        live_ok_at=10.0,
        failures=2,
        circuit="degraded",
        consec_weighted=3.0,
    )
    pool = AsyncConnectionPool([current], slots_per_host=1, heartbeat_interval=0)
    old_slot = pool._slots[0]

    published = (
        await pool.update_hosts(
            [
                HostEntry(
                    host=" 1.2.3.4 ",
                    port=7709,
                    family=Family.STANDARD,
                    rtt_ms=2.0,
                )
            ]
        )
    )[0]

    assert published.host == "1.2.3.4"
    assert published.name == "canonical-primary"
    assert published.verified is True
    assert published.live_rtt_ms == 9.0
    assert published.live_ok_at == 10.0
    assert published.failures == 2
    assert published.circuit == "degraded"
    assert published.consec_weighted == 3.0
    assert published.rtt_ms == 2.0
    assert pool._slots[0] is old_slot
    assert pool._slots[0].generation == 1
