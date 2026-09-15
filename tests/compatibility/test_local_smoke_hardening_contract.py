from __future__ import annotations

from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]


def test_local_wheel_smoke_is_source_isolated_and_checks_all_runtime_hardening() -> None:
    script = (_ROOT / "scripts" / "build_package.py").read_text(encoding="utf-8")

    assert 'work_dir = temp_root / "work"' in script
    assert '[str(python), "-I", "-c", probe]' in script
    assert "package_file.is_relative_to(venv_root)" in script
    assert "TdxClient.__init__.__module__ == 'tstdx.client._pool_binding_hardening'" in script
    assert "AsyncTdxClient.__init__.__module__ == 'tstdx.client._pool_binding_hardening'" in script
    assert "TdxClient.bestip.__module__ == 'tstdx.client._bestip_hardening'" in script
    assert "AsyncTdxClient.bestip.__module__ == 'tstdx.client._bestip_hardening'" in script
    assert (
        "AsyncTdxClient.quotes_concurrent.__module__ == "
        "'tstdx.client._async_concurrency_hardening'"
    ) in script
    assert "ConnectionPool.__init__.__module__ == 'tstdx.transport._pool_family_hardening'" in script
    assert (
        "AsyncConnectionPool.__init__.__module__ == "
        "'tstdx.transport._pool_family_hardening'"
    ) in script
    assert "ConnectionPool.request.__module__ == 'tstdx.transport._pool_hardening'" in script
    assert (
        "ConnectionPool.update_hosts.__module__ == "
        "'tstdx.transport._pool_provenance_hardening'"
    ) in script
    assert "AsyncConnectionPool.request.__module__ == 'tstdx.transport._async_pool_hardening'" in script
    assert (
        "AsyncConnectionPool.update_hosts.__module__ == "
        "'tstdx.transport._pool_provenance_hardening'"
    ) in script
    assert "RankingStore.load.__module__ == 'tstdx.transport._ranking_hardening'" in script
    assert "resolve_hosts.__module__ == 'tstdx.transport._host_selector_hardening'" in script
