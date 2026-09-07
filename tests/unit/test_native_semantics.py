"""native.py Python 回退语义测试（审计 §3-6）。

覆盖：
* ``read_day_file`` 的 ``output`` 三态透传（原生路径只产出 dict）；
* ``parse_kline_payload`` 回退链 ``lot_factor`` → ``volume_unit`` 映射契约
  （0=auto / 1=share / ≥100=lot，其余 NotImplementedError）；
* 回退路径 lot_factor=100 与 lot_factor=1 的成交量 100 倍一致性。
"""

from __future__ import annotations

import struct

import pytest

import tstdx.native as native_mod
from tstdx.native import parse_kline_payload, read_day_file

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _force_python_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    """无论本机是否装了编译扩展，测试一律走 Python 回退（确定性）。"""
    monkeypatch.setattr(native_mod, "native", None)


def _make_day_bytes(n: int = 3) -> bytes:
    rec = struct.pack("<IIIIIfII", 20260831, 1000, 1100, 900, 1050, 8_000_000.0, 800_000, 1000)
    return rec * n


def _make_kline_payload(n: int = 2, *, category: int = 4) -> bytes:
    """0x052D 合成载荷（与 tests/unit/test_native.py 的构造同构）。"""
    from tstdx.codec.primitive import encode_leb128

    out = bytearray(struct.pack("<H", n))
    for i in range(n):
        out += struct.pack("<I", 20_260_102 + i)  # yyyymmdd
        out += encode_leb128(10_000)  # open diff
        out += encode_leb128(500)  # close diff
        out += encode_leb128(1_400)  # high diff
        out += encode_leb128(-1_000)  # low diff
        out += struct.pack("<I", 0x4270_0000)  # tdx_float 60.0（量）
        out += struct.pack("<I", 0x4000_0000)  # tdx_float 2.0（额）
        out += struct.pack("<HH", 1234, 56)
    return bytes(out)


class TestReadDayFileOutputPassthrough:
    """output 参数透传 Python 参考（不再被原生路径无视）。"""

    def test_model_output_returns_bar_objects(self, tmp_path) -> None:
        from tstdx.domain.models import Bar

        p = tmp_path / "sh600000.day"
        p.write_bytes(_make_day_bytes())
        rows = read_day_file(p, output="model")
        assert isinstance(rows, list) and rows
        assert all(isinstance(r, Bar) for r in rows)

    def test_tuple_output_returns_tuples(self, tmp_path) -> None:
        p = tmp_path / "sh600000.day"
        p.write_bytes(_make_day_bytes())
        rows = read_day_file(p, output="tuple")
        assert isinstance(rows, list) and rows
        assert all(isinstance(r, tuple) for r in rows)

    def test_dict_output_matches_reference(self, tmp_path) -> None:
        from tstdx.reader.formats import read_day_file as ref

        p = tmp_path / "sh600000.day"
        p.write_bytes(_make_day_bytes())
        assert read_day_file(p) == ref(p)


class TestLotFactorMapping:
    """回退链 lot_factor 契约：0=auto / 1=share / ≥100=lot / 其余 raise。"""

    @pytest.fixture()
    def captured_units(self, monkeypatch: pytest.MonkeyPatch) -> list[str]:
        from tstdx.protocol.parsers.std7709 import SecurityBarsParser

        units: list[str] = []
        original = SecurityBarsParser.parse_payload

        def _spy(self, reader, **ctx):  # type: ignore[no-untyped-def]
            units.append(ctx.get("volume_unit", "<missing>"))
            return original(self, reader, **ctx)

        monkeypatch.setattr(SecurityBarsParser, "parse_payload", _spy)
        return units

    @pytest.mark.parametrize(
        ("lot_factor", "expected"),
        [(0, "auto"), (1, "share"), (100, "lot"), (200, "lot")],
    )
    def test_mapping(self, captured_units: list[str], lot_factor: int, expected: str) -> None:
        parse_kline_payload(_make_kline_payload(), category=4, lot_factor=lot_factor)
        assert captured_units[-1] == expected

    @pytest.mark.parametrize("bad", [-1, 2, 7, 99])
    def test_unsupported_lot_factor_raises(self, bad: int) -> None:
        with pytest.raises(NotImplementedError, match="lot_factor"):
            parse_kline_payload(_make_kline_payload(), category=4, lot_factor=bad)

    def test_lot_volume_is_100x_share(self) -> None:
        payload = _make_kline_payload()
        share_rows = parse_kline_payload(payload, category=4, lot_factor=1)
        lot_rows = parse_kline_payload(payload, category=4, lot_factor=100)
        assert len(share_rows) == len(lot_rows)
        for s_row, lot_row in zip(share_rows, lot_rows, strict=True):
            if s_row["volume"]:
                assert lot_row["volume"] == s_row["volume"] * 100


class TestFallbackParseMatchesParser:
    """回退路径与直接调用 SecurityBarsParser.parse_payload(auto) 一致。"""

    def test_auto_matches_parser_reference(self) -> None:
        from tstdx.codec.primitive import BinaryReader
        from tstdx.protocol.parsers.std7709 import SecurityBarsParser

        payload = _make_kline_payload()
        via_native_loader = parse_kline_payload(payload, category=4)  # lot_factor 默认 0=auto
        reference = SecurityBarsParser().parse_payload(
            BinaryReader(payload), category=4, price_scale=1000.0
        )
        assert [dict(r) for r in via_native_loader] == [dict(r) for r in reference]
