from __future__ import annotations

import pytest

from tstdx.transport import ConnectionPool
from tstdx.transport.async_ import AsyncConnectionPool


@pytest.mark.unit
def test_public_sync_pool_installs_hardening_layer() -> None:
    assert ConnectionPool.request.__module__ == "tstdx.transport._pool_hardening"
    assert ConnectionPool.request_multi.__module__ == "tstdx.transport._pool_hardening"
    assert ConnectionPool.iter_frames.__module__ == "tstdx.transport._pool_hardening"


@pytest.mark.unit
def test_public_async_pool_installs_hardening_layer() -> None:
    assert AsyncConnectionPool.request.__module__ == "tstdx.transport._async_pool_hardening"
