# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""帧编解码回路测试（P2-1）。

帧布局为洁净室推断（draft）——本测试验证**编解码自洽性**（组帧→解帧
往返一致、长度校验、记录表驱动编码），不代表真实券商协议兼容性。
"""

from __future__ import annotations

import pytest

from tstdx.errors import ProtocolError
from tstdx.trade.constants import (
    QUERY_CATEGORY_CANCELABLE_ORDER,
    QUERY_CATEGORY_CASH,
    QUERY_CATEGORY_DEAL_OF_TODAY,
    QUERY_CATEGORY_ORDER_OF_TODAY,
    QUERY_CATEGORY_SHAREHOLDERS_CODE,
    QUERY_CATEGORY_STOCKS,
)
from tstdx.trade.errors import TradeError
from tstdx.trade.frames import (
    build_cancel_body,
    build_cancel_response,
    build_heartbeat_response,
    build_login_body,
    build_login_response,
    build_order_body,
    build_order_response,
    build_query_body,
    build_query_response,
    build_request,
    build_response,
    encode_str,
    parse_cancel_body,
    parse_cancel_response,
    parse_heartbeat_response,
    parse_login_body,
    parse_login_response,
    parse_order_body,
    parse_order_response,
    parse_query_body,
    parse_query_response,
    parse_request,
    parse_response,
)

pytestmark = pytest.mark.unit


# --------------------------------------------------------------------------- #
# 帧层
# --------------------------------------------------------------------------- #
class TestFrame:
    def test_request_roundtrip(self) -> None:
        frame = build_request(0x0100, 7, b"\x00\x01")
        cmd, seq, body = parse_request(frame)
        assert (cmd, seq, body) == (0x0100, 7, b"\x00\x01")

    def test_response_roundtrip(self) -> None:
        frame = build_response(0x0100, 7, 0, b"\x00\x01")
        cmd, seq, status, body = parse_response(frame)
        assert (cmd, seq, status, body) == (0x0100, 7, 0, b"\x00\x01")

    def test_request_truncated_raises(self) -> None:
        with pytest.raises(ProtocolError):
            parse_request(b"\x00\x01")

    def test_response_length_mismatch_raises(self) -> None:
        # 头声明 100 字节，实际不足
        frame = build_response(0x0001, 1, 0, b"\x01")[:2] + b"\x00\x00\x00\x00\x00\x00"
        with pytest.raises(ProtocolError):
            parse_response(frame)


# --------------------------------------------------------------------------- #
# 字符串原语
# --------------------------------------------------------------------------- #
class TestStrPrimitive:
    def test_encode_str_roundtrip(self) -> None:
        buf = encode_str("贵州茅台")
        text, off = _decode_str_local(buf)
        assert text == "贵州茅台"
        assert off == len(buf)

    def test_encode_str_gbk(self) -> None:
        buf = encode_str("贵州")
        assert buf[:2] == b"\x04\x00"  # GBK 4 字节


# --------------------------------------------------------------------------- #
# 登录
# --------------------------------------------------------------------------- #
class TestLoginBody:
    def test_login_body_roundtrip(self) -> None:
        body = build_login_body(
            yyb_id=32, version=0x0123, account="100001", password=b"\xaa\xbb\xcc"
        )
        parsed = parse_login_body(body)
        assert parsed["yyb_id"] == 32
        assert parsed["version"] == 0x0123
        assert parsed["account"] == "100001"
        assert parsed["password"] == b"\xaa\xbb\xcc"

    def test_login_response_roundtrip(self) -> None:
        body = build_login_response(
            client_id=9,
            account="100001",
            branch="1000",
            name="模拟券商",
            yyb_id=32,
            login_flag=0,
            send_order_flag=1,
        )
        parsed = parse_login_response(body)
        assert parsed["client_id"] == 9
        assert parsed["account"] == "100001"
        assert parsed["branch"] == "1000"
        assert parsed["name"] == "模拟券商"
        assert parsed["login_flag"] == 0
        assert parsed["send_order_flag"] == 1


# --------------------------------------------------------------------------- #
# 心跳
# --------------------------------------------------------------------------- #
class TestHeartbeat:
    def test_heartbeat_response(self) -> None:
        assert parse_heartbeat_response(build_heartbeat_response(True)) == {"ok": True}
        assert parse_heartbeat_response(build_heartbeat_response(False)) == {"ok": False}


# --------------------------------------------------------------------------- #
# 查询
# --------------------------------------------------------------------------- #
class TestQueryBody:
    def test_query_body_roundtrip(self) -> None:
        assert parse_query_body(build_query_body(category=QUERY_CATEGORY_CASH)) == {
            "category": QUERY_CATEGORY_CASH
        }

    def test_unknown_category_raises(self) -> None:
        with pytest.raises(TradeError):
            build_query_response(9999, [])

    @pytest.mark.parametrize(
        ("category", "record"),
        [
            (
                QUERY_CATEGORY_CASH,
                {
                    "available_cash": 1000000,
                    "frozen_cash": 0,
                    "market_value": 0,
                    "total_asset": 1000000,
                    "debt": 0,
                },
            ),
            (
                QUERY_CATEGORY_STOCKS,
                {
                    "code": "600519",
                    "name": "贵州茅台",
                    "qty": 100,
                    "available_qty": 100,
                    "cost_price": 1000,
                    "last_price": 1200,
                    "profit": 20000,
                },
            ),
            (
                QUERY_CATEGORY_ORDER_OF_TODAY,
                {
                    "order_id": 1,
                    "code": "600519",
                    "name": "贵州茅台",
                    "side": 0,
                    "price": 1000,
                    "qty": 100,
                    "filled_qty": 0,
                    "status": 0,
                    "cancelable": 1,
                },
            ),
            (
                QUERY_CATEGORY_DEAL_OF_TODAY,
                {
                    "deal_id": 1,
                    "order_id": 1,
                    "code": "600519",
                    "name": "贵州茅台",
                    "side": 1,
                    "price": 1100,
                    "qty": 100,
                    "amount": 110000,
                    "time": "10:30:00",
                },
            ),
            (
                QUERY_CATEGORY_CANCELABLE_ORDER,
                {
                    "order_id": 2,
                    "code": "000001",
                    "name": "平安银行",
                    "side": 0,
                    "price": 500,
                    "qty": 200,
                    "filled_qty": 0,
                },
            ),
            (
                QUERY_CATEGORY_SHAREHOLDERS_CODE,
                {"code": "100001", "name": "模拟券商", "market": 0},
            ),
        ],
    )
    def test_query_record_roundtrip(self, category: int, record: dict) -> None:
        body = build_query_response(category, [record])
        parsed = parse_query_response(body)
        assert parsed["category"] == category
        assert parsed["records"] == [record]


# --------------------------------------------------------------------------- #
# 委托 / 撤单
# --------------------------------------------------------------------------- #
class TestOrderBody:
    def test_order_body_roundtrip(self) -> None:
        body = build_order_body(code="600519", side=0, price_type=0, price=1000, quantity=100)
        parsed = parse_order_body(body)
        assert parsed == {
            "code": "600519",
            "side": 0,
            "price_type": 0,
            "price": 1000,
            "quantity": 100,
        }

    def test_order_response_roundtrip(self) -> None:
        body = build_order_response(order_id=3, status=0, error_code=0, error_msg="")
        parsed = parse_order_response(body)
        assert parsed == {"order_id": 3, "status": 0, "error_code": 0, "error_msg": ""}

    def test_cancel_body_roundtrip(self) -> None:
        assert parse_cancel_body(build_cancel_body(order_id=3)) == {"order_id": 3}

    def test_cancel_response_roundtrip(self) -> None:
        body = build_cancel_response(status=1, error_code=0, error_msg="")
        parsed = parse_cancel_response(body)
        assert parsed == {"status": 1, "error_code": 0, "error_msg": ""}


# 局部辅助：复用 frames 私有解码逻辑以独立验证 encode_str
def _decode_str_local(buf: bytes) -> tuple[str, int]:
    import struct

    (ln,) = struct.unpack_from("<H", buf, 0)
    return buf[2 : 2 + ln].decode("gbk"), 2 + ln
