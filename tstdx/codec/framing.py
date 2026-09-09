# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""TDX 报文层：请求帧 / 响应帧的组装与拆解（§7.1）。

帧布局（7709 标准，Little-Endian）
----------------------------------

**请求帧（12 字节头 + body）**::

    偏移  长度  字段          说明
    0     1     zip           压缩标志，恒为 0x0c
    1     4     seq           请求序号（uint32，自增，用于匹配响应）
    5     1     packet_type   包类型，常规包恒为 0x01
    6     2     pkg_len_1     body 长度 + 2
    8     2     pkg_len_2     pkg_len_1 的副本（服务端校验用）
    10    2     method        命令号，如 0x052d

**响应帧（16 字节头 + body）**::

    偏移  长度  字段          说明
    0     4     magic         恒为 0x0074CBB1
    4     1     zip_flag      压缩标志
    5     4     seq           与请求匹配的序号
    9     1     reserved      保留
    10    2     method        响应命令号
    12    2     zip_size      body 压缩后长度
    14    2     unzip_size    body 解压后长度

约定：``zip_size != unzip_size`` 时 body 经过 deflate 压缩。
单帧最大 32 KiB（``1 << 15``）。

.. note::
   帧布局为**可配置 spec**（:class:`FrameSpec`）。若 Golden 抓包显示
   某个主站族使用不同布局，只需新增一份 spec 而不必改动本模块代码。
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from typing import Any

from ..errors import DecompressError, FramingError, ProtocolError
from .primitive import zlib_compress, zlib_decompress

__all__ = [
    "FrameSpec",
    "DEFAULT_7709_SPEC",
    "RequestFrame",
    "ResponseFrame",
    "build_request",
    "parse_response_header",
    "decode_response_body",
    "iter_frames",
    "MAX_FRAME_BYTES",
]

#: 单帧上限（含响应头）
MAX_FRAME_BYTES: int = 1 << 15

_MAGIC = 0x0074CBB1
_ZIP_FLAG = 0x0C
_PACKET_TYPE = 0x01


def _require_uint(name: str, value: Any, *, bits: int) -> int:
    maximum = (1 << bits) - 1
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= maximum:
        raise FramingError(
            f"{name} 必须是 uint{bits} 整数，收到 {value!r}",
            context={"field": name, "value": value, "minimum": 0, "maximum": maximum},
        )
    return value


def _require_bytes(name: str, value: Any) -> bytes:
    if not isinstance(value, bytes):
        raise FramingError(
            f"{name} 必须是 bytes，收到 {type(value).__name__}",
            context={"field": name, "value_type": type(value).__name__},
        )
    return value


def _require_bool(name: str, value: Any) -> bool:
    if not isinstance(value, bool):
        raise FramingError(
            f"{name} 必须是 bool，收到 {value!r}",
            context={"field": name, "value": value},
        )
    return value


# --------------------------------------------------------------------------- #
# Spec
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class FrameSpec:
    """帧布局描述。默认即 7709 标准布局。"""

    name: str = "tdx-7709-standard"
    magic: int = _MAGIC
    zip_flag: int = _ZIP_FLAG
    packet_type: int = _PACKET_TYPE
    max_frame_bytes: int = MAX_FRAME_BYTES
    #: 响应头 struct 格式（小端）
    resp_header_fmt: str = "<IBIBHHH"
    #: 请求头 struct 格式（小端）
    req_header_fmt: str = "<BIBHHH"
    #: 包长字段相对 body 长度的偏移（TDX 为 +2）
    pkg_len_bias: int = 2
    resp_header_len: int = 16
    req_header_len: int = 12
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def resp_header_size(self) -> int:
        return struct.calcsize(self.resp_header_fmt)

    @property
    def req_header_size(self) -> int:
        return struct.calcsize(self.req_header_fmt)


DEFAULT_7709_SPEC = FrameSpec(
    meta={
        "source": "protocol-fact",
        "confidence": "high",
        "note": "请求 12B / 响应 16B 头；magic 0x0074CBB1；zip_size!=unzip_size 时 deflate",
    }
)


# --------------------------------------------------------------------------- #
# 帧对象
# --------------------------------------------------------------------------- #
@dataclass
class RequestFrame:
    method: int
    body: bytes = b""
    seq: int = 0

    def encode(self, spec: FrameSpec = DEFAULT_7709_SPEC) -> bytes:
        method = _require_uint("method", self.method, bits=16)
        seq = _require_uint("seq", self.seq, bits=32)
        body = _require_bytes("body", self.body)
        pkg_len = len(body) + spec.pkg_len_bias
        # pkg_len 字段为 uint16（req_header_fmt 的 ``H``）：超限时 struct.pack
        # 会抛**原生 struct.error**，调用方 ``except TdxError`` 接不住——
        # 统一转 FramingError（深审 M5）。
        if pkg_len > 0xFFFF:
            raise FramingError(
                f"请求体过大: pkg_len {pkg_len} 超出 uint16 上限",
                context={"body_len": len(body), "pkg_len": pkg_len},
            )
        header = struct.pack(
            spec.req_header_fmt,
            spec.zip_flag,
            seq,
            spec.packet_type,
            pkg_len,
            pkg_len,
            method,
        )
        return header + body


@dataclass
class ResponseFrame:
    magic: int
    zip_flag: int
    seq: int
    method: int
    zip_size: int
    unzip_size: int
    body: bytes = b""
    #: 解压后的 body（== body 当未压缩）
    payload: bytes = b""
    #: 原始头部字节（调试/回放用）
    header_raw: bytes = b""
    #: 解析本帧所用 spec 的 magic（:func:`parse_response_header` 写入）。
    #: 直接构造帧时缺省为标准 magic，保持既有行为。
    spec_magic: int = _MAGIC

    @property
    def compressed(self) -> bool:
        return self.zip_size != self.unzip_size

    @property
    def ok(self) -> bool:
        """magic 正确且长度自洽。

        magic 与**解析本帧所用 spec** 的期望值比较（而非模块级常量），
        使自定义 :class:`FrameSpec`（非标准主站布局）的帧也能正确自检。
        """
        return self.magic == self.spec_magic and (
            len(self.payload) == self.unzip_size or not self.unzip_size
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "magic": hex(self.magic),
            "seq": self.seq,
            "method": hex(self.method),
            "zip_size": self.zip_size,
            "unzip_size": self.unzip_size,
            "compressed": self.compressed,
            "payload_len": len(self.payload),
        }


# --------------------------------------------------------------------------- #
# 组装 / 拆解
# --------------------------------------------------------------------------- #
class SeqGenerator:
    """线程安全的请求序号生成器。"""

    __slots__ = ("_seq", "_lock")

    def __init__(self, start: int = 0) -> None:
        self._seq = start
        self._lock: Any
        try:
            import threading

            self._lock = threading.Lock()
        except Exception:  # pragma: no cover
            self._lock = None

    def next(self) -> int:
        if self._lock is not None:
            with self._lock:
                self._seq = (self._seq + 1) & 0xFFFFFFFF
                return self._seq
        self._seq = (self._seq + 1) & 0xFFFFFFFF
        return self._seq


_default_seq = SeqGenerator()


def build_request(
    method: int,
    body: bytes = b"",
    *,
    seq: int | None = None,
    spec: FrameSpec = DEFAULT_7709_SPEC,
    compress: bool = False,
) -> tuple[bytes, int]:
    """组装请求帧。

    Returns
    -------
    (frame_bytes, seq)
    """
    method = _require_uint("method", method, bits=16)
    body = _require_bytes("body", body)
    compress = _require_bool("compress", compress)
    if seq is None:
        seq = _default_seq.next()
    else:
        seq = _require_uint("seq", seq, bits=32)
    payload = zlib_compress(body) if compress and body else body
    frame = RequestFrame(method=method, body=payload, seq=seq).encode(spec)
    if len(frame) > spec.max_frame_bytes:
        raise FramingError(
            "请求帧超过单帧上限",
            context={"size": len(frame), "limit": spec.max_frame_bytes},
        )
    return frame, seq


def parse_response_header(header: bytes, spec: FrameSpec = DEFAULT_7709_SPEC) -> ResponseFrame:
    """解析 16 字节响应头（不含 body）。"""
    if len(header) != spec.resp_header_size:
        raise FramingError(
            f"响应头长度错误: 期望 {spec.resp_header_size}, 实际 {len(header)}",
            context={"expect": spec.resp_header_size, "got": len(header)},
        )
    magic, zip_flag, seq, _reserved, method, zip_size, unzip_size = struct.unpack(
        spec.resp_header_fmt, header
    )
    return ResponseFrame(
        magic=magic,
        zip_flag=zip_flag,
        seq=seq,
        method=method,
        zip_size=zip_size,
        unzip_size=unzip_size,
        header_raw=header,
        spec_magic=spec.magic,
    )


def decode_response_body(
    frame: ResponseFrame, body: bytes, *, strict: bool = False
) -> ResponseFrame:
    """就地解压 body 并写回 ``frame.payload``。"""
    if len(body) != frame.zip_size:
        raise FramingError(
            "body 长度与 zip_size 不符",
            context={"body_len": len(body), "zip_size": frame.zip_size},
        )
    frame.body = body
    try:
        if frame.compressed:
            frame.payload = zlib_decompress(body, strict=True)
        else:
            frame.payload = body
            # 深审 L5 包装盲区：zip_size==unzip_size 声明「未压缩」，但少数
            # 主站响应实际是 zlib 包装（长度声明即压前长度）——长度自检
            # 矛盾时尝试解压兜底，解压后吻合则采用；否则维持原样由调用方
            # 按长度矛盾报错。
            if frame.unzip_size and len(body) != frame.unzip_size:
                try:
                    frame.payload = zlib_decompress(body, strict=True)
                except Exception:  # noqa: BLE001 —— 兜底失败按原样走长度校验
                    frame.payload = body
    except Exception as exc:  # zlib.error 及其子类
        # 深审 M6：无论 strict 与否都转 DecompressError——原生 zlib.error
        # 裸抛会让 golden 回放（iter_frames 默认 strict=False）等上层
        # ``except TdxError`` 失守。
        raise DecompressError(
            f"解压失败: {exc}", context={"method": hex(frame.method)}, cause=exc
        ) from exc
    if frame.compressed and len(frame.payload) != frame.unzip_size:
        raise DecompressError(
            "解压后长度与 unzip_size 不符",
            context={
                "method": hex(frame.method),
                "got": len(frame.payload),
                "expect": frame.unzip_size,
            },
        )
    return frame


def iter_frames(stream: bytes, spec: FrameSpec = DEFAULT_7709_SPEC):
    """从字节流中逐帧切分（用于 Golden 回放与抓包分析）。

    帧长守卫与 :meth:`TcpConnection.read_frame`（transport/base.py）同语义：
    ``zip_size`` 超出 ``spec.max_frame_bytes`` 即抛 :class:`FramingError`，
    防止错位流上的垃圾长度字段驱动超大读取。

    Yields
    ------
    ResponseFrame
    """
    offset = 0
    n = len(stream)
    while offset + spec.resp_header_size <= n:
        frame = parse_response_header(stream[offset : offset + spec.resp_header_size], spec)
        if frame.magic != spec.magic:
            raise ProtocolError(
                f"magic 不匹配，流已错位: {hex(frame.magic)}",
                context={"offset": offset},
            )
        if frame.zip_size > spec.max_frame_bytes:
            raise FramingError(
                f"响应帧过大: {frame.zip_size} > {spec.max_frame_bytes}",
                context={"zip_size": frame.zip_size, "offset": offset},
            )
        total = spec.resp_header_size + frame.zip_size
        if offset + total > n:
            raise FramingError(
                "流中最后一段不完整",
                context={"offset": offset, "need": total, "have": n - offset},
            )
        decode_response_body(frame, stream[offset + spec.resp_header_size : offset + total])
        yield frame
        offset += total
