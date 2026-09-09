from __future__ import annotations

import pytest

import tstdx.transport.hosts as hosts_module
from tstdx.transport import ConnectionPool, RankingStore
from tstdx.transport.async_ import AsyncConnectionPool


@pytest.mark.unit
def test_public_sync_pool_installs_hardening_layer() -> None:
    assert ConnectionPool.request.__module__ == "tstdx.transport._pool_hardening"
    assert ConnectionPool.request_multi.__module__ == "tstdx.transport._pool_hardening"
    assert ConnectionPool.iter_frames.__module__ == "tstdx.transport._pool_hardening"


@pytest.mark.unit
def test_public_async_pool_installs_hardening_layer() -> None:
    assert AsyncConnectionPool.request.__module__ == "tstdx.transport._async_pool_hardening"


@pytest.mark.unit
def test_public_ranking_store_installs_probe_only_hardening_layer() -> None:
    assert RankingStore.load.__module__ == "tstdx.transport._ranking_hardening"
    assert RankingStore.save.__module__ == "tstdx.transport._ranking_hardening"
    assert RankingStore.merge.__module__ == "tstdx.transport._ranking_hardening"
    assert (
        hosts_module._apply_ranked_observation.__module__
        == "tstdx.transport._ranking_hardening"
    )
