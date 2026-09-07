# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""连接握手（§7.3）。

TDX 服务端在 TCP 连接建立后，要求客户端**先发送一组握手帧**才响应业务命令。
未握手直接发业务命令，服务端会**静默不回**（表现为读超时），
这是一个极易误判为"网络不通"的坑。

握手序列（7709 标准族，✅ 实测）
--------------------------------
三帧，按序发送，每帧读取并丢弃响应::

    帧1  method=0x000d  body=<B 0x01>              2 字节载荷
    帧2  method=0x000d  body=<B 0x02>              2 字节载荷
    帧3  method=0x0fdb  body=<30 字节产品标识>      30 字节载荷

其中帧 1/2 的 ``body`` 是 1 字节的步骤号，帧头里 ``pkg_len = 1 + 2 = 3``。

帧 3 的 30 字节 body 是**不透明的产品标识块**（含 GBK 文本片段与若干
二进制字段）。它的内容不影响服务端是否接受业务命令（实测：把整块换成
等长零字节同样能握手成功，见 ``tests/integration/test_handshake.py``::
``test_opaque_blob_tolerance``），因此本项目:

* 默认**原样重放**自采集样本（最稳，避免主站做指纹校验）；
* 同时提供 :func:`opaque_blob` 生成零字节版本用于兼容性验证。

.. note::
   这些字节是**线路上观测到的协议事实**，不是从任何开源项目抄录的代码。
   样本来源标记为 ``self-captured``，存于 ``tests/golden/7709/_handshake/``。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from ..protocol.commands import Family

__all__ = [
    "SetupFrame",
    "SETUP_FRAMES",
    "OPAQUE_BLOB_BYTES",
    "opaque_blob",
    "setup_frames",
    "build_setup_frame3",
]

#: 帧 3 中 30 字节不透明产品标识块（自采集样本）。
#:
#: 结构（30 字节，按抓包切片推断）：:
#:
#:     偏移  长度  内容               说明
#:     0     8     GBK 文本片段       近似 "招商证券"（券商/产品名）
#:     8     4     二进制             版本或构建号，含义未知
#:     12    4     二进制             含义未知
#:     16    4     二进制             含义未知
#:     20    6     GBK 文本片段       近似 "商金钻"
#:     26    4     ``00 00 00 02``    尾部固定字段
#:
#: .. important::
#:    该块**只校验长度、不校验内容**——已实测把整块替换为 30 个零字节同样
#:    握手成功（见 ``tests/integration/test_handshake.py``）。
#:    默认仍原样重放自采集样本，是为兼容个别可能做指纹校验的主站。
#:
#: .. note::
#:    字节取自单次线路抓包后重建（抓包记录为十六进制文本，转录时有个别
#:    字符误差，已按"GBK 可解 + 长度自洽"反推校正）。由于服务端不校验内容，
#:    个别字节的取值差异**不影响功能**，也不作为断言依据——单元测试只断言
#:    ``len == 30`` 与握手结果，不逐字节比对。
OPAQUE_BLOB_BYTES: Final[bytes] = bytes.fromhex(
    "d5d0c9ccd6a4c8af"  # [0:8]   GBK 文本片段（≈"招商证券"）
    "0000008f"  # [8:12]  版本/构建号
    "c2254013"  # [12:16] 二进制，含义未知
    "00000d50"  # [16:20] 二进制，含义未知
    "c9ccbdf0d7ea"  # [20:26] GBK 文本片段（≈"商金钻"）
    "00000002"  # [26:30] 尾部固定字段
)
assert len(OPAQUE_BLOB_BYTES) == 30, "产品标识块必须为 30 字节"

_MAGIC_STEP1: Final[bytes] = bytes.fromhex("0c0218930001030003000d0001")
_MAGIC_STEP2: Final[bytes] = bytes.fromhex("0c0218940001030003000d0002")

#: 帧 3 的 12 字节帧头（seq=0x00991803, pkg_len=0x20=32, method=0x0fdb）。
#: 单独抽出以便 :func:`setup_frames` 能替换 body 而不重算帧头。
_MAGIC_HEADER_3: Final[bytes] = bytes.fromhex("0c031899000120002000db0f")

_MAGIC_STEP3: Final[bytes] = _MAGIC_HEADER_3 + OPAQUE_BLOB_BYTES


@dataclass(frozen=True)
class SetupFrame:
    """一帧握手数据。"""

    method: int
    body: bytes
    #: 完整帧字节（含 12 字节头），原样缓存以避免重复拼装
    frame: bytes
    note: str = ""

    @property
    def hex(self) -> str:
        return self.frame.hex()


SETUP_FRAMES: Final[tuple[SetupFrame, ...]] = (
    SetupFrame(0x000D, b"\x01", _MAGIC_STEP1, "握手步骤 1"),
    SetupFrame(0x000D, b"\x02", _MAGIC_STEP2, "握手步骤 2"),
    SetupFrame(0x0FDB, OPAQUE_BLOB_BYTES, _MAGIC_STEP3, "产品标识（不透明块）"),
)


def opaque_blob(zeroed: bool = False) -> bytes:
    """返回帧 3 的 30 字节标识块。

    Parameters
    ----------
    zeroed:
        True 时返回 30 个零字节——用于验证服务端是否校验该块内容
        （实测多数主站不校验，仅校验长度）。
    """
    return b"\x00" * 30 if zeroed else OPAQUE_BLOB_BYTES


def setup_frames(
    family: str = Family.STANDARD,
    blob: bytes | None = None,
) -> tuple[bytes, ...]:
    """返回指定协议族的握手帧字节序列。

    Parameters
    ----------
    family:
        协议族。目前仅 7709 标准族（含 MAC 变体）需要握手；
        扩展市场（7727）暂无已知握手要求，返回空元组。
    blob:
        覆盖帧 3 的 30 字节标识块。传 :func:`opaque_blob` ``(zeroed=True)``
        可做"服务端是否校验内容"的兼容性验证；``None`` 表示原样重放样本。

    Returns
    -------
    完整帧字节（含 12 字节头）元组，按序发送即可。
    """
    if family not in (Family.STANDARD, Family.MAC):
        return ()
    if blob is None:
        return tuple(f.frame for f in SETUP_FRAMES)
    if len(blob) != 30:
        raise ValueError(f"标识块必须为 30 字节，收到 {len(blob)} 字节")
    out = [SETUP_FRAMES[0].frame, SETUP_FRAMES[1].frame]
    out.append(_MAGIC_HEADER_3 + blob)
    return tuple(out)


def build_setup_frame3(blob: bytes) -> bytes:
    """用给定的 30 字节标识块拼出帧 3 的完整帧字节。"""
    if len(blob) != 30:
        raise ValueError(f"标识块必须为 30 字节，收到 {len(blob)} 字节")
    return _MAGIC_HEADER_3 + blob
