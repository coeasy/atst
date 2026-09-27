# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""逐笔成交 / 分时成交适配器（§33 扩展）。

接口事实（2026-09 真实抓包验证，atst 自有实现）:

**腾讯分笔明细** ``stock.gtimg.cn/data/index.php``::

    v_detail_data_sh600519=[0,"0/09:25:01/1295.00/0.00/423/54778500/S|1/..."]
    // 外层 JSON 数组: [页码, 记录串]
    // 记录以 "|" 分隔，单条为 "/" 分隔 7 列:
    //   [0]序号 [1]HH:MM:SS [2]成交价(元) [3]价格变动(元)
    //   [4]成交量(手) [5]成交额(元) [6]方向 S=卖 / B=买 / M=中性
    // 实测每页固定 70 条；参数 p 为页码（0 起），c 为 sh/sz 前缀代码。
    // 校验: 423 手 × 1295.00 元 = 547,785 元/手 × 100 = 54,778,500 元 ✓

**东财分时成交** ``push2his.../api/qt/stock/trends2/get``::

    {"data":{"trends":["2026-09-01 09:30,1295.00,1295.00,1295.00,1295.00,
      423,54778500.00,1295.000", ...]}}
    // 逗号分隔 8 列:
    //   [0]"YYYY-MM-DD HH:MM" [1]开盘 [2]收盘 [3]最高 [4]最低
    //   [5]成交量(手) [6]成交额(元) [7]均价(元)
    // ndays=1 当日、iscr=0 不复权；trends 为空表示非交易日或未开盘。

单位约定（全局契约）
--------------------
腾讯分笔与东财 trends2 的成交量均为**手**，进入领域模型前统一 ×100 转「股」；
成交额恒为「元」，价格恒为「元」。

.. note::
   逐笔数据属于高频接口，务必通过 :meth:`fetch_ticks` 的 ``max_pages``
   限制拉取页数，避免对行情站造成压力（默认仅取第 1 页）。
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from typing import Any

from ..domain.models import MinutePoint, Tick
from ..errors import SourceDeprecated
from .base import (
    BaseWebSource,
    _EastmoneyJson,
    to_eastmoney_secid,
    to_tencent_symbol,
)
from .base import (
    num_f as _f,
)
from .base import (
    num_i as _i,
)
from .sources import TICKS, TRENDS

__all__ = [
    "TencentTickSource",
    "EastmoneyTrendsSource",
    "TICKS_PER_PAGE",
]

#: 腾讯分笔每页条数（实测固定值，用于分页估算）
TICKS_PER_PAGE = 70

#: 翻页硬上限（P2 #1）：即便调用方请求无限翻页（``max_pages<=0``），
#: 也最多拉取这么多页，防止依赖服务端空页终止导致的失控翻页。
MAX_TICK_PAGES = 100

#: 腾讯方向标记 → 领域模型 buyorsell（0=买 1=卖 2=中性）
_DIRECTIONS = {"B": 0, "S": 1, "M": 2}

#: ``v_detail_data_sh600519=[...]`` 抽取
_DETAIL_RE = re.compile(r"v_detail_data_([A-Za-z0-9]+)\s*=\s*(\[.*\])", re.S)


# --------------------------------------------------------------------------- #
# 腾讯：逐笔成交明细
# --------------------------------------------------------------------------- #
class TencentTickSource(BaseWebSource):
    """腾讯逐笔成交明细（每页 70 条，可按页翻）。

    Example
    -------
    >>> src = TencentTickSource()
    >>> ticks = src.fetch_ticks("sh600519", max_pages=2)
    >>> ticks[0].price, ticks[0].volume
    """

    BASE = "https://stock.gtimg.cn/data/index.php"

    @property
    def source_name(self) -> str:
        return TICKS

    def build_url(self, symbols: Sequence[str], **kwargs: Any) -> str:
        symbol = to_tencent_symbol(symbols[0])
        page = int(kwargs.get("page", 0))
        return f"{self.BASE}?appn=detail&action=data&c={symbol}&p={page}"

    # -- 拉取 ---------------------------------------------------------------- #
    def fetch_ticks(self, symbol: str, *, max_pages: int = 1) -> list[Tick]:
        """拉取当日逐笔成交。

        Parameters
        ----------
        symbol:
            ``600519`` / ``sh600519`` / ``600519.SH`` 均可。
        max_pages:
            最多翻多少页（每页 :data:`TICKS_PER_PAGE` 条）。默认 1 页；设为 ``0``
            或负数表示一直翻到接口返回空页或**不满一页**为止（未满一页即末页，
            实测每页固定 :data:`TICKS_PER_PAGE` 条）——但受 :data:`MAX_TICK_PAGES`
            （100 页，P2 #1 硬上限）保护，不会无界翻页。

        Returns
        -------
        ``list[Tick]``，按成交时间升序；``volume`` 为股、``price`` 为元。
        """
        symbol = to_tencent_symbol(symbol)
        out: list[Tick] = []
        page = 0
        endless = max_pages <= 0
        while (endless or page < max_pages) and page < MAX_TICK_PAGES:
            url = self.build_url([symbol], page=page)
            text = self._request_text(url, encoding="gbk")
            batch = self.parse_ticks(text, symbol)
            if not batch:
                break
            out.extend(batch)
            page += 1
            #: 已证逆向事实「每页固定 ``TICKS_PER_PAGE`` 条」在这里兑现成终止条件：
            #: 未满一页即末页，不再花一次请求去确认空页（第 26 轮 F-79：这条声明
            #: 此前只被一个断言自己数值的测试读，没有任何执行方）。
            if len(batch) < TICKS_PER_PAGE:
                break
        return out

    # -- 解析 ---------------------------------------------------------------- #
    def parse_ticks(self, text: str, symbol: str = "") -> list[Tick]:
        """解析 ``v_detail_data_xxx=[page,"rec|rec|..."]``（gbk 文本）。"""
        m = _DETAIL_RE.search(text or "")
        if not m:
            raise SourceDeprecated(
                "逐笔明细响应格式变更（未匹配 v_detail_data_ 声明）",
                context={"source": TICKS, "sample": (text or "")[:160]},
            )
        try:
            arr = json.loads(m.group(2))
        except json.JSONDecodeError as exc:
            raise SourceDeprecated(
                "逐笔明细返回非 JSON",
                context={"source": TICKS, "sample": (text or "")[:160]},
                cause=exc,
            ) from exc
        if not isinstance(arr, list) or len(arr) < 2 or not isinstance(arr[1], str):
            # 非交易日 / 盘前：接口返回空串
            return []
        out: list[Tick] = []
        for rec in arr[1].split("|"):
            rec = rec.strip()
            if not rec:
                continue
            cols = rec.split("/")
            if len(cols) < 7:
                continue
            direction = cols[6].strip().upper()
            out.append(
                Tick(
                    time=cols[1].strip(),
                    price=_f(cols[2]),
                    volume=int(round(_f(cols[4]) * 100)),  # 手 → 股
                    num=_i(cols[0]),
                    buyorsell=_DIRECTIONS.get(direction, 2),
                )
            )
        return out

    def parse(self, text: str, symbols: Sequence[str], **kwargs: Any) -> list[Tick]:
        return self.parse_ticks(text, to_tencent_symbol(symbols[0]) if symbols else "")


# --------------------------------------------------------------------------- #
# 东财：当日分时成交
# --------------------------------------------------------------------------- #
class EastmoneyTrendsSource(_EastmoneyJson):
    """东财当日分时成交（1 分钟粒度，含均价）。

    多主机容灾继承 :class:`~atst.web.base._EastmoneyJson`：主站对高频 IP
    有断连风控，按 HOSTS 顺序 failover（push2his → 92.push2his →
    push2delay 延时镜像），失败计数接入下线检测。
    """

    #: push2his 主机池（与 push2 系不同，覆盖基类 HOSTS）
    HOSTS = (
        "https://push2his.eastmoney.com",
        "https://92.push2his.eastmoney.com",
        "https://push2delay.eastmoney.com",
    )
    JSON_LABEL = "东财分时成交"

    @property
    def source_name(self) -> str:
        return TRENDS

    def build_url(self, symbols: Sequence[str], **kwargs: Any) -> str:
        secid = to_eastmoney_secid(symbols[0])
        ndays = int(kwargs.get("ndays", 1))
        return (
            "/api/qt/stock/trends2/get"
            f"?secid={secid}&fields1=f1,f2,f3,f4,f5"
            "&fields2=f51,f52,f53,f54,f55,f56,f57,f58"
            f"&iscr=0&ndays={ndays}"
        )

    def fetch_minutes(self, symbol: str, *, ndays: int = 1) -> list[MinutePoint]:
        """拉取分时成交序列。

        Returns
        -------
        ``list[MinutePoint]``，``volume`` 为股、``amount`` 为元、
        ``price`` 取该分钟收盘价、``avg_price`` 取接口给出的均价。
        """
        self.rate_limiter.acquire(self.source_name)
        payload = self._get_json(self.build_url([symbol], ndays=ndays))
        return self.parse_minutes(payload)

    # -- 解析 ---------------------------------------------------------------- #
    def parse_minutes(self, payload: Any) -> list[MinutePoint]:
        """解析 trends2 响应（已解析的 JSON 或原始文本）。"""
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except json.JSONDecodeError as exc:
                raise SourceDeprecated(
                    "分时成交返回非 JSON",
                    context={"source": TRENDS, "sample": payload[:160]},
                    cause=exc,
                ) from exc
        rows = ((payload or {}).get("data") or {}).get("trends") or []
        out: list[MinutePoint] = []
        for r in rows:
            cols = str(r).split(",")
            if len(cols) < 8:
                continue
            out.append(
                MinutePoint(
                    time=cols[0].strip(),
                    price=_f(cols[2]),  # 该分钟收盘价
                    avg_price=_f(cols[7]),  # 均价
                    volume=int(round(_f(cols[5]) * 100)),  # 手 → 股
                    amount=_f(cols[6]),  # 元
                )
            )
        return out

    def parse(self, text: str, symbols: Sequence[str], **kwargs: Any) -> list[MinutePoint]:
        return self.parse_minutes(text)
