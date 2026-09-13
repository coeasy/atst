#!/usr/bin/env python3
# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""Run the deterministic synthetic benchmark smoke suite and validate outputs."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "benches" / "results"


def _run_benchmark(script: str, output: Path) -> dict[str, Any]:
    output.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            sys.executable,
            str(ROOT / "benches" / script),
            "--all",
            "--json",
            str(output),
        ],
        cwd=ROOT,
        check=True,
    )
    return json.loads(output.read_text(encoding="utf-8"))


def _validate(data: dict[str, Any], *, metric: str, label: str) -> None:
    if data.get("mode") != "synthetic":
        raise SystemExit(f"{label}: benchmark must run in synthetic mode")
    results = data.get("results")
    if not isinstance(results, dict) or not results:
        raise SystemExit(f"{label}: benchmark results are empty")

    for name, result in results.items():
        if not isinstance(result, dict):
            raise SystemExit(f"{label}/{name}: invalid result object")
        if float(result.get("rows", 0)) <= 0:
            raise SystemExit(f"{label}/{name}: rows must be positive")
        if float(result.get(metric, 0)) <= 0:
            raise SystemExit(f"{label}/{name}: {metric} must be positive")


def main() -> int:
    memory_path = RESULTS / "ci_smoke.json"
    time_path = RESULTS / "ci_time_smoke.json"

    memory = _run_benchmark("bench_memory.py", memory_path)
    _validate(memory, metric="peak_kib", label="memory")

    timing = _run_benchmark("bench_time.py", time_path)
    _validate(timing, metric="rows_per_s", label="time")

    print(
        "benchmark smoke OK:",
        ", ".join(sorted(memory["results"])),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
