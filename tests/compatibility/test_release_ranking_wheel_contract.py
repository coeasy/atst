from __future__ import annotations

from pathlib import Path


_ROOT = Path(__file__).resolve().parents[2]


def test_release_wheel_smoke_requires_probe_only_ranking_hardening() -> None:
    workflow = (_ROOT / ".github" / "workflows" / "wheels.yml").read_text(
        encoding="utf-8"
    )

    assert "from tstdx.transport import RankingStore" in workflow
    assert "RankingStore.load.__module__ == 'tstdx.transport._ranking_hardening'" in workflow
    assert "RankingStore.save.__module__ == 'tstdx.transport._ranking_hardening'" in workflow
