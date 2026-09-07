# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""龙虎榜（Dragon-Tiger Board）适配器（§33 扩展）。

接口事实（2026-09-01 真实抓包验证，tstdx 自有实现）::

    GET https://datacenter-web.eastmoney.com/api/data/v1/get
        ?reportName=RPT_DMSK_TS_STOCKNEW&columns=ALL
        &filter=(TRADE_DATE='2026-09-01 00:00:00')
        &pageSize=50&pageNumber=1&source=WEB&client=WEB

    // 成功:
    {"version":"...","result":{"pages":N,"data":[{ "SECURITY_CODE":"000001",
      "SECUCODE":"000001.SZ","TRADE_DATE":"2026-09-01 00:00:00",
      "SECURITY_NAME_ABBR":"平安银行","CLOSE_PRICE":11.92,"CHANGE_RATE":1.7065,
      "TURNOVERRATE":0.7849,"SUPERDEAL_INFLOW":481167664,
      "SUPERDEAL_OUTFLOW":484846848,"PRIME_INFLOW":56309200,
      "BIGDEAL_INFLOW":421304528,"BIGDEAL_OUTFLOW":361316144,
      "ORG_PARTICIPATE":0.473204,"PARTICIPATE_TYPE":"3","RANK":186,
      "RANK_UP":298,"FOCUS":90,"TOTALSCORE":77.94636268, ... }]},
     "success":true,"code":0}

    // 失败（日期格式错 / 当日无榜）:
    {"version":null,"result":null,"success":false,
     "message":"返回数据为空","code":9201}

关键口径（tstdx 全局契约：price=元，volume=股，amount=元）:

* ``CLOSE_PRICE`` / ``PRIME_COST`` 等已是**元**。
* ``SUPERDEAL_*`` / ``BIGDEAL_*`` / ``PRIME_INFLOW`` 资金额已是**元**；
  本源直接透传，不经过 ``normalize_quote``。
* **主力净流入 = 超大单净 + 大单净**，经样本核验：
  ``56309200 = (481167664-484846848) + (421304528-361316144)``，
  与 ``PRIME_INFLOW`` 一致，故 ``prime_net`` 直接取 ``PRIME_INFLOW``。
* ``TRADE_DATE`` 过滤必须用完整 ``'YYYY-MM-DD 00:00:00'`` 字面值，
  只传 ``'YYYY-MM-DD'`` 会返回 code=9201 空数据。
* 机构席位 / 营业部买卖子报表（``RPT_DMSK_TS_QSJJ`` 等）经探测已失效
  （code=9501 报表配置不存在），本源仅覆盖「每日上榜个股汇总」这一最常用视图。

.. note::
    龙虎榜不属于逐笔/分时行情，输出为结构化事件（dict 列表），不入 ``Quote``。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .base import num_f as _f
from .base import num_i as _i
from .corporate import EastmoneyDataCenterSource
from .sources import LHB

__all__ = [
    "EastmoneyTopListSource",
    "REASON_LABELS",
    "parse_lhb_row",
]

_REPORT = "RPT_DMSK_TS_STOCKNEW"

#: 上榜原因码 → 人类可读标签（best-effort，东财未公开稳定枚举，未知码回退空串）
REASON_LABELS: dict[str, str] = {
    "1": "日涨幅偏离值达7%",
    "2": "日振幅值达15%",
    "3": "日换手率达20%",
    "4": "连续三个交易日涨跌幅偏离值累计达20%",
    "5": "连续三个交易日收盘价涨幅偏离值累计达20%",
    "6": "连续三个交易日收盘价跌幅偏离值累计达20%",
    "7": "无价格涨跌幅限制的证券",
    "8": "日均换手率达20%且收盘价跌幅偏离值达15%",
}


def _s(value: Any) -> str:
    return "" if value is None else str(value)


def parse_lhb_row(raw: Mapping[str, Any]) -> dict[str, Any]:
    """把一条原始龙虎榜记录映射为 tstdx 统一字段。

    资金额单位均为「元」，与全局契约一致；不调用 ``normalize_quote``。
    """
    super_in = _f(raw.get("SUPERDEAL_INFLOW"))
    super_out = _f(raw.get("SUPERDEAL_OUTFLOW"))
    big_in = _f(raw.get("BIGDEAL_INFLOW"))
    big_out = _f(raw.get("BIGDEAL_OUTFLOW"))
    reason_code = _s(raw.get("PARTICIPATE_TYPE"))
    trade_date = _s(raw.get("TRADE_DATE"))
    if trade_date and len(trade_date) >= 10:
        trade_date = trade_date[:10]
    return {
        "code": _s(raw.get("SECURITY_CODE")),
        "secucode": _s(raw.get("SECUCODE")),
        "name": _s(raw.get("SECURITY_NAME_ABBR")),
        "trade_date": trade_date,
        # 行情
        "close": _f(raw.get("CLOSE_PRICE")),
        "change_pct": _f(raw.get("CHANGE_RATE")),
        "turnover_pct": _f(raw.get("TURNOVERRATE")),
        "pe_dynamic": _f(raw.get("PE_DYNAMIC")),
        # 资金（元）
        "super_inflow": super_in,
        "super_outflow": super_out,
        "super_net": super_in - super_out,
        "big_inflow": big_in,
        "big_outflow": big_out,
        "big_net": big_in - big_out,
        "prime_net": _f(raw.get("PRIME_INFLOW")),  # 主力净流入（元）
        "buy_super_ratio": _f(raw.get("BUY_SUPERDEAL_RATIO")),
        "buy_big_ratio": _f(raw.get("BUY_BIGDEAL_RATIO")),
        # 机构 / 上榜质量
        "org_participate": _f(raw.get("ORG_PARTICIPATE")),
        "reason_code": reason_code,
        "reason": REASON_LABELS.get(reason_code, ""),
        "rank": _i(raw.get("RANK")),
        "rank_up": _i(raw.get("RANK_UP")),
        "focus": _i(raw.get("FOCUS")),
        "total_score": _f(raw.get("TOTALSCORE")),
        "prime_cost": _f(raw.get("PRIME_COST")),
        "prime_cost_20d": _f(raw.get("PRIME_COST_20DAYS")),
        "prime_cost_60d": _f(raw.get("PRIME_COST_60DAYS")),
        # 上榜频次占比
        "appear_ratio": _f(raw.get("RATIO")),
        "appear_ratio_3d": _f(raw.get("RATIO_3DAYS")),
        "appear_ratio_50d": _f(raw.get("RATIO_50DAYS")),
        # 元数据
        "market_code": _s(raw.get("TRADE_MARKET_CODE")),
        "security_type": _s(raw.get("SECURITY_TYPE_CODE")),
        "listing_state": _s(raw.get("LISTING_STATE")),
    }


class EastmoneyTopListSource(EastmoneyDataCenterSource):
    """龙虎榜每日上榜个股（东财 datacenter-web ``RPT_DMSK_TS_STOCKNEW``）。

    .. note::
        复用了 :class:`~tstdx.web.corporate.EastmoneyDataCenterSource` 的
        datacenter-web 取数 / 失败校验逻辑，仅替换报表名与解析层。
    """

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(report=_REPORT, **kwargs)

    @property
    def source_name(self) -> str:
        return LHB

    # -- 取数 ---------------------------------------------------------------- #
    def fetch_lhb(
        self,
        date: str | None = None,
        *,
        symbol: str | None = None,
        page: int = 1,
        size: int = 50,
    ) -> list[dict[str, Any]]:
        """查询龙虎榜。

        Parameters
        ----------
        date:
            交易日 ``YYYY-MM-DD``（默认取最新一期）。
        symbol:
            按证券代码过滤（如 ``sh600519`` / ``600519``），可选。
        page, size:
            分页参数。
        """
        filters: list[str] = []
        if date:
            filters.append(f"TRADE_DATE='{date} 00:00:00'")
        if symbol:
            from ..domain.symbol import split_symbol

            _, code = split_symbol(symbol)
            filters.append(f'SECURITY_CODE="{code}"')
        rows = self.fetch_rows(
            filters=filters,
            sort_columns="RANK" if not symbol else "",
            sort_types="1",
            page=page,
            size=size,
        )
        return [parse_lhb_row(r) for r in rows]

    def fetch_lhb_by_code(self, symbol: str, *, date: str | None = None) -> list[dict[str, Any]]:
        """查询单只标的的龙虎榜记录（可选限定日期）。"""
        return self.fetch_lhb(date=date, symbol=symbol, size=50)
