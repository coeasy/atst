# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""二进制读写原语、TDX 变长数值编码、字符集探测。

本模块是纯函数式基础设施，不含任何协议业务语义。
所有多字节数值默认 **小端序**。

关于 TDX 数值编码（已 Golden 实测锁定）
--------------------------------------
TDX 协议族中存在 **两套** 数值编码，用错会得到数量级完全错误的结果。
本模块两套都实现，由 :class:`~tstdx.reader.profile.DataProfile` 选择。

**A. 价格：LEB128 风格有符号变长整数**（7709 标准族 K 线 / 行情）

    位布局（逐字节，小端续行）::

        bit7 (0x80)   续行标志：为 1 表示后面还有字节
        bit6 (0x40)   仅首字节有效：符号位（1 = 负）
        其余位        数据位：首字节 6 位，后续每字节 7 位

    解码得到的是 **差分增量**（见下），需与基准值相加后再除以 ``scale``。

    ✅ 已实测：600000 日线，5 条记录 92 字节精确耗尽，均价 amount/volume
    与 OHLC 区间吻合（见 ``tests/golden/7709/0x052d``）。

**B. 成交量 / 成交额：4 字节自定义浮点**（不可用于价格字段）

    ``uint32`` 小端，按字节拆成 4 段::

        byte3  logpoint  指数基数
        byte2  hleax     高精度
        byte1  lheax     中精度
        byte0  lleax     低精度

    详见 :func:`decode_tdx_float`。

**C. 6-bit 变长整数**（``decode_varint``，续行标志 0x40）

    用于扩展市场（7727）等路径的定长分档数值。7709 标准族**不使用**它编价格。

**差分编码（K 线）**
    日线及以上周期的价格是差分存储的，必须按顺序累积还原::

        open_abs  = open_diff + base          # base 初值 0，其后 = 上一条的 close_abs
        close_abs = open_abs + close_diff
        high_abs  = open_abs + high_diff
        low_abs   = open_abs + low_diff
        price     = abs / 1000.0             # 7709 标准 K 线缩放为 1000

.. warning::
   价格缩放倍数因协议族而异：7709 标准 K 线为 **1000**，行情快照为 **100**。
   该常量记在 ``PROTOCOL_SPEC/7709/`` 中，由 golden 回归测试守护。
"""

from __future__ import annotations

import builtins
import struct
import zlib
from collections.abc import Iterable
from typing import Any

from ..errors import ParseError

__all__ = [
    "BinaryReader",
    "BinaryWriter",
    "count_guard",
    # 6-bit 变长整数（扩展市场路径）
    "decode_varint",
    "encode_varint",
    # LEB128 风格有符号变长整数（7709 标准族价格）
    "decode_leb128",
    "encode_leb128",
    # TDX 4 字节自定义浮点（成交量 / 成交额）
    "decode_tdx_float",
    "decode_tdx_float_at",
    "decode_price",
    "decode_volume",
    "decode_amount",
    "PRICE_SCALE_BARS",
    "PRICE_SCALE_QUOTES",
    "get_datetime_from_lc",
    "decode_gbk",
    "detect_encoding",
    "zlib_decompress",
    "zlib_compress",
]

#: 7709 标准族 K 线价格缩放（Golden 实测确认）
PRICE_SCALE_BARS: int = 1000
#: 7709 标准族行情快照价格缩放
PRICE_SCALE_QUOTES: int = 100


# --------------------------------------------------------------------------- #
# 读写原语
# --------------------------------------------------------------------------- #
class BinaryReader:
    """带边界检查的顺序二进制读取器。

    所有 ``get_*`` 方法在越界时抛 :class:`~tstdx.errors.ParseError`
    （而不是struct.error），便于统一错误处理。
    """

    __slots__ = ("buf", "pos", "endian")

    def __init__(self, buf: bytes | bytearray, pos: int = 0, endian: str = "<") -> None:
        self.buf = memoryview(bytes(buf))
        self.pos = pos
        self.endian = endian

    # -- 基础 ------------------------------------------------------------- #
    @property
    def remaining(self) -> int:
        return len(self.buf) - self.pos

    def _need(self, n: int) -> int:
        start = self.pos
        if start + n > len(self.buf):
            raise ParseError(
                f"读取越界: 需要 {n} 字节，剩余 {self.remaining} 字节",
                context={"pos": start, "need": n, "have": self.remaining},
            )
        self.pos = start + n
        return start

    def unpack(self, fmt: str, size: int) -> tuple:
        start = self._need(size)
        return struct.unpack_from(self.endian + fmt, self.buf, start)

    def skip(self, n: int) -> None:
        self._need(n)

    def seek(self, pos: int) -> None:
        if pos < 0 or pos > len(self.buf):
            raise ParseError(f"seek 越界: {pos}", context={"pos": pos})
        self.pos = pos

    def bytes(self, n: int) -> bytes:
        start = self._need(n)
        return bytes(self.buf[start : start + n])

    def rest(self) -> builtins.bytes:
        return bytes(self.buf[self.pos :])

    # -- 定长标量 ---------------------------------------------------------- #
    def uint8(self) -> int:
        return self.unpack("B", 1)[0]

    def int8(self) -> int:
        return self.unpack("b", 1)[0]

    def uint16(self) -> int:
        return self.unpack("H", 2)[0]

    def int16(self) -> int:
        return self.unpack("h", 2)[0]

    def uint32(self) -> int:
        return self.unpack("I", 4)[0]

    def int32(self) -> int:
        return self.unpack("i", 4)[0]

    def uint64(self) -> int:
        return self.unpack("Q", 8)[0]

    def int64(self) -> int:
        return self.unpack("q", 8)[0]

    def float32(self) -> float:
        return self.unpack("f", 4)[0]

    def float64(self) -> float:
        return self.unpack("d", 8)[0]

    # -- TDX 私有数值编码 --------------------------------------------------- #
    def varint(self) -> int:
        """6-bit 变长整数（扩展市场路径）。"""
        value, self.pos = decode_varint(self.buf, self.pos)
        return value

    def leb128(self) -> int:
        """LEB128 风格有符号变长整数（7709 标准族价格差分）。"""
        value, self.pos = decode_leb128(self.buf, self.pos)
        return value

    def tdx_float(self) -> float:
        """TDX 4 字节自定义浮点（成交量 / 成交额）。"""
        value, self.pos = decode_tdx_float_at(self.buf, self.pos)
        return value

    def price(self, scale: int = PRICE_SCALE_BARS) -> float:
        value, self.pos = decode_leb128(self.buf, self.pos)
        return value / scale if scale else float(value)

    # -- 字符串 ------------------------------------------------------------ #
    def string(self, n: int, encoding: str = "gbk") -> str:
        """定长字符串，去除尾部 NUL 与空白。"""
        return decode_gbk(self.bytes(n), encoding)

    def count_guard(self, count: int, min_record_bytes: int) -> int:
        """按剩余字节数钳制 count 驱动循环的记录数上限（T2/T3 慢解析防御）。

        畸形响应常以 ``count=0xFFFF`` 声明远超剩余字节可容纳的记录数；
        本方法把 count 收敛到 ``remaining // min_record_bytes``，使逐条循环
        的迭代次数与真实数据量同阶。越界语义与其它读方法一致：钳制后的
        循环若仍读到越界位置，由 reader 抛 :class:`~tstdx.errors.ParseError`。
        """
        return count_guard(count, min_record_bytes, self.remaining)

    def cstring(self, encoding: str = "gbk") -> str:
        """NUL 结尾字符串。"""
        start = self.pos
        raw = bytes(self.buf[start:])
        end = raw.find(b"\x00")
        if end < 0:
            raise ParseError("未找到 NUL 终止符", context={"pos": start})
        self.pos = start + end + 1
        return decode_gbk(raw[:end], encoding)


class BinaryWriter:
    """顺序二进制写入器，与 :class:`BinaryReader` 对称。"""

    __slots__ = ("parts", "endian")

    def __init__(self, endian: str = "<") -> None:
        self.parts: list[bytes] = []
        self.endian = endian

    def pack(self, fmt: str, *values: Any) -> BinaryWriter:
        self.parts.append(struct.pack(self.endian + fmt, *values))
        return self

    def raw(self, data: bytes) -> BinaryWriter:
        self.parts.append(bytes(data))
        return self

    def uint8(self, v: int) -> BinaryWriter:
        return self.pack("B", v & 0xFF)

    def uint16(self, v: int) -> BinaryWriter:
        return self.pack("H", v & 0xFFFF)

    def uint32(self, v: int) -> BinaryWriter:
        return self.pack("I", v & 0xFFFFFFFF)

    def int32(self, v: int) -> BinaryWriter:
        return self.pack("i", v)

    def float32(self, v: float) -> BinaryWriter:
        return self.pack("f", v)

    def string(self, s: str, length: int, encoding: str = "gbk") -> BinaryWriter:
        raw = s.encode(encoding, errors="replace")[:length]
        return self.raw(raw.ljust(length, b"\x00"))

    def to_bytes(self) -> bytes:
        return b"".join(self.parts)


# --------------------------------------------------------------------------- #
# count 钳制（T2/T3 慢解析防御）
# --------------------------------------------------------------------------- #
def count_guard(n: int, min_rec: int, remaining: int) -> int:
    """钳制 count 驱动循环的记录数上限（T2/T3 慢解析防御）。

    Parameters
    ----------
    n:
        响应声明的记录数（uint16/uint32 读出）。
    min_rec:
        单条记录的最小字节数（与循环里的 ``remaining < rec_size`` 守卫一致）。
    remaining:
        剩余可读字节数（声明数消费完后最多能容纳多少完整记录）。

    Returns
    -------
    钳制后的记录数：``min(n, remaining // min_rec)``。
    ``n <= 0`` 或 ``min_rec <= 0`` 时原样返回（无意义守卫不干预）。

    .. note::
       这是**上限收敛**而非错误：钳制只把迭代次数压到与真实数据量同阶，
       循环内的 ``remaining`` 守卫与读越界 :class:`~tstdx.errors.ParseError`
       语义保持不变。调用方应把「钳制发生」记入 warnings 保持可观测。
    """
    if n <= 0 or min_rec <= 0:
        return n if n > 0 else 0
    max_fit = max(int(remaining), 0) // min_rec
    return n if n <= max_fit else max_fit


# --------------------------------------------------------------------------- #
# TDX 变长编码
# --------------------------------------------------------------------------- #
def encode_varint(value: int) -> bytes:
    """6-bit 变长编码（低 6 位数据 + 0x40 续行标志，小端顺序）。"""
    if value < 0:
        raise ValueError("encode_varint 仅支持非负整数")
    out = bytearray()
    while True:
        chunk = value & 0x3F
        value >>= 6
        if value:
            out.append(chunk | 0x40)
        else:
            out.append(chunk)
            return bytes(out)


def decode_varint(buf: bytes | bytearray | memoryview, pos: int = 0) -> tuple[int, int]:
    """解码 6-bit 变长整数。

    Returns
    -------
    (value, next_pos)
    """
    view = bytes(buf)
    value = 0
    shift = 0
    i = pos
    while True:
        if i >= len(view):
            raise ParseError("varint 解码越界", context={"pos": pos, "offset": i, "len": len(view)})
        b = view[i]
        value |= (b & 0x3F) << shift
        i += 1
        if not (b & 0x40):
            return value, i
        shift += 6
        if shift > 60:
            raise ParseError("varint 过长（> 10 字节）", context={"pos": pos})


# --------------------------------------------------------------------------- #
# LEB128 风格有符号变长整数（7709 标准族价格）
# --------------------------------------------------------------------------- #
def decode_leb128(
    buf: bytes | bytearray | memoryview, pos: int = 0, *, max_bytes: int = 8
) -> tuple[int, int]:
    """解码 LEB128 风格**有符号**变长整数。

    位布局::

        首字节:  [7]=续行  [6]=符号  [5..0]=数据(6 位)
        后续字节:[7]=续行          [6..0]=数据(7 位)

    Returns
    -------
    (value, next_pos)
    """
    view = buf if isinstance(buf, (bytes, bytearray)) else memoryview(buf)
    n = len(view)
    i = pos
    b = view[i] if i < n else None
    if b is None:
        raise ParseError("leb128 解码越界（首字节）", context={"pos": pos, "len": n})
    negative = bool(b & 0x40)
    value = b & 0x3F
    shift = 6
    i += 1
    consumed = 1
    while b & 0x80:
        if i >= n:
            raise ParseError("leb128 解码越界（续行）", context={"pos": pos, "offset": i, "len": n})
        if consumed >= max_bytes:
            raise ParseError(f"leb128 过长（>{max_bytes} 字节）", context={"pos": pos})
        b = view[i]
        value |= (b & 0x7F) << shift
        shift += 7
        i += 1
        consumed += 1
    return (-value if negative else value), i


def encode_leb128(value: int) -> bytes:
    """:func:`decode_leb128` 的逆运算。"""
    negative = value < 0
    v = -value if negative else value
    out = bytearray()
    first = True
    while True:
        if first:
            chunk = v & 0x3F
            v >>= 6
            out.append(chunk | (0x40 if negative else 0))
            first = False
        else:
            chunk = v & 0x7F
            v >>= 7
            out.append(chunk)
        if v:
            out[-1] |= 0x80
        else:
            return bytes(out)


# --------------------------------------------------------------------------- #
# TDX 4 字节自定义浮点（成交量 / 成交额，禁止用于价格）
# --------------------------------------------------------------------------- #
def _pow2(exp: int) -> float:
    if exp >= 0:
        return float(1 << exp) if exp < 63 else 2.0**exp
    return 1.0 / (1 << (-exp)) if -exp < 63 else 2.0**exp


def decode_tdx_float(raw: int) -> float:
    """TDX 4 字节自定义浮点 → Python float。

    ``uint32`` 拆四段（由高到低）：``logpoint / hleax / lheax / lleax``。
    语义等价于一个「隐含最高位为 1」的二进制小数::

        e = logpoint * 2 - 0x7F
        value = 2^e + hi + mid + lo

    其中 ``hi/mid/lo`` 分别由 ``hleax/lheax/lleax`` 按 2^(e-7)、2^(e-15)、
    2^(e-23) 的权重展开；当 ``hleax`` 最高位为 1 时 ``mid/lo`` 再翻倍。

    .. warning::
       该格式**只能用于成交量/成交额**。用于价格会得到荒谬结果。
    """
    ivol = raw & 0xFFFFFFFF
    if ivol == 0:
        return 0.0
    logpoint = (ivol >> 24) & 0xFF
    hleax = (ivol >> 16) & 0xFF
    lheax = (ivol >> 8) & 0xFF
    lleax = ivol & 0xFF

    exp = logpoint * 2 - 0x7F
    base = _pow2(exp)

    exp_hi = logpoint * 2 - 0x86
    if hleax > 0x80:
        hi = _pow2(exp_hi) * 128 + (hleax & 0x7F) * _pow2(exp_hi + 1)
    else:
        hi = _pow2(exp_hi) * hleax

    mid = _pow2(logpoint * 2 - 0x8E) * lheax
    lo = _pow2(logpoint * 2 - 0x96) * lleax
    if hleax & 0x80:
        mid *= 2.0
        lo *= 2.0
    return base + hi + mid + lo


def decode_tdx_float_at(buf: bytes | bytearray | memoryview, pos: int = 0) -> tuple[float, int]:
    """从缓冲区 ``pos`` 处读 4 字节并按自定义浮点解码。

    Returns
    -------
    (value, next_pos)
    """
    view = buf if isinstance(buf, (bytes, bytearray)) else memoryview(buf)
    if pos + 4 > len(view):
        raise ParseError(
            "tdx_float 解码越界: 需要 4 字节",
            context={"pos": pos, "need": 4, "have": len(view) - pos},
        )
    (raw,) = struct.unpack_from("<I", view, pos)
    return decode_tdx_float(raw), pos + 4


# --------------------------------------------------------------------------- #
# 语义化封装
# --------------------------------------------------------------------------- #
def decode_price(
    raw: bytes | bytearray, pos: int = 0, scale: int = PRICE_SCALE_BARS
) -> tuple[float, int]:
    """TDX 变长价格 → 浮点数。

    Returns
    -------
    (price, next_pos)

    ``scale`` 由 :class:`~tstdx.reader.profile.DataProfile` 决定：
    7709 K 线为 1000，行情快照为 100，指数/期货另见 profile。
    """
    value, next_pos = decode_leb128(raw, pos)
    return (value / scale if scale else float(value)), next_pos


def decode_volume(
    buf: bytes | bytearray, pos: int = 0, *, encoding: str = "tdx_float"
) -> tuple[float, int]:
    """成交量解码。默认走 TDX 自定义浮点（7709 标准族）。

    ``encoding`` 可选 ``tdx_float`` / ``uint32`` / ``float32`` / ``varint``，
    由 :class:`~tstdx.reader.profile.DataProfile` 指定。
    """
    if encoding == "tdx_float":
        return decode_tdx_float_at(buf, pos)
    view = bytes(buf)
    if encoding == "uint32":
        if pos + 4 > len(view):
            raise ParseError("uint32 成交量越界", context={"pos": pos})
        (v,) = struct.unpack_from("<I", view, pos)
        return float(v), pos + 4
    if encoding == "float32":
        if pos + 4 > len(view):
            raise ParseError("float32 成交量越界", context={"pos": pos})
        (v,) = struct.unpack_from("<f", view, pos)
        return float(v), pos + 4
    if encoding == "varint":
        v, next_pos = decode_varint(view, pos)
        return float(v), next_pos
    raise ParseError(f"未知成交量编码: {encoding!r}", context={"encoding": encoding})


def decode_amount(
    buf: bytes | bytearray, pos: int = 0, *, encoding: str = "tdx_float"
) -> tuple[float, int]:
    """成交额解码（单位：元）。与 :func:`decode_volume` 同编码体系。"""
    return decode_volume(buf, pos, encoding=encoding)


# --------------------------------------------------------------------------- #
# 本地文件时间编码
# --------------------------------------------------------------------------- #
def get_datetime_from_lc(num: int) -> tuple[int, int, int]:
    """``.lc1`` / ``.lc5`` 分钟线中的 uint16 日期解码。

    编码公式（公开事实）::

        year  = num // 2048 + 2004
        month = (num % 2048) // 100
        day   = (num % 2048) % 100

    .. note::
       契约变更（F2 P2 微增补，2026-09）：删除第二形参
       ``minutes_from_midnight``——全库三个消费点（reader/profile、
       reader/formats、profile/detect）均为单参调用，且函数体从未使用过
       该参数。单参调用形态不变；如需分钟→时刻转换用
       :func:`minutes_to_hhmm`。

    Returns
    -------
    (year, month, day)
    """
    year = num // 2048 + 2004
    remainder = num % 2048
    month = remainder // 100
    day = remainder % 100
    return year, month, day


def minutes_to_hhmm(minutes: int) -> tuple[int, int]:
    return minutes // 60, minutes % 60


# --------------------------------------------------------------------------- #
# 字符集
# --------------------------------------------------------------------------- #
#: 候选字符集，按探测优先级排列
CANDIDATE_ENCODINGS: tuple[str, ...] = ("gbk", "gb18030", "big5", "utf-8")


def decode_gbk(raw: bytes, encoding: str = "gbk", *, strip_nul: str = "first") -> str:
    """解码字节串并清理 NUL / 空白。

    ``gb18030`` 是 ``gbk`` 的超集，解码失败时自动回退，
    避免因为生僻字导致整批数据失败。

    Parameters
    ----------
    strip_nul:
        * ``"first"``（缺省）：在**首个** NUL 处截断——适配 8 字节定长
          字段的尾部 padding 语义；
        * ``"tail"``：只剥**尾部** NUL 与空白——适配整篇正文场景（深审
          M26：正文中间的 NUL 是数据残留，在首个 NUL 截断会静默丢弃
          其后全部正文）。
    """
    for enc in (encoding, "gb18030", "utf-8", "latin-1"):
        try:
            text = raw.decode(enc)
            break
        except (UnicodeDecodeError, LookupError):
            continue
    else:  # pragma: no cover - latin-1 永不失败
        text = raw.decode("latin-1")
    if strip_nul == "tail":
        return text.rstrip("\x00").strip()
    return text.split("\x00")[0].strip()


def detect_encoding(raw: bytes, candidates: Iterable[str] = CANDIDATE_ENCODINGS) -> str:
    """四字符集探测（§23.4）。

    策略：依次尝试解码，全部成功时按「GBK 优先」原则取第一个；
    只有唯一可解码者胜出。都不行则回退 ``gb18030``（最宽超集）。
    """
    ok: list[str] = []
    for enc in candidates:
        try:
            decoded = raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
        # UTF-8 多字节序列误判为中文 GBK 的概率较低，用可打印比例粗筛
        if _printable_ratio(decoded) >= 0.8:
            ok.append(enc)
    if not ok:
        return "gb18030"
    # GBK / GB18030 优先于 UTF-8（TDX 服务端以 GBK 为主）
    for pref in ("gbk", "gb18030", "big5", "utf-8"):
        if pref in ok:
            return pref
    return ok[0]


def _printable_ratio(text: str) -> float:
    if not text:
        return 1.0
    good = sum(1 for ch in text if ch.isprintable() or ch in "\x00")
    return good / len(text)


# --------------------------------------------------------------------------- #
# zlib
# --------------------------------------------------------------------------- #
def zlib_decompress(body: bytes, strict: bool = True) -> bytes:
    """TDX 使用原始 deflate 流；兼容 zlib 头。

    Parameters
    ----------
    strict:
        True（**默认**）时解压失败直接抛 :class:`zlib.error`；
        False 时先尝试 ``-MAX_WBITS``，再尝试 ``+MAX_WBITS``，最后原样返回
        （由上层判定）。

    .. note::
       默认值在 F2 批次由 ``False`` 收紧为 ``True``：全仓唯一调用点
       （:func:`tstdx.codec.framing.decode_response_body`）本就显式传
       ``strict=True``，宽松默认只会让「解压失败」静默变成「原样字节」，
       把帧错误伪装成解析错误。需要旧宽容行为的调用方显式传
       ``strict=False``。
    """
    if not body:
        return b""
    for wbits in (-zlib.MAX_WBITS, zlib.MAX_WBITS, 47):
        try:
            return zlib.decompress(body, wbits)
        except zlib.error:
            continue
    if strict:
        raise zlib.error("无法解压：raw deflate / zlib / gzip 均失败")
    return body  # 未压缩，原样返回


def zlib_compress(body: bytes, level: int = 6) -> bytes:
    co = zlib.compressobj(level, zlib.DEFLATED, -zlib.MAX_WBITS)
    return co.compress(body) + co.flush()
