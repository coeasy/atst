# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""资金面适配器：通用排行 / 资金流 / 涨跌停池 / 沪深港通（§33 扩展）。

接口事实（2026-09 真实抓包验证，atst 自有实现）:

**东财通用排行** ``push2.../api/qt/clist/get``::

    {"data":{"total":5555,"diff":[{"f2":26.0,"f3":290.98,"f5":779923,
      "f6":1978792312.0,"f12":"601123","f13":1,"f14":"N马矿"}, ...]}}
    // fs 决定市场：m:0+t:6(深主板) m:0+t:80(创业板) m:1+t:2(沪主板)
    //              m:1+t:23(科创板) b:MK0021..24(ETF) m:90+t:2(行业板块)
    // fid 决定排序：f3 涨跌幅 / f6 成交额 / f8 换手 / f62 主力净流入
    // f13 市场位：1=上交所、0=深交所/北交所；f5 成交量单位为**手**

**东财实时资金流** ``push2.../api/qt/ulist.np/get``::

    {"data":{"diff":[{"f12":"600519","f62":220531760.0,"f66":168048960.0,
      "f69":396,"f72":52482800.0,"f75":124,"f78":-220434016.0,"f81":-520,
      "f84":-97733.0,"f87":0,"f184":520}]}}
    // 恒等式（已用实盘数据验证）:
    //   主力净额 f62 = 超大单 f66 + 大单 f72
    //   主力 f62 + 中单 f78 + 小单 f84 ≈ 0
    // **必须显式 fltt=2**：不加时 f2/f3/f184 等均为 ×100 整数
    //   （129956 / 0 / 520），加上后才返回原生浮点 1299.56 / 0.0 / 5.2。
    // atst 一律走 fltt=2，避免 100 倍误差。

**东财历史资金流** ``push2.../api/qt/stock/fflow/kline/get``::

    {"data":{"klines":["2026-09-01,220531760.0,-97733.0,-220434016.0,
      52482800.0,168048960.0"]}}
    // 行 = 日期,主力净额,小单净额,中单净额,大单净额,超大单净额（元）
    // 注意顺序与实时接口不同：此处第 3 列是**小单**、第 4 列是**中单**。

**东财涨跌停池** ``push2ex.../getTopic{ZT|DT|ZB}Pool``::

    {"data":{"tc":83,"qdate":20260901,"pool":[{"c":"000635","m":0,
      "n":"英 力 特","p":7220,"zdp":10.06,"amount":22285339,"ltsz":...,
      "hs":0.84,"lbc":1,"fbt":92500,"lbt":92500,"fund":100684257,
      "zbc":0,"hybk":"化学原料","zttj":{"days":1,"ct":1}}]}}
    // p 为价格 ×1000 整数（7220 → 7.22 元，已用腾讯行情交叉验证）；
    // fbt/lbt 为 HHMMSS 整数（92500 → 09:25:00）；非交易日返回空 pool。

**东财沪深港通** ``push2.../api/qt/kamt/get``::

    {"data":{"hk2sh":{"status":3,"dayNetAmtIn":0.0,"dayAmtRemain":0.0,
      "dayAmtThreshold":5200000.0,"date":"09-01","date2":"2026-09-01"},...}}
    // 金额单位为**万元**；status=3 表示当日已收盘

.. warning::
   东财主站对高频 IP 有断连风控（``RemoteDisconnected``）。走 push2 主机的东财
   适配器继承 :class:`_EastmoneyJson`，按 ``HOSTS`` 顺序 failover
   （push2 → 92.push2 → push2delay）；push2ex 端点（涨跌停池 / 盘中异动）
   继承 :class:`BaseWebSource`。

.. note::
   P2-4c（F14）：新浪资金流历史源 ``SinaFundFlowSource`` 已按 Provider 归组迁至
   :mod:`atst.sources.sina.fundflow`；本模块保留同名 re-export 兼容旧 import
   （``providers/sina.py`` 的 ``fund_flow`` 绑定与 ``atst.sources`` 惰性导出表
   仍经旧路径取类）。本模块现在只承载东财系适配器。
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

from ..domain.models import Quote
from ..errors import SourceDeprecated
from ._scale import MILLI_INT_DIVISOR
from .base import (
    BaseWebSource,
    _EastmoneyJson,
    to_eastmoney_secid,
)
from .base import (
    num_f as _f,
)
from .base import (
    num_i as _i,
)
from .sina.fundflow import SinaFundFlowSource  # P2-4c/F14：re-export 兼容旧 import
from .sources import (
    FUND_FLOW,
    LIMIT_POOL,
    NORTHBOUND,
    RANK,
    STOCK_CHANGES,
)

__all__ = [
    "EastmoneyRankSource",
    "EastmoneyFundFlowSource",
    "EastmoneyLimitPoolSource",
    "EastmoneyStockChangesSource",
    "EastmoneyNorthboundSource",
    "SinaFundFlowSource",
    "MARKET_FILTERS",
    "SORT_FIELDS",
    "RANK_FIELD_NAMES",
    "FUND_FLOW_FIELDS",
]

# --------------------------------------------------------------------------- #
# 常量表
# --------------------------------------------------------------------------- #
#: 东财公开静态 ``ut`` token（2026-09 真实抓包；前端 JS 公开常量，非用户凭据）。
#: push2ex 的涨跌停池 / 盘中异动端点使用本 token。
_EM_PUSH2EX_UT = "7eea3edcaed734bea9cbfc24409ed989"
#: 东财公开静态 ``ut`` token（同上来源 / 日期）；push2 ``kamt`` 沪深港通端点使用。
#: 与 push2ex 的 token 不同，不能合并为单一常量，否则破坏取数行为。
_EM_PUSH2_UT = "b2884a393a59ad64002292a3e90d46a5"

#: 市场 / 板块过滤表达式（clist 的 ``fs`` 参数）
MARKET_FILTERS: dict[str, str] = {
    "all_a": "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23",  # 沪深 A 股（默认）
    "sh_a": "m:1+t:2,m:1+t:23",  # 沪 A（含科创）
    "sz_a": "m:0+t:6,m:0+t:80",  # 深 A（含创业板）
    "main": "m:0+t:6,m:1+t:2",  # 沪深主板
    "gem": "m:0+t:80",  # 创业板
    "star": "m:1+t:23",  # 科创板
    "bse": "m:0+t:81+s:2048",  # 北交所
    "etf": "b:MK0021,b:MK0022,b:MK0023,b:MK0024",  # 全部 ETF
    "index": "b:MK0010",  # 主要指数
    "industry": "m:90+t:2",  # 行业板块
    "concept": "m:90+t:3",  # 概念板块
    "region": "m:90+t:1",  # 地域板块
}

#: 排序字段（clist 的 ``fid`` 参数）
SORT_FIELDS: dict[str, str] = {
    "change_pct": "f3",  # 涨跌幅
    "price": "f2",  # 最新价
    "volume": "f5",  # 成交量
    "amount": "f6",  # 成交额
    "amplitude": "f7",  # 振幅
    "turnover": "f8",  # 换手率
    "main_net": "f62",  # 主力净流入
    "main_ratio": "f184",  # 主力净占比
}

#: 排行字段 → 语义名（用于 :meth:`EastmoneyRankSource.fetch_rows` 输出）
RANK_FIELD_NAMES: dict[str, str] = {
    "f2": "price",
    "f3": "change_pct",
    "f4": "change",
    "f5": "volume",
    "f6": "amount",
    "f7": "amplitude",
    "f8": "turnover",
    "f9": "pe_dynamic",
    "f10": "volume_ratio",
    "f12": "code",
    "f13": "market",
    "f14": "name",
    "f15": "high",
    "f16": "low",
    "f17": "open",
    "f18": "last_close",
    "f20": "total_market_cap",
    "f21": "float_market_cap",
    "f62": "main_net",
    "f66": "super_large_net",
    "f72": "large_net",
    "f78": "medium_net",
    "f84": "small_net",
    "f184": "main_net_ratio",
}

#: 实时资金流默认字段集
FUND_FLOW_FIELDS = (
    "f12",
    "f14",
    "f2",
    "f3",
    "f62",
    "f184",
    "f66",
    "f69",
    "f72",
    "f75",
    "f78",
    "f81",
    "f84",
    "f87",
)

#: 历史资金流行字段顺序（与实时接口**不同**，注意小单/中单位次）
_FLOW_KLINE_KEYS = ("main_net", "small_net", "medium_net", "large_net", "super_large_net")

#: 涨跌停池类型 → 端点
_POOL_ENDPOINTS = {"zt": "ZTPool", "dt": "DTPool", "zb": "ZBPool"}

#: 沪深港通方向语义
_NB_DIRECTIONS = {
    "hk2sh": "沪股通（北向）",
    "hk2sz": "深股通（北向）",
    "sh2hk": "港股通（沪）",
    "sz2hk": "港股通（深）",
}


def _symbol_from_market(code: str, market: int) -> str:
    """f13 市场位 → atst symbol；板块（90/105 等）与非标市场直接回原码。

    ``f13``: 1=上交所、0=深交所/北交所、90=板块（BKxxxx）。

    W9: ``f13=0`` 不再一律标 ``sz``——北交所标的（代码段 4/8/92 开头）按
    :mod:`atst.domain.symbol` 的 BJ 段定义（单一事实源）标 ``bj``。
    """
    if market == 1:
        return f"sh{code}"
    if market == 0:
        return f"bj{code}" if _is_bj_code(code) else f"sz{code}"
    return code


def _is_bj_code(code: str) -> bool:
    """北交所代码段判定：委托 :func:`atst.domain.symbol.parse_symbol` 推断。

    只信任「推断为 bj 且代码主体一致」的结果，避免把 ``000001``（上证指数
    惯例归沪）这类沪深段代码误判为北交所。
    """
    from ..domain.symbol import Market, parse_symbol

    if not code or not code.isdigit() or len(code) != 6:
        return False
    try:
        sym = parse_symbol(code)
    except Exception:  # noqa: BLE001  无法解析的代码按非北交所处理
        return False
    return sym.market == Market.BJ and sym.code == code


# --------------------------------------------------------------------------- #
# 东财 JSON 基类（主机池 failover）
# --------------------------------------------------------------------------- #
# P1 #12: canonical 实现已上收至 :class:`atst.sources.base._EastmoneyJson`
# （failover + 失败计数 + UTF-8 解码一处实现）；此处保留同名引用，
# 既有 ``from .fundflow import _EastmoneyJson`` 的用法不受影响。


# --------------------------------------------------------------------------- #
# 通用排行
# --------------------------------------------------------------------------- #
class EastmoneyRankSource(_EastmoneyJson):
    """东财通用排行（个股 / ETF / 指数 / 板块，支持资金流排序）。

    Example
    -------
    >>> src = EastmoneyRankSource()
    >>> rows = src.fetch_rows(market="all_a", sort="change_pct", limit=10)
    >>> rows[0]["name"], rows[0]["change_pct"]
    """

    FIELDS = (
        "f2",
        "f3",
        "f4",
        "f5",
        "f6",
        "f7",
        "f8",
        "f10",
        "f12",
        "f13",
        "f14",
        "f15",
        "f16",
        "f17",
        "f18",
    )

    @property
    def source_name(self) -> str:
        return RANK

    def build_url(self, symbols: Sequence[str], **kwargs: Any) -> str:
        return self._path(
            market=kwargs.get("market", "all_a"),
            sort=kwargs.get("sort", "change_pct"),
            limit=int(kwargs.get("limit", 20)),
            page=int(kwargs.get("page", 1)),
            ascending=bool(kwargs.get("ascending", False)),
        )

    def _path(
        self,
        *,
        market: str,
        sort: str,
        limit: int,
        page: int,
        ascending: bool,
        fields: Sequence[str] | None = None,
        extra_fields: Sequence[str] = (),
    ) -> str:
        fs = MARKET_FILTERS.get(market, market)
        fid = SORT_FIELDS.get(sort, sort)
        cols = list(fields) if fields else list(self.FIELDS)
        cols.extend(f for f in extra_fields if f not in cols)
        # W1: po 为排序方向——升序=0、降序=1（东财 clist 接口语义；库内
        # boards.py build_url 亦固定 po=1 配合「按涨跌幅降序」）。旧实现
        # 「po=1 if ascending else 1」两分支恒 1，排序方向参数完全失效。
        return (
            "/api/qt/clist/get?pn={pn}&pz={pz}&po={po}&np=1&fltt=2&invt=2"
            "&fid={fid}&fs={fs}&fields={fields}".format(
                pn=page,
                pz=limit,
                po=0 if ascending else 1,
                fid=fid,
                fs=fs,
                fields=",".join(cols),
            )
        )

    # -- 拉取 ---------------------------------------------------------------- #
    def fetch_rows(
        self,
        market: str = "all_a",
        *,
        sort: str = "change_pct",
        limit: int = 20,
        page: int = 1,
        ascending: bool = False,
        extra_fields: Sequence[str] = (),
    ) -> list[dict[str, Any]]:
        """排行原始行（字段已按 :data:`RANK_FIELD_NAMES` 重命名）。

        额外保留 ``symbol``（``sh600519`` 形式）与 ``total``（结果总数）。
        """
        # P12 优化：按资金流排序时自动附带资金流字段——否则服务端按 f62
        # 排序正确、但响应缺该字段，行值静默为 None（实测陷阱）。
        moneyflow_sorts = {"main_net", "main_ratio"}
        extra = tuple(extra_fields)
        if sort in moneyflow_sorts:
            extra = extra + tuple(
                f for f in ("f62", "f184", "f66", "f72", "f78", "f84") if f not in extra
            )
        self.rate_limiter.acquire(self.source_name)
        payload = self._get_json(
            self._path(
                market=market,
                sort=sort,
                limit=limit,
                page=page,
                ascending=ascending,
                extra_fields=extra,
            )
        )
        data = payload.get("data") or {}
        total = _i(data.get("total")) if isinstance(data, Mapping) else 0
        out: list[dict[str, Any]] = []
        for r in self._diff(payload):
            row: dict[str, Any] = {}
            for k, v in r.items():
                row[RANK_FIELD_NAMES.get(k, k)] = v
            code = str(r.get("f12", ""))
            row["symbol"] = _symbol_from_market(code, _i(r.get("f13")))
            if isinstance(row.get("price"), str):
                row["price"] = _f(row["price"])
            if isinstance(row.get("change_pct"), str):
                row["change_pct"] = _f(row["change_pct"])
            row["total"] = total
            out.append(row)
        return out

    def fetch(
        self,
        symbols: Sequence[str] = (),
        *,
        market: str = "all_a",
        sort: str = "change_pct",
        limit: int = 20,
        page: int = 1,
        **kwargs: Any,
    ) -> list[Quote]:
        """排行结果转 :class:`Quote` 列表（已归一化到股 / 元）。"""
        rows = self.fetch_rows(market, sort=sort, limit=limit, page=page)
        out: list[Quote] = []
        for r in rows:
            q = Quote(
                code=str(r.get("symbol", "")),
                price=_f(r.get("price")),
                last_close=_f(r.get("last_close")),
                open=_f(r.get("open")),
                high=_f(r.get("high")),
                low=_f(r.get("low")),
                volume=_i(r.get("volume")),  # 原始单位为手
                amount=_f(r.get("amount")),
                extra={
                    "name": r.get("name", ""),
                    "change_pct": _f(r.get("change_pct")),
                    "turnover": _f(r.get("turnover")),
                    "volume_ratio": _f(r.get("volume_ratio")),
                    "main_net": _f(r.get("main_net")),
                    "main_net_ratio": _f(r.get("main_net_ratio")),
                },
            )
            out.append(self.normalize_quote(q))
        return out


# --------------------------------------------------------------------------- #
# 资金流
# --------------------------------------------------------------------------- #
class EastmoneyFundFlowSource(_EastmoneyJson):
    """东财资金流：实时（ulist.np）与历史（fflow/kline）。"""

    @property
    def source_name(self) -> str:
        return FUND_FLOW

    def build_url(self, symbols: Sequence[str], **kwargs: Any) -> str:
        if kwargs.get("mode") == "history":
            return self._history_path(
                symbols[0], period=kwargs.get("period", "day"), count=int(kwargs.get("count", 10))
            )
        return self._realtime_path(symbols)

    @staticmethod
    def _realtime_path(symbols: Sequence[str]) -> str:
        # fltt=2 → f2/f3/f184 等为原生浮点（元 / %）；缺省 fltt 时为 ×100 整数
        secids = ",".join(to_eastmoney_secid(s) for s in symbols)
        return "/api/qt/ulist.np/get?fltt=2&secids={s}&fields={f}".format(
            s=secids, f=",".join(FUND_FLOW_FIELDS)
        )

    @staticmethod
    def _history_path(symbol: str, *, period: str, count: int) -> str:
        try:
            klt = {
                "5min": 5,
                "15min": 15,
                "30min": 30,
                "60min": 60,
                "day": 101,
                "week": 102,
                "month": 103,
            }[period]
        except KeyError:
            # 深审 M25：未知周期显式报错——旧实现静默回退 101（day），
            # 把分钟级资金流请求偷换成日线数据（错误数据比报错更危险）。
            raise ValueError(
                f"未知资金流周期 {period!r}；可选: 5min/15min/30min/60min/day/week/month"
            ) from None
        return (
            f"/api/qt/stock/fflow/kline/get?secid={to_eastmoney_secid(symbol)}"
            "&fields1=f1,f2,f3,f7&fields2=f51,f52,f53,f54,f55,f56"
            f"&klt={klt}&lmt={count}"
        )

    # -- 实时 ---------------------------------------------------------------- #
    def fetch_flow(self, symbols: Sequence[str]) -> list[dict[str, Any]]:
        """多个标的的当日实时资金流。

        Returns
        -------
        ``[{"code","name","price","change_pct","main_net",
        "main_net_ratio","super_large_net","super_large_ratio",
        "large_net","large_ratio","medium_net","medium_ratio",
        "small_net","small_ratio"}, ...]``

        金额单位为元、价格单位为元、``*_ratio`` 为百分数（``5.2`` 即 5.20%）。
        已通过 ``fltt=2`` 取原生浮点，无需再做 ×100 还原。
        """
        symbols = list(symbols)
        if not symbols:
            return []
        self.rate_limiter.acquire(self.source_name)
        payload = self._get_json(self._realtime_path(symbols))
        out: list[dict[str, Any]] = []
        for r in self._diff(payload):
            out.append(
                {
                    "code": r.get("f12", ""),
                    "name": r.get("f14", ""),
                    "price": _f(r.get("f2")),
                    "change_pct": _f(r.get("f3")),
                    "main_net": _f(r.get("f62")),
                    "main_net_ratio": _f(r.get("f184")),
                    "super_large_net": _f(r.get("f66")),
                    "super_large_ratio": _f(r.get("f69")),
                    "large_net": _f(r.get("f72")),
                    "large_ratio": _f(r.get("f75")),
                    "medium_net": _f(r.get("f78")),
                    "medium_ratio": _f(r.get("f81")),
                    "small_net": _f(r.get("f84")),
                    "small_ratio": _f(r.get("f87")),
                }
            )
        return out

    # -- 历史 ---------------------------------------------------------------- #
    def fetch_history(
        self, symbol: str, *, period: str = "day", count: int = 10
    ) -> list[dict[str, Any]]:
        """历史资金流序列（按时间升序）。

        Returns
        -------
        ``[{"date","main_net","small_net","medium_net",
        "large_net","super_large_net"}, ...]``（元）
        """
        self.rate_limiter.acquire(self.source_name)
        payload = self._get_json(self._history_path(symbol, period=period, count=count))
        return self.parse_history(payload)

    def parse_history(self, payload: Any) -> list[dict[str, Any]]:
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except json.JSONDecodeError as exc:
                raise SourceDeprecated(
                    "历史资金流返回非 JSON",
                    context={"source": FUND_FLOW, "sample": payload[:160]},
                    cause=exc,
                ) from exc
        rows = ((payload or {}).get("data") or {}).get("klines") or []
        out: list[dict[str, Any]] = []
        for line in rows:
            cols = str(line).split(",")
            if len(cols) < 6:
                continue
            item: dict[str, Any] = {"date": cols[0].strip()}
            for key, raw in zip(_FLOW_KLINE_KEYS, cols[1:6], strict=False):
                item[key] = _f(raw)
            out.append(item)
        return out


# --------------------------------------------------------------------------- #
# 涨跌停 / 炸板池
# --------------------------------------------------------------------------- #
class EastmoneyLimitPoolSource(BaseWebSource):
    """东财涨停 / 跌停 / 炸板池（push2ex 端点，无主机池容灾）。

    S-10/F22 复核结论（保持 :class:`BaseWebSource`，不继承 :class:`_EastmoneyJson`）：
    1. push2ex 的 ``getTopic{ZT|DT|ZB}Pool`` 仅部署在 ``push2ex.eastmoney.com``；
       本仓登记的 push2 备站（92.push2 / push2delay）是**不同服务**，并不提供
       push2ex 端点，把它们塞进 ``HOSTS`` 只会制造必败的假 failover。
    2. ``_EastmoneyJson._get_json`` 直接调 ``client.get``，绕开
       :meth:`~atst.sources._base_core.BaseWebSource._request_http` 的限流、
       指数退避重试与 403/429 细分；切换会丢失现有容错语义。
    3. :meth:`parse_pool` 公开接受 JSON 文本，已有调用方/测试直传文本串；
       改成接 dict 会破坏该 seam。
    因此维持现状：仍走 ``_request_text``（已含失败桶 / 下线检测 / 重试）。
    """

    BASE = "https://push2ex.eastmoney.com"
    #: 池类型 → 中文名
    POOL_NAMES = {"zt": "涨停板", "dt": "跌停板", "zb": "炸板"}

    #: fbt/lbt 为 HHMMSS 整数，需补零到 6 位
    @property
    def source_name(self) -> str:
        return LIMIT_POOL

    def build_url(self, symbols: Sequence[str], **kwargs: Any) -> str:
        pool = kwargs.get("pool", "zt")
        return self._path(
            pool,
            date=kwargs.get("date", ""),
            page=int(kwargs.get("page", 1)),
            limit=int(kwargs.get("limit", 20)),
        )

    def _path(self, pool: str, *, date: str, page: int, limit: int) -> str:
        ep = _POOL_ENDPOINTS.get(pool, _POOL_ENDPOINTS["zt"])
        return (
            f"{self.BASE}/getTopic{ep}"
            f"?ut={_EM_PUSH2EX_UT}&dpt=wz.ztzt"
            f"&Pageindex={max(page - 1, 0)}&pagesize={limit}&sort=fbt%3Aasc&date={date}"
        )

    def fetch_pool(
        self, pool: str = "zt", *, date: str = "", page: int = 1, limit: int = 20
    ) -> list[dict[str, Any]]:
        """拉取涨跌停 / 炸板池。

        Parameters
        ----------
        pool:
            ``"zt"`` 涨停 / ``"dt"`` 跌停 / ``"zb"`` 炸板。
        date:
            ``YYYYMMDD``；空串表示最新交易日。

        Returns
        -------
        ``[{"code","symbol","name","price","change_pct","amount",
        "float_market_cap","turnover","limit_up_days","first_seal_time",
        "last_seal_time","seal_fund","broken_times","industry","total"}, ...]``
        """
        self._check_deprecated()  # 深审 M13：下线检测前置（旧实现裸 get/post 绕过失败桶）
        url = self._path(pool, date=date, page=page, limit=limit)
        return self.parse_pool(
            self._request_text(url, encoding="utf-8", err_msg="涨跌停池请求失败")
        )

    def parse_pool(self, text: str) -> list[dict[str, Any]]:
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            self._record_failure(parse=True)
            raise SourceDeprecated(
                "涨跌停池返回非 JSON",
                context={"source": LIMIT_POOL, "sample": text[:160]},
                cause=exc,
            ) from exc
        data = payload.get("data") or {}
        total = _i(data.get("tc"))
        rows = data.get("pool") or []
        out: list[dict[str, Any]] = []
        for r in rows:
            if not isinstance(r, Mapping):
                continue
            code = str(r.get("c", ""))
            mkt = _i(r.get("m"))
            zttj_raw = r.get("zttj")
            zttj: dict[str, Any] = dict(zttj_raw) if isinstance(zttj_raw, dict) else {}
            out.append(
                {
                    "code": code,
                    "symbol": _symbol_from_market(code, mkt),
                    "name": _clean_name(r.get("n", "")),
                    "price": _f(r.get("p")) / MILLI_INT_DIVISOR,  # ×1000 整数 → 元
                    "change_pct": _f(r.get("zdp")),
                    "amount": _f(r.get("amount")),
                    "float_market_cap": _f(r.get("ltsz")),
                    "total_market_cap": _f(r.get("tshare")),
                    "turnover": _f(r.get("hs")),
                    "limit_up_days": _i(r.get("lbc")),
                    "first_seal_time": _hhmmss(r.get("fbt")),
                    "last_seal_time": _hhmmss(r.get("lbt")),
                    "seal_fund": _f(r.get("fund")),
                    "broken_times": _i(r.get("zbc")),
                    "industry": r.get("hybk", ""),
                    "stat_days": _i(zttj.get("days")),
                    "stat_count": _i(zttj.get("ct")),
                    "total": total,
                }
            )
        return out

    def parse(self, text: str, symbols: Sequence[str], **kwargs: Any) -> list[Quote]:
        return []


# --------------------------------------------------------------------------- #
# 盘中异动池
# --------------------------------------------------------------------------- #
class EastmoneyStockChangesSource(BaseWebSource):
    """盘中异动池（push2ex ``getAllStockChanges``，20 类异动枚举）。

    接口事实（2026-09 实测验证 + 页面公开枚举提取）::

        GET https://push2ex.eastmoney.com/getAllStockChanges
            ?type={8201,8202,...}&ut={公开静态 token}
            &pageindex={0-based}&pagesize={n}&dpt=wzchanges
        响应: {"data": {"tc": 总数, "allstock": [
            {"tm": HHMMSS 整数, "c": 代码, "m": 市场, "n": 名称,
             "t": 异动类型, "i": "逗号分隔指标串"}]}}

    ``i`` 指标串含义随异动类型不同（如火箭发射为 ``涨速,现价,涨幅``），
    本库不强行语义归一，拆为 ``metrics: list[float]``（无法解析的段丢弃），
    语义解读留给调用方。
    """

    BASE = "https://push2ex.eastmoney.com"

    #: 异动类型 → 中文名（页面公开枚举，2026-09 实测提取）
    CHANGE_TYPES: dict[int, str] = {
        8193: "大笔买入",
        8194: "大笔卖出",
        8195: "拖拉机买",
        8196: "拖拉机卖",
        8201: "火箭发射",
        8202: "快速反弹",
        8203: "高台跳水",
        8204: "加速下跌",
        8205: "买入撤单",
        8206: "卖出撤单",
        8207: "竞价上涨",
        8208: "竞价下跌",
        8209: "高开5日线",
        8210: "低开5日线",
        8211: "向上缺口",
        8212: "向下缺口",
        8213: "60日新高",
        8214: "60日新低",
        8215: "60日大幅上涨",
        8216: "60日大幅下跌",
    }

    @property
    def source_name(self) -> str:
        return STOCK_CHANGES

    def build_url(self, symbols: Sequence[str], **kwargs: Any) -> str:
        return self._path(
            tuple(kwargs.get("types") or ()),
            page=int(kwargs.get("page", 1)),
            size=int(kwargs.get("size", 50)),
        )

    def _path(self, types: tuple[int, ...], *, page: int, size: int) -> str:
        type_param = (
            ",".join(str(int(t)) for t in types)
            if types
            else ",".join(str(t) for t in self.CHANGE_TYPES)
        )
        return (
            f"{self.BASE}/getAllStockChanges?type={type_param}"
            f"&ut={_EM_PUSH2EX_UT}"
            f"&pageindex={max(page - 1, 0)}&pagesize={max(1, min(size, 500))}"
            f"&dpt=wzchanges"
        )

    def fetch_changes(
        self,
        types: Sequence[int] = (),
        *,
        page: int = 1,
        size: int = 50,
    ) -> list[dict[str, Any]]:
        """盘中异动列表（按时间倒序，交易时段实时滚动）。

        Parameters
        ----------
        types:
            异动类型（:data:`CHANGE_TYPES` 的键）；空 = 全部 20 类。
        page, size:
            分页（pageindex 0-based）。

        Returns
        -------
        ``[{"time": "11:19:05", "code": "600186", "market": 1,
        "name": "莲花控股", "change_type": 8202, "change_name": "快速反弹",
        "metrics": [0.0258, 11.93, 0.0258], "total": 2091}, ...]``

        非交易时段返回 ``[]``（``allstock`` 为空是合法状态）。
        """
        self._check_deprecated()  # 深审 M13：下线检测前置（旧实现裸 get/post 绕过失败桶）
        self.rate_limiter.acquire(self.source_name)
        url = self._path(
            tuple(int(t) for t in types if int(t) in self.CHANGE_TYPES), page=page, size=size
        )
        return self.parse_changes(
            self._request_text(url, encoding="utf-8", err_msg="盘中异动请求失败")
        )

    def parse_changes(self, text: str) -> list[dict[str, Any]]:
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            self._record_failure(parse=True)
            raise SourceDeprecated(
                "盘中异动返回非 JSON",
                context={"source": STOCK_CHANGES, "sample": text[:160]},
                cause=exc,
            ) from exc
        data = payload.get("data") or {}
        total = _i(data.get("tc"))
        rows = data.get("allstock") or []
        out: list[dict[str, Any]] = []
        for r in rows:
            if not isinstance(r, Mapping):
                continue
            tm = _i(r.get("tm"))
            ct = _i(r.get("t"))
            metrics: list[float] = []
            for part in str(r.get("i", "")).split(","):
                with_valid = part.strip()
                if not with_valid or with_valid == "-":
                    continue
                try:
                    metrics.append(float(with_valid))
                except ValueError:
                    continue
            out.append(
                {
                    "time": f"{tm // 10000:02d}:{tm // 100 % 100:02d}:{tm % 100:02d}",
                    "code": str(r.get("c", "")),
                    "market": _i(r.get("m")),
                    "name": str(r.get("n", "")),
                    "change_type": ct,
                    "change_name": self.CHANGE_TYPES.get(ct, ""),
                    "metrics": metrics,
                    "total": total,
                }
            )
        return out

    def parse(self, text: str, symbols: Sequence[str], **kwargs: Any) -> list[Quote]:
        return []


# --------------------------------------------------------------------------- #
# 沪深港通
# --------------------------------------------------------------------------- #
class EastmoneyNorthboundSource(_EastmoneyJson):
    """东财沪深港通资金（单位万元）。"""

    @property
    def source_name(self) -> str:
        return NORTHBOUND

    def build_url(self, symbols: Sequence[str], **kwargs: Any) -> str:
        return (
            "/api/qt/kamt/get?fields1=f1,f2,f3,f4"
            "&fields2=f51,f52,f53,f54,f55,f56"
            f"&ut={_EM_PUSH2_UT}"
        )

    def fetch_northbound(self) -> list[dict[str, Any]]:
        """四个方向的当日资金与额度（金额单位：万元）。

        Returns
        -------
        ``[{"direction","name","net_amount","amount_remain",
        "amount_threshold","date","status","closed"}, ...]``
        """
        self.rate_limiter.acquire(self.source_name)
        payload = self._get_json(self.build_url([]))
        data = payload.get("data") or {}
        out: list[dict[str, Any]] = []
        for key, node in (data or {}).items():
            if not isinstance(node, Mapping):
                continue
            out.append(
                {
                    "direction": key,
                    "name": _NB_DIRECTIONS.get(key, key),
                    "net_amount": _f(node.get("dayNetAmtIn")),
                    "amount_remain": _f(node.get("dayAmtRemain")),
                    "amount_threshold": _f(node.get("dayAmtThreshold")),
                    "date": node.get("date2", node.get("date", "")),
                    "status": _i(node.get("status")),
                    "closed": _i(node.get("status")) == 3,
                }
            )
        return out


# --------------------------------------------------------------------------- #
# 新浪资金流历史（S5）
# --------------------------------------------------------------------------- #
# 工具
# --------------------------------------------------------------------------- #
def _clean_name(value: Any) -> str:
    """东财池名称含全角间隔（``英 力 特``），去掉内部空格。"""
    return str(value or "").replace(" ", "")


def _hhmmss(value: Any) -> str:
    """``92500`` / ``143000`` → ``"09:25:00"``。"""
    try:
        n = int(float(value))
    except (TypeError, ValueError):
        return ""
    s = f"{n:06d}"
    return f"{s[:2]}:{s[2:4]}:{s[4:6]}"
