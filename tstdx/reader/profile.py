# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""DataProfile：把「数据差异」全部参数化（§6）。

通达信生态里同一份"日线"至少有这些维度的差异::

    市场 × 品种 × 周期 × price_scale × price_encoding
             × volume_unit × amount_unit × time_encoding × charset

硬编码任何一种组合都会在其他品种上产生错误数据（典型事故：
把「手」当「股」，成交量差 100 倍；把「万元」当「元」，成交额差 10000 倍）。
下面几张词表**只登记有人按它行动的成员**，各自的规模由
``tests/architecture/test_profile_vocabulary_gates.py`` 现读成员数钉住，
不在本 docstring 里手抄份数。

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
    """市场（6 类）：只登记产品真的按它行动的市场。

    每个成员的兑现处：``SH``/``SZ``/``BJ``/``HK``/``US`` 有 :attr:`CODES` 的
    文件/探测链编号，探测与预设按编号裁决；``SHFE`` **没有任何编号**，它只作为
    :data:`BUILTIN_PROFILES` 里 ``future_day`` 的市场身份存在，随档案序列化交给读者。

    本类曾登记 中金所/大商所/郑商所/能源中心/广期所/外汇 六个成员，外加两张
    零读取点的表（``ALL``、``DIRS``），本轮删除，理由是逐格量出来的：

    * 那六个成员在 ``tstdx/`` 里既没有 ``Market.X`` 形式的读取点，取值字符串也没有
      任何落点（``"cffex"``/``"dce"``/``"czce"``/``"ine"``/``"gfex"`` 全仓仅出现在它们
      自己的声明行）；
    * 符号引擎认的市场号只有五个 —— ``parse_symbol(code, market="cffex")`` 当场
      ``SymbolError: 未知市场 'cffex'；可选: ('sh', 'sz', 'bj', 'hk', 'us')``，
      所以那六个是"档案层声明了、主链解不开"的词表；
    * ``DIRS``（vipdoc 目录名）恒等于市场 token 本身，真正拼路径的
      :func:`tstdx.reader.formats.resolve_vipdoc_path` 用的是 ``sym.market``，
      这张表是它的第二份手抄件；
    * ``ALL`` 曾被 8 处 ``Market.ALL`` 指认，但那 8 处全部解析到
      :class:`tstdx.domain.symbol.Market` —— 与本类**重名而不同定义**，本类的 ``ALL``
      一个读取点都没有。同名两处 ``Market`` 的口径分界见
      ``tests/architecture/test_profile_vocabulary_gates.py``。
    """

    SH = "sh"  # 上交所
    SZ = "sz"  # 深交所
    BJ = "bj"  # 北交所
    HK = "hk"  # 港股
    US = "us"  # 美股
    SHFE = "shfe"  # 上期所：仅作期货档案的身份，无协议/文件编号

    #: 市场代码（TDX 协议中的 market 字段）
    CODES = {SH: 1, SZ: 0, BJ: 2, HK: 71, US: 74}


class AssetClass:
    """品种（4 类）：只登记有生产者的品种。

    兑现处：:data:`BUILTIN_PROFILES` 逐份档案写着 ``STOCK``/``INDEX``/``FUTURE``/
    ``OPTION``，探测层 :mod:`tstdx.profile.detect` 目前只会产出 ``STOCK``；这四个值
    都随 :attr:`DataProfile.asset_class` 序列化给用户看。

    本类曾登记 ``ETF``/``LOF``/``BOND``/``WARRANT``/``FX``/``OTHER`` 六个成员与一张
    零读取点的 ``ALL``，本轮删除。逐格实测：六个成员在 ``tstdx/`` 里没有任何
    ``AssetClass.X`` 读取点，也没有任何代码把它们写进档案或按它们分支；全仓命中的
    ``"etf"``/``"bond"`` 字面量属于**别的词表**（Web 源的 capability 名、资金流板块码、
    基金资产配置键），与档案层的品种口径无关。基金/债券在本产品里是按
    :mod:`tstdx.profile.presets` 的预设名与代码前缀行动的，不是按档案品种。
    """

    STOCK = "stock"
    INDEX = "index"
    FUTURE = "future"
    OPTION = "option"


class Period:
    """周期（11 档）：取值必须是 :data:`tstdx.domain.period.CANONICAL_PERIODS` 的成员。

    本类是"档案按哪一档周期描述布局"的常量名，**不是**另一份周期词表：规范拼写只有
    :mod:`tstdx.domain.period` 那一处声明，两边的一致性由
    ``tests/architecture/test_period_vocabulary_gates.py`` 现比集合。

    十档里除 ``TICK`` 外每档都有一个协议落点：``tstdx/client/core.py`` 的
    ``_CANONICAL_TO_CATEGORY`` 把规范拼写映射成 7709 的 K 线 category 编号，
    ``bars(period="year")`` 与 ``bars(period="1y")`` 因此走同一格。``TICK`` 是唯一
    不进那张编号表的规范周期——分笔是另一条命令，不是 K 线的一档。

    ``QUARTER = "quarter"`` 已在第 18 轮删除，理由是实测出来的：域内规范表写着
    ``"quarter" -> "season"``（季线的规范拼写是 ``season``，协议号 10 的名字也叫
    ``season``），所以它不是"另一档周期"，而是 :attr:`SEASON` 的一个**别名**却被登记成
    平级成员；``BUILTIN_PROFILES`` 里也没有任何档案写它。删常量不影响用户仍可以打字
    ``period="quarter"``——别名照旧被接受，只是档案层不再假装有两种季线。

    本类曾另带三张表，第 17 轮实测**零读取点**后删除（``ALL``、``FILE_EXT``、
    ``CMD_CATEGORY``；最后一张把 ``quarter`` 记成 10 而 ``KlineCategory.NAMES[10]``
    是 ``season``，还拿 ``"day_alt"`` 当键——那根本不是本类任何一个成员）。守这条线
    （成员必须有人读或有人按值行动、表必须有人查）的判据见
    ``tests/architecture/test_profile_vocabulary_gates.py``。
    """

    TICK = "tick"
    M1 = "1min"
    M5 = "5min"
    M15 = "15min"
    M30 = "30min"
    M60 = "60min"
    DAY = "day"
    WEEK = "week"
    MONTH = "month"
    YEAR = "year"
    SEASON = "season"


class PriceEncoding:
    """价格编码（3 档）。

    ``INT32``（注释写着"有符号，可能为负，如 MAC 协议返回"）已删除：全仓没有任何
    档案用它，也没有任何解码器按它分支，取值 ``"int32"`` 在本包里的两处字面量都是
    :mod:`tstdx.tools.codegen` 的**字段类型**词表，不是价格编码。若 MAC 侧确实需要
    有符号价格，那是一格待接的功能缺口，不该由一个成员占位。
    """

    UINT32 = "uint32"  # 定长整数，需除以 price_scale
    FLOAT32 = "float32"  # 无需缩放
    VARINT = "varint"  # 6-bit 变长（网络协议）


class VolumeUnit:
    """成交量单位（3 档）。

    ``SHARE`` 是档案默认值也是探测层的默认产出；``LOT`` 有人**按它行动**——
    :meth:`DataProfile.to_volume` 与 :class:`~tstdx.sink.local_day.LocalDaySink` 各自
    乘 100。``CONTRACT``（张/合约）只作为两份期货/期权档案的标签随档案序列化，换算
    上等同「股」：这一格是本库口径，不是漏接。
    """

    SHARE = "share"  # 股
    LOT = "lot"  # 手（A 股 1 手 = 100 股）
    CONTRACT = "contract"  # 张/合约（期权、债券）


class AmountUnit:
    """成交额单位（3 档）。

    ``YUAN`` 是八份内置档案共同写的值；``WAN``/``YI`` 在 ``to_amount`` 里有换算分支，
    但内置档案表里没有生产者——它们只经 :meth:`DataProfile.from_dict`（用户自定义档案）
    进入运行期。把这两档删掉就等于宣布"自定义档案不能声明万元/亿元"。
    """

    YUAN = "yuan"  # 元
    WAN = "wan"  # 万元
    YI = "yi"  # 亿元


class TimeEncoding:
    """时间编码（3 档）。

    ``EPOCH``（``"epoch"``，Unix 秒/毫秒）已删除：没有任何档案声明它，也没有任何
    解码分支比较它，取值在全仓零落点——它是 :data:`BUILTIN_PROFILES` 之外的一格
    "词表里备着、链路没人走"的选项。
    """

    YYYYMMDD = "yyyymmdd"  # uint32 20260831
    LC16 = "lc16"  # uint16：(n//2048+2004, n%2048//100, n%2048%100)
    DATETIME32 = "datetime32"  # 位域：year@20 month@16 day@11 hour@6 minute@0


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
