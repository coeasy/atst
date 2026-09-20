# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""外盘行情 / 大盘统计适配器（§33 扩展）。

接口事实（2026-09 真实抓包验证，tstdx 自有实现）:

**腾讯外盘** ``qt.gtimg.cn/q=hf_CL,hf_GC,...``（gbk）::

    v_hf_CL="87.75,2.38,87.76,87.77,88.13,86.13,18:58:09,85.76,86.31,0,1,7,
    2026-09-01,纽约原油";
    // 定长 14 列（逗号分隔）:
    //   [0]最新价  [1]涨跌幅%   [2]买价  [3]卖价  [4]最高  [5]最低
    //   [6]时间    [7]昨收      [8]今开  [9]持仓量 [10]买量 [11]卖量
    //   [12]日期   [13]中文名称
    // 实测可用品种共 13 个（见 :data:`GLOBAL_CODES`）；请求不存在的代码时
    // 该代码**静默缺席**（不报错、不占位），解析时以实际返回键为准。

**腾讯大盘统计** ``qt.gtimg.cn/q=s_sh000001``（gbk）::

    v_s_sh000001="1~上证指数~000001~3979.89~-6.41~-0.16~573538949~94430756~
    ~704688.34~ZS~";
    // 定长 12 列（~ 分隔）:
    //   [0]市场标识 [1]名称   [2]代码   [3]最新点位 [4]涨跌额 [5]涨跌幅%
    //   [6]成交量(手) [7]成交额(万元) [8]保留 [9]总市值(亿元)
    //   [10]类型码（ZS=指数）[11]保留

单位归一化
----------
* 外盘：价格与涨跌幅均为原生单位，成交量字段为合约手数（**非股票股数**），
  因此 :class:`Quote.volume` 存原始手数，不做 ×100 换算，并在
  ``extra["volume_unit"]`` 标注 ``"lot"`` 以免误用。
* 大盘统计：成交量单位为**手**（×100 → 股），成交额单位为**万元**
  （×10000 → 元），与全局契约一致。
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any

from ..domain.models import Quote
from ..errors import SourceDeprecated
from .base import BaseWebSource
from .base import num_f as _f
from .base import num_i as _i
from .sources import GLOBAL, MARKET_STAT

__all__ = [
    "TencentGlobalSource",
    "TencentMarketStatSource",
    "GLOBAL_CODES",
    "STAT_SYMBOLS",
]

#: 实测可用的腾讯外盘代码（2026-09 验证）
GLOBAL_CODES: dict[str, str] = {
    "CL": "纽约原油",
    "OIL": "布伦特原油",
    "GC": "纽约黄金",
    "SI": "纽约白银",
    "XAU": "伦敦金（现货黄金）",
    "XAG": "伦敦银（现货白银）",
    "CAD": "伦铜",
    "ZSD": "伦锌",
    "NID": "伦镍",
    "S": "美国大豆",
    "C": "美国玉米",
    "W": "美国小麦",
    "NG": "美国天然气",
}

#: 常用大盘统计代码（``s_`` 前缀由适配器自动补）
STAT_SYMBOLS: dict[str, str] = {
    "sh000001": "上证指数",
    "sz399001": "深证成指",
    "sz399006": "创业板指",
    "sh000300": "沪深300",
    "sh000688": "科创50",
    "sz399905": "中证500",
}

_HF_RE = re.compile(r'v_(hf_[A-Za-z0-9]+)="([^"]*)"')
_S_RE = re.compile(r'v_(s_[A-Za-z0-9]+)="([^"]*)"')


# --------------------------------------------------------------------------- #
# 腾讯：外盘期货 / 现货
# --------------------------------------------------------------------------- #
class TencentGlobalSource(BaseWebSource):
    """腾讯外盘行情（贵金属 / 能源 / 有色 / 农产品）。

    Example
    -------
    >>> src = TencentGlobalSource()
    >>> quotes = src.fetch(["CL", "GC"])
    >>> quotes[0].extra["name"]
    '纽约原油'
    """

    BASE = "https://qt.gtimg.cn/q="

    @property
    def source_name(self) -> str:
        return GLOBAL

    def build_url(self, symbols: Sequence[str], **kwargs: Any) -> str:
        return self.BASE + ",".join(f"hf_{s}" for s in symbols)

    def fetch(self, symbols: Sequence[str], **kwargs: Any) -> list[Quote]:
        """按给定外盘代码拉取行情。

        Parameters
        ----------
        symbols:
            外盘代码，可带 ``hf_`` 前缀也可不带（``CL`` / ``hf_CL`` 等价）。
            传空序列表示拉取 :data:`GLOBAL_CODES` 全部品种。
        """
        codes = [s[3:] if s.lower().startswith("hf_") else s for s in symbols]
        if not codes:
            codes = list(GLOBAL_CODES)
        self._check_deprecated()  # 深审 M13：下线检测前置（旧实现裸 get/post 绕过失败桶）
        url = self.build_url(codes)
        return self.parse(
            self._request_text(url, encoding="gbk", err_msg="外盘行情请求失败"), codes
        )

    def parse(self, text: str, symbols: Sequence[str], **kwargs: Any) -> list[Quote]:
        """解析 ``v_hf_XXX="..."`` 声明（gbk 文本）。"""
        matches = _HF_RE.findall(text or "")
        if not matches:
            raise SourceDeprecated(
                "外盘响应格式变更（未匹配 v_hf_ 声明）",
                context={"source": GLOBAL, "sample": (text or "")[:160]},
            )
        out: list[Quote] = []
        for key, body in matches:
            cols = body.split(",")
            if len(cols) < 14:
                continue
            code = key[3:]  # 去掉 hf_ 前缀
            out.append(
                Quote(
                    code=code,
                    datetime=f"{cols[12]} {cols[6]}",
                    price=_f(cols[0]),
                    last_close=_f(cols[7]),
                    open=_f(cols[8]),
                    high=_f(cols[4]),
                    low=_f(cols[5]),
                    volume=_i(cols[9]),  # 合约持仓量（手），非股票股数
                    amount=0.0,
                    extra={
                        "name": cols[13],
                        "pct_change": _f(cols[1]),
                        "bid_price": _f(cols[2]),
                        "ask_price": _f(cols[3]),
                        "bid_volume": _i(cols[10]),
                        "ask_volume": _i(cols[11]),
                        "open_interest": _i(cols[9]),
                        "date": cols[12],
                        "time": cols[6],
                        "volume_unit": "lot",
                    },
                )
            )
        return out


# --------------------------------------------------------------------------- #
# 腾讯：大盘统计
# --------------------------------------------------------------------------- #
class TencentMarketStatSource(BaseWebSource):
    """腾讯大盘统计（指数点位 + 全市场成交 + 总市值）。

    与个股接口不同，``s_`` 前缀接口直接给出**汇总口径**：成交量（手）、
    成交额（万元）、总市值（亿元），适合做市场温度判断。
    """

    BASE = "https://qt.gtimg.cn/q="

    @property
    def source_name(self) -> str:
        return MARKET_STAT

    def build_url(self, symbols: Sequence[str], **kwargs: Any) -> str:
        return self.BASE + ",".join(f"s_{s}" for s in symbols)

    def fetch(self, symbols: Sequence[str], **kwargs: Any) -> list[Quote]:
        """拉取大盘统计。

        Parameters
        ----------
        symbols:
            指数代码（``sh000001``），可带 ``s_`` 前缀。传空序列表示
            拉取 :data:`STAT_SYMBOLS` 全部主要指数。
        """
        codes = [s[2:] if s.lower().startswith("s_") else s for s in symbols]
        if not codes:
            codes = list(STAT_SYMBOLS)
        self._check_deprecated()  # 深审 M13：下线检测前置（旧实现裸 get/post 绕过失败桶）
        url = self.build_url(codes)
        return self.parse(
            self._request_text(url, encoding="gbk", err_msg="大盘统计请求失败"), codes
        )

    def parse(self, text: str, symbols: Sequence[str], **kwargs: Any) -> list[Quote]:
        """解析 ``v_s_xxx="..."`` 声明（gbk 文本）。"""
        matches = _S_RE.findall(text or "")
        if not matches:
            raise SourceDeprecated(
                "大盘统计响应格式变更（未匹配 v_s_ 声明）",
                context={"source": MARKET_STAT, "sample": (text or "")[:160]},
            )
        out: list[Quote] = []
        for key, body in matches:
            cols = body.split("~")
            if len(cols) < 12:
                continue
            code = key[2:]  # 去掉 s_ 前缀
            # 解析层只做原始解析（volume=手、amount=万元），缩放交由
            # MarketStatNormalizer（与 SourceSpec 对齐），单一事实源、避免双重缩放
            raw = Quote(
                code=code,
                datetime=None,
                price=_f(cols[3]),
                last_close=_f(cols[3]) - _f(cols[4]),
                open=0.0,
                high=0.0,
                low=0.0,
                volume=round(_f(cols[6])),  # 手（原始；Quote.volume 契约为 int）
                amount=_f(cols[7]),  # 万元（原始）
                extra={
                    "name": cols[1],
                    "market_id": cols[0],
                    "change": _f(cols[4]),
                    "pct_change": _f(cols[5]),
                    "total_market_cap_yi": _f(cols[9]),  # 亿元
                    "kind": cols[10],  # ZS=指数
                },
            )
            out.append(self.normalize_quote(raw))
        return out
