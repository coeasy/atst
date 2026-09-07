# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""Market presets：常见市场/品种的默认数据规格（Tier D item D3）。

每个预设是 :class:`MarketPreset` 实例，为 :mod:`tstdx.profile.detect`
提供**先验**（先验越强，探测置信度越高）。

预设与 :mod:`tstdx.reader.profile.Market` / :mod:`AssetClass` 的关系：

* ``market_id`` 采用 :class:`~tstdx.reader.profile.Market.CODES` 的
  **文件/探测链编号**（0=深 1=沪 2=北 71=港 74=美），与 PROTOCOL_SPEC
  yaml、:class:`DataProfile.market` 同一口径；**这不是 TDX 二进制协议
  链的市场号**——协议帧里北交所复用 0（与深市同段，见下）；
* ``market_name`` 用 :class:`~tstdx.reader.profile.Market` 的字符串常量
  （如 ``"sh"`` / ``"sz"``），便于与 :class:`DataProfile.market` 互转；
* ``asset_class`` 用 :class:`~tstdx.reader.profile.AssetClass` 的字符串
  （``"stock"`` / ``"etf"`` / ``"bond"`` / ``"future"`` 等）。

**BJ 市场码双轨口径（§5 对齐）**：协议链 BJ=0（TDX 老协议把北交所并入
深市市场号段，0=深/北）；文件链 BJ=2（reader/profile 探测与 PROTOCOL_SPEC
用 2 区分北交所样本，避免与深市混流）。历史缘由：北交所 2021 由三板
精选层平移而来，协议帧沿用旧市场号 0，文件格式层则新开编号。**裁决
单一事实源是 :mod:`tstdx.domain.symbol`**——按代码前缀
（92/43/83/87/88）判定 BJ，从不依赖市场号；协议侧换算走
:data:`tstdx.domain.symbol.Symbol.tdx_market`（1=沪，其余=0）。

9 个开箱预设
------------
================================= ========= ======================
名称              market_id   典型代码前缀
================================= ========= ======================
SH_A              1           600/601/603/605/688
SZ_A              0           000/001/002/003/300/301
BJ_A              2           43/83/87/88/92
SH_FUND           1           510/511/512/513/515/516/517/518/560/588
SZ_FUND           0           15/16/18
SH_BOND           1           01/10/11/12/13/14
SZ_BOND           0           11/12/13/14/15
EX_GOLD           73          au/AU（上期所黄金）
EX_FUTURES        75          cu/ru/al/zn/pb/IF/IH/IC（中金所/上期所）
================================= ========= ======================
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..reader.profile import (
    AssetClass,
    Market,
    Period,
    PriceEncoding,
    TimeEncoding,
    VolumeUnit,
)

__all__ = [
    "MarketPreset",
    "PRESETS",
    "get_preset",
    "match_preset",
    "list_preset_names",
]


# --------------------------------------------------------------------------- #
# MarketPreset
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class MarketPreset:
    """一份市场/品种预设。

    Attributes
    ----------
    name:
        预设名（如 ``"SH_A"``），用于 :func:`get_preset` 查找。
    market_id:
        TDX 协议中的原始市场编号（0=深 1=沪 2=北 …）。
    market_name:
        :class:`~tstdx.reader.profile.Market` 的字符串常量
        （``"sh"`` / ``"sz"`` / ``"bj"`` …）。
    asset_class:
        :class:`~tstdx.reader.profile.AssetClass` 的字符串常量。
    code_prefixes:
        该市场/品种下 6 字节代码串的常见前缀。用于 :func:`match_preset`。
    typical_categories:
        常见 K 线 category 取值（对应
        :class:`~tstdx.reader.profile.Period.CMD_CATEGORY` 的 category 编号），
        供探测时缩小搜索空间。
    quote_scale:
        价格缩放倍数（行情快照用；K 线通常为 1000）。
    volume_unit:
        :class:`~tstdx.reader.profile.VolumeUnit` 字符串（``"share"`` /
        ``"lot"`` / ``"contract"``）。
    price_encoding:
        :class:`~tstdx.reader.profile.PriceEncoding` 字符串。
    time_encoding:
        :class:`~tstdx.reader.profile.TimeEncoding` 字符串。
    default_period:
        默认周期（:class:`~tstdx.reader.profile.Period` 字符串）。
    notes:
        额外说明（如交易所名称、注意事项）。
    """

    name: str
    market_id: int
    market_name: str
    asset_class: str
    code_prefixes: tuple[str, ...]
    typical_categories: tuple[int, ...]
    quote_scale: int
    volume_unit: str
    price_encoding: str = PriceEncoding.UINT32
    time_encoding: str = TimeEncoding.YYYYMMDD
    default_period: str = Period.DAY
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        """序列化为 dict，便于配置持久化。"""
        return {
            "name": self.name,
            "market_id": self.market_id,
            "market_name": self.market_name,
            "asset_class": self.asset_class,
            "code_prefixes": list(self.code_prefixes),
            "typical_categories": list(self.typical_categories),
            "quote_scale": self.quote_scale,
            "volume_unit": self.volume_unit,
            "price_encoding": self.price_encoding,
            "time_encoding": self.time_encoding,
            "default_period": self.default_period,
            "notes": self.notes,
        }

    def data_profile_kwargs(self, *, period: str | None = None) -> dict[str, Any]:
        """生成 :class:`DataProfile` 关键字参数。

        Parameters
        ----------
        period:
            覆盖默认周期；None 使用 :attr:`default_period`。
        """
        eff_period = period or self.default_period
        return {
            "market": self.market_name,
            "asset_class": self.asset_class,
            "period": eff_period,
            "price_scale": self.quote_scale,
            "price_encoding": self.price_encoding,
            "volume_unit": self.volume_unit,
            "time_encoding": self.time_encoding,
        }


# --------------------------------------------------------------------------- #
# 预设表
# --------------------------------------------------------------------------- #
_PRESETS: dict[str, MarketPreset] = {
    "SH_A": MarketPreset(
        name="SH_A",
        market_id=Market.CODES[Market.SH],
        market_name=Market.SH,
        asset_class=AssetClass.STOCK,
        code_prefixes=("600", "601", "603", "605", "688"),
        typical_categories=(4, 7),  # day / 1min
        quote_scale=100,
        volume_unit=VolumeUnit.SHARE,
        price_encoding=PriceEncoding.UINT32,
        time_encoding=TimeEncoding.YYYYMMDD,
        default_period=Period.DAY,
        notes="上交所主板 + 科创板；A 股日线价格 ×100，成交量股",
    ),
    "SZ_A": MarketPreset(
        name="SZ_A",
        market_id=Market.CODES[Market.SZ],
        market_name=Market.SZ,
        asset_class=AssetClass.STOCK,
        code_prefixes=("000", "001", "002", "003", "300", "301"),
        typical_categories=(4, 7),
        quote_scale=100,
        volume_unit=VolumeUnit.SHARE,
        price_encoding=PriceEncoding.UINT32,
        time_encoding=TimeEncoding.YYYYMMDD,
        default_period=Period.DAY,
        notes="深交所主板 + 创业板；规则同 SH_A",
    ),
    "BJ_A": MarketPreset(
        name="BJ_A",
        market_id=Market.CODES[Market.BJ],  # 文件链=2；协议链 BJ=0（见模块 docstring 双轨口径）
        market_name=Market.BJ,
        asset_class=AssetClass.STOCK,
        code_prefixes=("43", "83", "87", "88", "92"),
        typical_categories=(4, 7),
        quote_scale=100,
        volume_unit=VolumeUnit.SHARE,
        price_encoding=PriceEncoding.UINT32,
        time_encoding=TimeEncoding.YYYYMMDD,
        default_period=Period.DAY,
        notes=(
            "北交所；市场码双轨：文件/探测链 market_id=2，TDX 二进制协议帧"
            "复用 0（与深市同段）——部分老主站因此把 BJ 归到 market_id=0 下。"
            "裁决以代码前缀为准（domain.symbol 单一事实源），不依赖市场号"
        ),
    ),
    "SH_FUND": MarketPreset(
        name="SH_FUND",
        market_id=Market.CODES[Market.SH],
        market_name=Market.SH,
        asset_class=AssetClass.ETF,
        code_prefixes=("510", "511", "512", "513", "515", "516", "517", "518", "560", "588"),
        typical_categories=(4, 7),
        quote_scale=100,
        volume_unit=VolumeUnit.SHARE,
        price_encoding=PriceEncoding.UINT32,
        time_encoding=TimeEncoding.YYYYMMDD,
        default_period=Period.DAY,
        notes="上交所 ETF / LOF；单位「股」而非「份」需按发行文件核对",
    ),
    "SZ_FUND": MarketPreset(
        name="SZ_FUND",
        market_id=Market.CODES[Market.SZ],
        market_name=Market.SZ,
        asset_class=AssetClass.ETF,
        code_prefixes=("15", "16", "18"),
        typical_categories=(4, 7),
        quote_scale=100,
        volume_unit=VolumeUnit.SHARE,
        price_encoding=PriceEncoding.UINT32,
        time_encoding=TimeEncoding.YYYYMMDD,
        default_period=Period.DAY,
        notes="深交所 ETF / LOF；代码 15/16/18 开头",
    ),
    "SH_BOND": MarketPreset(
        name="SH_BOND",
        market_id=Market.CODES[Market.SH],
        market_name=Market.SH,
        asset_class=AssetClass.BOND,
        code_prefixes=("01", "10", "11", "12", "13", "14"),
        typical_categories=(4,),
        quote_scale=100,
        volume_unit=VolumeUnit.SHARE,
        price_encoding=PriceEncoding.UINT32,
        time_encoding=TimeEncoding.YYYYMMDD,
        default_period=Period.DAY,
        notes="上交所债券；成交量单位「张」在部分报表里等同「元」面值",
    ),
    "SZ_BOND": MarketPreset(
        name="SZ_BOND",
        market_id=Market.CODES[Market.SZ],
        market_name=Market.SZ,
        asset_class=AssetClass.BOND,
        code_prefixes=("11", "12", "13", "14", "15"),
        typical_categories=(4,),
        quote_scale=100,
        volume_unit=VolumeUnit.SHARE,
        price_encoding=PriceEncoding.UINT32,
        time_encoding=TimeEncoding.YYYYMMDD,
        default_period=Period.DAY,
        notes="深交所债券；与 SH_BOND 共享大部分编码规则",
    ),
    "EX_GOLD": MarketPreset(
        name="EX_GOLD",
        market_id=73,
        market_name=Market.SHFE,
        asset_class=AssetClass.FUTURE,
        code_prefixes=("au", "AU"),
        typical_categories=(4, 7),
        quote_scale=1000,
        volume_unit=VolumeUnit.LOT,
        price_encoding=PriceEncoding.UINT32,
        time_encoding=TimeEncoding.YYYYMMDD,
        default_period=Period.DAY,
        notes="上期所黄金期货（TDX 扩展市场 73）；量单位「手」，价格 ×1000",
    ),
    "EX_FUTURES": MarketPreset(
        name="EX_FUTURES",
        market_id=75,
        market_name=Market.SHFE,
        asset_class=AssetClass.FUTURE,
        code_prefixes=("cu", "CU", "ru", "al", "AL", "zn", "pb", "IF", "IH", "IC"),
        typical_categories=(4, 7),
        quote_scale=1000,
        volume_unit=VolumeUnit.CONTRACT,
        price_encoding=PriceEncoding.UINT32,
        time_encoding=TimeEncoding.YYYYMMDD,
        default_period=Period.DAY,
        notes="期货主连/主力合约（TDX 扩展市场 75）；量单位「张/合约」",
    ),
}

#: 预设表（只读别名）。
PRESETS: dict[str, MarketPreset] = dict(_PRESETS)


# --------------------------------------------------------------------------- #
# 查询
# --------------------------------------------------------------------------- #
def list_preset_names() -> list[str]:
    """所有预设名称，按字典序。"""
    return sorted(PRESETS)


def get_preset(name: str) -> MarketPreset:
    """按名称取预设。

    Raises
    ------
    KeyError
        名称不存在。
    """
    if name not in PRESETS:
        known = ", ".join(list_preset_names())
        raise KeyError(f"未知 market preset: {name!r}；可用: {known}")
    return PRESETS[name]


def match_preset(code: str, market: int) -> MarketPreset | None:
    """按代码前缀 + 市场编号匹配预设。

    匹配规则（按优先级）：

    1. ``code_prefixes`` 是完整前缀（长度 ≥ 3）时，要求
       ``code.startswith(prefix)`` 精确命中；
    2. 前缀长度为 2 时，允许 ``code.startswith(prefix)``；
    3. 匹配成功的预设中，``market_id == market`` 者优先；
       **市场号全部不符时返回 ``None``**——不再硬塞首个前缀命中者
       （沪深交叉代码如 ``600519`` + market=0 属于上游数据错误，
       硬塞会把错误伪装成有效先验，审计 §3-5）。

    Returns
    -------
    :class:`MarketPreset` 或 ``None``（无匹配 / 市场号不符）。

    Examples
    --------
    >>> match_preset("600519", 1).name
    'SH_A'
    >>> match_preset("000001", 0).name
    'SZ_A'
    >>> match_preset("600519", 0) is None  # 沪深交叉：市场号不符 → None
    True
    """
    if not code:
        return None
    code_s = str(code).strip()

    # 精确前缀匹配（长度 ≥ 3）
    candidates: list[MarketPreset] = []
    for p in PRESETS.values():
        for prefix in p.code_prefixes:
            if len(prefix) >= 3 and code_s.startswith(prefix):
                candidates.append(p)
                break
    if not candidates:
        # 长度 2 的短前缀
        for p in PRESETS.values():
            for prefix in p.code_prefixes:
                if len(prefix) == 2 and code_s.startswith(prefix):
                    candidates.append(p)
                    break
    if not candidates:
        return None

    # 优先市场编号匹配；不符 → None（不再硬塞首候选）
    for p in candidates:
        if p.market_id == market:
            return p
    return None


__all__.append("_PRESETS")
