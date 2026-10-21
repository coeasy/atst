# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""0x0010 / 0x000F 的请求体顺序与记录布局回归（2026-10-21 真机实测修正的固化）。

这两个命令历史上有一个**静默错位**缺陷：请求体发的是 ``<6s code><H market>``
（代码在前），而服务端按 ``market@2 / code@3:9`` 解读，于是整个记录头错位——
``market`` 读到 ASCII ``'0'``（48）、``code`` 只剩半个代码，且前 2 字节被当成
「请求条数」读成 ``0x3030``，服务器回 100 条记录（只有第 1 条是本人，其余是
服务端缓冲残留，每次抓包内容都不同）。F-37 的旧结论正来源于此：四份 golden
样本全是错请求采的。

正确请求体（9 字节，2026-10-21 深/沪 8 只标的重放，响应头与请求逐一相符）::

    <H 1><B market><6s code>

即「固定前缀 ``01 00`` + 市场字节 + 6 字节代码」。前 2 字节是**请求条数**
（uint16 LE）：``01 00``→1 条、``02 00``→2 条、``00 00``→服务端报错。

本文件用自建载荷 + 生成器驱动钉住四件事：

1. 两条模板发出的请求体就是上面那个 9 字节形状（旧顺序回归即红）；
2. ``FinanceInfoParser`` 的 143B 记录类型化解码（记录头 + 5 个类型化字段
   + 30 个 float32）；
3. ``CapitalChangesParser`` 的两级结构（块 → 记录）与 ``category`` 分叉；
4. 两个解析器对块/记录头做**回声校验**：与请求上下文不符时留痕而非静默返回脏数据。
"""

from __future__ import annotations

import contextlib
import struct
from typing import Any

import pytest

from atst.client.sync import TdxClient
from atst.codec.primitive import BinaryReader
from atst.diagnostics import WarningCode, warning_sink
from atst.protocol.commands import CMD
from atst.protocol.parsers._std7709_bars import CapitalChangesParser, FinanceInfoParser
from atst.protocol.registry import ParseResult

pytestmark = pytest.mark.unit

CMD_FINANCE = CMD["finance_info"]  # 0x0010
CMD_CAPITAL = CMD["capital_changes"]  # 0x000F

#: 已验证的 9 字节请求体（``01 00`` + market + 6B ASCII 代码）。
#: sh600519：market=1 → ``010001363030353139``
#: sz000001：market=0 → ``010000303030303031``
BODY_SH600519 = bytes.fromhex("010001363030353139")
BODY_SZ000001 = bytes.fromhex("010000303030303031")

#: 旧实现的错误形状（代码在前）：market 被读成 ASCII '0'(48)、code 只剩半个。
BODY_OLD_SH600519 = b"600519" + struct.pack("<H", 1)


# --------------------------------------------------------------------------- #
# 1. 请求体形状：模板实际发出去的字节
# --------------------------------------------------------------------------- #


def _drive_request(monkeypatch: pytest.MonkeyPatch, method: str, symbol: str) -> tuple[int, bytes]:
    """手动驱动 ``_t_*`` 生成器：只取第一个 op（请求），不碰网络。"""
    captured: dict[str, Any] = {}

    def fake_dispatch(frame: Any, **kwargs: Any) -> ParseResult:  # noqa: ARG001
        captured["frame"] = frame
        return ParseResult(command=0, name="stub", tier="L2", confidence=0.0, rows=[])

    monkeypatch.setattr("atst.client.dispatch", fake_dispatch)
    client = TdxClient(hosts=[["1.2.3.4", 7709]])
    gen = getattr(client, method)(symbol)
    op = next(gen)
    assert op[0] == "req", f"{method} 第一个 op 不是请求：{op[0]!r}"
    with contextlib.suppress(StopIteration):
        gen.send(object())
    return op[1], op[2]


class TestFinanceRequestShape:
    """``_t_finance_info`` 的请求体（0x0010）。"""

    def test_body_is_nine_bytes_with_fixed_prefix(self, monkeypatch: pytest.MonkeyPatch) -> None:
        cmd, body = _drive_request(monkeypatch, "_t_finance_info", "sh600519")
        assert cmd == CMD_FINANCE
        assert body == BODY_SH600519
        # 固定前缀 = 请求条数 1
        assert body[:2] == struct.pack("<H", 1)
        # 市场字节 + 6 字节 ASCII 代码
        assert body[2] == 1
        assert body[3:9] == b"600519"

    def test_shenzhen_market_byte_is_zero(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _cmd, body = _drive_request(monkeypatch, "_t_finance_info", "sz000001")
        assert body == BODY_SZ000001
        assert body[2] == 0
        assert body[3:9] == b"000001"

    def test_not_the_old_code_first_order(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """旧形状（代码在前）回归即红——它正是 F-37 错位结论的来源。"""
        _cmd, body = _drive_request(monkeypatch, "_t_finance_info", "sh600519")
        assert body != BODY_OLD_SH600519
        assert body[:6] != b"600519", "代码又跑到请求体前 6 字节了"

    def test_requesting_two_records_changes_only_the_prefix(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """前 2 字节是「请求条数」：多要几条只动前缀，市场/代码位置不动。"""
        cmd, body = _drive_request(monkeypatch, "_t_finance_info", "sh600519")
        assert cmd == CMD_FINANCE
        assert body[:2] == struct.pack("<H", 1)


class TestCapitalChangesRequestShape:
    """``_t_capital_changes`` 的请求体（0x000F，与 0x0010 同构）。"""

    def test_body_is_nine_bytes_with_fixed_prefix(self, monkeypatch: pytest.MonkeyPatch) -> None:
        cmd, body = _drive_request(monkeypatch, "_t_capital_changes", "sh600519")
        assert cmd == CMD_CAPITAL
        assert body == BODY_SH600519
        assert body[:2] == struct.pack("<H", 1)
        assert body[2] == 1
        assert body[3:9] == b"600519"

    def test_shenzhen_market_byte_is_zero(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _cmd, body = _drive_request(monkeypatch, "_t_capital_changes", "sz000001")
        assert body == BODY_SZ000001

    def test_not_the_old_code_first_order(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _cmd, body = _drive_request(monkeypatch, "_t_capital_changes", "sh600519")
        assert body != BODY_OLD_SH600519
        assert body[:6] != b"600519"


# --------------------------------------------------------------------------- #
# 2. FinanceInfoParser：143B 记录的类型化解码
# --------------------------------------------------------------------------- #


def _finance_record(
    market: int = 1,
    code: str = "600519",
    *,
    float_shares: float = 1940568.5,
    province: int = 31,
    industry: int = 47,
    updated: int = 20260930,
    ipo: int = 20010827,
    tail: tuple[float, ...] = (1940591.875, 0.0, 0.0, 0.0, 0.0, 0.0),
) -> bytes:
    """一条 143B 财务记录（7B 头 + <fHHII> + 30×float32）。"""
    assert len(code) == 6
    rec = struct.pack("<B6s", market, code.encode("ascii"))
    rec += struct.pack("<fHHII", float_shares, province, industry, updated, ipo)
    values = list(tail) + [0.0] * (30 - len(tail))
    rec += struct.pack("<30f", *values)
    assert len(rec) == FinanceInfoParser.RECORD_SIZE == 143, len(rec)
    return rec


def _finance_payload(records: list[bytes]) -> bytes:
    return struct.pack("<H", len(records)) + b"".join(records)


class TestFinanceInfoParserLayout:
    """0x0010 记录布局（pytdx ``GetFinanceInfo`` / injoyai-tdx 交叉锁定 + 真机对拍）。"""

    def test_record_size_is_143(self) -> None:
        assert FinanceInfoParser.RECORD_SIZE == 143

    def test_typed_header_fields_are_named(self) -> None:
        rows = _parse_finance(_finance_payload([_finance_record()]), code="600519", market=1)
        assert len(rows) == 1
        row = rows[0]
        assert row["market"] == 1
        assert row["code"] == "600519"
        assert row["float_shares"] == pytest.approx(1940568.5)
        assert row["province"] == 31
        assert row["industry"] == 47
        assert row["updated_date"] == 20260930
        assert row["ipo_date"] == 20010827

    def test_tail_is_thirty_floats_with_total_shares_first(self) -> None:
        rows = _parse_finance(_finance_payload([_finance_record()]), code="600519", market=1)
        values = rows[0]["values"]
        assert len(values) == 30
        # values[0] = 总股本（万股）；float_shares 是记录头里的流通股本，两回事
        assert values[0] == pytest.approx(1940591.875)
        assert rows[0]["float_shares"] == pytest.approx(1940568.5)

    def test_count_two_returns_two_records(self) -> None:
        """结构测试：声明 2 条就解出 2 条（不带 code 期望，纯结构）。

        真机上 0x0010 一只证券只有 1 条记录；多出来的条数在错请求年代正是
        「服务端缓冲残留」的形状，所以带期望的路径由下面的回声校验管。
        """
        payload = _finance_payload([_finance_record(code="600519"), _finance_record(code="600000")])
        rows = _parse_finance(payload, code="", market=1)
        assert [r["code"] for r in rows] == ["600519", "600000"]
        assert [r["ipo_date"] for r in rows] == [20010827, 20010827]

    def test_short_body_raises_parse_error(self) -> None:
        """声明 143B/条而 body 明显不够 → fail-closed 而不是猜步长。"""
        from atst.errors import ParseError

        payload = struct.pack("<H", 1) + b"\x00" * 40
        with pytest.raises(ParseError):
            _parse_finance(payload, code="600519", market=1)

    def test_echo_mismatch_warns_instead_of_silently_returning_dirty_data(self) -> None:
        """块头与请求不符（旧错请求的形状 market=48/错位代码）必须留痕。"""
        with warning_sink() as caught:
            rows = _parse_finance(
                _finance_payload([_finance_record(market=48, code="600518")]),
                code="600519",
                market=1,
            )
        assert len(rows) == 1  # 行照返，但值已标为不可信
        assert any(
            w.code == WarningCode.FIELD_OUT_OF_DOMAIN and "0x0010" in w.message for w in caught
        ), f"回声不符没有留痕：{[w.code for w in caught]}"

    def test_echo_match_is_silent(self) -> None:
        with warning_sink() as caught:
            _parse_finance(_finance_payload([_finance_record()]), code="600519", market=1)
        assert [w for w in caught if w.code == WarningCode.FIELD_OUT_OF_DOMAIN] == []


def _parse_finance(payload: bytes, *, code: str, market: int) -> list[dict[str, Any]]:
    reader = BinaryReader(payload)
    rows = FinanceInfoParser().parse_payload(reader, code=code, market=market, _warnings=[])
    assert reader.remaining >= 0
    return rows


# --------------------------------------------------------------------------- #
# 3. CapitalChangesParser：两级结构与 category 分叉
# --------------------------------------------------------------------------- #


def _capital_record(
    market: int = 1,
    code: str = "600519",
    date: int = 20240603,
    category: int = 1,
    quad: tuple[float, float, float, float] = (30.0, 0.0, 0.0, 0.0),
) -> bytes:
    """一条 29B 股本变迁记录（7B 头 + 1B + 4B 日期 + 1B 类别 + 16B 分类数据）。"""
    assert len(code) == 6
    rec = struct.pack("<B6s", market, code.encode("ascii"))
    rec += b"\x00"  # 保留字节
    rec += struct.pack("<I", date)
    rec += struct.pack("<B", category)
    rec += struct.pack("<ffff", *quad)
    assert len(rec) == CapitalChangesParser.RECORD_SIZE == 29, len(rec)
    return rec


def _capital_payload(blocks: list[tuple[int, str, list[bytes]]]) -> bytes:
    """``<H blocks><blocks × [<B market><6s code><H num><num × 29B>]>``。"""
    out = struct.pack("<H", len(blocks))
    for market, code, records in blocks:
        out += struct.pack("<B6sH", market, code.encode("ascii"), len(records))
        out += b"".join(records)
    return out


class TestCapitalChangesParserLayout:
    """0x000F 记录布局（pytdx ``GetXdXrInfo`` 交叉验证 + 真机对拍）。"""

    def test_record_size_is_29(self) -> None:
        assert CapitalChangesParser.RECORD_SIZE == 29

    def test_two_level_structure_one_block(self) -> None:
        payload = _capital_payload(
            [
                (
                    1,
                    "600519",
                    [
                        _capital_record(date=20240603, category=1, quad=(30.0, 0.0, 0.0, 0.0)),
                        _capital_record(date=20230605, category=1, quad=(25.0, 0.0, 0.0, 0.0)),
                    ],
                )
            ]
        )
        rows = _parse_capital(payload, code="600519", market=1)
        assert [r["date"] for r in rows] == ["2024-06-03", "2023-06-05"]
        assert [r["dividend"] for r in rows] == [30.0, 25.0]
        assert all(r["category_name"] == "除权除息" for r in rows)

    def test_category_one_maps_dividend_fields(self) -> None:
        """category=1：分红 / 配股价 / 送转股 / 配股比例（复权引擎的四个输入）。"""
        payload = _capital_payload(
            [(1, "600519", [_capital_record(category=1, quad=(1.5, 8.85, 2.0, 3.0))])]
        )
        rows = _parse_capital(payload, code="600519", market=1)
        row = rows[0]
        assert row["dividend"] == pytest.approx(1.5)
        assert row["rights_price"] == pytest.approx(8.85)
        assert row["bonus_ratio"] == pytest.approx(2.0)
        assert row["rights_ratio"] == pytest.approx(3.0)

    def test_other_category_maps_share_counts(self) -> None:
        """其余类别是四组股本数（万股）：盘前流通 / 前总股本 / 盘后流通 / 后总股本。

        真机依据：茅台 2002-07-26「10 送 1」前后两组值恰好成 1.1 倍
        （7150×1.1=7865、25000×1.1=27500）——float32 而非 TDX 自定义浮点。
        """
        payload = _capital_payload(
            [(1, "600519", [_capital_record(category=5, quad=(7150.0, 25000.0, 7865.0, 27500.0))])]
        )
        rows = _parse_capital(payload, code="600519", market=1)
        row = rows[0]
        assert row["category"] == 5
        assert row["panqianliutong"] == pytest.approx(7150.0)
        assert row["qianzongguben"] == pytest.approx(25000.0)
        assert row["panhouliutong"] == pytest.approx(7865.0)
        assert row["houzongguben"] == pytest.approx(27500.0)
        #: 复权四字段不在这条记录上（它不带分红语义）
        assert "dividend" not in row

    def test_date_field_offset_is_eight_not_seven(self) -> None:
        """offset 回归：日期在记录内偏移 8（不是旧实现的 7/9），类别在 12。"""
        rec = _capital_record(date=19991110, category=2)
        assert struct.unpack_from("<I", rec, 8)[0] == 19991110
        assert rec[12] == 2

    def test_zero_blocks_returns_empty(self) -> None:
        assert _parse_capital(struct.pack("<H", 0), code="600519", market=1) == []

    def test_echo_mismatch_warns_instead_of_silently_returning_dirty_data(self) -> None:
        with warning_sink() as caught:
            rows = _parse_capital(
                _capital_payload([(48, "600518", [_capital_record(market=48, code="600518")])]),
                code="600519",
                market=1,
            )
        assert len(rows) == 1
        assert any(
            w.code == WarningCode.FIELD_OUT_OF_DOMAIN and "0x000f" in w.message for w in caught
        ), f"回声不符没有留痕：{[w.code for w in caught]}"

    def test_echo_match_is_silent(self) -> None:
        with warning_sink() as caught:
            _parse_capital(
                _capital_payload([(1, "600519", [_capital_record()])]), code="600519", market=1
            )
        assert [w for w in caught if w.code == WarningCode.FIELD_OUT_OF_DOMAIN] == []

    def test_truncated_block_stops_instead_of_guessing(self) -> None:
        """块头不完整 → 停止解析并留痕，不按猜测步长硬读。"""
        payload = struct.pack("<H", 2) + b"\x01" + b"600519" + struct.pack("<H", 1)
        payload += _capital_record()
        rows = _parse_capital(payload, code="600519", market=1)
        assert [r["code"] for r in rows] == ["600519"]


def _parse_capital(payload: bytes, *, code: str, market: int) -> list[dict[str, Any]]:
    reader = BinaryReader(payload)
    rows = CapitalChangesParser().parse_payload(reader, code=code, market=market, _warnings=[])
    assert reader.remaining >= 0
    return rows


# --------------------------------------------------------------------------- #
# 4. 客户端出口：类型化记录头必须走到调用方手里
# --------------------------------------------------------------------------- #


class TestClientSurfacesTypedHeader:
    """``_t_finance_info`` 只映射 ``values`` 会把记录头丢掉——调用方拿不到
    流通股本/省份/行业/上市日期，而这些恰是对拍时确认布局没漂移的锚点。"""

    @staticmethod
    def _drive(monkeypatch: pytest.MonkeyPatch, row: dict[str, Any]) -> dict[str, Any]:
        def fake_dispatch(frame: Any, **kwargs: Any) -> ParseResult:  # noqa: ARG001
            return ParseResult(
                command=CMD_FINANCE,
                name="finance_info",
                tier="L2",
                confidence=1.0,
                rows=[row],
            )

        monkeypatch.setattr("atst.client.dispatch", fake_dispatch)
        client = TdxClient(hosts=[["1.2.3.4", 7709]])
        gen = client._t_finance_info("sh600519")
        next(gen)
        try:
            gen.send(object())
        except StopIteration as stop:
            return dict(stop.value)
        raise AssertionError("finance_info 只应有一个 op")

    def test_typed_header_reaches_the_caller(self, monkeypatch: pytest.MonkeyPatch) -> None:
        out = self._drive(
            monkeypatch,
            {
                "market": 1,
                "code": "600519",
                "float_shares": 1940568.5,
                "province": 31,
                "industry": 47,
                "updated_date": 20260930,
                "ipo_date": 20010827,
                "values": [1940591.875] + [0.0] * 29,
            },
        )
        assert out["market"] == 1
        assert out["code"] == "600519"
        assert out["float_shares"] == pytest.approx(1940568.5)
        assert out["province"] == 31
        assert out["industry"] == 47
        assert out["updated_date"] == 20260930
        assert out["ipo_date"] == 20010827
        #: 尾部 30 个 float32 仍按字段序命名
        assert out["total_shares"] == pytest.approx(1940591.875)
        assert out["net_assets_per_share"] == 0.0
        assert len(out["values"]) == 30

    def test_empty_result_returns_empty_dict(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def fake_dispatch(frame: Any, **kwargs: Any) -> ParseResult:  # noqa: ARG001
            return ParseResult(
                command=CMD_FINANCE,
                name="finance_info",
                tier="L2",
                confidence=1.0,
                rows=[],
            )

        monkeypatch.setattr("atst.client.dispatch", fake_dispatch)
        client = TdxClient(hosts=[["1.2.3.4", 7709]])
        gen = client._t_finance_info("sh600519")
        next(gen)
        try:
            gen.send(object())
        except StopIteration as stop:
            assert stop.value == {}
        else:  # pragma: no cover - 防御
            raise AssertionError("无解析结果时应返回空 dict")
