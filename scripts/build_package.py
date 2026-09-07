#!/usr/bin/env python3
# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""tstdx 一键构建脚本：清理 → 构建 sdist+wheel → 校验产物 →（可选）安装冒烟。

用法::

    python scripts/build_package.py                # 构建（自动安装缺失的 build 模块）
    python scripts/build_package.py --smoke        # 构建 + 临时 venv 安装冒烟
    python scripts/build_package.py --no-clean     # 跳过清理（增量构建）
    python scripts/build_package.py --dist-out build_out   # 自定义产物目录

流程:
    1. 环境自检（Python 版本 ≥ 3.10；build 模块缺失时自动 ``pip install build``）
    2. 清理 ``dist/``、``build/``、``*.egg-info``（--no-clean 跳过）
    3. ``python -m build`` 产出 sdist + wheel
    4. 校验：产物存在性 / 关键模块在 wheel 内 / sha256 摘要
    5. --smoke：临时 venv 安装 wheel（--no-deps）并 ``import tstdx`` 冒烟

任何一步失败以非零码退出；全部成功打印产物清单与 SHA-256。
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import os
import pathlib
import subprocess
import sys
import tempfile
import venv
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version

# Windows 控制台默认 GBK，中文输出/✓ 等字符可能触发 UnicodeEncodeError——
# 统一重配 stdout/stderr 为 UTF-8（replace 兜底），不让打印本身决定成败。
with contextlib.suppress(Exception):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]

# 关键：把 UTF-8 模式传播给全部子进程（pip / build / 冒烟 venv 的 python）。
# 否则 Windows 上 build 隔离环境里 pip 输出 GBK 字节，build 按 UTF-8 解码
# 直接 UnicodeDecodeError 崩溃（实测复现）。
os.environ.setdefault("PYTHONUTF8", "1")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

ROOT = pathlib.Path(__file__).resolve().parents[1]
DEFAULT_DIST = ROOT / "dist"
PROJECT_NAME = "tstdx"
REQUIRED_PYTHON = (3, 10)


def _run(cmd: list[str], *, cwd: pathlib.Path | None = None) -> None:
    """运行子进程；非零退出即抛异常终止脚本。"""
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
        f"[环境] Python {sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro} ✓"
    )


def _ensure_build() -> None:
    """确保 build 与构建后端 hatchling 可用；缺失时自动安装。"""
    try:
        import build  # noqa: F401

        print(f"[环境] build {build.__version__} ✓")
    except ImportError:
        print("[环境] 未安装 build —— 自动 pip install build")
        _run([sys.executable, "-m", "pip", "install", "build"])
    try:
        import hatchling  # noqa: F401

        try:
            ver = _pkg_version("hatchling")
        except PackageNotFoundError:
            ver = "?"
        print(f"[环境] hatchling {ver} ✓")
    except ImportError:
        print("[环境] 未安装 hatchling —— 自动 pip install hatchling")
        _run([sys.executable, "-m", "pip", "install", "hatchling"])


def _clean(dist_out: pathlib.Path) -> None:
    import shutil

    targets = [dist_out, ROOT / "build"]
    targets += [p for p in ROOT.glob("*.egg-info") if p.is_dir()]
    for t in targets:
        if t.exists():
            shutil.rmtree(t, ignore_errors=True)
            print(f"[清理] 删除 {t.relative_to(ROOT)}")
    dist_out.mkdir(parents=True, exist_ok=True)


def _build(isolated: bool) -> None:
    cmd = [sys.executable, "-m", "build", "--sdist", "--wheel"]
    if not isolated:
        # conda 环境下 build 的隔离环境有兼容问题（pip --python 装依赖失败，
        # 实测复现），而 hatchling 是唯一 build-system 依赖——非隔离模式
        # 直接用当前环境的 hatchling 构建，稳定可靠。
        cmd.append("--no-isolation")
        print("[构建] python -m build --no-isolation --sdist --wheel ...")
    else:
        print("[构建] python -m build --sdist --wheel ...")
    _run(cmd)


def _sha256(path: pathlib.Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _verify(dist_out: pathlib.Path) -> list[pathlib.Path]:
    """校验产物：sdist + wheel 各一；wheel 内含核心模块。"""
    wheels = sorted(dist_out.glob("*.whl"))
    sdists = sorted(dist_out.glob("*.tar.gz"))
    if not wheels or not sdists:
        raise SystemExit("[校验失败] 产物缺失：需要 1 个 .whl + 1 个 .tar.gz")
    if len(wheels) != 1 or len(sdists) != 1:
        raise SystemExit(f"[校验失败] 产物数量异常：wheel={len(wheels)} sdist={len(sdists)}")

    import zipfile

    whl = wheels[0]
    with zipfile.ZipFile(whl) as z:
        names = set(z.namelist())
        for required in (
            f"{PROJECT_NAME}/__init__.py",
            f"{PROJECT_NAME}/cli.py",
            f"{PROJECT_NAME}/client.py",
        ):
            if required not in names:
                raise SystemExit(f"[校验失败] wheel 缺少核心模块 {required}")
    print(f"[校验] wheel 核心模块齐全 ✓ ({whl.name})")
    return [*sdists, *wheels]


def _smoke(whl: pathlib.Path) -> None:
    """临时 venv 安装 wheel（--no-deps，核心零依赖可独立冒烟）并 import 验证。"""
    print("[冒烟] 创建临时 venv 并安装产物 ...")
    with tempfile.TemporaryDirectory(prefix="tstdx-smoke-") as tmp:
        venv_dir = pathlib.Path(tmp) / "venv"
        venv.create(venv_dir, with_pip=True)
        py = venv_dir / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
        _run([str(py), "-m", "pip", "install", "--quiet", "--no-deps", str(whl)])
        probe = (
            "import tstdx; "
            "from tstdx.client import TdxClient; "
            "from tstdx.facade import UnifiedQuoteAPI; "
            "print('tstdx', tstdx.__version__, '导入冒烟 OK')"
        )
        _run([str(py), "-c", probe])
    print("[冒烟] 通过 ✓")


def main() -> int:
    ap = argparse.ArgumentParser(description="tstdx 一键构建脚本")
    ap.add_argument(
        "--no-clean", action="store_true", help="跳过 dist/build/egg-info 清理（增量构建）"
    )
    ap.add_argument("--smoke", action="store_true", help="构建后在临时 venv 安装并 import 冒烟")
    ap.add_argument(
        "--isolated", action="store_true", help="使用 build 隔离环境构建（默认非隔离，兼容 conda）"
    )
    ap.add_argument("--dist-out", default=str(DEFAULT_DIST), help="产物目录（默认 dist/）")
    args = ap.parse_args()

    dist_out = pathlib.Path(args.dist_out).resolve()
    print("=" * 60)
    print(f"{PROJECT_NAME} 一键构建")
    print(f"  仓库根: {ROOT}")
    print(f"  产物目录: {dist_out}")
    print("=" * 60)

    _check_python()
    _ensure_build()
    if not args.no_clean:
        _clean(dist_out)
    _build(isolated=args.isolated)
    artifacts = _verify(dist_out)

    if args.smoke:
        wheel = next(p for p in artifacts if p.suffix == ".whl")
        _smoke(wheel)

    print("=" * 60)
    print("构建完成，产物：")
    for p in artifacts:
        print(f"  {p.relative_to(ROOT)}  ({p.stat().st_size / 1024:.1f} KB)")
        print(f"    sha256: {_sha256(p)}")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
