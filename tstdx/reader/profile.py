# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""DataProfile：把「数据差异」全部参数化（§6）。

通达信生态里同一份"日线"至少有这些维度的差异::

    市场(12) × 品种(10) × 周期(12) × price_scale × price_encoding
             × volume_unit × amount_unit × time_encoding × charset

硬编码任何一种组合都会在其他品种上产生错误数据（典型事故：
把「手」当「股」，成交量差 100 倍；把「万元」当「元」，成交额差 10000 倍）。

本模块提供：

* :class:`DataProfile` —— 一份完整的规格档案（可序列化、可版本化）
* :class:`ProfileDetector` —— 六步自动探测，输出置信度
* :data:`BUILTIN_PROFILES` —— 常见市场/品种的开箱即用档案

**定位（与 profile/detect 的关系，两套探测器互相指认）**：本模块是
**生产文件档案**（本地 vipdoc 二进制，探测结果直接接
``DayBarReader/MinBarReader`` 主链路）；:mod:`tstdx.profile.detect` 是
**网络帧档案**（对网络 payload / 未知归档字节做证据化探测，供协议
发现与人工评审）。两者共享 :class:`DataProfile`，但互不调用。

探测失败（置信度 < 0.5）时抛 :class:`~tstdx.errors.ProfileUndetectable`，
**绝不静默猜测**。
"""

from __future__ import annotations

import struct
from dataclasses import asdict, dataclass, replace
from typing import Any

from ..codec.primitive import detect_encoding, get_datetime_from_lc
from ..errors import ProfileUndetectable

__all__ = [
    "Market",
    "AssetClass",
    "Period",
    "PriceEncoding",
    "VolumeUnit",
    "AmountUnit",
    "TimeEncoding",
    "DataProfile",
    "BUILTIN_PROFILES",
    "ProfileDetector",
    "get_profile",
    "detect_profile",
]


# --------------------------------------------------------------------------- #
# 枚举常量（用简单类而非 Enum，便于外部扩展与序列化）
# --------------------------------------------------------------------------- #
class Market:
    """市场（12 类）。"""

    SH = "sh"  # 上交所
    SZ = "sz"  # 深交所
    BJ = "bj"  # 北交所
    HK = "hk"  # 港股
    US = "us"  # 美股
    CFFEX = "cffex"  # 中金所
    SHFE = "shfe"  # 上期所
    DCE = "dce"  # 大商所
    CZCE = "czce"  # 郑商所
    INE = "ine"  # 能源中心
    GFEX = "gfex"  # 广期所
    FX = "fx"  # 外汇

    ALL = (SH, SZ, BJ, HK, US, CFFEX, SHFE, DCE, CZCE, INE, GFEX, FX)

    #: 市场代码（TDX 协议中的 market 字段）
    CODES = {SH: 1, SZ: 0, BJ: 2, HK: 71, US: 74}
    #: vipdoc 目录名
    DIRS = {SH: "sh", SZ: "sz", BJ: "bj", HK: "hk", US: "us"}


class AssetClass:
    """品种（10 类）。"""

    STOCK = "stock"
    INDEX = "index"
    ETF = "etf"
    LOF = "lof"
    BOND = "bond"
    WARRANT = "warrant"
    FUTURE = "future"
    OPTION = "option"
    FX = "fx"
    OTHER = "other"

    ALL = (STOCK, INDEX, ETF, LOF, BOND, WARRANT, FUTURE, OPTION, FX, OTHER)


class Period:
    """周期（12 档）。"""

    TICK = "tick"
    M1 = "1min"
    M5 = "5min"
    M15 = "15min"
    M30 = "30min"
    M60 = "60min"
    DAY = "day"
    WEEK = "week"
    MONTH = "month"
    QUARTER = "quarter"
    YEAR = "year"
    SEASON = "season"

    ALL = (TICK, M1, M5, M15, M30, M60, DAY, WEEK, MONTH, QUARTER, YEAR, SEASON)

    #: 本地文件扩展名
    FILE_EXT = {M1: "lc1", M5: "lc5", DAY: "day"}
    #: K 线请求 category 参数
    CMD_CATEGORY = {
        M5: 0,
        M15: 1,
        M30: 2,
        M60: 3,
        DAY: 4,
        WEEK: 5,
        MONTH: 6,
        M1: 7,
        DAY + "_alt": 9,
        QUARTER: 10,
        YEAR: 11,
    }


class PriceEncoding:
    UINT32 = "uint32"  # 定长整数，需除以 price_scale
    FLOAT32 = "float32"  # 无需缩放
    VARINT = "varint"  # 6-bit 变长（网络协议）
    INT32 = "int32"  # 有符号（可能为负，如 MAC 协议返回）


class VolumeUnit:
    SHARE = "share"  # 股
    LOT = "lot"  # 手（A 股 1 手 = 100 股）
    CONTRACT = "contract"  # 张/合约（期权、债券）


class AmountUnit:
    YUAN = "yuan"  # 元
    WAN = "wan"  # 万元
    YI = "yi"  # 亿元


class TimeEncoding:
    YYYYMMDD = "yyyymmdd"  # uint32 20260831
    LC16 = "lc16"  # uint16：(n//2048+2004, n%2048//100, n%2048%100)
    DATETIME32 = "datetime32"  # 位域：year@20 month@16 day@11 hour@6 minute@0
    EPOCH = "epoch"  # Unix 秒/毫秒


# --------------------------------------------------------------------------- #
# DataProfile
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class DataProfile:
    """一份数据规格档案。

    所有影响"数值正确性"的差异都在此显式声明；
    未声明的差异（如期货持仓量、期权希腊字母）放在 ``extra_fields``。
    """

    name: str = "default"
    market: str = Market.SH
    asset_class: str = AssetClass.STOCK
    period: str = Period.DAY

    #: 价格 = 原始整数 / price_scale
    price_scale: int = 100
    price_encoding: str = PriceEncoding.UINT32
    volume_unit: str = VolumeUnit.SHARE
    amount_unit: str = AmountUnit.YUAN
    time_encoding: str = TimeEncoding.YYYYMMDD
    charset: str = "gbk"

    #: 本地文件记录字节数（0 表示变长）
    record_size: int = 32
    #: 变长品种的额外字段
    extra_fields: tuple[str, ...] = ()

    #: 探测置信度（人工指定时恒为 1.0）
    confidence: float = 1.0
    notes: str = ""

    # -- 便捷换算 ---------------------------------------------------------- #
    def to_price(self, raw: int | float) -> float:
        if self.price_encoding == PriceEncoding.FLOAT32:
            return float(raw)
        return float(raw) / self.price_scale if self.price_scale else float(raw)

    def to_volume(self, raw: int | float) -> float:
        """成交量 → 「股」为基准单位。"""
        v = float(raw)
        if self.volume_unit == VolumeUnit.LOT:
            return v * 100
        return v

    def to_amount(self, raw: int | float) -> float:
        """成交额 → 「元」为基准单位。"""
        a = float(raw)
        if self.amount_unit == AmountUnit.WAN:
            return a * 10_000
        if self.amount_unit == AmountUnit.YI:
            return a * 100_000_000
        return a

    def with_overrides(self, **kw: Any) -> DataProfile:
        return replace(self, **kw)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["extra_fields"] = list(self.extra_fields)
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> DataProfile:
        known = {f for f in cls.__dataclass_fields__}
        payload = {k: v for k, v in d.items() if k in known}
        if "extra_fields" in payload:
            payload["extra_fields"] = tuple(payload["extra_fields"])
        return cls(**payload)


# --------------------------------------------------------------------------- #
# 内置档案
# --------------------------------------------------------------------------- #
BUILTIN_PROFILES: dict[str, DataProfile] = {
    # A 股日线：价格 ×100 整数，成交量「股」，成交额「元」
    "a_share_day": DataProfile(
        name="a_share_day",
        market=Market.SH,
        asset_class=AssetClass.STOCK,
        period=Period.DAY,
        price_scale=100,
        price_encoding=PriceEncoding.UINT32,
        volume_unit=VolumeUnit.SHARE,
        amount_unit=AmountUnit.YUAN,
        time_encoding=TimeEncoding.YYYYMMDD,
        record_size=32,
        notes="沪深 A 股 .day 日线：32 字节/记录，日期 uint32 YYYYMMDD",
    ),
    "a_share_day_sz": DataProfile(
        name="a_share_day_sz",
        market=Market.SZ,
        asset_class=AssetClass.STOCK,
        period=Period.DAY,
        price_scale=100,
        price_encoding=PriceEncoding.UINT32,
        volume_unit=VolumeUnit.SHARE,
        amount_unit=AmountUnit.YUAN,
        time_encoding=TimeEncoding.YYYYMMDD,
        record_size=32,
    ),
    # 分钟线：价格 float32，时间 lc16 + 分钟偏移
    "a_share_min": DataProfile(
        name="a_share_min",
        market=Market.SH,
        asset_class=AssetClass.STOCK,
        period=Period.M1,
        price_scale=1,
        price_encoding=PriceEncoding.FLOAT32,
        volume_unit=VolumeUnit.SHARE,
        amount_unit=AmountUnit.YUAN,
        time_encoding=TimeEncoding.LC16,
        record_size=32,
        notes="沪深 .lc1 / .lc5：价格 float32，日期 uint16 lc16 编码",
    ),
    # 指数：成交量单位与个股不同，且无成交额语义
    "index_day": DataProfile(
        name="index_day",
        market=Market.SH,
        asset_class=AssetClass.INDEX,
        period=Period.DAY,
        price_scale=100,
        price_encoding=PriceEncoding.UINT32,
        volume_unit=VolumeUnit.SHARE,
        amount_unit=AmountUnit.YUAN,
        time_encoding=TimeEncoding.YYYYMMDD,
        record_size=32,
    ),
    # 期货：成交量「手」，含持仓量 extra field
    "future_day": DataProfile(
        name="future_day",
        market=Market.SHFE,
        asset_class=AssetClass.FUTURE,
        period=Period.DAY,
        price_scale=100,
        price_encoding=PriceEncoding.UINT32,
        volume_unit=VolumeUnit.CONTRACT,
        amount_unit=AmountUnit.YUAN,
        time_encoding=TimeEncoding.YYYYMMDD,
        record_size=32,
        extra_fields=("open_interest",),
        notes="期货 .day 的末字段为持仓量（open interest）而非上日收盘",
    ),
    # 期权：需要希腊字母
    "option_day": DataProfile(
        name="option_day",
        market=Market.SH,
        asset_class=AssetClass.OPTION,
        period=Period.DAY,
        price_scale=1000,
        price_encoding=PriceEncoding.UINT32,
        volume_unit=VolumeUnit.CONTRACT,
        amount_unit=AmountUnit.YUAN,
        time_encoding=TimeEncoding.YYYYMMDD,
        record_size=32,
        extra_fields=("open_interest", "delta", "gamma", "theta", "vega", "rho"),
        notes="期权价格常为 ×1000 缩放",
    ),
    # 港股
    "hk_day": DataProfile(
        name="hk_day",
        market=Market.HK,
        asset_class=AssetClass.STOCK,
        period=Period.DAY,
        price_scale=1000,
        price_encoding=PriceEncoding.UINT32,
        volume_unit=VolumeUnit.SHARE,
        amount_unit=AmountUnit.YUAN,
        time_encoding=TimeEncoding.YYYYMMDD,
        record_size=32,
    ),
    # 网络协议 K 线（0x052D）
    "net_bars": DataProfile(
        name="net_bars",
        market=Market.SH,
        asset_class=AssetClass.STOCK,
        period=Period.DAY,
        price_scale=100,
        price_encoding=PriceEncoding.VARINT,
        volume_unit=VolumeUnit.SHARE,
        amount_unit=AmountUnit.YUAN,
        time_encoding=TimeEncoding.DATETIME32,
        record_size=0,
        notes="7709 0x052D 网络 K 线：变长记录，时间位域编码",
    ),
}


def get_profile(name: str) -> DataProfile:
    if name not in BUILTIN_PROFILES:
        known = ", ".join(sorted(BUILTIN_PROFILES))
        raise KeyError(f"未知 profile: {name!r}；可用: {known}")
    return BUILTIN_PROFILES[name]


# --------------------------------------------------------------------------- #
# ProfileDetector：六步自动探测
# --------------------------------------------------------------------------- #
class ProfileDetector:
    """六步自动探测数据规格（§6.3）。

    步骤与判据::

        1 记录长度      filesize % 32 / 29 / 16 / 8 == 0
        2 价格缩放      采样价格字段，判断 ×100 / ×1000 / float32
        3 编码类型      uint32 整数 vs float32（浮点位模式检验）
        4 成交量单位    用 amount/volume 反推均价，与 (high+low)/2 比对
        5 时间编码      YYYYMMDD / lc16 / datetime32 三者合法性检验
        6 字符编码      GBK / GB18030 / BIG5 / UTF-8 探测

    每步给出 0~1 得分，加权后得总置信度。
    """

    #: 常见记录长度
    RECORD_SIZES: tuple[int, ...] = (32, 29, 24, 16, 8, 4)
    #: 候选价格缩放
    PRICE_SCALES: tuple[int, ...] = (1, 100, 1000, 10000)

    def __init__(self, sample_count: int = 64) -> None:
        self.sample_count = sample_count

    # ---- 主入口 ---------------------------------------------------------- #
    def detect(self, raw: bytes, *, hint: DataProfile | None = None) -> DataProfile:
        """探测字节流的规格档案。

        Parameters
        ----------
        raw:
            原始文件/流内容。
        hint:
            调用方提供的先验（如已知市场），用于缩小搜索空间。
        """
        if not raw:
            raise ProfileUndetectable("空数据无法探测", context={"len": 0})

        steps: dict[str, float] = {}
        notes: list[str] = []

        # 1) 记录长度
        record_size, s1 = self._detect_record_size(raw)
        steps["record_size"] = s1

        # 2) + 3) 价格缩放与编码
        price_encoding, s_enc = self._detect_price_encoding(raw, record_size)
        steps["price_encoding"] = s_enc
        price_scale, s_scale = self._detect_price_scale(raw, record_size, price_encoding, hint)
        steps["price_scale"] = s_scale

        # 4) 成交量单位（依赖日线布局）
        volume_unit, s_vol = self._detect_volume_unit(raw, record_size, price_scale)
        steps["volume_unit"] = s_vol

        # 5) 时间编码
        time_encoding, s_time = self._detect_time_encoding(raw, record_size)
        steps["time_encoding"] = s_time
        if time_encoding == TimeEncoding.LC16:
            notes.append("时间呈 lc16 编码，判定为分钟线（.lc1/.lc5）")

        # 6) 字符编码（对含名称字段的格式才有意义）
        charset = self._detect_charset(raw)
        steps["charset"] = 0.5  # 权重低，仅作记录

        confidence = round(
            0.20 * s1 + 0.25 * s_enc + 0.20 * s_scale + 0.15 * s_vol + 0.20 * s_time, 4
        )

        base = hint or DataProfile()
        profile = base.with_overrides(
            name="auto-detected",
            record_size=record_size,
            price_scale=price_scale,
            price_encoding=price_encoding,
            volume_unit=volume_unit,
            time_encoding=time_encoding,
            charset=charset,
            confidence=confidence,
            notes="; ".join(notes),
        )

        if confidence < 0.5:
            raise ProfileUndetectable(
                f"数据规格探测置信度过低: {confidence:.2f}",
                context={
                    "steps": {k: round(v, 3) for k, v in steps.items()},
                    "record_size": record_size,
                    "hint": hint.name if hint else None,
                },
            )
        return profile

    # ---- 步骤 1 ---------------------------------------------------------- #
    def _detect_record_size(self, raw: bytes) -> tuple[int, float]:
        n = len(raw)
        for size in self.RECORD_SIZES:
            if n % size == 0 and n // size >= 1:
                # 32 是日线/分钟线最常见的记录长度
                return size, 1.0 if size == 32 else 0.7
        # 都不整除：用最大公约数思路回退到 1
        return 1, 0.2

    # ---- 步骤 3 ---------------------------------------------------------- #
    def _detect_price_encoding(self, raw: bytes, record_size: int) -> tuple[str, float]:
        """判断价格字段是 float32 还是 uint32 整数。"""
        if record_size < 8:
            return PriceEncoding.UINT32, 0.3
        samples = self._sample_records(raw, record_size)
        float_like = 0
        int_like = 0
        for rec in samples:
            for off in (4, 8, 12, 16):
                if off + 4 > len(rec):
                    continue
                (u32,) = struct.unpack_from("<I", rec, off)
                (f32,) = struct.unpack_from("<f", rec, off)
                # float32 判定：值落在金融价格合理区间，且不是"整齐"的整数
                if 0.01 < abs(f32) < 100_000:
                    float_like += 1
                elif 0 < u32 < 100_000_000:
                    int_like += 1
        total = float_like + int_like
        if total == 0:
            return PriceEncoding.UINT32, 0.3
        if float_like / total > 0.6:
            return PriceEncoding.FLOAT32, float_like / total
        return PriceEncoding.UINT32, int_like / total

    # ---- 步骤 2 ---------------------------------------------------------- #
    def _detect_price_scale(
        self,
        raw: bytes,
        record_size: int,
        encoding: str,
        hint: DataProfile | None = None,
    ) -> tuple[int, float]:
        """判断整数价格的缩放倍数（×100 / ×1000 / …）。

        判据（按优先级）::

            1. 合理价格区间粗筛（0.001 ~ 100000 元）
            2. 价量一致性：amount/volume ≈ (high+low)/2（仅 32 字节日线布局有效）
            3. hint 保护：hint 的缩放同样自洽时优先采用

        典型事故：A 股日线 ``raw=1000``（真实价 10.00）只做区间粗筛会被
        误判为 ``scale=1`` → 价格 1250.0，相差 100 倍。价量一致性 + hint
        双重保护可消除该误判：正确缩放下 ``amount/volume`` 必然落在 OHLC
        区间附近，而错误缩放会使均价偏离参考价 100 倍。
        """
        if encoding == PriceEncoding.FLOAT32:
            return 1, 1.0
        samples = self._sample_records(raw, record_size)
        if not samples:
            return 100, 0.2

        # 1) 合理价格区间粗筛
        range_scores: dict[int, float] = {}
        for scale in self.PRICE_SCALES:
            good = total = 0
            for rec in samples:
                for off in (4, 8, 12, 16):
                    if off + 4 > len(rec):
                        continue
                    (u32,) = struct.unpack_from("<I", rec, off)
                    total += 1
                    if 0.001 <= u32 / scale <= 100_000:
                        good += 1
            range_scores[scale] = good / total if total else 0.0

        # 2) 价量一致性（仅 32 字节日线布局才有意义）
        if record_size == 32:
            vol_scores = {s: self._scale_volume_consistency(samples, s) for s in self.PRICE_SCALES}
        else:
            vol_scores = {s: 0.0 for s in self.PRICE_SCALES}

        def combined(scale: int) -> float:
            return 0.6 * range_scores[scale] + 0.4 * vol_scores[scale]

        # 先验偏好 ×100（A 股/期货最常见）；仅在明显更优时才切换
        best_scale, best_score = 100, combined(100)
        for scale in self.PRICE_SCALES:
            if scale == best_scale:
                continue
            c = combined(scale)
            if c > best_score + 1e-6:
                best_scale, best_score = scale, c

        # 3) hint 保护：hint 的缩放同样自洽时优先采用（避免 100x 价格误判）
        hint_scale = getattr(hint, "price_scale", None)
        if hint_scale in self.PRICE_SCALES and combined(hint_scale) >= best_score - 0.05:
            best_scale = hint_scale
            best_score = max(best_score, combined(hint_scale))

        return best_scale, round(best_score, 4)

    def _scale_volume_consistency(self, samples: list[bytes], scale: int) -> float:
        """价量一致性得分：``amount/volume`` 与 ``(high+low)/2`` 对齐的比例。

        成交量单位不参与本判据（避免 price_scale 与 volume_unit 相互混淆）；
        成交量以「手/张」下发的品种在此拿不到高分，交给 hint 保护兜底。
        """
        votes = total = 0
        for rec in samples:
            if len(rec) < 28:
                continue
            high = struct.unpack_from("<I", rec, 8)[0] / scale
            low = struct.unpack_from("<I", rec, 12)[0] / scale
            (amount,) = struct.unpack_from("<f", rec, 20)
            (volume,) = struct.unpack_from("<I", rec, 24)
            if volume <= 0 or amount <= 0 or high <= 0:
                continue
            ref = (high + low) / 2
            if ref <= 0:
                continue
            ratio = (amount / volume) / ref
            total += 1
            if 0.5 <= ratio <= 2.0:
                votes += 1
        return votes / total if total else 0.0

    # ---- 步骤 4 ---------------------------------------------------------- #
    def _detect_volume_unit(
        self, raw: bytes, record_size: int, price_scale: int
    ) -> tuple[str, float]:
        """用 ``amount / volume`` 反推成交均价，与 ``(high+low)/2`` 比对。

        若均价落在合理区间 → 单位为「股」；
        若均价是合理价的 100 倍 → 单位为「手」。
        """
        if record_size != 32:
            return VolumeUnit.SHARE, 0.3
        samples = self._sample_records(raw, record_size)
        share_votes = lot_votes = 0
        for rec in samples:
            if len(rec) < 28:
                continue
            high = struct.unpack_from("<I", rec, 8)[0] / price_scale
            low = struct.unpack_from("<I", rec, 12)[0] / price_scale
            (amount,) = struct.unpack_from("<f", rec, 20)
            (volume,) = struct.unpack_from("<I", rec, 24)
            if volume <= 0 or amount <= 0 or high <= 0:
                continue
            ref_price = (high + low) / 2
            avg_share = amount / volume  # 假设 volume 单位为「股」
            if ref_price <= 0:
                continue
            ratio = avg_share / ref_price
            if 0.5 <= ratio <= 2.0:
                share_votes += 1
            elif 50 <= ratio <= 200:  # volume 实际是「手」
                lot_votes += 1
        total = share_votes + lot_votes
        if total == 0:
            return VolumeUnit.SHARE, 0.3
        if lot_votes / total > 0.6:
            return VolumeUnit.LOT, lot_votes / total
        return VolumeUnit.SHARE, share_votes / total

    # ---- 步骤 5 ---------------------------------------------------------- #
    def _detect_time_encoding(self, raw: bytes, record_size: int) -> tuple[str, float]:
        samples = self._sample_records(raw, record_size)
        ymd = lc16 = dt32 = 0
        total = 0
        for rec in samples:
            if record_size >= 4:
                (v32,) = struct.unpack_from("<I", rec, 0)
                total += 1
                y, m, d = v32 // 10000, (v32 // 100) % 100, v32 % 100
                if 1990 <= y <= 2100 and 1 <= m <= 12 and 1 <= d <= 31:
                    ymd += 1
                # datetime32 位域
                y2 = (v32 >> 20) & 0xFFF
                m2 = (v32 >> 16) & 0x0F
                d2 = (v32 >> 11) & 0x1F
                if 1990 <= y2 <= 2100 and 1 <= m2 <= 12 and 1 <= d2 <= 31:
                    dt32 += 1
            if record_size >= 2:
                (v16,) = struct.unpack_from("<H", rec, 0)
                y3, m3, d3 = get_datetime_from_lc(v16)
                if 2004 <= y3 <= 2100 and 1 <= m3 <= 12 and 1 <= d3 <= 31:
                    lc16 += 1
        if total == 0:
            return TimeEncoding.YYYYMMDD, 0.2
        scores = {
            TimeEncoding.YYYYMMDD: ymd / total,
            TimeEncoding.DATETIME32: dt32 / total,
            TimeEncoding.LC16: lc16 / total,
        }
        best = max(scores, key=lambda k: scores[k])
        return best, round(scores[best], 4)

    # ---- 步骤 6 ---------------------------------------------------------- #
    def _detect_charset(self, raw: bytes) -> str:
        return detect_encoding(raw[:4096])

    # ---- 工具 ------------------------------------------------------------ #
    def _sample_records(self, raw: bytes, record_size: int) -> list[bytes]:
        if record_size <= 0:
            return []
        count = len(raw) // record_size
        if count == 0:
            return []
        step = max(1, count // self.sample_count)
        return [raw[i * record_size : (i + 1) * record_size] for i in range(0, count, step)]


_DEFAULT_DETECTOR = ProfileDetector()


def detect_profile(raw: bytes, hint: DataProfile | None = None) -> DataProfile:
    return _DEFAULT_DETECTOR.detect(raw, hint=hint)
