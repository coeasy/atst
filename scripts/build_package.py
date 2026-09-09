#!/usr/bin/env python3
# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""Safe local package build: clean -> isolated build -> verify -> optional smoke.

Usage::

    python scripts/build_package.py
    python scripts/build_package.py --smoke
    python scripts/build_package.py --no-clean
    python scripts/build_package.py --dist-out build_out
    python scripts/build_package.py --no-isolation  # explicit compatibility escape hatch

The default path intentionally mirrors the release workflow: PEP 517 isolation is
ON, one universal wheel plus one sdist are required, the wheel must contain the
PEP 561 marker, and no command silently publishes anything.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import venv
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version

# Windows consoles may default to GBK. Printing diagnostics must never decide
# whether a build succeeds, so keep this best-effort and propagate UTF-8 to all
# subprocesses as well.
with contextlib.suppress(Exception):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]

os.environ.setdefault("PYTHONUTF8", "1")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

ROOT = pathlib.Path(__file__).resolve().parents[1]
DEFAULT_DIST = ROOT / "dist"
PROJECT_NAME = "tstdx"
REQUIRED_PYTHON = (3, 10)


def _display_path(path: pathlib.Path) -> str:
    """Render paths without assuming a custom output directory is under ROOT."""

    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def _validate_dist_out(path: pathlib.Path) -> pathlib.Path:
    """Return a safe resolved output directory.

    Cleaning is destructive by design. Never allow the repository root or any
    ancestor of it as the output directory, otherwise ``--dist-out .`` or a
    parent path could recursively delete source code before the build starts.
    """

    resolved = path.expanduser().resolve()
    if resolved == ROOT or ROOT.is_relative_to(resolved):
        raise SystemExit(
            "[安全] --dist-out 不能是仓库根目录或其祖先目录: " f"{resolved}"
        )
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


def _require_build_tools(*, isolated: bool) -> None:
    """Require explicit packaging tools instead of mutating the environment."""

    try:
        import build
    except ImportError as exc:
        raise SystemExit(
            "[环境] 缺少 build；请先运行 `python -m pip install build` "
            "或 `make install`"
        ) from exc
    print(f"[环境] build {build.__version__} ✓")

    # PEP 517 isolation installs the backend declared by pyproject itself. Only
    # the explicit --no-isolation escape hatch requires hatchling locally.
    if isolated:
        return
    try:
        import hatchling  # noqa: F401
    except ImportError as exc:
        raise SystemExit(
            "[环境] --no-isolation 需要本地 hatchling；请先运行 `make install`"
        ) from exc
    try:
        version = _pkg_version("hatchling")
    except PackageNotFoundError:
        version = "?"
    print(f"[环境] hatchling {version} ✓ (--no-isolation)")


def _clean(dist_out: pathlib.Path) -> None:
    """Remove known build outputs; never suppress deletion errors."""

    targets = [dist_out, ROOT / "build"]
    targets.extend(p for p in ROOT.glob("*.egg-info") if p.is_dir())

    seen: set[pathlib.Path] = set()
    for target in targets:
        target = target.resolve()
        if target in seen or not target.exists():
            continue
        seen.add(target)
        if not target.is_dir():
            raise SystemExit(f"[清理失败] 期望目录但发现文件: {target}")
        shutil.rmtree(target)
        print(f"[清理] 删除 {_display_path(target)}")

    dist_out.mkdir(parents=True, exist_ok=True)


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


def _sha256(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _verify(dist_out: pathlib.Path) -> list[pathlib.Path]:
    """Verify artifact count, universal-wheel identity and required package files."""

    wheels = sorted(dist_out.glob("*.whl"))
    sdists = sorted(dist_out.glob("*.tar.gz"))
    if len(wheels) != 1 or len(sdists) != 1:
        raise SystemExit(
            f"[校验失败] 需要恰好 1 wheel + 1 sdist："
            f"wheel={len(wheels)} sdist={len(sdists)}"
        )

    wheel = wheels[0]
    if not wheel.name.endswith("-py3-none-any.whl"):
        raise SystemExit(f"[校验失败] 预期 universal wheel，实际 {wheel.name}")

    import zipfile

    with zipfile.ZipFile(wheel) as archive:
        names = set(archive.namelist())
        for required in (
            f"{PROJECT_NAME}/__init__.py",
            f"{PROJECT_NAME}/cli.py",
            f"{PROJECT_NAME}/client.py",
            f"{PROJECT_NAME}/py.typed",
        ):
            if required not in names:
                raise SystemExit(f"[校验失败] wheel 缺少文件 {required}")

    print(f"[校验] universal typed wheel 结构完整 ✓ ({wheel.name})")
    return [*sdists, *wheels]


def _smoke(wheel: pathlib.Path) -> None:
    """Install the exact wheel into a clean venv and verify its public identity."""

    print("[冒烟] 创建临时 venv 并安装 canonical wheel ...")
    with tempfile.TemporaryDirectory(prefix="tstdx-smoke-") as tmp:
        venv_dir = pathlib.Path(tmp) / "venv"
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
            ]
        )
        probe = (
            "import importlib.metadata as m; "
            "from importlib.resources import files; "
            "import tstdx; "
            "from tstdx.client import TdxClient; "
            "from tstdx.facade import UnifiedQuoteAPI; "
            "assert tstdx.__version__ == m.version('tstdx'); "
            "assert files('tstdx').joinpath('py.typed').is_file(); "
            "print('tstdx', tstdx.__version__, 'wheel smoke OK')"
        )
        _run([str(python), "-c", probe])
        _run([str(cli), "--help"])
        _run([str(python), "-m", "pip", "check"])
    print("[冒烟] 通过 ✓")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="tstdx 安全一键构建脚本")
    parser.add_argument(
        "--no-clean",
        action="store_true",
        help="跳过 dist/build/egg-info 清理（增量构建）",
    )
    parser.add_argument(
        "--smoke",
        action="store_true",
        help="构建后在临时 venv 安装 canonical wheel 并验证",
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
        help="产物目录（默认 dist/；禁止仓库根目录及其祖先）",
    )
    return parser


def main() -> int:
    args = _parser().parse_args()
    dist_out = _validate_dist_out(pathlib.Path(args.dist_out))

    print("=" * 60)
    print(f"{PROJECT_NAME} 安全一键构建")
    print(f"  仓库根: {ROOT}")
    print(f"  产物目录: {_display_path(dist_out)}")
    print(f"  隔离构建: {'yes' if args.isolated else 'NO (explicit escape hatch)'}")
    print("=" * 60)

    _check_python()
    _require_build_tools(isolated=args.isolated)
    if not args.no_clean:
        _clean(dist_out)
    else:
        dist_out.mkdir(parents=True, exist_ok=True)
    _build(dist_out, isolated=args.isolated)
    artifacts = _verify(dist_out)

    if args.smoke:
        wheel = next(path for path in artifacts if path.suffix == ".whl")
        _smoke(wheel)

    print("=" * 60)
    print("构建完成，产物：")
    for path in artifacts:
        print(f"  {_display_path(path)}  ({path.stat().st_size / 1024:.1f} KB)")
        print(f"    sha256: {_sha256(path)}")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
