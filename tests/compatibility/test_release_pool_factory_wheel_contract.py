from __future__ import annotations

from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]


def test_release_wheel_smoke_requires_authoritative_pool_factory_selector() -> None:
    workflow = (_ROOT / ".github" / "workflows" / "wheels.yml").read_text(
        encoding="utf-8"
    )
    smoke = workflow.split("  smoke-install:", 1)[1].split("  publish-pypi:", 1)[0]

    assert "ConnectionPool.from_config.__func__.__module__" in smoke
    assert "tstdx.transport._pool_factory_hardening" in smoke
    assert "actions/checkout" not in smoke
