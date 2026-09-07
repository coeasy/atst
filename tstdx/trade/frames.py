# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""TDX 交易协议帧编解码（P2-1 · 洁净室推断布局）。

**状态说明（务必阅读）**
------------------------
本模块定义交易协议族的**自洽帧布局**（请求/响应头 + 各命令 body），
用于协议探测研究与模拟器回路。真实券商侧帧布局封装于闭源 ``trade.dll``，
本布局是**推断占位**（``status: draft``，见 PROTOCOL_SPEC/TRADE/），
真机样本定标前不得当作协议事实。

帧布局（Little-Endian，推断）
-----------------------------

**请求帧（8 字节头 + body）**::

    偏移  长度  字段         说明
    0     2     total_len    整个帧长度（含 8 字节头）
    2     2     cmd          命令号（见 :mod:`tstdx.trade.constants`）
    4     2     seq          会话序号（自增，用于匹配响应）
    6     2     flags        标志位（保留，恒 0）

**响应帧（8 字节头 + body）**::

    偏移  长度  字段         说明
    0     2     total_len    整个帧长度
    2     2     cmd          回显命令号
    4     2     seq          回显请求序号
    6     2     status       0=成功，非 0=错误码

字段编码约定：金额/价格以**分**（整数）传输（``PRICE_SCALE=100``）；
字符串为 ``<H 长度> + GBK 字节``；证券代码为 6 字节 ASCII 右补 NUL。
"""

from __future__ import annotations

import struct
from collections.abc import Mapping, Sequence
from typing import Any

from ..errors import ProtocolError
from .constants import (
    QUERY_CATEGORY_CANCELABLE_ORDER,
    QUERY_CATEGORY_CASH,
    QUERY_CATEGORY_DEAL_OF_TODAY,
    QUERY_CATEGORY_ORDER_OF_TODAY,
    QUERY_CATEGORY_SHAREHOLDERS_CODE,
    QUERY_CATEGORY_STOCKS,
)
from .errors import TradeError

__all__ = [
    "TRADE_HEADER_LEN",
    "build_request",
    "parse_request",
    "build_response",
    "parse_response",
    "encode_str",
    "decode_str",
    "encode_bytes",
    "build_login_body",
    "parse_login_body",
    "build_login_response",
    "parse_login_response",
    "build_heartbeat_response",
    "parse_heartbeat_response",
    "build_query_body",
    "parse_query_body",
    "build_query_response",
    "parse_query_response",
    "build_order_body",
    "parse_order_body",
    "build_order_response",
    "parse_order_response",
    "build_cancel_body",
    "parse_cancel_body",
    "build_cancel_response",
    "parse_cancel_response",
]

#: 请求/响应头长度（推断布局）
TRADE_HEADER_LEN = 8
_REQ_HEADER = "<HHHH"
_RESP_HEADER = "<HHHH"


# --------------------------------------------------------------------------- #
# 帧层
# --------------------------------------------------------------------------- #
def build_request(cmd: int, seq: int, body: bytes = b"") -> bytes:
    """组装请求帧：``<H total_len><H cmd><H seq><H flags> + body``。"""
    total = TRADE_HEADER_LEN + len(body)
    return struct.pack(_REQ_HEADER, total, cmd, seq, 0) + body


def parse_request(frame: bytes) -> tuple[int, int, bytes]:
    """拆解请求帧，返回 ``(cmd, seq, body)``；长度不符抛 :class:`ProtocolError`。"""
    if len(frame) < TRADE_HEADER_LEN:
        raise ProtocolError(f"请求帧过短: {len(frame)} < {TRADE_HEADER_LEN}")
    total, cmd, seq, _flags = struct.unpack_from(_REQ_HEADER, frame, 0)
    if total > len(frame):
        raise ProtocolError(f"请求帧长度不符: 头声明 {total}，实收 {len(frame)}")
    return cmd, seq, frame[TRADE_HEADER_LEN:total]


def build_response(cmd: int, seq: int, status: int, body: bytes = b"") -> bytes:
    """组装响应帧：``<H total_len><H cmd><H seq><H status> + body``。"""
    total = TRADE_HEADER_LEN + len(body)
    return struct.pack(_RESP_HEADER, total, cmd, seq, status) + body


def parse_response(frame: bytes) -> tuple[int, int, int, bytes]:
    """拆解响应帧，返回 ``(cmd, seq, status, body)``。"""
    if len(frame) < TRADE_HEADER_LEN:
        raise ProtocolError(f"响应帧过短: {len(frame)} < {TRADE_HEADER_LEN}")
    total, cmd, seq, status = struct.unpack_from(_RESP_HEADER, frame, 0)
    if total > len(frame):
        raise ProtocolError(f"响应帧长度不符: 头声明 {total}，实收 {len(frame)}")
    return cmd, seq, status, frame[TRADE_HEADER_LEN:total]


# --------------------------------------------------------------------------- #
# 字符串 / 字节原语
# --------------------------------------------------------------------------- #
def encode_bytes(data: bytes) -> bytes:
    """长度前缀字节串：``<H len> + data``。"""
    return struct.pack("<H", len(data)) + data


def encode_str(text: str) -> bytes:
    """长度前缀 GBK 字符串：``<H len> + GBK 字节``。"""
    return encode_bytes(text.encode("gbk", errors="replace"))


def decode_str(buf: bytes, offset: int) -> tuple[str, int]:
    """从 ``buf[offset:]`` 解码长度前缀 GBK 字符串，返回 ``(text, 新偏移)``。"""
    (ln,) = struct.unpack_from("<H", buf, offset)
    raw = buf[offset + 2 : offset + 2 + ln]
    return raw.decode("gbk", errors="replace"), offset + 2 + ln


# --------------------------------------------------------------------------- #
# 登录（CMD_LOGIN = 0x0001）
# --------------------------------------------------------------------------- #
def build_login_body(*, yyb_id: int, version: int, account: str, password: bytes) -> bytes:
    """登录请求体：``<H yyb_id><H version><str account><bytes 混淆口令>``。"""
    body = struct.pack("<HH", yyb_id, version)
    body += encode_str(account)
    body += encode_bytes(password)
    return body


def parse_login_body(body: bytes) -> dict[str, Any]:
    """拆解登录请求体。"""
    yyb_id, version = struct.unpack_from("<HH", body, 0)
    account, off = decode_str(body, 4)
    (pwd_len,) = struct.unpack_from("<H", body, off)
    password = body[off + 2 : off + 2 + pwd_len]
    return {"yyb_id": yyb_id, "version": version, "account": account, "password": password}


def build_login_response(
    *,
    client_id: int,
    account: str,
    branch: str,
    name: str,
    yyb_id: int,
    login_flag: int,
    send_order_flag: int,
) -> bytes:
    """登录响应体：会话 ID + 账号/营业部/姓名 + 二次密码与下单开关。"""
    body = struct.pack("<I", client_id)
    body += encode_str(account)
    body += encode_str(branch)
    body += encode_str(name)
    body += struct.pack("<HBB", yyb_id, login_flag, send_order_flag)
    return body


def parse_login_response(body: bytes) -> dict[str, Any]:
    """拆解登录响应体。"""
    (client_id,) = struct.unpack_from("<I", body, 0)
    account, off = decode_str(body, 4)
    branch, off = decode_str(body, off)
    name, off = decode_str(body, off)
    yyb_id, login_flag, send_order_flag = struct.unpack_from("<HBB", body, off)
    return {
        "client_id": client_id,
        "account": account,
        "branch": branch,
        "name": name,
        "yyb_id": yyb_id,
        "login_flag": login_flag,
        "send_order_flag": send_order_flag,
    }


# --------------------------------------------------------------------------- #
# 心跳（CMD_HEARTBEAT = 0x0002）/ 登出（CMD_LOGOUT = 0x0003）
# --------------------------------------------------------------------------- #
def build_heartbeat_response(ok: bool) -> bytes:
    """心跳响应体：``<B 1>`` 表示连接正常。"""
    return struct.pack("<B", 1 if ok else 0)


def parse_heartbeat_response(body: bytes) -> dict[str, bool]:
    """拆解心跳响应体。"""
    return {"ok": bool(body) and body[0] != 0}


# --------------------------------------------------------------------------- #
# 查询（CMD_QUERY = 0x0100）
# --------------------------------------------------------------------------- #
#: 记录字段类型：``I``=uint32 / ``i``=int32 / ``H``=uint16 / ``B``=uint8 /
#: ``s``=长度前缀 GBK 串 / ``6s``=6 字节 ASCII 代码（右补 NUL）
_QUERY_RECORD_FIELDS: dict[int, tuple[tuple[str, str], ...]] = {
    QUERY_CATEGORY_CASH: (
        ("available_cash", "I"),  # 可用资金（分）
        ("frozen_cash", "I"),  # 冻结资金（分）
        ("market_value", "I"),  # 持仓市值（分）
        ("total_asset", "I"),  # 总资产（分）
        ("debt", "i"),  # 负债（分）
    ),
    QUERY_CATEGORY_STOCKS: (
        ("code", "6s"),
        ("name", "s"),
        ("qty", "I"),
        ("available_qty", "I"),
        ("cost_price", "I"),
        ("last_price", "I"),
        ("profit", "i"),
    ),
    QUERY_CATEGORY_ORDER_OF_TODAY: (
        ("order_id", "H"),
        ("code", "6s"),
        ("name", "s"),
        ("side", "B"),
        ("price", "I"),
        ("qty", "I"),
        ("filled_qty", "I"),
        ("status", "B"),
        ("cancelable", "B"),
    ),
    QUERY_CATEGORY_DEAL_OF_TODAY: (
        ("deal_id", "H"),
        ("order_id", "H"),
        ("code", "6s"),
        ("name", "s"),
        ("side", "B"),
        ("price", "I"),
        ("qty", "I"),
        ("amount", "I"),
        ("time", "s"),
    ),
    QUERY_CATEGORY_CANCELABLE_ORDER: (
        ("order_id", "H"),
        ("code", "6s"),
        ("name", "s"),
        ("side", "B"),
        ("price", "I"),
        ("qty", "I"),
        ("filled_qty", "I"),
    ),
    QUERY_CATEGORY_SHAREHOLDERS_CODE: (
        ("code", "s"),
        ("name", "s"),
        ("market", "B"),
    ),
}


def query_record_fields(category: int) -> tuple[tuple[str, str], ...]:
    """返回某查询类别的记录字段表；未知类别抛 :class:`TradeError`。"""
    fields = _QUERY_RECORD_FIELDS.get(category)
    if fields is None:
        raise TradeError(f"不支持的查询类别 {category}")
    return fields


def _encode_record(fields: Sequence[tuple[str, str]], values: Mapping[str, Any]) -> bytes:
    buf = bytearray()
    for name, fmt in fields:
        value = values[name]
        if fmt == "s":
            buf += encode_str(str(value))
        elif fmt == "6s":
            buf += str(value).encode("ascii", errors="replace")[:6].ljust(6, b"\x00")
        else:
            buf += struct.pack("<" + fmt, value)
    return bytes(buf)


def _decode_record(
    fields: Sequence[tuple[str, str]], buf: bytes, offset: int
) -> tuple[dict[str, Any], int]:
    record: dict[str, Any] = {}
    off = offset
    for name, fmt in fields:
        if fmt == "s":
            record[name], off = decode_str(buf, off)
        elif fmt == "6s":
            raw = buf[off : off + 6]
            record[name] = raw.split(b"\x00", 1)[0].decode("ascii", errors="replace")
            off += 6
        else:
            size = struct.calcsize("<" + fmt)
            record[name] = struct.unpack_from("<" + fmt, buf, off)[0]
            off += size
    return record, off


def build_query_body(*, category: int) -> bytes:
    """查询请求体：``<H category>``。"""
    return struct.pack("<H", category)


def parse_query_body(body: bytes) -> dict[str, int]:
    """拆解查询请求体。"""
    (category,) = struct.unpack_from("<H", body, 0)
    return {"category": category}


def build_query_response(category: int, records: Sequence[Mapping[str, Any]]) -> bytes:
    """查询响应体：``<H category><H count> + 记录``。"""
    fields = query_record_fields(category)
    body = struct.pack("<HH", category, len(records))
    for record in records:
        body += _encode_record(fields, record)
    return body


def parse_query_response(body: bytes) -> dict[str, Any]:
    """拆解查询响应体，返回 ``{"category": ..., "records": [...]}``。"""
    category, count = struct.unpack_from("<HH", body, 0)
    fields = query_record_fields(category)
    off = 4
    records: list[dict[str, Any]] = []
    for _ in range(count):
        record, off = _decode_record(fields, body, off)
        records.append(record)
    return {"category": category, "records": records}


# --------------------------------------------------------------------------- #
# 委托（CMD_SEND_ORDER = 0x1000）
# --------------------------------------------------------------------------- #
def build_order_body(*, code: str, side: int, price_type: int, price: int, quantity: int) -> bytes:
    """委托请求体：``<6s code><B side><B price_type><I price><I qty>``。

    价格以**分**为单位（``PRICE_SCALE=100``）；市价单 ``price`` 可传 0。
    """
    code_raw = code.encode("ascii", errors="replace")[:6].ljust(6, b"\x00")
    return struct.pack("<6sBBII", code_raw, side, price_type, price, quantity)


def parse_order_body(body: bytes) -> dict[str, Any]:
    """拆解委托请求体。"""
    code_raw, side, price_type, price, quantity = struct.unpack_from("<6sBBII", body, 0)
    code = code_raw.split(b"\x00", 1)[0].decode("ascii", errors="replace")
    return {
        "code": code,
        "side": side,
        "price_type": price_type,
        "price": price,
        "quantity": quantity,
    }


def build_order_response(*, order_id: int, status: int, error_code: int, error_msg: str) -> bytes:
    """委托响应体：``<H order_id><B status><H error_code><str error_msg>``。"""
    body = struct.pack("<HBB", order_id, status, error_code)
    body += encode_str(error_msg)
    return body


def parse_order_response(body: bytes) -> dict[str, Any]:
    """拆解委托响应体。"""
    order_id, status, error_code = struct.unpack_from("<HBB", body, 0)
    error_msg, _off = decode_str(body, 4)
    return {
        "order_id": order_id,
        "status": status,
        "error_code": error_code,
        "error_msg": error_msg,
    }


# --------------------------------------------------------------------------- #
# 撤单（CMD_CANCEL_ORDER = 0x1001）
# --------------------------------------------------------------------------- #
def build_cancel_body(*, order_id: int) -> bytes:
    """撤单请求体：``<H order_id>``。"""
    return struct.pack("<H", order_id)


def parse_cancel_body(body: bytes) -> dict[str, int]:
    """拆解撤单请求体。"""
    (order_id,) = struct.unpack_from("<H", body, 0)
    return {"order_id": order_id}


def build_cancel_response(*, status: int, error_code: int, error_msg: str) -> bytes:
    """撤单响应体：``<B status><H error_code><str error_msg>``。"""
    body = struct.pack("<BH", status, error_code)
    body += encode_str(error_msg)
    return body


def parse_cancel_response(body: bytes) -> dict[str, Any]:
    """拆解撤单响应体。"""
    status, error_code = struct.unpack_from("<BH", body, 0)
    error_msg, _off = decode_str(body, 3)
    return {"status": status, "error_code": error_code, "error_msg": error_msg}
