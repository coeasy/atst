from __future__ import annotations

from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]


def _release_workflow() -> str:
    return (_ROOT / ".github" / "workflows" / "wheels.yml").read_text(encoding="utf-8")


def test_release_wheel_smoke_requires_canonical_ranking_module() -> None:
    workflow = _release_workflow()

    assert "from atst.transport import RankingStore" in workflow
    assert "RankingStore.load.__module__ == 'atst.transport.hosts'" in workflow
    assert "RankingStore.save.__module__ == 'atst.transport.hosts'" in workflow


def test_release_wheel_smoke_requires_generation_safe_pool_provenance() -> None:
    workflow = _release_workflow()

    # 代际发布规则住在池类体内；provenance 侧车层已解散。
    assert "ConnectionPool.update_hosts.__module__ == 'atst.transport.pool'" in workflow
    assert (
        "ConnectionPool._trigger_background_speedtest.__module__ == 'atst.transport.pool'"
        in workflow
    )
    assert "AsyncConnectionPool.update_hosts.__module__ == 'atst.transport.async_'" in workflow
