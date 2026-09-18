from __future__ import annotations

from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]


def _release_workflow() -> str:
    return (_ROOT / ".github" / "workflows" / "wheels.yml").read_text(encoding="utf-8")


def test_release_wheel_smoke_requires_probe_only_ranking_hardening() -> None:
    workflow = _release_workflow()

    assert "from tstdx.transport import RankingStore" in workflow
    assert "RankingStore.load.__module__ == 'tstdx.transport._ranking_hardening'" in workflow
    assert "RankingStore.save.__module__ == 'tstdx.transport._ranking_hardening'" in workflow


def test_release_wheel_smoke_requires_generation_safe_pool_provenance() -> None:
    workflow = _release_workflow()

    assert (
        "ConnectionPool.update_hosts.__module__ == 'tstdx.transport._pool_provenance_hardening'"
        in workflow
    )
    assert (
        "ConnectionPool._trigger_background_speedtest.__module__ == 'tstdx.transport._pool_provenance_hardening'"
        in workflow
    )
    assert (
        "AsyncConnectionPool.update_hosts.__module__ == 'tstdx.transport._pool_provenance_hardening'"
        in workflow
    )
