from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest


_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = _ROOT / "scripts" / "build_package.py"


def _load_build_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("tstdx_build_package_contract", _SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_build_script_rejects_repository_root_and_ancestor_outputs() -> None:
    build = _load_build_script()

    with pytest.raises(SystemExit, match="--dist-out"):
        build._validate_dist_out(build.ROOT)
    with pytest.raises(SystemExit, match="--dist-out"):
        build._validate_dist_out(build.ROOT.parent)


def test_build_script_defaults_to_pep517_isolation() -> None:
    build = _load_build_script()

    args = build._parser().parse_args([])

    assert args.isolated is True


def test_build_script_passes_exact_custom_outdir_to_python_build(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    build = _load_build_script()
    commands: list[list[str]] = []

    def capture(cmd: list[str], *, cwd: Path | None = None) -> None:
        del cwd
        commands.append(cmd)

    monkeypatch.setattr(build, "_run", capture)
    build._build(tmp_path, isolated=True)

    assert len(commands) == 1
    command = commands[0]
    assert "--outdir" in command
    assert command[command.index("--outdir") + 1] == str(tmp_path)
    assert "--no-isolation" not in command


def test_no_isolation_is_an_explicit_build_escape_hatch(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    build = _load_build_script()
    commands: list[list[str]] = []

    def capture(cmd: list[str], *, cwd: Path | None = None) -> None:
        del cwd
        commands.append(cmd)

    monkeypatch.setattr(build, "_run", capture)
    build._build(tmp_path, isolated=False)

    assert "--no-isolation" in commands[0]


def test_wheel_verifier_requires_universal_pep561_artifact(tmp_path: Path) -> None:
    build = _load_build_script()
    wheel = tmp_path / "tstdx-1.4.0-py3-none-any.whl"
    sdist = tmp_path / "tstdx-1.4.0.tar.gz"

    import zipfile

    with zipfile.ZipFile(wheel, "w") as archive:
        for name in (
            "tstdx/__init__.py",
            "tstdx/cli.py",
            "tstdx/client.py",
        ):
            archive.writestr(name, "")
    sdist.write_bytes(b"placeholder")

    with pytest.raises(SystemExit, match="py.typed"):
        build._verify(tmp_path)
