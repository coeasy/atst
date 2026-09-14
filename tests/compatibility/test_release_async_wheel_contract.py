from __future__ import annotations

from pathlib import Path


_ROOT = Path(__file__).resolve().parents[2]


def _smoke_job() -> str:
    workflow = (_ROOT / ".github" / "workflows" / "wheels.yml").read_text(encoding="utf-8")
    return workflow.split("  smoke-install:", 1)[1].split("  publish-pypi:", 1)[0]


def test_release_matrix_smokes_async_circuit_hardening_from_installed_wheel() -> None:
    smoke = _smoke_job()

    assert "from tstdx.transport.async_ import AsyncConnectionPool" in smoke
    assert "_circuit_allows" in smoke
    assert "_release_probe_token" in smoke
    assert (
        "AsyncConnectionPool.close.__module__ == "
        "'tstdx.transport._async_close_hardening'"
    ) in smoke
    assert "actions/checkout" not in smoke


def test_release_matrix_smokes_client_behavior_and_pool_binding_hardening() -> None:
    smoke = _smoke_job()

    assert "from tstdx.client import AsyncF10Client, AsyncTdxClient, F10Client, TdxClient" in smoke
    assert "TdxClient.__init__.__module__ == 'tstdx.client._pool_binding_hardening'" in smoke
    assert "AsyncTdxClient.__init__.__module__ == 'tstdx.client._pool_binding_hardening'" in smoke
    assert "F10Client.__init__.__module__ == 'tstdx.client._subclient_family_hardening'" in smoke
    assert (
        "AsyncF10Client.__init__.__module__ == "
        "'tstdx.client._subclient_family_hardening'"
    ) in smoke
    assert "TdxClient.bestip.__module__ == 'tstdx.client._bestip_hardening'" in smoke
    assert "AsyncTdxClient.bestip.__module__ == 'tstdx.client._bestip_hardening'" in smoke
    assert (
        "AsyncTdxClient.quotes_concurrent.__module__ == "
        "'tstdx.client._async_concurrency_hardening'"
    ) in smoke


def test_release_matrix_smokes_direct_connection_contract_hardening() -> None:
    smoke = _smoke_job()

    assert "from tstdx.transport import TcpConnection" in smoke
    assert "from tstdx.transport.async_ import AsyncTcpConnection" in smoke
    assert (
        "TcpConnection.__init__.__module__ == "
        "'tstdx.transport._connection_contract_hardening'"
    ) in smoke
    assert (
        "AsyncTcpConnection.__init__.__module__ == "
        "'tstdx.transport._connection_contract_hardening'"
    ) in smoke
    assert (
        "TcpConnection.request.__module__ == "
        "'tstdx.transport._connection_contract_hardening'"
    ) in smoke
    assert (
        "AsyncTcpConnection.request.__module__ == "
        "'tstdx.transport._connection_contract_hardening'"
    ) in smoke


def test_release_matrix_smokes_transport_family_binding_hardening() -> None:
    smoke = _smoke_job()

    assert "ConnectionPool.__init__.__module__ == 'tstdx.transport._pool_family_hardening'" in smoke
    assert (
        "AsyncConnectionPool.__init__.__module__ == "
        "'tstdx.transport._pool_family_hardening'"
    ) in smoke


def test_release_matrix_smokes_selector_hardening_from_installed_wheel() -> None:
    smoke = _smoke_job()

    assert "from tstdx.transport import RankingStore, resolve_hosts" in smoke
    assert "resolve_hosts.__module__ == 'tstdx.transport._host_selector_hardening'" in smoke
