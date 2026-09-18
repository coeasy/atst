"""The released wheel must ship exactly one config → transport translation point.

v17 Phase 6 (F-16): ``ConnectionPool.from_config`` was a second, production-unreferenced
reader of ``Config`` whose key names never matched the objects it read. The kernel now
resolves transport settings through ``pool_settings_from_config`` alone, so the release
smoke has to prove both halves: the seam exists and the dead factory cannot come back.
"""

from __future__ import annotations

from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]


def test_release_wheel_smoke_requires_the_single_pool_config_seam() -> None:
    workflow = (_ROOT / ".github" / "workflows" / "wheels.yml").read_text(encoding="utf-8")
    smoke = workflow.split("  smoke-install:", 1)[1].split("  publish-pypi:", 1)[0]

    assert "pool_settings_from_config" in smoke
    assert "from_config" in smoke, "smoke 不再守卫已删除的池工厂，门禁失效"
    assert "ConnectionPool.from_config.__func__" not in smoke
    assert "_pool_factory_hardening" not in smoke
    assert "actions/checkout" not in smoke
