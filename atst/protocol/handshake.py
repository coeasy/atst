# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""连接握手（§7.3）。

TDX 服务端在 TCP 连接建立后，多数主站要求客户端**先发送握手帧**才响应业务命令。
一帧都不发时，实测 7 台可达主站里 6 台对 ``0x052D`` 直接静默不回（表现为读超时），
仅 60.191.117.167 例外；同一台主机（218.75.126.9）在相隔两分钟的两轮里一次回了数据、
一次读超时 ⇒ 不握手能否侥幸**不稳定，不可依赖**。这是极易误判为"网络不通"的坑。

握手序列（7709 标准族，✅ 实测）
--------------------------------
三帧，按序发送，每帧读取并丢弃响应::

    帧1  method=0x000d  body=<B 0x01>              2 字节载荷
    帧2  method=0x000d  body=<B 0x02>              2 字节载荷
    帧3  method=0x0fdb  body=<30 字节产品标识>      30 字节载荷

其中帧 1/2 的 ``body`` 是 1 字节的步骤号，帧头里 ``pkg_len = 1 + 2 = 3``。
**帧 1+2 足以拿到数据**：只发这两帧，7/7 可达主机对 ``0x052D`` 一律回 180 字节 / 10 根。

帧 3 的 30 字节 body 是**产品标识块**。它不是取数据的必要条件，但它的**内容会改变
服务端给不给业务数据**——所以既不能不校验长度，也不能"照抄抓包样本"：

* 2026-09-19 逐位 A/B：同一主机、同一条新建连接、同一个逐字节照抄 golden 的
  ``0x052D`` 请求体，只换这 30 字节。重放自采集样本（含 GBK 券商名）让响应缩成
  2 字节 ``2003``（count 字段声明 800 条、记录字节 0 条，K 线恒 0 根）；换成 30 个
  零字节则拿回 180 字节 / 10 根真实日线。每台可达主机跑 2 轮全同向，golden 采集主机
  218.75.126.9 再交替重复 3 轮（该机累计 5 次），7/7 主机无一例外；第 8 台候选
  119.147.212.81 两轮均连接超时，不在此列。
* 因此本包默认发送 **30 个零字节**。这不是"任何非零值都被拒"：``b"A"*30`` 与
  ``b"\\xff"*30`` 同样拿到 180 字节 / 10 根（3/3 受测主机）。削成空桩是那 30 个样本
  字节特有的。另有一条实测边界：全零块把末 4 字节改成 ``00000002``（样本块的尾巴）
  会让 3/3 主机在握手中途直接断开连接——这块字节是被**结构化解析**的，不是不透明填充。

.. important::
   本文件此前两处断言"内容不影响服务端是否接受业务命令""只校验长度、不校验内容"，
   并把它交给了一个不存在的证据：``tests/integration/test_handshake.py``
   （及其 ``test_opaque_blob_tolerance``）和 ``tests/golden/7709/_handshake/``
   在仓库任何一次提交里都没有出现过（F-59）。断言因此从未被测到，而默认值又照它
   选了"原样重放"，于是 F-37 的"主站只回 2 字节空桩"从头到尾是本端握手问题。
   本段上方那些实测结论由 :mod:`tests.protocol.test_handshake_frames` 钉住**线路形状**
   （默认零块、帧头 ``pkg_len``、覆盖只替换 body、无握手族返回空），服务端反应属真实
   网络行为，不在离线断言射程内——重测方法见本文档 §1 第 34 步。

.. note::
   那 30 个样本字节本身仍是线路上观测到的协议事实（GBK 片段近似"招商证券"＋若干
   二进制字段＋尾部 ``00000002``），但既然默认不再重放它，字节就不留在代码里。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from .commands import Family

__all__ = [
    "SetupFrame",
    "SETUP_FRAMES",
    "setup_frames",
]

#: 帧 3 的 30 字节产品标识块——全零。
#:
#: 取值理由见模块 docstring 的 A/B 实测：带券商名的自采集样本会让主站对
#: ``0x052D`` 只回 2 字节空响应，全零才拿得到 K 线数据。
_PRODUCT_ID_BLOB: Final[bytes] = b"\x00" * 30
assert len(_PRODUCT_ID_BLOB) == 30, "产品标识块必须为 30 字节"

_MAGIC_STEP1: Final[bytes] = bytes.fromhex("0c0218930001030003000d0001")
_MAGIC_STEP2: Final[bytes] = bytes.fromhex("0c0218940001030003000d0002")

#: 帧 3 的 12 字节帧头（seq=0x00991803, pkg_len=0x20=32, method=0x0fdb）。
#: 单独抽出以便 :func:`setup_frames` 能替换 body 而不重算帧头。
_MAGIC_HEADER_3: Final[bytes] = bytes.fromhex("0c031899000120002000db0f")

_MAGIC_STEP3: Final[bytes] = _MAGIC_HEADER_3 + _PRODUCT_ID_BLOB


@dataclass(frozen=True)
class SetupFrame:
    """一帧握手数据。"""

    method: int
    body: bytes
    #: 完整帧字节（含 12 字节头），构造时预生成以避免重复拼装
    frame: bytes
    note: str = ""

    @property
    def hex(self) -> str:
        return self.frame.hex()


SETUP_FRAMES: Final[tuple[SetupFrame, ...]] = (
    SetupFrame(0x000D, b"\x01", _MAGIC_STEP1, "握手步骤 1"),
    SetupFrame(0x000D, b"\x02", _MAGIC_STEP2, "握手步骤 2"),
    SetupFrame(0x0FDB, _PRODUCT_ID_BLOB, _MAGIC_STEP3, "产品标识（全零块）"),
)


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
        覆盖帧 3 的 30 字节标识块；``None`` 表示用模块默认的 30 个零字节。
        服务端对这块内容的反应见模块 docstring——它不是可有可无的填充。

    Returns
    -------
    完整帧字节（含 12 字节头）元组，按序发送即可。
    """
    if family not in (Family.STANDARD, Family.MAC):
        return ()
    if blob is None:
        return tuple(f.frame for f in SETUP_FRAMES)
    if len(blob) != len(_PRODUCT_ID_BLOB):
        raise ValueError(f"标识块必须为 {len(_PRODUCT_ID_BLOB)} 字节，收到 {len(blob)} 字节")
    out = [SETUP_FRAMES[0].frame, SETUP_FRAMES[1].frame]
    out.append(_MAGIC_HEADER_3 + blob)
    return tuple(out)
