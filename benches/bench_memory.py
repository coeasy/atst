# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""Q4 内存峰值基准：tracemalloc 三类场景基线。

场景
----
1. ``market``   全市场行情（约 5400 只 A 股 Quote 对象驻留）
2. ``kline``    1000 标的 K 线（每标的 320 根 Bar 对象驻留）
3. ``vipdoc``   vipdoc 全目录扫描（全部 .day 文件记录驻留）

数据来源
--------
* ``--synthetic``（默认）：合成数据，确定性、离线可跑，供 CI 与回归对比；
* ``--live``：接真实数据源（新浪全市场 / TDX 主站 / 本地 vipdoc_root），
  仅在需要贴近生产的峰值时使用。

输出
----
* 控制台表格（峰值 / 稳态 / 条目数 / 每条目均摊）；
* ``--json PATH`` 可选把结果写 JSON（供 CI 归档与 diff）。

用法示例
--------
.. code-block:: console

    python benches/bench_memory.py --scenario market
    python benches/bench_memory.py --all --json benches/results/memory_baseline.json
    python benches/bench_memory.py --live --all --vipdoc-root C:/tdx/vipdoc
"""

from __future__ import annotations

import argparse
import gc
import json
import struct
import sys
import tempfile
import time
import tracemalloc
from collections.abc import Callable
from pathlib import Path
from typing import Any

# 保证以仓库根为 cwd 运行时可直接 import tstdx（无需安装）
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


# --------------------------------------------------------------------------- #
# 合成数据生成器
# --------------------------------------------------------------------------- #
def _synthetic_market_quotes(count: int) -> list[Any]:
    """生成 ``count`` 只 A 股 Quote 对象（模拟 fetch_all 驻留）。"""
    from tstdx.domain.models import Level, Quote

    out: list[Quote] = []
    for i in range(count):
        price = 10.0 + (i % 1000) / 100.0
        out.append(
            Quote(
                code=f"{600000 + i:06d}",
                datetime=f"2026-09-02 15:00:{i % 60:02d}",
                price=round(price, 2),
                last_close=round(price - 0.1, 2),
                open=round(price - 0.05, 2),
                high=round(price + 0.2, 2),
                low=round(price - 0.3, 2),
                volume=100000 + i * 100,
                amount=round(price * (100000 + i * 100), 2),
                bid=[Level(price=round(price - 0.01, 2), volume=1000 + i) for _ in range(5)],
                ask=[Level(price=round(price + 0.01, 2), volume=1000 + i) for _ in range(5)],
                extra={"float_shares": 10**9 + i, "turnover": 1.23},
            )
        )
    return out


def _synthetic_day_bytes(rows: int, *, seed: int = 1) -> bytes:
    """生成 ``rows`` 条 .day 日线记录的原始字节（u32 价格 ×100）。"""
    buf = bytearray()
    for i in range(rows):
        date = 20260101 + i  # 递增日期（仅用于合成，超出真实日历无妨）
        base = 1000 + ((i + seed) % 9000)
        o, h, lo, c = base, base + 20, base - 10, base + 5
        amount = float(123456.78 + i)
        volume = 100000 + i * 100
        prev_close = base - 5
        buf += struct.pack(
            "<IIIIIfII",
            date,
            o,
            h,
            lo,
            c,
            amount,
            volume,
            prev_close,
        )
    return bytes(buf)


def _synthetic_vipdoc_tree(root: Path, *, files: int, rows: int) -> None:
    """构造合成 vipdoc 目录树：``root/sh|sz/lday/*.day``。"""
    for market in ("sh", "sz"):
        d = root / market / "lday"
        d.mkdir(parents=True, exist_ok=True)
        for i in range(files // 2):
            code = f"{market}{600000 + i:06d}"
            (d / f"{code}.day").write_bytes(_synthetic_day_bytes(rows, seed=i))


# --------------------------------------------------------------------------- #
# 三类场景
# --------------------------------------------------------------------------- #
def scenario_market(synthetic: bool = True) -> dict[str, Any]:
    """全市场行情驻留：fetch_all 结果 ≈ 5400 只 Quote。"""
    if synthetic:
        rows = _synthetic_market_quotes(5400)
    else:
        from tstdx.web import create_source

        src = create_source("sina")
        try:
            rows = src.fetch_all(node="hs_a", page_size=100)
        finally:
            src.close()
    return {"rows": len(rows), "payload": rows}


def scenario_kline(synthetic: bool = True, *, count: int = 1000) -> dict[str, Any]:
    """1000 标的 K 线驻留：每标的 320 根 Bar。"""
    rows: list[Any] = []
    if synthetic:
        from tstdx.domain.models import Bar

        for i in range(count):
            for j in range(320):
                rows.append(
                    Bar(
                        datetime=f"2026-{1 + j // 28:02d}-{1 + j % 28:02d}",
                        open=10.0 + i / 100,
                        high=10.2 + i / 100,
                        low=9.9 + i / 100,
                        close=10.1 + i / 100,
                        volume=100000 + j * 10,
                        amount=1_234_567.89 + j,
                    )
                )
    else:
        from tstdx.client import TdxClient

        client = TdxClient()
        try:
            for i in range(count):
                code = f"600{600 + i:03d}"
                client.bars(code, period="day", count=320, as_format="obj")
        finally:
            client.close()
    return {"rows": len(rows), "payload": rows}


def scenario_vipdoc(synthetic: bool = True, *, root: str | None = None) -> dict[str, Any]:
    """vipdoc 全目录扫描：读取全部 .day 记录并驻留。"""
    from tstdx.reader.formats import DayBarReader

    rows: list[Any] = []
    scan_root = Path(root) if (not synthetic and root) else None
    if scan_root is not None and scan_root.exists():
        files = list(scan_root.rglob("*.day"))
    else:
        # 合成：临时目录建 200 个文件 × 400 行 = 8 万条记录
        tmp = tempfile.mkdtemp(prefix="tstdx_bench_vipdoc_")
        _synthetic_vipdoc_tree(Path(tmp), files=200, rows=400)
        files = list(Path(tmp).rglob("*.day"))

    reader = DayBarReader()
    for f in files:
        rows.extend(reader.read(f, output="model"))
    return {"rows": len(rows), "payload": rows}


SCENARIOS: dict[str, Callable[..., dict[str, Any]]] = {
    "market": scenario_market,
    "kline": scenario_kline,
    "vipdoc": scenario_vipdoc,
}


# --------------------------------------------------------------------------- #
# 测量与输出
# --------------------------------------------------------------------------- #
def measure(fn: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    """在 tracemalloc 下运行场景，返回峰值内存（KiB）与耗时。"""
    gc.collect()
    tracemalloc.start()
    t0 = time.perf_counter()
    result = fn()
    elapsed = time.perf_counter() - t0
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    result["elapsed_s"] = round(elapsed, 4)
    result["peak_kib"] = round(peak / 1024, 2)
    result["peak_mib"] = round(peak / 1024 / 1024, 2)
    # 释放 payload 引用，便于下一场景
    result["payload"] = None
    return result


def _fmt(kib: float) -> str:
    if kib >= 1024:
        return f"{kib / 1024:.2f} MiB"
    return f"{kib:.0f} KiB"


def run_one(name: str, *, synthetic: bool, vipdoc_root: str | None) -> dict[str, Any]:
    fn = SCENARIOS[name]
    if name == "kline":
        return measure(lambda: fn(synthetic=synthetic))
    if name == "vipdoc":
        return measure(lambda: fn(synthetic=synthetic, root=vipdoc_root))
    return measure(lambda: fn(synthetic=synthetic))


def report(results: dict[str, dict[str, Any]]) -> None:
    """控制台表格输出。"""
    print(f"\n{'场景':<10}{'条目数':>10}{'峰值内存':>14}{'每条均摊':>14}{'耗时':>10}")
    print("-" * 60)
    for name, r in results.items():
        per = r["peak_kib"] / r["rows"] if r["rows"] else 0
        print(
            f"{name:<10}{r['rows']:>10,}{_fmt(r['peak_kib']):>14}"
            f"{per:>9.3f} KiB{r['elapsed_s']:>9.2f}s"
        )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="tstdx Q4 内存峰值基准（tracemalloc）")
    ap.add_argument("--scenario", choices=list(SCENARIOS) + ["all"], default="all")
    ap.add_argument("--all", action="store_true", help="跑全部场景（等价 --scenario all）")
    ap.add_argument(
        "--synthetic",
        dest="synthetic",
        action="store_true",
        default=True,
        help="使用合成数据（默认，离线可跑）",
    )
    ap.add_argument("--live", dest="synthetic", action="store_false", help="接真实数据源")
    ap.add_argument("--vipdoc-root", default=None, help="live 模式下本地 vipdoc 根目录")
    ap.add_argument("--json", default=None, help="结果写入 JSON 文件路径")
    args = ap.parse_args(argv)

    names = list(SCENARIOS) if args.scenario == "all" or args.all else [args.scenario]
    results: dict[str, dict[str, Any]] = {}
    for name in names:
        results[name] = run_one(name, synthetic=args.synthetic, vipdoc_root=args.vipdoc_root)

    mode = "synthetic" if args.synthetic else "live"
    print(f"tstdx Q4 内存基准（{mode}，python {sys.version.split()[0]}）")
    report(results)

    if args.json:
        out = {
            "mode": mode,
            "python": sys.version.split()[0],
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            "results": {
                k: {kk: vv for kk, vv in v.items() if kk != "payload"} for k, v in results.items()
            },
        }
        Path(args.json).parent.mkdir(parents=True, exist_ok=True)
        Path(args.json).write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\nJSON 已写入: {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
