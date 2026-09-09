from __future__ import annotations

import importlib.util
import io
import tarfile
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


def _write_minimal_wheel(
    path: Path,
    *,
    version: str = "1.4.0",
    include_typed: bool = True,
) -> None:
    import zipfile

    with zipfile.ZipFile(path, "w") as archive:
        for name in ("tstdx/__init__.py", "tstdx/cli.py", "tstdx/client.py"):
            archive.writestr(name, "")
        if include_typed:
            archive.writestr("tstdx/py.typed", "")
        archive.writestr(
            f"tstdx-{version}.dist-info/METADATA",
            f"Metadata-Version: 2.1\nName: tstdx\nVersion: {version}\n\n",
        )


def _write_minimal_sdist(
    path: Path,
    *,
    version: str = "1.4.0",
    include_typed: bool = True,
    extra_member: str | None = None,
) -> None:
    root = f"tstdx-{version}"
    files = {
        f"{root}/PKG-INFO": f"Metadata-Version: 2.1\nName: tstdx\nVersion: {version}\n\n",
        f"{root}/pyproject.toml": "[project]\nname='tstdx'\n",
        f"{root}/tstdx/__init__.py": f'__version__ = "{version}"\n',
        f"{root}/README.md": "readme\n",
        f"{root}/CHANGELOG.md": "changes\n",
        f"{root}/LICENSE": "MIT\n",
    }
    if include_typed:
        files[f"{root}/tstdx/py.typed"] = ""
    if extra_member is not None:
        files[extra_member] = "escape\n"

    with tarfile.open(path, "w:gz") as archive:
        for name, text in files.items():
            payload = text.encode()
            info = tarfile.TarInfo(name)
            info.size = len(payload)
            archive.addfile(info, io.BytesIO(payload))


def _write_fake_project(root: Path, *, version: str = "1.4.0") -> None:
    package = root / "tstdx"
    package.mkdir(parents=True)
    (root / "pyproject.toml").write_text(
        f'[project]\nname = "tstdx"\nversion = "{version}"\n',
        encoding="utf-8",
    )
    (package / "__init__.py").write_text(
        f'__version__ = "{version}"\n',
        encoding="utf-8",
    )


def test_build_script_rejects_repository_root_and_ancestor_outputs() -> None:
    build = _load_build_script()

    with pytest.raises(SystemExit, match="--dist-out"):
        build._validate_dist_out(build.ROOT)
    with pytest.raises(SystemExit, match="--dist-out"):
        build._validate_dist_out(build.ROOT.parent)


def test_build_script_rejects_outputs_inside_protected_source_trees() -> None:
    build = _load_build_script()

    for relative in ("tstdx/build-out", "tests/build-out", "docs/build-out"):
        with pytest.raises(SystemExit, match="受保护源码树"):
            build._validate_dist_out(build.ROOT / relative)


def test_build_script_rejects_symlink_output(tmp_path: Path) -> None:
    build = _load_build_script()
    target = tmp_path / "real-output"
    target.mkdir()
    link = tmp_path / "linked-output"
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip("platform does not permit symlink creation")

    with pytest.raises(SystemExit, match="符号链接"):
        build._validate_dist_out(link)


def test_build_cleanup_removes_only_tstdx_artifacts_from_custom_output(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    build = _load_build_script()
    fake_root = tmp_path / "repo"
    fake_root.mkdir()
    monkeypatch.setattr(build, "ROOT", fake_root)

    output = tmp_path / "output"
    output.mkdir()
    wheel = output / "tstdx-1.4.0-py3-none-any.whl"
    sdist = output / "tstdx-1.4.0.tar.gz"
    unrelated = output / "keep-me.txt"
    wheel.write_bytes(b"wheel")
    sdist.write_bytes(b"sdist")
    unrelated.write_text("keep", encoding="utf-8")

    build._clean(output)

    assert output.is_dir()
    assert not wheel.exists()
    assert not sdist.exists()
    assert unrelated.read_text(encoding="utf-8") == "keep"


def test_build_script_defaults_to_pep517_isolation() -> None:
    build = _load_build_script()
    args = build._parser().parse_args([])

    assert args.isolated is True
    assert args.verify_only is False


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

    command = commands[0]
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


def test_distribution_verifier_rejects_source_project_version_drift(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    build = _load_build_script()
    fake_root = tmp_path / "repo"
    dist = fake_root / "dist"
    _write_fake_project(fake_root, version="1.4.1")
    dist.mkdir()
    _write_minimal_wheel(dist / "tstdx-1.4.0-py3-none-any.whl")
    _write_minimal_sdist(dist / "tstdx-1.4.0.tar.gz")
    monkeypatch.setattr(build, "ROOT", fake_root)

    with pytest.raises(SystemExit, match="canonical wheel"):
        build._verify(dist)


def test_wheel_verifier_requires_universal_pep561_artifact(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    build = _load_build_script()
    fake_root = tmp_path / "repo"
    dist = fake_root / "dist"
    _write_fake_project(fake_root)
    dist.mkdir()
    _write_minimal_wheel(dist / "tstdx-1.4.0-py3-none-any.whl", include_typed=False)
    _write_minimal_sdist(dist / "tstdx-1.4.0.tar.gz")
    monkeypatch.setattr(build, "ROOT", fake_root)

    with pytest.raises(SystemExit, match="wheel 缺少文件 tstdx/py.typed"):
        build._verify(dist)


def test_sdist_verifier_requires_typed_source_and_metadata(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    build = _load_build_script()
    fake_root = tmp_path / "repo"
    dist = fake_root / "dist"
    _write_fake_project(fake_root)
    dist.mkdir()
    _write_minimal_wheel(dist / "tstdx-1.4.0-py3-none-any.whl")
    _write_minimal_sdist(dist / "tstdx-1.4.0.tar.gz", include_typed=False)
    monkeypatch.setattr(build, "ROOT", fake_root)

    with pytest.raises(SystemExit, match="sdist 缺少文件 .*py.typed"):
        build._verify(dist)


def test_sdist_verifier_rejects_repository_escape_member(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    build = _load_build_script()
    fake_root = tmp_path / "repo"
    dist = fake_root / "dist"
    _write_fake_project(fake_root)
    dist.mkdir()
    _write_minimal_wheel(dist / "tstdx-1.4.0-py3-none-any.whl")
    _write_minimal_sdist(
        dist / "tstdx-1.4.0.tar.gz",
        extra_member="../outside.txt",
    )
    monkeypatch.setattr(build, "ROOT", fake_root)

    with pytest.raises(SystemExit, match="越界路径"):
        build._verify(dist)


def test_twine_check_runs_on_the_exact_verified_artifacts(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    build = _load_build_script()
    commands: list[list[str]] = []
    artifacts = [
        tmp_path / "tstdx-1.4.0.tar.gz",
        tmp_path / "tstdx-1.4.0-py3-none-any.whl",
    ]

    def capture(cmd: list[str], *, cwd: Path | None = None) -> None:
        del cwd
        commands.append(cmd)

    monkeypatch.setattr(build, "_run", capture)
    build._twine_check(artifacts)

    assert commands[0][1:4] == ["-m", "twine", "check"]
    assert commands[0][4:] == [str(path) for path in artifacts]
