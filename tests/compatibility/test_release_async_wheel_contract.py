from __future__ import annotations

from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]


def _smoke_job() -> str:
    workflow = (_ROOT / ".github" / "workflows" / "wheels.yml").read_text(encoding="utf-8")
    return workflow.split("  smoke-install:", 1)[1].split("  extras-install:", 1)[0]


def test_release_matrix_smokes_async_circuit_hardening_from_installed_wheel() -> None:
    smoke = _smoke_job()

    assert "from atst.transport.async_ import AsyncConnectionPool" in smoke
    assert "_circuit_allows" in smoke
    assert "_release_probe_token" in smoke
    assert "AsyncConnectionPool.close.__module__ == 'atst.transport.async_'" in smoke
    assert "actions/checkout" not in smoke


def test_release_matrix_smokes_client_behavior_and_pool_binding() -> None:
    smoke = _smoke_job()

    assert "from atst.client import AsyncF10Client, AsyncTdxClient, F10Client, TdxClient" in smoke
    assert "TdxClient.__init__.__module__ == 'atst.client.sync'" in smoke
    assert "AsyncTdxClient.__init__.__module__ == 'atst.client.async_'" in smoke
    assert "F10Client.__init__.__module__ == 'atst.client.sync'" in smoke
    assert ("AsyncF10Client.__init__.__module__ == 'atst.client.async_'") in smoke
    assert "TdxClient.bestip.__module__ == 'atst.client.sync'" in smoke
    assert "AsyncTdxClient.bestip.__module__ == 'atst.client.async_'" in smoke
    assert ("AsyncTdxClient.quotes_concurrent.__module__ == 'atst.client.async_'") in smoke


def test_release_matrix_smokes_direct_connection_contract() -> None:
    smoke = _smoke_job()

    assert "from atst.transport import TcpConnection" in smoke
    assert "from atst.transport.async_ import AsyncTcpConnection" in smoke
    assert ("TcpConnection.__init__.__module__ == 'atst.transport.base'") in smoke
    assert ("AsyncTcpConnection.__init__.__module__ == 'atst.transport.async_'") in smoke
    assert ("TcpConnection.request.__module__ == 'atst.transport.base'") in smoke
    assert ("AsyncTcpConnection.request.__module__ == 'atst.transport.async_'") in smoke


def test_release_matrix_smokes_transport_family_binding() -> None:
    smoke = _smoke_job()

    assert "ConnectionPool.__init__.__module__ == 'atst.transport.pool'" in smoke
    assert ("AsyncConnectionPool.__init__.__module__ == 'atst.transport.async_'") in smoke


def test_release_matrix_smokes_selector_from_installed_wheel() -> None:
    smoke = _smoke_job()

    assert "from atst.transport import RankingStore, resolve_hosts" in smoke
    assert "resolve_hosts.__module__ == 'atst.transport.hosts'" in smoke
