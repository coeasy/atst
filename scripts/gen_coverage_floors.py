# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""Generate the per-file coverage floor table from ``coverage.xml``.

The floor gate lives in ``tests/compatibility/test_coverage_floor_contract.py``.
Its keys MUST be the ``<class filename=...>`` values that ``coverage xml`` emits
with ``source = ["atst"]`` — i.e. ``cli/parser.py``, not ``atst/cli/cli/parser.py``.
A key that never matches a manifest entry silently turns the gate into a no-op,
so this script is the only supported writer of that table.

Usage::

    .venv/Scripts/python -m coverage xml -o coverage.xml
    .venv/Scripts/python scripts/gen_coverage_floors.py            # print table
    .venv/Scripts/python scripts/gen_coverage_floors.py --write    # rewrite test file

Floor rule: ``floor(measured * 100) / 100 - 0.01``, clamped at ``0.0`` — the
measured rate rounded down to two decimals, then one hundredth below it. This
matches the gate's original "measured minus 0.01" intent while staying stable
against floating-point noise.
"""

from __future__ import annotations

import argparse
import math
import re
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COVERAGE_XML = ROOT / "coverage.xml"
TARGET = ROOT / "tests" / "compatibility" / "test_coverage_floor_contract.py"
START = "FLOORS: dict[str, float] = {"


def floors_from_coverage(path: Path) -> dict[str, float]:
    """Return ``{filename: floor}`` for every class in the coverage manifest."""
    tree = ET.parse(path)
    floors: dict[str, float] = {}
    for cls in tree.iter("class"):
        filename = cls.get("filename") or ""
        if not filename:
            continue
        measured = float(cls.get("line-rate") or 0.0)
        floor = math.floor(measured * 100) / 100 - 0.01
        floors[filename] = max(0.0, round(floor, 2))
    return floors


def render(floors: dict[str, float]) -> str:
    lines = [START]
    for filename in sorted(floors):
        lines.append(f"    {filename!r}: {floors[filename]:.2f},")
    lines.append("}")
    return "\n".join(lines)


def write_into(target: Path, block: str) -> None:
    source = target.read_text(encoding="utf-8")
    match = re.search(r"FLOORS: dict\[str, float\] = \{.*?\n\}", source, re.S)
    if match is None:
        raise SystemExit(f"{target}: FLOORS table not found")
    target.write_text(source[: match.start()] + block + source[match.end() :], encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--coverage", type=Path, default=COVERAGE_XML)
    parser.add_argument("--target", type=Path, default=TARGET)
    parser.add_argument("--write", action="store_true", help="rewrite the floor table in place")
    args = parser.parse_args(argv)

    if not args.coverage.exists():
        raise SystemExit(f"{args.coverage}: not found; run `python -m coverage xml` first")
    floors = floors_from_coverage(args.coverage)
    block = render(floors)
    if args.write:
        write_into(args.target, block)
        print(f"wrote {len(floors)} floors into {args.target}")
    else:
        print(block)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
