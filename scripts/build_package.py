#!/usr/bin/env python3
# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""Safe local/release distribution verifier and package builder.

Usage::

    python scripts/build_package.py
    python scripts/build_package.py --smoke
    python scripts/build_package.py --no-clean
    python scripts/build_package.py --dist-out build_out
    python scripts/build_package.py --no-isolation
    python scripts/build_package.py --verify-only --dist-out dist

The normal build path mirrors the release workflow: PEP 517 isolation is on,
one universal wheel plus one sdist are required, source/artifact metadata must
agree, both archives must retain the complete Python runtime closure and PEP 561
marker, Twine metadata validation is mandatory, and no command silently
publishes anything.
"""

from __future__ import annotations

import argparse
import contextlib
import email
import hashlib
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import venv
import zipfile
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version

with contextlib.suppress(Exception):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]

os.environ.setdefault("PYTHONUTF8", "1")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

ROOT = pathlib.Path(__file__).resolve().parents[1]
DEFAULT_DIST = ROOT / "dist"
PROJECT_NAME = "tstdx"
REQUIRED_PYTHON = (3, 10)
_PROTECTED_OUTPUT_ROOTS = {
    ".github",
    "ORIGINALITY",
    "PROTOCOL_SPEC",
    "benches",
    "docs",
    "scripts",
    "tests",
    "tstdx",
}
_PROJECT_SECTION_RE = re.compile(
    r"^\[project\]\s*$\n(?P<body>.*?)(?=^\[|\Z)",
    re.MULTILINE | re.DOTALL,
)
_VERSION_RE = re.compile(r'^version\s*=\s*"([^"]+)"\s*$', re.MULTILINE)
_SOURCE_VERSION_RE = re.compile(r'^__version__\s*=\s*"([^"]+)"\s*$', re.MULTILINE)
_HATCHLING_FLOOR_RE = re.compile(r'"hatchling>=(\d+(?:\.\d+)*)"')


def _display_path(path: pathlib.Path) -> str:
    """Render paths without assuming a custom output directory is under ROOT."""

    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def _validate_dist_out(path: pathlib.Path) -> pathlib.Path:
    """Return a safe resolved output directory for artifact cleanup/build output."""

    expanded = path.expanduser()
    if expanded.is_symlink():
        raise SystemExit(f"[安全] --dist-out 不能是符号链接: {expanded}")

    resolved = expanded.resolve()
    if resolved == ROOT or ROOT.is_relative_to(resolved):
        raise SystemExit(f"[安全] --dist-out 不能是仓库根目录或其祖先目录: {resolved}")

    if resolved.is_relative_to(ROOT):
        relative = resolved.relative_to(ROOT)
        if relative.parts and relative.parts[0] in _PROTECTED_OUTPUT_ROOTS:
            raise SystemExit(f"[安全] --dist-out 不能位于受保护源码树: {relative}")

    if resolved.exists() and not resolved.is_dir():
        raise SystemExit(f"[安全] --dist-out 必须是目录: {resolved}")
    return resolved


def _run(cmd: list[str], *, cwd: pathlib.Path | None = None) -> None:
    """Run one subprocess and fail closed on any non-zero exit."""

    print(f"  $ {' '.join(cmd)}")
    proc = subprocess.run(cmd, cwd=cwd or ROOT)
    if proc.returncode != 0:
        raise SystemExit(f"[构建失败] 命令退出码 {proc.returncode}: {' '.join(cmd)}")


def _check_python() -> None:
    if sys.version_info[:2] < REQUIRED_PYTHON:
        raise SystemExit(
            f"[环境] 需要 Python >= {REQUIRED_PYTHON[0]}.{REQUIRED_PYTHON[1]}，"
            f"当前 {sys.version_info.major}.{sys.version_info.minor}"
        )
    print(
        f"[环境] Python {sys.version_info.major}.{sys.version_info.minor}."
        f"{sys.version_info.micro} ✓"
    )


def _require_packaging_tools(*, need_build: bool) -> None:
    """Require build/Twine explicitly instead of mutating the environment."""

    if need_build:
        try:
            import build
        except ImportError as exc:
            raise SystemExit(
                "[环境] 缺少 build；请先运行 `python -m pip install build` 或 `make install`"
            ) from exc
        print(f"[环境] build {build.__version__} ✓")

    try:
        import twine  # noqa: F401
    except ImportError as exc:
        raise SystemExit(
            "[环境] 缺少 twine；请先运行 `python -m pip install twine` 或 `make install`"
        ) from exc
    try:
        twine_version = _pkg_version("twine")
    except PackageNotFoundError:
        twine_version = "?"
    print(f"[环境] twine {twine_version} ✓")


def _stable_version_tuple(raw: str, *, label: str) -> tuple[int, ...]:
    """Parse a stable dotted numeric version used by build-tool floor checks."""

    if re.fullmatch(r"\d+(?:\.\d+)*", raw) is None:
        raise SystemExit(f"[环境] {label} 版本无法进行稳定下限比较: {raw!r}")
    return tuple(int(part) for part in raw.split("."))


def _hatchling_floor() -> str:
    """Read the Hatchling lower bound from the repository build-system contract."""

    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    match = _HATCHLING_FLOOR_RE.search(text)
    if match is None:
        raise SystemExit("[环境] pyproject.toml 缺少 hatchling build-system 下限")
    return match.group(1)


def _require_local_backend_for_no_isolation() -> None:
    """Require a compliant Hatchling for the explicit non-isolated build path."""

    try:
        import hatchling  # noqa: F401
    except ImportError as exc:
        raise SystemExit(
            "[环境] --no-isolation 需要本地 hatchling；请先运行 `make install`"
        ) from exc
    try:
        version = _pkg_version("hatchling")
    except PackageNotFoundError as exc:
        raise SystemExit("[环境] 无法确定本地 hatchling 版本") from exc

    floor = _hatchling_floor()
    if _stable_version_tuple(version, label="hatchling") < _stable_version_tuple(
        floor,
        label="hatchling build-system floor",
    ):
        raise SystemExit(
            f"[环境] hatchling {version} 低于项目 build-system 要求 {floor}; "
            "请运行 `make install` 更新本地构建后端"
        )
    print(f"[环境] hatchling {version} ✓ (--no-isolation, floor={floor})")


def _remove_tree(path: pathlib.Path, *, label: str) -> None:
    """Remove one known repository build tree without following symlinks."""

    if path.is_symlink():
        raise SystemExit(f"[清理失败] {label} 不能是符号链接: {path}")
    if not path.exists():
        return

    resolved = path.resolve()
    if not resolved.is_relative_to(ROOT) or resolved == ROOT:
        raise SystemExit(f"[清理失败] {label} 越过仓库边界: {resolved}")
    if not resolved.is_dir():
        raise SystemExit(f"[清理失败] {label} 期望目录但发现文件: {resolved}")

    shutil.rmtree(resolved)
    print(f"[清理] 删除 {_display_path(resolved)}")


def _clean(dist_out: pathlib.Path) -> None:
    """Remove only known artifacts plus repository-owned build metadata."""

    dist_out = _validate_dist_out(dist_out)
    dist_out.mkdir(parents=True, exist_ok=True)
    for pattern in (f"{PROJECT_NAME}-*.whl", f"{PROJECT_NAME}-*.tar.gz"):
        for artifact in dist_out.glob(pattern):
            if artifact.is_symlink() or not artifact.is_file():
                raise SystemExit(f"[清理失败] 非普通构建产物: {artifact}")
            artifact.unlink()
            print(f"[清理] 删除 {_display_path(artifact)}")

    _remove_tree(ROOT / "build", label="build")
    for egg_info in ROOT.glob("*.egg-info"):
        _remove_tree(egg_info, label="egg-info")


def _build(dist_out: pathlib.Path, *, isolated: bool) -> None:
    """Build one sdist and wheel into the exact requested output directory."""

    cmd = [
        sys.executable,
        "-m",
        "build",
        "--sdist",
        "--wheel",
        "--outdir",
        str(dist_out),
    ]
    if not isolated:
        cmd.append("--no-isolation")
        print("[构建] 显式使用 --no-isolation 兼容模式")
    else:
        print("[构建] 使用 PEP 517 隔离模式（与 release workflow 一致）")
    _run(cmd)


def _project_version_from_text(text: str) -> str:
    """Read only ``[project].version`` rather than matching unrelated TOML keys."""

    project_match = _PROJECT_SECTION_RE.search(text)
    if project_match is None:
        raise SystemExit("[校验失败] pyproject.toml 缺少 [project] section")
    version_match = _VERSION_RE.search(project_match.group("body"))
    if version_match is None:
        raise SystemExit("[校验失败] pyproject.toml 缺少 [project] version")
    return version_match.group(1)


def _declared_versions() -> tuple[str, str]:
    """Return ``(project_metadata_version, source_version)`` without importing tstdx."""

    project_text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    source_text = (ROOT / "tstdx" / "__init__.py").read_text(encoding="utf-8")
    project_version = _project_version_from_text(project_text)
    source_match = _SOURCE_VERSION_RE.search(source_text)
    if source_match is None:
        raise SystemExit("[校验失败] tstdx.__version__ 声明缺失")
    return project_version, source_match.group(1)


def _normalized_distribution_name(raw: str) -> str:
    return re.sub(r"[-_.]+", "-", raw.strip().lower())


def _wheel_metadata(wheel: pathlib.Path) -> tuple[str, str, set[str]]:
    """Return normalized wheel name/version plus archive member names."""

    with zipfile.ZipFile(wheel) as archive:
        names = set(archive.namelist())
        metadata_files = [name for name in names if name.endswith(".dist-info/METADATA")]
        if len(metadata_files) != 1:
            raise SystemExit(f"[校验失败] wheel METADATA 数量异常: {len(metadata_files)}")
        message = email.message_from_bytes(archive.read(metadata_files[0]))

    name = _normalized_distribution_name(str(message.get("Name", "")))
    version = str(message.get("Version", "")).strip()
    return name, version, names


def _sdist_metadata(sdist: pathlib.Path, *, version: str) -> tuple[str, str, set[str]]:
    """Verify sdist archive boundaries and return normalized PKG-INFO identity."""

    root = f"{PROJECT_NAME}-{version}"
    with tarfile.open(sdist, mode="r:gz") as archive:
        members = archive.getmembers()
        if not members:
            raise SystemExit("[校验失败] sdist 为空")

        names: set[str] = set()
        for member in members:
            path = pathlib.PurePosixPath(member.name)
            if path.is_absolute() or ".." in path.parts:
                raise SystemExit(f"[校验失败] sdist 存在越界路径: {member.name}")
            if not path.parts or path.parts[0] != root:
                raise SystemExit(
                    f"[校验失败] sdist 成员不在 canonical 根目录 {root}/: {member.name}"
                )
            if member.issym() or member.islnk():
                raise SystemExit(f"[校验失败] sdist 不允许符号/硬链接成员: {member.name}")
            names.add(member.name.rstrip("/"))

        pkg_info_name = f"{root}/PKG-INFO"
        try:
            pkg_info_member = archive.getmember(pkg_info_name)
        except KeyError as exc:
            raise SystemExit("[校验失败] sdist 缺少 PKG-INFO") from exc
        stream = archive.extractfile(pkg_info_member)
        if stream is None:
            raise SystemExit("[校验失败] sdist PKG-INFO 不是普通文件")
        message = email.message_from_bytes(stream.read())

    name = _normalized_distribution_name(str(message.get("Name", "")))
    metadata_version = str(message.get("Version", "")).strip()
    return name, metadata_version, names


def _source_runtime_members() -> set[str]:
    """Return every repository-owned Python runtime member that artifacts must ship."""

    package_root = ROOT / PROJECT_NAME
    if not package_root.is_dir() or package_root.is_symlink():
        raise SystemExit(f"[校验失败] package source tree 无效: {package_root}")

    members: set[str] = set()
    for path in package_root.rglob("*.py"):
        if path.is_symlink():
            raise SystemExit(f"[校验失败] package runtime 不允许符号链接源码: {path}")
        if path.is_file():
            members.add(path.relative_to(ROOT).as_posix())

    marker = package_root / "py.typed"
    if marker.is_symlink() or not marker.is_file():
        raise SystemExit("[校验失败] source package 缺少普通文件 tstdx/py.typed")
    members.add(marker.relative_to(ROOT).as_posix())

    init_member = f"{PROJECT_NAME}/__init__.py"
    if init_member not in members:
        raise SystemExit(f"[校验失败] source runtime 缺少 {init_member}")
    return members


def _require_archive_runtime_members(
    *,
    archive_label: str,
    actual: set[str],
    required: set[str],
) -> None:
    missing = sorted(required - actual)
    if not missing:
        return
    preview = ", ".join(missing[:8])
    suffix = " ..." if len(missing) > 8 else ""
    raise SystemExit(
        f"[校验失败] {archive_label} 缺少 {len(missing)} 个运行时源码文件: {preview}{suffix}"
    )


def _verify(dist_out: pathlib.Path) -> list[pathlib.Path]:
    """Verify source, artifacts, complete runtime closure and distribution identity."""

    wheels = sorted(dist_out.glob("*.whl"))
    sdists = sorted(dist_out.glob("*.tar.gz"))
    if len(wheels) != 1 or len(sdists) != 1:
        raise SystemExit(
            f"[校验失败] 需要恰好 1 wheel + 1 sdist：wheel={len(wheels)} sdist={len(sdists)}"
        )

    project_version, source_version = _declared_versions()
    if source_version != project_version:
        raise SystemExit(
            f"[校验失败] source version {source_version!r} != project version {project_version!r}"
        )

    wheel = wheels[0]
    sdist = sdists[0]
    expected_wheel = f"{PROJECT_NAME}-{project_version}-py3-none-any.whl"
    expected_sdist = f"{PROJECT_NAME}-{project_version}.tar.gz"
    if wheel.name != expected_wheel:
        raise SystemExit(f"[校验失败] 预期 canonical wheel {expected_wheel}，实际 {wheel.name}")
    if sdist.name != expected_sdist:
        raise SystemExit(f"[校验失败] 预期 canonical sdist {expected_sdist}，实际 {sdist.name}")

    source_runtime = _source_runtime_members()
    wheel_name, wheel_version, wheel_members = _wheel_metadata(wheel)
    if wheel_name != PROJECT_NAME or wheel_version != project_version:
        raise SystemExit(
            "[校验失败] wheel metadata identity mismatch: "
            f"name={wheel_name!r} version={wheel_version!r}"
        )
    _require_archive_runtime_members(
        archive_label="wheel",
        actual=wheel_members,
        required=source_runtime,
    )

    sdist_name, sdist_version, sdist_members = _sdist_metadata(
        sdist,
        version=project_version,
    )
    if sdist_name != PROJECT_NAME or sdist_version != project_version:
        raise SystemExit(
            "[校验失败] sdist metadata identity mismatch: "
            f"name={sdist_name!r} version={sdist_version!r}"
        )

    sdist_root = f"{PROJECT_NAME}-{project_version}"
    _require_archive_runtime_members(
        archive_label="sdist",
        actual=sdist_members,
        required={f"{sdist_root}/{member}" for member in source_runtime},
    )
    for required in (
        f"{sdist_root}/pyproject.toml",
        f"{sdist_root}/README.md",
        f"{sdist_root}/CHANGELOG.md",
        f"{sdist_root}/LICENSE",
    ):
        if required not in sdist_members:
            raise SystemExit(f"[校验失败] sdist 缺少文件 {required}")

    print(
        f"[校验] canonical typed distribution ✓ "
        f"({wheel.name}, {sdist.name}, version={project_version}, "
        f"runtime_files={len(source_runtime)})"
    )
    return [sdist, wheel]


def _twine_check(artifacts: list[pathlib.Path]) -> None:
    """Run Twine metadata/rendering validation on the exact verified artifacts."""

    _run([sys.executable, "-m", "twine", "check", *map(str, artifacts)])


def _sha256(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _smoke(wheel: pathlib.Path) -> None:
    """Install and verify the exact wheel without letting the source checkout shadow it."""

    wheel = wheel.resolve()
    print("[冒烟] 创建临时 venv 并安装 canonical wheel ...")
    with tempfile.TemporaryDirectory(prefix="tstdx-smoke-") as tmp:
        temp_root = pathlib.Path(tmp)
        venv_dir = temp_root / "venv"
        work_dir = temp_root / "work"
        work_dir.mkdir()
        venv.create(venv_dir, with_pip=True)
        if sys.platform == "win32":
            python = venv_dir / "Scripts" / "python.exe"
            cli = venv_dir / "Scripts" / "tstdx.exe"
        else:
            python = venv_dir / "bin" / "python"
            cli = venv_dir / "bin" / "tstdx"

        _run(
            [
                str(python),
                "-m",
                "pip",
                "install",
                "--quiet",
                "--no-deps",
                str(wheel),
            ],
            cwd=work_dir,
        )
        probe = (
            "import importlib.metadata as m, pathlib, sys; "
            "from importlib.resources import files; "
            "import tstdx; "
            "from tstdx.client import AsyncTdxClient, TdxClient; "
            "from tstdx.facade import UnifiedQuoteAPI; "
            "from tstdx.tools.host_audit import audit_all; "
            "from tstdx.transport import ConnectionPool, RankingStore, resolve_hosts; "
            "from tstdx.transport.async_ import AsyncConnectionPool; "
            "package_file = pathlib.Path(tstdx.__file__).resolve(); "
            "venv_root = pathlib.Path(sys.prefix).resolve(); "
            "assert package_file.is_relative_to(venv_root), (package_file, venv_root); "
            "assert tstdx.__version__ == m.version('tstdx'); "
            "assert files('tstdx').joinpath('py.typed').is_file(); "
            "assert callable(audit_all); "
            "assert TdxClient.__init__.__module__ == 'tstdx.client._pool_binding_hardening'; "
            "assert AsyncTdxClient.__init__.__module__ == 'tstdx.client._pool_binding_hardening'; "
            "assert TdxClient.bestip.__module__ == 'tstdx.client._bestip_hardening'; "
            "assert AsyncTdxClient.bestip.__module__ == 'tstdx.client._bestip_hardening'; "
            "assert AsyncTdxClient.quotes_concurrent.__module__ == 'tstdx.client._async_concurrency_hardening'; "
            "assert ConnectionPool.__init__.__module__ == 'tstdx.transport._pool_family_hardening'; "
            "assert ConnectionPool.request.__module__ == 'tstdx.transport._pool_hardening'; "
            "assert ConnectionPool.update_hosts.__module__ == 'tstdx.transport._pool_provenance_hardening'; "
            "assert AsyncConnectionPool.__init__.__module__ == 'tstdx.transport._pool_family_hardening'; "
            "assert AsyncConnectionPool.request.__module__ == 'tstdx.transport._async_pool_hardening'; "
            "assert AsyncConnectionPool.update_hosts.__module__ == 'tstdx.transport._pool_provenance_hardening'; "
            "assert RankingStore.load.__module__ == 'tstdx.transport._ranking_hardening'; "
            "assert resolve_hosts.__module__ == 'tstdx.transport._host_selector_hardening'; "
            "print('tstdx', tstdx.__version__, package_file, 'wheel smoke OK')"
        )
        _run([str(python), "-I", "-c", probe], cwd=work_dir)
        _run([str(cli), "--help"], cwd=work_dir)
        _run([str(cli), "hosts", "audit", "--help"], cwd=work_dir)
        _run([str(python), "-I", "-m", "pip", "check"], cwd=work_dir)
    print("[冒烟] 通过 ✓")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="tstdx 安全构建/分发校验脚本")
    parser.add_argument(
        "--no-clean",
        action="store_true",
        help="跳过历史构建产物清理（增量构建）",
    )
    parser.add_argument(
        "--smoke",
        action="store_true",
        help="在临时 venv 安装 canonical wheel 并验证",
    )
    parser.add_argument(
        "--verify-only",
        action="store_true",
        help="不构建，只校验 wheel/sdist provenance + Twine metadata",
    )
    isolation = parser.add_mutually_exclusive_group()
    isolation.add_argument(
        "--isolated",
        dest="isolated",
        action="store_true",
        help="使用 PEP 517 隔离构建（默认，与 release 一致）",
    )
    isolation.add_argument(
        "--no-isolation",
        dest="isolated",
        action="store_false",
        help="显式使用当前环境 hatchling（仅兼容特殊本地环境）",
    )
    parser.set_defaults(isolated=True)
    parser.add_argument(
        "--dist-out",
        default=str(DEFAULT_DIST),
        help="产物目录（默认 dist/；禁止仓库根/祖先、受保护源码树和 symlink）",
    )
    return parser


def main() -> int:
    args = _parser().parse_args()
    dist_out = _validate_dist_out(pathlib.Path(args.dist_out))

    if args.verify_only and not args.isolated:
        raise SystemExit("[参数] --verify-only 与 --no-isolation 无关，请移除后者")
    if args.verify_only and args.no_clean:
        raise SystemExit("[参数] --verify-only 不接受 --no-clean")

    print("=" * 60)
    print(f"{PROJECT_NAME} {'分发校验' if args.verify_only else '安全一键构建'}")
    print(f"  仓库根: {ROOT}")
    print(f"  产物目录: {_display_path(dist_out)}")
    if not args.verify_only:
        print(f"  隔离构建: {'yes' if args.isolated else 'NO (explicit escape hatch)'}")
    print("=" * 60)

    _check_python()
    _require_packaging_tools(need_build=not args.verify_only)

    if not args.verify_only:
        if not args.isolated:
            _require_local_backend_for_no_isolation()
        if not args.no_clean:
            _clean(dist_out)
        else:
            dist_out.mkdir(parents=True, exist_ok=True)
        _build(dist_out, isolated=args.isolated)

    artifacts = _verify(dist_out)
    _twine_check(artifacts)

    if args.smoke:
        wheel = next(path for path in artifacts if path.suffix == ".whl")
        _smoke(wheel)

    print("=" * 60)
    print("校验完成，产物：")
    for path in artifacts:
        print(f"  {_display_path(path)}  ({path.stat().st_size / 1024:.1f} KB)")
        print(f"    sha256: {_sha256(path)}")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
