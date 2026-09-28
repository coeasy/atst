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
    spec = importlib.util.spec_from_file_location("atst_build_package_contract", _SCRIPT)
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
    extra_members: tuple[str, ...] = (),
) -> None:
    import zipfile

    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("atst/__init__.py", "from ._version import __version__\n")
        archive.writestr("atst/_version.py", f'__version__ = "{version}"\n')
        if include_typed:
            archive.writestr("atst/py.typed", "")
        for name in extra_members:
            archive.writestr(name, "")
        archive.writestr(
            f"atst-{version}.dist-info/METADATA",
            f"Metadata-Version: 2.1\nName: atst\nVersion: {version}\n\n",
        )


def _write_minimal_sdist(
    path: Path,
    *,
    version: str = "1.4.0",
    include_typed: bool = True,
    extra_member: str | None = None,
    extra_runtime_members: tuple[str, ...] = (),
) -> None:
    root = f"atst-{version}"
    files = {
        f"{root}/PKG-INFO": f"Metadata-Version: 2.1\nName: atst\nVersion: {version}\n\n",
        f"{root}/pyproject.toml": (
            "[project]\nname='atst'\ndynamic=['version']\n"
            "[tool.hatch.version]\npath='atst/_version.py'\n"
        ),
        f"{root}/atst/__init__.py": "from ._version import __version__\n",
        f"{root}/atst/_version.py": f'__version__ = "{version}"\n',
        f"{root}/README.md": "readme\n",
        f"{root}/CHANGELOG.md": "changes\n",
        f"{root}/LICENSE": "MIT\n",
    }
    if include_typed:
        files[f"{root}/atst/py.typed"] = ""
    for member in extra_runtime_members:
        files[f"{root}/{member}"] = ""
    if extra_member is not None:
        files[extra_member] = "escape\n"

    with tarfile.open(path, "w:gz") as archive:
        for name, text in files.items():
            payload = text.encode()
            info = tarfile.TarInfo(name)
            info.size = len(payload)
            archive.addfile(info, io.BytesIO(payload))


def _write_fake_project(
    root: Path,
    *,
    source_version: str = "1.4.0",
) -> None:
    package = root / "atst"
    package.mkdir(parents=True)
    (root / "pyproject.toml").write_text(
        '[project]\nname = "atst"\ndynamic = ["version"]\n'
        '[tool.hatch.version]\npath = "atst/_version.py"\n',
        encoding="utf-8",
    )
    (package / "__init__.py").write_text(
        "from ._version import __version__\n",
        encoding="utf-8",
    )
    (package / "_version.py").write_text(
        f'__version__ = "{source_version}"\n',
        encoding="utf-8",
    )
    (package / "py.typed").write_text("", encoding="utf-8")


def test_build_script_rejects_repository_root_and_ancestor_outputs() -> None:
    build = _load_build_script()

    with pytest.raises(SystemExit, match="--dist-out"):
        build._validate_dist_out(build.ROOT)
    with pytest.raises(SystemExit, match="--dist-out"):
        build._validate_dist_out(build.ROOT.parent)


def test_build_script_rejects_outputs_inside_protected_source_trees() -> None:
    build = _load_build_script()

    for relative in ("atst/build-out", "tests/build-out", "docs/build-out"):
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


def test_build_cleanup_removes_only_atst_artifacts_from_custom_output(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    build = _load_build_script()
    fake_root = tmp_path / "repo"
    fake_root.mkdir()
    monkeypatch.setattr(build, "ROOT", fake_root)

    output = tmp_path / "output"
    output.mkdir()
    wheel = output / "atst-1.4.0-py3-none-any.whl"
    sdist = output / "atst-1.4.0.tar.gz"
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


def test_distribution_verifier_rejects_artifact_version_drift(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    build = _load_build_script()
    fake_root = tmp_path / "repo"
    dist = fake_root / "dist"
    _write_fake_project(fake_root, source_version="1.4.1")
    dist.mkdir()
    _write_minimal_wheel(dist / "atst-1.4.0-py3-none-any.whl")
    _write_minimal_sdist(dist / "atst-1.4.0.tar.gz")
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
    _write_minimal_wheel(dist / "atst-1.4.0-py3-none-any.whl", include_typed=False)
    _write_minimal_sdist(dist / "atst-1.4.0.tar.gz")
    monkeypatch.setattr(build, "ROOT", fake_root)

    with pytest.raises(SystemExit, match="wheel 缺少 .*atst/py.typed"):
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
    _write_minimal_wheel(dist / "atst-1.4.0-py3-none-any.whl")
    _write_minimal_sdist(dist / "atst-1.4.0.tar.gz", include_typed=False)
    monkeypatch.setattr(build, "ROOT", fake_root)

    with pytest.raises(SystemExit, match="sdist 缺少 .*atst/py.typed"):
        build._verify(dist)


def test_wheel_verifier_requires_every_source_runtime_module(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    build = _load_build_script()
    fake_root = tmp_path / "repo"
    dist = fake_root / "dist"
    _write_fake_project(fake_root)
    (fake_root / "atst" / "_runtime_hardening.py").write_text("VALUE = 1\n", encoding="utf-8")
    dist.mkdir()
    _write_minimal_wheel(dist / "atst-1.4.0-py3-none-any.whl")
    _write_minimal_sdist(
        dist / "atst-1.4.0.tar.gz",
        extra_runtime_members=("atst/_runtime_hardening.py",),
    )
    monkeypatch.setattr(build, "ROOT", fake_root)

    with pytest.raises(SystemExit, match="wheel 缺少 .*_runtime_hardening.py"):
        build._verify(dist)


def test_sdist_verifier_requires_every_source_runtime_module(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    build = _load_build_script()
    fake_root = tmp_path / "repo"
    dist = fake_root / "dist"
    _write_fake_project(fake_root)
    nested = fake_root / "atst" / "client"
    nested.mkdir()
    (nested / "_runtime_hardening.py").write_text("VALUE = 1\n", encoding="utf-8")
    dist.mkdir()
    _write_minimal_wheel(
        dist / "atst-1.4.0-py3-none-any.whl",
        extra_members=("atst/client/_runtime_hardening.py",),
    )
    _write_minimal_sdist(dist / "atst-1.4.0.tar.gz")
    monkeypatch.setattr(build, "ROOT", fake_root)

    with pytest.raises(SystemExit, match="sdist 缺少 .*_runtime_hardening.py"):
        build._verify(dist)


def test_distribution_verifier_accepts_complete_nested_runtime_closure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    build = _load_build_script()
    fake_root = tmp_path / "repo"
    dist = fake_root / "dist"
    _write_fake_project(fake_root)
    nested = fake_root / "atst" / "client"
    nested.mkdir()
    (nested / "_runtime_hardening.py").write_text("VALUE = 1\n", encoding="utf-8")
    dist.mkdir()
    runtime_member = "atst/client/_runtime_hardening.py"
    _write_minimal_wheel(
        dist / "atst-1.4.0-py3-none-any.whl",
        extra_members=(runtime_member,),
    )
    _write_minimal_sdist(
        dist / "atst-1.4.0.tar.gz",
        extra_runtime_members=(runtime_member,),
    )
    monkeypatch.setattr(build, "ROOT", fake_root)

    artifacts = build._verify(dist)

    assert [path.name for path in artifacts] == [
        "atst-1.4.0.tar.gz",
        "atst-1.4.0-py3-none-any.whl",
    ]


def test_sdist_verifier_rejects_repository_escape_member(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    build = _load_build_script()
    fake_root = tmp_path / "repo"
    dist = fake_root / "dist"
    _write_fake_project(fake_root)
    dist.mkdir()
    _write_minimal_wheel(dist / "atst-1.4.0-py3-none-any.whl")
    _write_minimal_sdist(
        dist / "atst-1.4.0.tar.gz",
        extra_member="../outside.txt",
    )
    monkeypatch.setattr(build, "ROOT", fake_root)

    with pytest.raises(SystemExit, match="越界路径"):
        build._verify(dist)


def test_local_smoke_never_imports_package_from_source_checkout(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    build = _load_build_script()
    calls: list[tuple[list[str], Path | None]] = []
    wheel = tmp_path / "atst-1.4.0-py3-none-any.whl"
    wheel.touch()

    monkeypatch.setattr(build.venv, "create", lambda *args, **kwargs: None)

    def capture(cmd: list[str], *, cwd: Path | None = None) -> None:
        calls.append((cmd, cwd))

    monkeypatch.setattr(build, "_run", capture)
    build._smoke(wheel)

    assert calls
    assert all(cwd is not None and cwd.name == "work" for _, cwd in calls)
    probe_cmd = next(cmd for cmd, _ in calls if "-c" in cmd)
    assert "-I" in probe_cmd
    probe_text = probe_cmd[probe_cmd.index("-c") + 1]
    assert "package_file.is_relative_to(venv_root)" in probe_text
    assert "TdxClient.bestip.__module__ == 'atst.client.sync'" in probe_text
    assert "AsyncTdxClient.bestip.__module__ == 'atst.client.async_'" in probe_text


def test_twine_check_runs_on_the_exact_verified_artifacts(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    build = _load_build_script()
    commands: list[list[str]] = []
    artifacts = [
        tmp_path / "atst-1.4.0.tar.gz",
        tmp_path / "atst-1.4.0-py3-none-any.whl",
    ]

    def capture(cmd: list[str], *, cwd: Path | None = None) -> None:
        del cwd
        commands.append(cmd)

    monkeypatch.setattr(build, "_run", capture)
    build._twine_check(artifacts)

    assert commands[0][1:4] == ["-m", "twine", "check"]
    assert commands[0][4:] == [str(path) for path in artifacts]
