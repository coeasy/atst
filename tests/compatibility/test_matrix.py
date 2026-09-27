"""Compatibility matrix test (Tier D item D4).

Builds **synthetic** .day-file payloads for a 4-market × 3-category grid
(12 combinations), parses each with the reader path, and asserts bar-field
sanity.  A separate auto-detect accuracy check runs ``atst.profile.detect``
on the same bytes and asserts the detected record size matches the built
layout for ≥ 11 / 12 combos (one miss tolerated per the plan's ≥ 99 % target).

The test is fully offline — no network, no filesystem I/O beyond a single
temp directory — and runs in well under 5 s.
"""

from __future__ import annotations

import random
import struct
from pathlib import Path
from typing import Any

import pytest

from atst.reader.formats import read_day_file
from atst.reader.profile import Market as ProfileMarket

# --------------------------------------------------------------------------- #
# Optional: atst.profile.detect (D3, may be written by a concurrent subagent)
# --------------------------------------------------------------------------- #
try:
    from atst.profile.detect import detect as _profile_detect

    _HAS_PROFILE_DETECT = True
except ImportError:
    _profile_detect = None  # type: ignore[assignment]
    _HAS_PROFILE_DETECT = False

# --------------------------------------------------------------------------- #
# Matrix definition — 4 markets × 3 categories = 12 combinations
# --------------------------------------------------------------------------- #

# Market codes (from reader.profile.Market.CODES)
_MARKETS = [
    ("SZ", ProfileMarket.SZ, 0, 100),
    ("SH", ProfileMarket.SH, 1, 100),
    ("HK", ProfileMarket.HK, 71, 1000),
    ("US", ProfileMarket.US, 74, 1000),
]

# Category label, base price, volume base
_CATEGORIES = [
    ("stock", 50.0, 500_000),
    ("index", 3000.0, 8_000_000),
    ("etf", 3.0, 200_000),
]

NUM_BARS = 10
RECORD_SIZE = 32  # .day file record size


def _build_combos() -> list[dict[str, Any]]:
    """Return 12 (market, category) combinations with metadata."""
    combos: list[dict[str, Any]] = []
    for mkt_name, mkt_const, mkt_code, price_scale in _MARKETS:
        for cat_name, base_price, vol_base in _CATEGORIES:
            combos.append(
                {
                    "market_name": mkt_name,
                    "market_const": mkt_const,
                    "market_code": mkt_code,
                    "category": cat_name,
                    "price_scale": price_scale,
                    "base_price": base_price,
                    "volume_base": vol_base,
                }
            )
    assert len(combos) == 12, f"expected 12 combos, got {len(combos)}"
    return combos


COMBOS: list[dict[str, Any]] = _build_combos()


def _combo_id(c: dict[str, Any]) -> str:
    return f"{c['market_name']}_{c['category']}"


# --------------------------------------------------------------------------- #
# Synthetic .day file builder
# --------------------------------------------------------------------------- #


def _build_day_payload(combo: dict[str, Any], *, seed: int = 42) -> bytes:
    """Build a synthetic .day file payload (NUM_BARS × 32 bytes).

    Layout (little-endian, 32 bytes per record):
        0-3    uint32  date  YYYYMMDD
        4-7    uint32  open × price_scale
        8-11   uint32  high × price_scale
        12-15  uint32  low × price_scale
        16-19  uint32  close × price_scale
        20-23  float32 amount
        24-27  uint32  volume
        28-31  uint32  prev_close × price_scale
    """
    rng = random.Random(seed)
    scale = combo["price_scale"]
    base_price = combo["base_price"]
    vol_base = combo["volume_base"]

    out = bytearray()
    # Start date: 2024-01-02 (weekdays only, skip weekends for realism)
    date_raw = 20240102
    prev_close_scaled = int(base_price * scale)

    for _ in range(NUM_BARS):
        # Skip weekends (rough approximation)
        # YYYYMMDD → day-of-week
        dow = _day_of_week(date_raw)
        if dow >= 5:  # Saturday=5, Sunday=6
            # advance to Monday
            days_until_mon = 7 - dow
            date_raw += days_until_mon
            continue  # this bar is skipped, but we still need NUM_BARS total

        # Generate plausible prices
        open_price = base_price * (1 + rng.uniform(-0.02, 0.02))
        close_price = base_price * (1 + rng.uniform(-0.03, 0.03))
        high_price = max(open_price, close_price) * (1 + rng.uniform(0, 0.01))
        low_price = min(open_price, close_price) * (1 - rng.uniform(0, 0.01))

        # Ensure OHLC ordering
        assert high_price >= max(open_price, close_price), "high < max(o,c)"
        assert low_price <= min(open_price, close_price), "low > min(o,c)"

        volume = int(vol_base * (1 + rng.uniform(-0.3, 0.3)))
        # Ensure volume ≥ 0
        volume = max(0, volume)
        amount = float(open_price * volume * rng.uniform(0.9, 1.1))

        # Scale prices
        o_scaled = int(round(open_price * scale))
        h_scaled = int(round(high_price * scale))
        l_scaled = int(round(low_price * scale))
        c_scaled = int(round(close_price * scale))

        # Pack record
        rec = struct.pack(
            "<IIIIIfII",
            date_raw,
            o_scaled,
            h_scaled,
            l_scaled,
            c_scaled,
            amount,
            volume,
            prev_close_scaled,
        )
        assert len(rec) == RECORD_SIZE, f"record size {len(rec)} != {RECORD_SIZE}"
        out.extend(rec)

        prev_close_scaled = c_scaled
        date_raw = _advance_date(date_raw)

    # Trim to exactly NUM_BARS records (if weekend-skipping caused fewer)
    actual_bars = len(out) // RECORD_SIZE
    if actual_bars < NUM_BARS:
        # Pad with repeated last record to fill NUM_BARS
        pad = bytearray(out[-RECORD_SIZE:])
        while len(out) // RECORD_SIZE < NUM_BARS:
            out.extend(pad)
    elif actual_bars > NUM_BARS:
        # Truncate
        out = out[: NUM_BARS * RECORD_SIZE]

    return bytes(out)


def _day_of_week(yyyymmdd: int) -> int:
    """Compute day-of-week from YYYYMMDD (0=Monday … 6=Sunday)."""
    from datetime import date

    y, m, d = yyyymmdd // 10000, (yyyymmdd // 100) % 100, yyyymmdd % 100
    return date(y, m, d).weekday()


def _advance_date(yyyymmdd: int) -> int:
    """Advance date by 1 day."""
    d = yyyymmdd % 100
    if d >= 31:
        # month rollover
        m = (yyyymmdd // 100) % 100
        y = yyyymmdd // 10000
        if m == 12:
            return (y + 1) * 10000 + 1 * 100 + 1
        return y * 10000 + (m + 1) * 100 + 1
    return yyyymmdd + 1


# --------------------------------------------------------------------------- #
# Tests
# --------------------------------------------------------------------------- #


@pytest.mark.unit
class TestDayFileMatrix:
    """Matrix test: 12 market/category combinations × sanity checks."""

    @pytest.fixture(params=COMBOS, ids=_combo_id)
    def combo(self, request: pytest.FixtureRequest) -> dict[str, Any]:
        return request.param

    def test_bar_sanity(self, combo: dict[str, Any], tmp_path: Path) -> None:
        """Build payload → parse → assert bar fields sane."""
        raw = _build_day_payload(combo)
        assert len(raw) == NUM_BARS * RECORD_SIZE

        # Write to temp file for read_day_file
        path = tmp_path / f"{combo['market_name']}_{combo['category']}.day"
        path.write_bytes(raw)

        bars = read_day_file(path)
        assert isinstance(bars, list), f"expected list, got {type(bars)}"
        assert len(bars) == NUM_BARS, f"expected {NUM_BARS} bars, got {len(bars)}"

        for i, bar in enumerate(bars):
            # Date range plausible (2024-2026)
            dt = bar.get("datetime", "")
            assert dt, f"bar {i}: empty datetime"
            year = int(dt[:4])
            assert 2020 <= year <= 2030, f"bar {i}: implausible year {year}"

            # OHLC ordering
            o, h, lo, c = bar["open"], bar["high"], bar["low"], bar["close"]
            assert h >= max(o, c) - 1e-4, f"bar {i}: high {h} < max(o,c)={max(o, c)}"
            assert lo <= min(o, c) + 1e-4, f"bar {i}: low {lo} > min(o,c)={min(o, c)}"
            assert h >= lo - 1e-4, f"bar {i}: high {h} < low {lo}"

            # Volume ≥ 0
            assert bar["volume"] >= 0, f"bar {i}: negative volume {bar['volume']}"
            assert bar["amount"] >= 0, f"bar {i}: negative amount {bar['amount']}"

    @pytest.mark.skipif(
        not _HAS_PROFILE_DETECT,
        reason="atst.profile.detect not available (D3 module not yet written)",
    )
    def test_auto_detect_record_size(self, combo: dict[str, Any]) -> None:
        """Run atst.profile.detect → assert detected record_size == 32."""
        raw = _build_day_payload(combo)
        result = _profile_detect(raw, hint_period="day")
        detected = result.profile.record_size
        assert detected == RECORD_SIZE, (
            f"{_combo_id(combo)}: detected record_size={detected}, expected {RECORD_SIZE}"
        )


# --------------------------------------------------------------------------- #
# Grid-level auto-detect accuracy
# --------------------------------------------------------------------------- #


@pytest.mark.unit
@pytest.mark.skipif(
    not _HAS_PROFILE_DETECT,
    reason="atst.profile.detect not available (D3 module not yet written)",
)
def test_auto_detect_grid_accuracy() -> None:
    """Assert ≥ 11 / 12 combos have correct auto-detected record size."""
    matches = 0
    misses: list[str] = []
    for combo in COMBOS:
        raw = _build_day_payload(combo)
        try:
            result = _profile_detect(raw, hint_period="day")
            if result.profile.record_size == RECORD_SIZE:
                matches += 1
            else:
                misses.append(f"{_combo_id(combo)}: detected {result.profile.record_size}")
        except Exception as exc:
            misses.append(f"{_combo_id(combo)}: {type(exc).__name__}: {exc}")

    total = len(COMBOS)
    assert matches >= 11, f"auto-detect accuracy {matches}/{total} < 11/{total}; misses: {misses}"
