# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""P6 解析/请求热路径耗时基准（合成数据，离线可跑）。

场景
----
1. ``parse_quotes``   新浪文本行情解析（约 1000 只 A 股原始 ``hq_str_*`` 文本 → Quote）
2. ``parse_kline``    二进制日线解析（约 8 万条 .day 记录 → Bar，DayBarReader）
3. ``serialize``      门面输出序列化（约 5400 只 Quote → ``to_dicts``，HTTP/MCP/CLI 共享热路径）

数据来源
--------
* ``--synthetic``（默认）：合成载荷，确定性、离线可跑，供 CI 与回归对比；
* ``--live``：接真实数据源（新浪全市场 / 本地 vipdoc_root），仅贴近生产时使用。

输出
----
* 控制台表格（条目数 / 总耗时 / 吞吐 rows/s）；
* ``--json PATH`` 可选把结果写 JSON（供 CI 归档与 diff）。

用法示例
--------
.. code-block:: console

    python benches/bench_time.py --scenario parse_quotes
    python benches/bench_time.py --all --json benches/results/time_baseline.json
    python benches/bench_time.py --live --all --vipdoc-root C:/tdx/vipdoc
"""

from __future__ import annotations

import argparse
import gc
import json
import struct
import sys
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

# 保证以仓库根为 cwd 运行时可直接 import tstdx（无需安装）
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


# --------------------------------------------------------------------------- #
# 合成载荷
# --------------------------------------------------------------------------- #
def _synthetic_sina_payload(count: int) -> str:
    """生成 ``count`` 条新浪 ``hq_str_*`` 原始文本（模拟 fetch_all 单页拼接）。

    字段布局对齐 SinaSource.parse 的读取位（32 字段）：
    0 名称 / 1 今开 / 2 昨收 / 3 现价 / 4 最高 / 5 最低 /
    6-15 买一~买五（量,价 交替；f[8]/f[9] 被读取为成交量/成交额）/
    16-25 卖一~卖五（量,价 交替；ask 从 index 20 起）/
    30 日期 / 31 时间。
    """
    lines: list[str] = []
    for i in range(count):
        code = f"sh{600000 + i:06d}"
        price = 10.0 + (i % 1000) / 100.0
        bid: list[str] = []
        for k in range(5):  # 买一~买五（量,价 交替）
            bid += [str((k + 1) * 1000 + i), f"{price - 0.01 * (k + 1):.2f}"]
        ask: list[str] = []
        for k in range(5):  # 卖一~卖五（量,价 交替）
            ask += [str((k + 6) * 1000 + i), f"{price + 0.01 * (k + 1):.2f}"]
        fields = [
            f"股票{i:04d}",  # 0 名称
            f"{price - 0.05:.2f}",  # 1 今开
            f"{price - 0.10:.2f}",  # 2 昨收
            f"{price:.2f}",  # 3 现价
            f"{price + 0.20:.2f}",  # 4 最高
            f"{price - 0.30:.2f}",  # 5 最低
            *bid,  # 6-15
            *ask,  # 16-25
            "",
            "",
            "",
            "",  # 26-29 备用
            "2026-09-03",  # 30 日期
            "15:00:00",  # 31 时间
        ]
        lines.append(f'var hq_str_{code}="{",".join(fields)}";')
    return "\n".join(lines)


def _synthetic_day_bytes(rows: int, *, seed: int = 1) -> bytes:
    """生成 ``rows`` 条 .day 日线原始字节（u32 价格 ×100，与 bench_memory 同构）。"""
    buf = bytearray()
    for i in range(rows):
        date = 20260101 + i
        base = 1000 + ((i + seed) % 9000)
        o, h, lo, c = base, base + 20, base - 10, base + 5
        amount = float(123456.78 + i)
        volume = 100000 + i * 100
        prev_close = base - 5
        buf += struct.pack("<IIIIIfII", date, o, h, lo, c, amount, volume, prev_close)
    return bytes(buf)


# --------------------------------------------------------------------------- #
# 三类热路径
# --------------------------------------------------------------------------- #
def scenario_parse_quotes(synthetic: bool = True) -> dict[str, Any]:
    """新浪文本行情解析：原始 ``hq_str_*`` 文本 → list[Quote]。"""
    if synthetic:
        from tstdx.web.sina.adapters import SinaSource

        text = _synthetic_sina_payload(1000)
        symbols = [f"sh{600000 + i:06d}" for i in range(1000)]
        src = SinaSource()
        try:
            payload = src.parse(text, symbols)
        finally:
            src.close()
    else:
        from tstdx.web import create_source

        src = create_source("sina")
        try:
            payload = src.fetch_all(node="hs_a", page_size=100)
        finally:
            src.close()
    return {"rows": len(payload), "payload": payload}


def scenario_parse_kline(synthetic: bool = True, *, root: str | None = None) -> dict[str, Any]:
    """二进制日线解析：.day 原始字节 → list[Bar]（DayBarReader 热路径）。"""
    from tstdx.reader.formats import DayBarReader

    rows: list[Any] = []
    if not synthetic and root:
        p = Path(root)
        files = list(p.rglob("*.day")) if p.exists() else []
    else:
        tmp = Path(tempfile.mkdtemp(prefix="tstdx_bench_time_kline_"))
        (tmp / "lday").mkdir(parents=True, exist_ok=True)
        (tmp / "lday" / "sh600519.day").write_bytes(_synthetic_day_bytes(80_000))
        files = [tmp / "lday" / "sh600519.day"]

    reader = DayBarReader()
    for f in files:
        rows.extend(reader.read(f, output="model"))
    return {"rows": len(rows), "payload": rows}


def scenario_serialize(synthetic: bool = True) -> dict[str, Any]:
    """门面输出序列化：Quote → to_dicts（HTTP/MCP/CLI 共享热路径）。"""
    from tstdx.domain.models import Quote, to_dicts

    quotes: list[Quote] = []
    for i in range(5400):
        price = 10.0 + (i % 1000) / 100.0
        quotes.append(
            Quote(
                code=f"{600000 + i:06d}",
                price=price,
                last_close=price - 0.1,
                open=price - 0.05,
                high=price + 0.2,
                low=price - 0.3,
                volume=100000 + i * 100,
                amount=price * 1000000,
            )
        )
    payload = to_dicts(quotes)
    return {"rows": len(payload), "payload": payload}


SCENARIOS: dict[str, Callable[..., dict[str, Any]]] = {
    "parse_quotes": scenario_parse_quotes,
    "parse_kline": scenario_parse_kline,
    "serialize": scenario_serialize,
}


# --------------------------------------------------------------------------- #
# 测量与输出
# --------------------------------------------------------------------------- #
def measure(fn: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    """计时运行场景，返回耗时与吞吐（rows/s）。"""
    gc.collect()
    t0 = time.perf_counter()
    result = fn()
    elapsed = time.perf_counter() - t0
    result["elapsed_s"] = round(elapsed, 4)
    result["rows_per_s"] = round(result["rows"] / elapsed, 1) if elapsed else 0
    result["payload"] = None  # 释放引用，便于下一场景
    return result


def run_one(name: str, *, synthetic: bool, vipdoc_root: str | None) -> dict[str, Any]:
    fn = SCENARIOS[name]
    if name == "parse_kline":
        return measure(lambda: fn(synthetic=synthetic, root=vipdoc_root))
    return measure(lambda: fn(synthetic=synthetic))


def report(results: dict[str, dict[str, Any]]) -> None:
    """控制台表格输出。"""
    print(f"\n{'场景':<16}{'条目数':>12}{'耗时':>10}{'吞吐(rows/s)':>14}")
    print("-" * 54)
    for name, r in results.items():
        print(f"{name:<16}{r['rows']:>12,}{r['elapsed_s']:>9.2f}s{r['rows_per_s']:>14,.0f}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="tstdx P6 解析/请求热路径耗时基准")
    ap.add_argument("--scenario", choices=list(SCENARIOS) + ["all"], default="all")
    ap.add_argument("--all", action="store_true", help="跑全部场景（等价 --scenario all）")
    ap.add_argument(
        "--synthetic",
        dest="synthetic",
        action="store_true",
        default=True,
        help="使用合成载荷（默认，离线可跑）",
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
    print(f"tstdx P6 耗时基准（{mode}，python {sys.version.split()[0]}）")
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
