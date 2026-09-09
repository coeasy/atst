from __future__ import annotations

from pathlib import Path


_ROOT = Path(__file__).resolve().parents[2]


def test_release_matrix_smokes_async_circuit_hardening_from_installed_wheel() -> None:
    workflow = (_ROOT / ".github" / "workflows" / "wheels.yml").read_text(encoding="utf-8")
    smoke = workflow.split("  smoke-install:", 1)[1].split("  publish-pypi:", 1)[0]

    assert "from tstdx.transport.async_ import AsyncConnectionPool" in smoke
    assert "_circuit_allows" in smoke
    assert "_release_probe_token" in smoke
    assert "actions/checkout" not in smoke
