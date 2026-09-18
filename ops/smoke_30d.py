#!/usr/bin/env python3
# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""30 天真实环境冒烟（R5）。

每日定时执行（Ops 或本机计划任务）：对 4 个独立数据源（TDX / 新浪 / 腾讯 /
东财）各做一次真实行情拉取，结果追加写入 :file:`ops/smoke_results.jsonl`。

- 任一源失败**不中断**（逐源隔离），但进程退出码非 0，便于上层告警；
- 结果行含 ``ok``（该源是否成功）、``latency_ms``、``price``、``error``；
- 30 天期满后可用 :func:`summarize` 聚合出各源可用率。

用法::

    python -m ops.smoke_30d            # 运行一轮并追加结果
    python -m ops.smoke_30d --summarize  # 聚合已有结果（可用率/延迟）
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_RESULT_FILE = _HERE / "smoke_results.jsonl"

#: 各源统一探针标的：贵州茅台（A 股），要求返回非空价格。
_SYMBOL = "600519"


def _iso_now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _probe_tdx() -> float:
    """TDX 原生协议独立调用：quotes_concurrent 单只（返回 dict）。"""
    from tstdx.client import TdxClient

    with TdxClient(timeout=5.0, max_retries=2) as client:
        quotes = client.quotes_concurrent([_SYMBOL])
        if not quotes or quotes[0] is None:
            raise RuntimeError("TDX 返回空行情")
        price = (
            quotes[0].get("price")
            if isinstance(quotes[0], dict)
            else getattr(quotes[0], "price", None)
        )
        if not price:
            raise RuntimeError(f"TDX 行情缺 price: {quotes[0]!r}")
        return float(price)


def _probe_web(source: str) -> float:
    """HTTP Web 源独立调用（新浪 / 腾讯 / 东财）。"""
    from tstdx.web.session import web_session

    sess = web_session(source)
    try:
        quotes = sess.quotes([_SYMBOL])
    finally:
        sess.close()
    if not quotes:
        raise RuntimeError(f"{source} 返回空行情")
    return float(quotes[0].price)


_SOURCES = {
    "tdx": _probe_tdx,
    "sina": lambda: _probe_web("sina"),
    "tencent": lambda: _probe_web("tencent"),
    "eastmoney": lambda: _probe_web("eastmoney"),
}


def run_one_round() -> int:
    """执行一轮冒烟，写一行聚合结果；返回失败源个数。"""
    started = time.monotonic()
    row: dict[str, object] = {
        "ts": _iso_now(),
        "version": None,
        "symbol": _SYMBOL,
        "sources": {},
        "ok": True,
        "duration_ms": 0.0,
    }
    try:
        import tstdx

        row["version"] = tstdx.__version__
    except Exception:  # pragma: no cover - 版本读取失败不致命
        row["version"] = "unknown"

    failures = 0
    for name, probe in _SOURCES.items():
        t0 = time.monotonic()
        try:
            price = probe()
            src: dict[str, object] = {
                "ok": True,
                "latency_ms": round((time.monotonic() - t0) * 1000, 1),
                "price": price,
            }
        except Exception as exc:  # noqa: BLE001 - 逐源隔离，全部捕获
            failures += 1
            src = {
                "ok": False,
                "latency_ms": round((time.monotonic() - t0) * 1000, 1),
                "error": f"{type(exc).__name__}: {exc}",
            }
        row["sources"][name] = src  # type: ignore[assignment]

    row["ok"] = failures == 0
    row["duration_ms"] = round((time.monotonic() - started) * 1000, 1)
    _RESULT_FILE.parent.mkdir(parents=True, exist_ok=True)
    with _RESULT_FILE.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")

    print(
        f"[smoke] {row['ts']} v{row['version']} ok={row['ok']} "
        f"dur={row['duration_ms']}ms failures={failures}"
    )
    for name, src in row["sources"].items():  # type: ignore[union-attr]
        status = "OK " if src["ok"] else "FAIL"
        extra = src.get("price") or src.get("error", "")
        print(f"  {status} {name:<9} {src['latency_ms']:>7.1f}ms  {extra}")
    return failures


def summarize() -> None:
    """聚合历史结果：每源可用率 + 平均/最大延迟。"""
    if not _RESULT_FILE.exists():
        print("无结果文件，尚未运行。")
        return
    totals: dict[str, dict[str, float]] = {}
    for line in _RESULT_FILE.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        for name, src in row.get("sources", {}).items():
            agg = totals.setdefault(name, {"runs": 0.0, "ok": 0.0, "lat": 0.0, "max": 0.0})
            agg["runs"] += 1
            if src.get("ok"):
                agg["ok"] += 1
                lat = float(src.get("latency_ms", 0))
                agg["lat"] += lat
                agg["max"] = max(agg["max"], lat)
    print(f"{'源':<10} {'可用率':>8} {'平均延迟ms':>10} {'最大延迟ms':>10}")
    for name, agg in totals.items():
        rate = agg["ok"] / agg["runs"] * 100 if agg["runs"] else 0.0
        avg = agg["lat"] / agg["ok"] if agg["ok"] else 0.0
        print(f"{name:<10} {rate:>7.1f}% {avg:>10.1f} {agg['max']:>10.1f}")


def main() -> int:
    parser = argparse.ArgumentParser(description="tstdx 30 天真实环境冒烟")
    parser.add_argument("--summarize", action="store_true", help="聚合历史结果")
    args = parser.parse_args()
    if args.summarize:
        summarize()
        return 0
    return 0 if run_one_round() == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
