# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""Centralized normalization for HTTP Web market data sources (§33.7).

Different data sources return volume/amount/price in different units:

=================  =============  ==============  =============
Source             Volume         Amount          Price
=================  =============  ==============  =============
Sina               股 (×1)        元 (×1)         元
Tencent            手 (×100)      万元 (×10000)   元
Eastmoney          手 (×100)      元 (×1)         ×100 int
Others             股 (×1)        元 (×1)         元
=================  =============  ==============  =============

Global contract: **volume = 股, amount = 元, price = 元**.
Raw values must be normalized before :class:`~tstdx.domain.models.Quote`
is produced, otherwise mixing sources causes 100 / 10000× errors.

Usage::

    from tstdx.web.normalize import normalize_volume, normalize_amount

    volume = normalize_volume("tencent", 5000)    # 5000 手 → 500000 股
    amount = normalize_amount("eastmoney", 169550)  # 169550 元 → 169550 元

    # Or use the registry to register a custom normalizer:
    from tstdx.web.normalize import register_normalizer, VolumeNormalizer

    @register_normalizer("my_source")
    class MyNormalizer(VolumeNormalizer):
        volume_scale = 200.0
        amount_scale = 1.0
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, TypeVar

from .sources import (
    BAIDU,
    BOC,
    CORPORATE,
    EASTMONEY,
    FUND,
    FUND_FLOW,
    GLOBAL,
    HK,
    HK_SINA,
    HOT_RANK,
    INDEX_CONS,
    JSL,
    KLINE,
    LHB,
    LIMIT_POOL,
    MARGIN,
    MARKET_STAT,
    MINUTE,
    MINUTE_KLINE,
    NEWS,
    NORTHBOUND,
    RANK,
    SINA,
    SINA_FUND_FLOW,
    STOCK_CHANGES,
    SUGGEST,
    TENCENT,
    TICKS,
    TRENDS,
    US,
    WENCAI,
)

__all__ = [
    "SINA",
    "TENCENT",
    "EASTMONEY",
    "JSL",
    "HK",
    "KLINE",
    "BOC",
    "VolumeNormalizer",
    "register_normalizer",
    "normalize_volume",
    "normalize_amount",
    "normalize_price",
    "normalize_quote",
    "normalize_bar",
]

# --------------------------------------------------------------------------- #
# Registry
# --------------------------------------------------------------------------- #
_NORMALIZER_REGISTRY: dict[str, type[VolumeNormalizer]] = {}


def register_normalizer(
    source: str,
) -> Callable[[type[_T]], type[_T]]:
    """Decorator to register a :class:`VolumeNormalizer` class for *source*.

    Parameters
    ----------
    source:
        Source name string (e.g. ``"sina"``, ``"tencent"``).

    Returns
    -------
    Callable
        Class decorator that stores the normalizer class in the registry.

    Examples
    --------
    ::

        @register_normalizer("tencent")
        class TencentNormalizer(VolumeNormalizer):
            volume_scale = 100.0
            amount_scale = 10_000.0
    """

    def decorator(cls: type[_T]) -> type[_T]:
        _NORMALIZER_REGISTRY[source] = cls
        return cls

    return decorator


# --------------------------------------------------------------------------- #
# Normalizer base class
# --------------------------------------------------------------------------- #
_T = TypeVar("_T", bound="VolumeNormalizer")


class VolumeNormalizer:
    """Normalizes raw source values to the global contract (股 / 元 / 元).

    Subclasses set ``volume_scale``, ``amount_scale``, and ``price_scale``
    as class attributes. The ``normalize_*`` methods apply these multipliers
    and round to 6 decimal places.

    Attributes
    ----------
    volume_scale:
        Raw volume → 股 (shares) multiplier.
    amount_scale:
        Raw amount → 元 (yuan) multiplier.
    price_scale:
        Raw price → 元 (yuan) multiplier.
    """

    volume_scale: float = 1.0
    amount_scale: float = 1.0
    price_scale: float = 1.0

    def normalize_volume(self, value: float) -> float:
        """Normalize a raw volume to 股 (shares)."""
        return round(float(value) * self.volume_scale, 6)

    def normalize_amount(self, value: float) -> float:
        """Normalize a raw amount to 元 (yuan)."""
        return round(float(value) * self.amount_scale, 6)

    def normalize_price(self, value: float) -> float:
        """Normalize a raw price to 元 (yuan)."""
        return round(float(value) * self.price_scale, 6)

    def __repr__(self) -> str:
        return (
            f"{self.__class__.__name__}("
            f"volume_scale={self.volume_scale}, "
            f"amount_scale={self.amount_scale}, "
            f"price_scale={self.price_scale})"
        )


# --------------------------------------------------------------------------- #
# Built-in normalizers (registered at import time)
# --------------------------------------------------------------------------- #
@register_normalizer(SINA)
class SinaNormalizer(VolumeNormalizer):
    """新浪: volume = 股, amount = 元, price = 元 (identity)."""

    volume_scale = 1.0
    amount_scale = 1.0
    price_scale = 1.0


@register_normalizer(BAIDU)
class BaiduNormalizer(VolumeNormalizer):
    """百度财经: 解析层 :class:`~tstdx.web.baidu.adapters.BaiduSource`
    已把日K量统一为「股」、金额为「元」（与 tstdx 全局契约一致），
    此处 identity 防二次缩放。"""

    volume_scale = 1.0
    amount_scale = 1.0
    price_scale = 1.0


@register_normalizer(FUND)
class FundNormalizer(VolumeNormalizer):
    """东财基金: 数据型源（``list[dict]`` / ``dict``），不经行情归一化
    （净值/估值本身即为最终数值），此处 identity 显式登记防静默兜底。"""

    volume_scale = 1.0
    amount_scale = 1.0
    price_scale = 1.0


@register_normalizer(INDEX_CONS)
class IndexConstituentsNormalizer(VolumeNormalizer):
    """东财指数成分: 数据型源（``list[dict]``），成分权重/指标本身即为
    最终数值，不经行情归一化，此处 identity 显式登记防静默兜底。"""

    volume_scale = 1.0
    amount_scale = 1.0
    price_scale = 1.0


@register_normalizer(MARGIN)
class MarginNormalizer(VolumeNormalizer):
    """东财融资融券: 数据型源（``list[dict]``），两融金额/余量本身即为
    最终数值（元/股，量纲照抄），不经行情归一化，identity 显式登记。"""

    volume_scale = 1.0
    amount_scale = 1.0
    price_scale = 1.0


@register_normalizer(TENCENT)
class TencentNormalizer(VolumeNormalizer):
    """腾讯: volume = 手 → 股 (×100), amount = 万元 → 元 (×10000)."""

    volume_scale = 100.0
    amount_scale = 10_000.0
    price_scale = 1.0


@register_normalizer(EASTMONEY)
class EastmoneyNormalizer(VolumeNormalizer):
    """东方财富: volume = 手 → 股 (×100), amount = 元, price = ×100 int → 元 (/100)."""

    volume_scale = 100.0
    amount_scale = 1.0
    price_scale = 1 / 100.0


@register_normalizer(JSL)
class JslNormalizer(VolumeNormalizer):
    """集思录: identity."""

    volume_scale = 1.0
    amount_scale = 1.0
    price_scale = 1.0


@register_normalizer(HK)
class HkNormalizer(VolumeNormalizer):
    """港股: identity."""

    volume_scale = 1.0
    amount_scale = 1.0
    price_scale = 1.0


@register_normalizer(KLINE)
class KlineNormalizer(VolumeNormalizer):
    """日 K 线: 解析层 :meth:`KlineSource.parse_bars` 已按市场内联缩放
    （A 股手→股 ×100，港美股不缩放），此处 identity 防二次缩放。

    .. note::
        深审 L6：旧实现 docstring 声称「解析层只输出原始手、缩放统一在此」
        且 ``volume_scale=100.0``——与解析层实际行为（adapters.py 已内联
        市场分支缩放）矛盾；一旦被调用会把 A 股量二次 ×100、把港美股量
        错误 ×100。现对齐为 identity（与 MinuteKlineNormalizer 同模式）。
    """

    volume_scale = 1.0
    amount_scale = 1.0
    price_scale = 1.0


@register_normalizer(MINUTE_KLINE)
class MinuteKlineNormalizer(VolumeNormalizer):
    """分钟 K 线: 解析层 MinuteKlineSource.parse_bars 已按市场内联缩放
    （A 股手→股 ×100，港美股不缩放），此处 identity 防二次缩放。"""

    volume_scale = 1.0
    amount_scale = 1.0
    price_scale = 1.0


@register_normalizer(BOC)
class BocNormalizer(VolumeNormalizer):
    """中行汇率: identity (no volume/amount/price semantics)."""

    volume_scale = 1.0
    amount_scale = 1.0
    price_scale = 1.0


@register_normalizer(RANK)
class RankNormalizer(VolumeNormalizer):
    """东财排行 clist（``fltt=2``）: volume = 手 → 股 (×100)，其余原生单位。

    ``fltt=2`` 下 f2 最新价与 f3 涨跌幅已是浮点元 / 百分数，
    成交额 f6 已是元——只有成交量 f5 仍是**手**。
    """

    volume_scale = 100.0
    amount_scale = 1.0
    price_scale = 1.0


@register_normalizer(GLOBAL)
class GlobalNormalizer(VolumeNormalizer):
    """外盘: identity——解析层已按合约口径直接换算并在 extra 标注单位。"""

    volume_scale = 1.0
    amount_scale = 1.0
    price_scale = 1.0


@register_normalizer(MARKET_STAT)
class MarketStatNormalizer(VolumeNormalizer):
    """大盘统计: volume = 手 → 股 (×100)，amount = 万元 → 元 (×10000)。

    解析层 (:class:`~tstdx.web.global_market.TencentMarketStatSource`) 只做
    **原始解析**，缩放统一交本归一器——与 SourceSpec 的 ``volume_scale=
    100.0 / amount_scale=10_000.0`` 对齐，避免「解析层与归一器双重缩放」。
    """

    volume_scale = 100.0
    amount_scale = 10_000.0
    price_scale = 1.0


# --------------------------------------------------------------------------- #
# 显式注册此前依赖 identity 兜底的源（P1 #7：防止未来系数漂移）
# 这些源的量/额口径要么已是契约单位（股/元）、要么由解析层内联缩放，
# 故 normalizer 为 identity——此处显式登记，使「spec ↔ normalizer」对齐可审计。
# --------------------------------------------------------------------------- #
@register_normalizer(US)
class UsNormalizer(VolumeNormalizer):
    """美股(腾讯): volume = 股、amount = 元（解析层已按原生币种输出，无需缩放）。"""


@register_normalizer(HK_SINA)
class HkSinaNormalizer(VolumeNormalizer):
    """新浪港股: volume = 股、amount = 元（HKD 原币种，解析层直接输出）。"""


@register_normalizer(NEWS)
class NewsNormalizer(VolumeNormalizer):
    """个股新闻: 非行情语义（dict 列表），无 volume/amount，identity。"""


@register_normalizer(MINUTE)
class MinuteNormalizer(VolumeNormalizer):
    """当日分时: 解析层 MinuteSource 已内联 ×100（手→股），此处 identity 防二次缩放。"""


@register_normalizer(SUGGEST)
class SuggestNormalizer(VolumeNormalizer):
    """代码联想: 非行情语义（dict 列表），identity。"""


@register_normalizer(TICKS)
class TicksNormalizer(VolumeNormalizer):
    """逐笔成交: 解析层 TencentTickSource 已内联 ×100（手→股），identity 防二次缩放。"""


@register_normalizer(TRENDS)
class TrendsNormalizer(VolumeNormalizer):
    """分时成交: 解析层 EastmoneyTrendsSource 已内联 ×100（手→股），identity 防二次缩放。"""


@register_normalizer(FUND_FLOW)
class FundFlowNormalizer(VolumeNormalizer):
    """资金流: 额为元、占比为 ×100 整数（解析层已处理），identity。"""


@register_normalizer(LIMIT_POOL)
class LimitPoolNormalizer(VolumeNormalizer):
    """涨停/跌停/炸板池: 价为 ×1000 整数（解析层已除 1000），identity。"""


@register_normalizer(NORTHBOUND)
class NorthboundNormalizer(VolumeNormalizer):
    """沪深港通资金: 额为万元（解析层已处理），identity。"""


@register_normalizer(CORPORATE)
class CorporateNormalizer(VolumeNormalizer):
    """基本面/公司行为: 无全局量额缩放需求，identity。"""


@register_normalizer(LHB)
class LhbNormalizer(VolumeNormalizer):
    """龙虎榜: 额为元（解析层已处理），identity。"""


@register_normalizer(SINA_FUND_FLOW)
class SinaFundFlowNormalizer(VolumeNormalizer):
    """新浪资金流历史: 净额原生即为**元**、占比原生为小数（解析层已 ×100 转百分数），
    且无成交量字段，故 identity。"""


@register_normalizer(WENCAI)
class WencaiNormalizer(VolumeNormalizer):
    """i问财选股: 非行情语义（title+rows 的 dict 列表），identity。"""


@register_normalizer(STOCK_CHANGES)
class StockChangesNormalizer(VolumeNormalizer):
    """盘中异动池: 非行情语义（time/code/change_type/metrics 的 dict 列表），identity。"""


@register_normalizer(HOT_RANK)
class HotRankNormalizer(VolumeNormalizer):
    """股吧人气榜: 非行情语义（rank/symbol 的 dict 列表），identity。"""


# --------------------------------------------------------------------------- #
# Standalone functions
# --------------------------------------------------------------------------- #
def _get_normalizer(source: str) -> VolumeNormalizer:
    """Look up the normalizer instance for *source* (identity fallback)."""
    cls = _NORMALIZER_REGISTRY.get(source)
    if cls is None:
        return VolumeNormalizer()
    return cls()


def normalize_volume(source: str, raw_value: float) -> float:
    """Normalize a raw volume to 股 (shares).

    Parameters
    ----------
    source:
        Source name (e.g. ``"sina"``, ``"tencent"``, ``"eastmoney"``).
    raw_value:
        Raw volume value from the source.

    Returns
    -------
    float
        Volume in 股 (shares), rounded to 6 decimal places.
    """
    return _get_normalizer(source).normalize_volume(raw_value)


def normalize_amount(source: str, raw_value: float) -> float:
    """Normalize a raw amount to 元 (yuan).

    Parameters
    ----------
    source:
        Source name.
    raw_value:
        Raw amount value from the source.

    Returns
    -------
    float
        Amount in 元 (yuan), rounded to 6 decimal places.
    """
    return _get_normalizer(source).normalize_amount(raw_value)


def normalize_price(source: str, raw_value: float) -> float:
    """Normalize a raw price to 元 (yuan).

    Parameters
    ----------
    source:
        Source name.
    raw_value:
        Raw price value from the source.

    Returns
    -------
    float
        Price in 元 (yuan), rounded to 6 decimal places.
    """
    return _get_normalizer(source).normalize_price(raw_value)


def normalize_quote(source: str, raw_data: dict[str, Any]) -> dict[str, Any]:
    """Normalize a quote dict's volume/amount/bid/ask fields.

    Parameters
    ----------
    source:
        Source name (e.g. ``"sina"``, ``"tencent"``).
    raw_data:
        Dict with keys like ``volume``, ``amount``, ``bid``, ``ask``,
        ``price``, ``open``, ``high``, ``low``, ``last_close``.

    Returns
    -------
    dict
        New dict with normalized values (original dict is not mutated).
    """
    d = dict(raw_data)

    # Volume
    if "volume" in d:
        d["volume"] = int(round(normalize_volume(source, float(d["volume"]))))

    # Amount
    if "amount" in d:
        d["amount"] = normalize_amount(source, float(d["amount"]))

    # Price fields
    for key in ("price", "open", "high", "low", "last_close"):
        if key in d:
            d[key] = normalize_price(source, float(d[key]))

    # Bid/ask levels
    for side in ("bid", "ask"):
        levels = d.get(side)
        if isinstance(levels, list):
            new_levels = []
            for lv in levels:
                if isinstance(lv, dict):
                    lv = dict(lv)  # shallow copy to avoid mutating original
                    if "volume" in lv:
                        lv["volume"] = int(round(normalize_volume(source, float(lv["volume"]))))
                    if "price" in lv:
                        lv["price"] = normalize_price(source, float(lv["price"]))
                new_levels.append(lv)
            d[side] = new_levels

    # Extra fields that may need price scaling
    for key in ("limit_up", "limit_down", "avg_price"):
        if key in d:
            d[key] = normalize_price(source, float(d[key]))

    return d


def normalize_bar(source: str, raw_data: dict[str, Any]) -> dict[str, Any]:
    """Normalize a bar dict's volume/amount/price fields.

    Parameters
    ----------
    source:
        Source name (e.g. ``"kline"``, ``"tencent"``).
    raw_data:
        Dict with keys like ``volume``, ``amount``, ``open``, ``close``,
        ``high``, ``low``.

    Returns
    -------
    dict
        New dict with normalized values (original dict is not mutated).
    """
    d = dict(raw_data)

    # Volume
    if "volume" in d:
        d["volume"] = int(round(normalize_volume(source, float(d["volume"]))))

    # Amount
    if "amount" in d:
        d["amount"] = normalize_amount(source, float(d["amount"]))

    # Price fields
    for key in ("open", "high", "low", "close"):
        if key in d:
            d[key] = normalize_price(source, float(d[key]))

    return d
