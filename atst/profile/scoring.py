# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""两套六步探测器共享的打分内核（P1-7 探测合一，方案 §4 P1-7）。

:mod:`atst.reader.profile` 的 ``ProfileDetector``（生产文件档案）与
:mod:`atst.profile.detect`（网络帧档案）是两个独立入口，各自保留
权重与输出形态；但六步探测里**同构的机械部分**——候选记录长度表、
等距抽样、字段类型探针、日期三编码投票——历史上是两份逐行同构的
手抄件（审计 M2）。本模块把它们收敛为单一事实源，两入口只做
「调用 + 加权 + 组装」。

本模块保持纯函数、零档案业务语义：

* 不 import ``DataProfile`` / presets，不决定任何权重——权重是各
  入口自己的口径（reader：0.20/0.25/0.20/0.15/0.20 五步加权；
  detect：``WEIGHT_*`` 四步加权），不在本模块仲裁；
* 价格字段的合理性阈值两入口各有细微差别（如 ``abs`` 与闭开区间），
  因此 :func:`probe_field_types` 把判定谓词留作注入参数，本模块只
  收敛「解包 → 逐偏移分类 → 计数」的机械循环。
"""

from __future__ import annotations

import struct
from collections.abc import Callable, Iterable

from ..codec.primitive import get_datetime_from_lc

__all__ = [
    "RECORD_SIZES",
    "FIELD_PROBE_OFFSETS",
    "MAX_SAMPLES",
    "sample_records",
    "probe_field_types",
    "vote_date_encodings",
    "plausible_u32_price",
    "plausible_float32_price",
    "plausible_tdx_float",
    "looks_like_yyyymmdd",
    "looks_like_datetime32",
    "looks_like_lc16",
]

#: 常见记录长度候选（SSOT；两入口此前各抄一份）
RECORD_SIZES: tuple[int, ...] = (32, 29, 24, 16, 8, 4)

#: 字段探针扫描的价格字段偏移（相对记录起点；两入口此前各抄一份）
FIELD_PROBE_OFFSETS: tuple[int, ...] = (4, 8, 12, 16)

#: 抽样上限（避免大数据集拖慢探测）
MAX_SAMPLES: int = 64


# --------------------------------------------------------------------------- #
# 抽样
# --------------------------------------------------------------------------- #
def sample_records(data: bytes, record_size: int, limit: int = MAX_SAMPLES) -> list[bytes]:
    """等距抽样 ``record_size`` 字节的记录（两入口原实现逐行同构）。

    ``record_size <= 0``（变长）或数据不足一条记录时返回空表。
    """
    if record_size <= 0:
        return []
    count = len(data) // record_size
    if count == 0:
        return []
    step = max(1, count // limit)
    return [data[i * record_size : (i + 1) * record_size] for i in range(0, count, step)]


# --------------------------------------------------------------------------- #
# 字段类型探针
# --------------------------------------------------------------------------- #
def probe_field_types(
    samples: list[bytes],
    *,
    plausible_float32: Callable[[float], bool],
    plausible_uint32: Callable[[int], bool],
    offsets: Iterable[int] = FIELD_PROBE_OFFSETS,
) -> tuple[int, int, int]:
    """字段类型探针：逐样本逐偏移解包 ``uint32`` / ``float32`` 并分类计数。

    判定谓词由调用方注入（两入口阈值口径不同，见模块 docstring）；
    返回 ``(float_like, int_like, total)``，其中 ``total`` 是实际探查的
    (样本, 偏移) 对数——两入口对「两个谓词都不命中的探查」的计入方式
    不同（reader 把它排除出分母，detect 计入），分母口径由入口自定，
    本函数只把三类原始计数如数上报。
    """
    float_like = int_like = total = 0
    for rec in samples:
        for off in offsets:
            if off + 4 > len(rec):
                continue
            total += 1
            (u32,) = struct.unpack_from("<I", rec, off)
            (f32,) = struct.unpack_from("<f", rec, off)
            if plausible_float32(f32):
                float_like += 1
            elif plausible_uint32(u32):
                int_like += 1
    return float_like, int_like, total


# --------------------------------------------------------------------------- #
# 日期三编码投票
# --------------------------------------------------------------------------- #
def vote_date_encodings(samples: list[bytes], record_size: int) -> tuple[int, int, int, int]:
    """对 ``yyyymmdd`` / ``datetime32`` / ``lc16`` 三种时间编码投票。

    只在偏移 0 探测（``record_size >= 4`` 测 uint32 两种编码并计入
    ``total``，``record_size >= 2`` 补测 uint16 lc16）。返回
    ``(ymd, dt32, lc16, total)``；各编码得分 = 票数 / total，由入口
    自行择优（两入口的择优与平票规则一致）。
    """
    ymd = dt32 = lc16 = total = 0
    for rec in samples:
        if record_size >= 4:
            (v32,) = struct.unpack_from("<I", rec, 0)
            total += 1
            if looks_like_yyyymmdd(v32):
                ymd += 1
            if looks_like_datetime32(v32):
                dt32 += 1
        if record_size >= 2:
            (v16,) = struct.unpack_from("<H", rec, 0)
            if looks_like_lc16(v16):
                lc16 += 1
    return ymd, dt32, lc16, total


# --------------------------------------------------------------------------- #
# 判定谓词（canonical 版本 = profile/detect 原实现；reader 的差异阈值
# 由 reader 侧自持并在调用 :func:`probe_field_types` 时注入）
# --------------------------------------------------------------------------- #
def plausible_u32_price(v: int) -> bool:
    """uint32 价格是否落在合理区间（0.001–100000 元，×100 缩放）。"""
    return 100 <= v <= 100_000_000


def plausible_float32_price(v: float) -> bool:
    """float32 价格是否落在合理区间（0.01–100000 元）。"""
    return 0.01 <= v <= 100_000


def plausible_tdx_float(v: float) -> bool:
    """tdx_float 是否落在合理的成交量/成交额区间（股或元）。"""
    return 0.0 < v < 1e12


def looks_like_yyyymmdd(v: int) -> bool:
    """uint32 是否呈 ``yyyymmdd`` 形（1990–2100 年，月 1–12，日 1–31）。"""
    if not isinstance(v, int) or v <= 0:
        return False
    y, m, d = v // 10000, (v // 100) % 100, v % 100
    return 1990 <= y <= 2100 and 1 <= m <= 12 and 1 <= d <= 31


def looks_like_datetime32(v: int) -> bool:
    """uint32 是否呈 datetime32 位域形（year@20 month@16 day@11）。"""
    y = (v >> 20) & 0xFFF
    m = (v >> 16) & 0x0F
    d = (v >> 11) & 0x1F
    return 1990 <= y <= 2100 and 1 <= m <= 12 and 1 <= d <= 31


def looks_like_lc16(v: int) -> bool:
    """uint16 是否呈 lc16 形（``get_datetime_from_lc`` 语义，2004 起）。"""
    y, m, d = get_datetime_from_lc(v)
    return 2004 <= y <= 2100 and 1 <= m <= 12 and 1 <= d <= 31
