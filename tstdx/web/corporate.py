# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""基本面与公司行为适配器（§33 扩展）。

覆盖东财三套后端：``datacenter-web`` 报表族、``np-anotice`` 公告、
``reportapi`` 研报，以及 ``push2`` 的 F10 基础资料。

接口事实（2026-09 真实抓包验证，tstdx 自有实现）:

**datacenter-web 报表族** ``datacenter-web.eastmoney.com/api/data/v1/get``::

    ?reportName=RPT_XXX&columns=ALL&sortColumns=...&sortTypes=-1
    &pageSize=5&pageNumber=1&filter=(SECURITY_CODE%3D%22600519%22)
    &source=WEB&client=WEB

    // 成功:
    {"version":"...","result":{"pages":N,"data":[{...}],"count":M},
     "success":true,"message":"ok","code":0}
    // 失败（报表名拼错时高频出现）:
    {"version":null,"result":null,"success":false,
     "message":"报表配置不存在,RPT_XXX","code":9501}

    // 已验证可用报表:
    //   RPT_LICO_FN_CPD            业绩报表
    //   RPT_F10_EH_FREEHOLDERS     十大流通股东
    //   RPT_HOLDERNUMLATEST        股东户数
    //   RPT_DATA_BLOCKTRADE        大宗交易
    //   RPT_LIFT_STAGE             限售股解禁
    // 实测不可用（报表配置不存在）: RPT_IPO_INFO / RPT_IPO_APPLY /
    //   RPT_MARGIN_DAILY / RPT_MARGIN_DAILYSUM —— 不要写进代码。

**东财 F10 基础资料** ``push2.../api/qt/stock/get``::

    {"data":{"f43":129956,"f47":32664,"f48":4242440861.0,"f49":15894,
      "f57":"600519","f58":"贵州茅台","f60":129952,"f84":1250081601.0,
      "f85":1250081601.0,"f116":1624556045395.56,"f117":1624556045395.56,
      "f127":"白酒Ⅱ","f128":"贵州板块","f162":1825,"f167":647,
      "f173":16.75,"f174":153998,"f175":115101}}
    // ×100 字段: f43 最新价 / f60 昨收 / f162 市盈率(动) / f167 市净率
    //            / f174 52 周最高 / f175 52 周最低
    // 原生单位: f47 成交量(手) / f48 成交额(元) / f49 外盘(手)
    //           f84 总股本 / f85 流通股 / f116 总市值 / f117 流通市值
    //           f127 所属行业 / f128 所属板块 / f173 ROE(%)

**东财公告** ``np-anotice-stock.eastmoney.com/api/security/ann``::

    {"data":{"list":[{"art_code":"AN2026...","title":"贵州茅台:...",
      "notice_date":"2026-08-15 00:00:00","display_time":"2026-08-14 20:41:29",
      "codes":[{"stock_code":"600519","short_name":"贵州茅台"}],
      "columns":[{"column_name":"其他"}]}]}}

**东财研报** ``reportapi.eastmoney.com/report/list``::

    {"hits":46,"size":5,"data":[{"title":"2026年中报点评：...",
      "stockCode":"600519","orgSName":"西南证券","publishDate":"2026-08-21 ...",
      "predictThisYearEps":"69.83","predictThisYearPe":"18.59",
      "emRatingName":"买入","researcher":"朱会振,舒尚立"}]}

.. note::
   基本面接口变更频率高于行情接口。所有适配器在响应结构不匹配时抛
   :class:`~tstdx.errors.SourceDeprecated`，便于上层切换到备用源。
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any
from urllib.parse import quote, urlsplit

from ..diagnostics import WarningCode, record_warning
from ..errors import SourceDeprecated, WebSourceError
from .base import (
    _EastmoneyJson,
    to_eastmoney_secid,
)
from .base import (
    num_f as _f,
)
from .base import (
    num_i as _i,
)
from .sources import CORPORATE

__all__ = [
    "EastmoneyDataCenterSource",
    "EastmoneyProfileSource",
    "EastmoneyNoticeSource",
    "EastmoneyResearchSource",
    "EastmoneyShareholderSource",
    "EastmoneyBlockTradeSource",
    "EastmoneyUnlockSource",
    "EastmoneyPerformanceSource",
    "EastmoneyForecastSource",
    "EastmoneyIpoSource",
    "VALID_REPORTS",
]

#: 实测可用的 datacenter-web 报表名（写死前须重新抓包验证）
VALID_REPORTS: dict[str, str] = {
    "performance": "RPT_LICO_FN_CPD",
    "forecast": "RPT_PUBLIC_OP_NEWPREDICT",
    "free_holders": "RPT_F10_EH_FREEHOLDERS",
    "holder_num": "RPT_HOLDERNUMLATEST",
    "block_trade": "RPT_DATA_BLOCKTRADE",
    "unlock": "RPT_LIFT_STAGE",
    "ipo": "RPTA_APP_IPOAPPLY",
    # 分红送配（2026-09-06 实测可用；字段较稀疏，金额单位以东财报表页为准）
    "dividend": "RPT_SHAREBONUS_DET",
    # ---- 治理 / 评级预测（2026-09-09 扩展，best-effort 映射）----
    # 董监高持股明细（内部人减持 / 套现排查）
    "executive_hold": "RPT_EXECUTIVE_HOLD_DETAILS",
    # 股东增减持（大股东 / 机构持股比例变动）
    "shareholder_change": "RPT_SHARE_HOLDER_INCREASE",
    # 公司概况（法定代表人 / 董事长 / 主营 / 员工数）
    "org_profile": "RPT_F10_BASIC_ORGINFO",
    # 券商评级与目标价（一致预期 EPS / PE / 目标价 / 覆盖机构数）
    "rating_forecast": "RPT_WEB_RESPREDICT",
    # ---- 行业 / 概念指数（2026-09-11 扩展）----
    # 行业指数（板块/概念指标，含涨跌幅/排名）
    "industry_index": "RPT_INDUSTRY_INDEX",
    # 概念指数成分（股票代码→概念映射）
    "concept_index": "RPT_CONCEPT_INDEX",
    # ---- 宏观经济（2026-09-11 扩展）----
    # CPI（全国/城镇/农村，含同比/环比/累计）
    "macro_cpi": "RPT_ECONOMY_CPI",
    # PPI（出厂价同比/环比/累计）
    "macro_ppi": "RPT_ECONOMY_PPI",
    # GDP（GDP总量/三产占比/同比增速）
    "macro_gdp": "RPT_ECONOMY_GDP",
    # ---- 可转债（2026-09-11 扩展）----
    "convertible_bonds": "RPT_BOND_CB_LIST",
    # ---- 北向持股（2026-09-11 P3 扩展）----
    # 沪股通/深股通持仓明细（按个股/日期，含持股数/市值/持股比例）
    "northbound_hold": "RPT_MUTUAL_HOLD",
    # ---- 十大股东（2026-09-11 P3 扩展）----
    # 全部十大股东（含非流通股，区别于 free_holders 仅流通股东）
    "top_holders": "RPT_F10_EH_HOLDERS",
    # ---- 解禁股票（2026-09-11 P3 扩展）----
    # 按个股解禁明细（区别于 unlock=RPT_LIFT_STAGE 按批次解禁）
    "unlock_stocks": "RPT_LIFT_STOCK",
    # ---- 业绩预告旧版（2026-09-11 P3 扩展）----
    # 含 FORECASTCONTENT 文本描述 + CHANGEREASONDSCRPT 原因
    # （区别于 forecast=RPT_PUBLIC_OP_NEWPREDICT 新版结构化字段）
    "earnings_preview": "RPT_PUBLIC_OP_PREDICT",
}

_HOSTS = (
    "https://datacenter-web.eastmoney.com",
    "https://datacenter.eastmoney.com",
)


def _s(value: Any) -> str:
    return "" if value is None else str(value)


def _base_path(base: str) -> str:
    """取 :attr:`BASE` 的路径部分。

    :meth:`_EastmoneyJson._get_json` 只补**主机**，不补 ``BASE``；直接传完整
    ``BASE`` 会拼出「主机+主机」的非法 URL。独立主机源（公告 / 研报）的
    ``fetch_*`` 需自行拼上路径段。
    """
    return urlsplit(base).path or "/"


# --------------------------------------------------------------------------- #
# datacenter-web 报表族基类
# --------------------------------------------------------------------------- #
class EastmoneyDataCenterSource(_EastmoneyJson):
    """东财 datacenter-web 报表查询基类。

    Parameters
    ----------
    report:
        :data:`VALID_REPORTS` 的键或原始报表名。
    """

    BASE = _HOSTS[0]
    #: datacenter-web 主机池（与 push2 系不同，覆盖基类 HOSTS）
    HOSTS = _HOSTS
    JSON_LABEL = "datacenter-web"

    def __init__(self, report: str = "performance", **kwargs: Any) -> None:
        self.report = VALID_REPORTS.get(report, report)
        super().__init__(**kwargs)

    @property
    def source_name(self) -> str:
        return CORPORATE

    def build_url(self, symbols: Sequence[str], **kwargs: Any) -> str:
        return self._path(
            filters=kwargs.get("filters") or [],
            sort_columns=kwargs.get("sort_columns", ""),
            sort_types=kwargs.get("sort_types", "-1"),
            page=int(kwargs.get("page", 1)),
            size=int(kwargs.get("size", 20)),
            columns=kwargs.get("columns", "ALL"),
        )

    def _path(
        self,
        *,
        filters: Sequence[str],
        sort_columns: str,
        sort_types: str,
        page: int,
        size: int,
        columns: str = "ALL",
        report: str | None = None,
    ) -> str:
        parts = [
            f"reportName={report or self.report}",
            f"columns={columns or 'ALL'}",
            f"pageSize={max(1, min(size, 500))}",
            f"pageNumber={max(1, page)}",
            "source=WEB",
            "client=WEB",
        ]
        if filters:
            parts.append("filter=" + quote("(" + ")(".join(filters) + ")"))
        if sort_columns:
            parts.append(f"sortColumns={sort_columns}")
            parts.append(f"sortTypes={sort_types}")
        return "/api/data/v1/get?" + "&".join(parts)

    # -- 取数 ---------------------------------------------------------------- #
    def fetch_rows(
        self,
        *,
        filters: Sequence[str] = (),
        sort_columns: str = "",
        sort_types: str = "-1",
        page: int = 1,
        size: int = 20,
        report: str | None = None,
        all_pages: bool = False,
        max_pages: int = 50,
    ) -> list[dict[str, Any]]:
        """查询报表并返回原始行（列名即报表字段名）。

        ``report`` 可临时指定报表名；优先于实例属性 ``self.report``，
        避免「运行时临时替换实例属性」的线程不安全写法（P1 #14）。

        v5 PG3：``all_pages=True`` 时按 ``pageNumber`` 循环拉全量——
        终止条件为「服务端报告页数取尽」「短页」或 ``max_pages`` 防御
        上限（耗尽仍满页时发 :class:`UserWarning`）。默认 ``False``
        保持旧的单页语义。
        """
        if not all_pages:
            self.rate_limiter.acquire(self.source_name)
            query = self._path(
                filters=list(filters),
                sort_columns=sort_columns,
                sort_types=sort_types,
                page=page,
                size=size,
                report=report,
            )
            return self.parse_rows(self._get_json(query))
        return self._fetch_rows_all(
            filters=filters,
            sort_columns=sort_columns,
            sort_types=sort_types,
            page=page,
            size=size,
            report=report,
            max_pages=max_pages,
        )

    def _fetch_rows_all(
        self,
        *,
        filters: Sequence[str],
        sort_columns: str,
        sort_types: str,
        page: int,
        size: int,
        report: str | None,
        max_pages: int,
    ) -> list[dict[str, Any]]:
        """翻页拉全量（v5 PG3）：pages 元数据优先，短页/防御上限兜底。"""
        out: list[dict[str, Any]] = []
        pages_total: int | None = None
        cur = max(1, int(page))
        for _ in range(max(1, int(max_pages))):
            self.rate_limiter.acquire(self.source_name)
            query = self._path(
                filters=list(filters),
                sort_columns=sort_columns,
                sort_types=sort_types,
                page=cur,
                size=size,
                report=report,
            )
            rows, meta = self._parse_rows_meta(self._get_json(query))
            out.extend(rows)
            if pages_total is None and meta.get("pages"):
                try:
                    pages_total = int(meta["pages"])
                except (TypeError, ValueError):
                    pages_total = None
            if len(rows) < size:
                break  # 短页：取尽
            cur += 1
            if pages_total is not None and cur > pages_total:
                break  # 服务端报告页数取尽
        else:
            record_warning(
                WarningCode.WEB_EASTMONEY_PAGE_LIMIT,
                f"东财报表 {report or self.report} 在 max_pages={max_pages} 页内"
                f"未取尽（已取 {len(out)} 行，最后一页仍满页），结果可能截断",
                stacklevel=2,
            )
        return out

    def fetch_rows_by_code(
        self,
        symbol: str,
        *,
        column: str = "SECURITY_CODE",
        sort_columns: str = "",
        sort_types: str = "-1",
        page: int = 1,
        size: int = 20,
        report: str | None = None,
        all_pages: bool = False,
        max_pages: int = 50,
    ) -> list[dict[str, Any]]:
        """按证券代码过滤查询（``filter=(COLUMN="600519")``）。"""
        from ..domain.symbol import split_symbol

        _, code = split_symbol(symbol)
        return self.fetch_rows(
            filters=[f'{column}="{code}"'],
            sort_columns=sort_columns,
            sort_types=sort_types,
            page=page,
            size=size,
            report=report,
            all_pages=all_pages,
            max_pages=max_pages,
        )

    def parse_rows(self, payload: Any) -> list[dict[str, Any]]:
        """校验 ``success`` 并取出 ``result.data``。"""
        return self._parse_rows_meta(payload)[0]

    def _parse_rows_meta(self, payload: Any) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        """校验 ``success`` 并取出 ``result.data`` 与分页元数据（v5 PG3）。"""
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except json.JSONDecodeError as exc:
                raise SourceDeprecated(
                    "datacenter-web 返回非 JSON",
                    context={"source": CORPORATE, "sample": payload[:160]},
                    cause=exc,
                ) from exc
        if not payload.get("success"):
            raise WebSourceError(
                f"报表查询失败: {payload.get('message')} (code={payload.get('code')})",
                context={"source": CORPORATE, "report": self.report},
            )
        result = payload.get("result") or {}
        rows = result.get("data") or []
        return (
            [r for r in rows if isinstance(r, dict)],
            {"pages": result.get("pages"), "count": result.get("count")},
        )

    def parse(self, text: str, symbols: Sequence[str], **kwargs: Any) -> list[Any]:
        return self.parse_rows(text)


# --------------------------------------------------------------------------- #
# F10 基础资料
# --------------------------------------------------------------------------- #
class EastmoneyProfileSource(_EastmoneyJson):
    """东财 F10 基础资料（估值 / 股本 / 市值 / 52 周区间）。

    主机池与基类一致（push2 → 92.push2 → push2delay），仅覆盖错误标签。
    """

    JSON_LABEL = "F10"

    FIELDS = (
        "f43",
        "f47",
        "f48",
        "f49",
        "f57",
        "f58",
        "f60",
        "f84",
        "f85",
        "f116",
        "f117",
        "f127",
        "f128",
        "f162",
        "f167",
        "f173",
        "f174",
        "f175",
    )

    @property
    def source_name(self) -> str:
        return CORPORATE

    def build_url(self, symbols: Sequence[str], **kwargs: Any) -> str:
        return (
            f"/api/qt/stock/get?secid={to_eastmoney_secid(symbols[0])}"
            f"&fields={','.join(self.FIELDS)}"
        )

    def fetch_profile(self, symbol: str) -> dict[str, Any]:
        """拉取单只证券的基础资料。

        Returns
        -------
        ``{"code","name","price","last_close","volume","amount","outer_volume",
        "total_shares","float_shares","total_market_cap","float_market_cap",
        "industry","board","pe_dynamic","pb","roe","high_52w","low_52w"}``

        其中 ``volume`` / ``outer_volume`` 为股（接口原始「手」已 ×100），
        金额单位为元。
        """
        self.rate_limiter.acquire(self.source_name)
        query = self.build_url([symbol])
        payload = self._get_json(query)
        d = payload.get("data") or {}
        if not d:
            raise SourceDeprecated(
                "F10 基础资料返回空（接口可能已改版）",
                context={"source": CORPORATE, "symbol": symbol},
            )
        return {
            "code": _s(d.get("f57")),
            "name": _s(d.get("f58")),
            "price": _f(d.get("f43")) / 100.0,
            "last_close": _f(d.get("f60")) / 100.0,
            "volume": int(round(_f(d.get("f47")) * 100)),  # 手 → 股
            "amount": _f(d.get("f48")),
            "outer_volume": int(round(_f(d.get("f49")) * 100)),
            "total_shares": _f(d.get("f84")),
            "float_shares": _f(d.get("f85")),
            "total_market_cap": _f(d.get("f116")),
            "float_market_cap": _f(d.get("f117")),
            "industry": _s(d.get("f127")),
            "board": _s(d.get("f128")),
            "pe_dynamic": _f(d.get("f162")) / 100.0,
            "pb": _f(d.get("f167")) / 100.0,
            "roe": _f(d.get("f173")),
            "high_52w": _f(d.get("f174")) / 100.0,
            "low_52w": _f(d.get("f175")) / 100.0,
        }

    def parse(self, text: str, symbols: Sequence[str], **kwargs: Any) -> list[Any]:
        return []


# --------------------------------------------------------------------------- #
# 公告
# --------------------------------------------------------------------------- #
class EastmoneyNoticeSource(_EastmoneyJson):
    """东财上市公司公告。

    v9 Q4-1：由 ``BaseWebSource`` 手写取数改继承 :class:`_EastmoneyJson`，
    复用主机池 failover / 黑名单 / 失败计数（单主机池，行为等价）。
    """

    BASE = "https://np-anotice-stock.eastmoney.com/api/security/ann"
    #: 公告接口为独立主机（非 push2/datacenter 系），单元素主机池
    HOSTS = ("https://np-anotice-stock.eastmoney.com",)
    JSON_LABEL = "公告"

    @property
    def source_name(self) -> str:
        return CORPORATE

    def build_url(self, symbols: Sequence[str], **kwargs: Any) -> str:
        return self.BASE + self._path(
            symbols,
            page=int(kwargs.get("page", 1)),
            size=int(kwargs.get("size", 20)),
            ann_type=kwargs.get("ann_type", "A"),
        )

    def _path(self, symbols: Sequence[str], *, page: int, size: int, ann_type: str) -> str:
        from ..domain.symbol import split_symbol

        codes = ",".join(split_symbol(s)[1] for s in symbols)
        return (
            f"?sr=-1&page_size={max(1, min(size, 500))}"
            f"&page_index={max(1, page)}&ann_type={ann_type}"
            f"&client_source=web&stock_list={codes}&f_node=0"
        )

    def fetch_notices(
        self,
        symbols: Sequence[str],
        *,
        page: int = 1,
        size: int = 20,
        ann_type: str = "A",
    ) -> list[dict[str, Any]]:
        """拉取公告列表。

        Parameters
        ----------
        symbols:
            可传多个代码（接口支持批量）。
        ann_type:
            ``"A"`` 全部 / ``"SHA"`` 沪市 / ``"SZA"`` 深市。

        Returns
        -------
        ``[{"art_code","title","notice_date","display_time",
        "categories","codes"}, ...]``
        """
        symbols = [str(s) for s in symbols]
        if not symbols:
            return []
        # 必须拼上 BASE 的路径段（/api/security/ann）：``_get_json`` 仅补主机
        url = _base_path(self.BASE) + self._path(symbols, page=page, size=size, ann_type=ann_type)
        return self._parse_notices_payload(self._get_json(url))

    def parse_notices(self, text: str) -> list[dict[str, Any]]:
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise SourceDeprecated(
                "公告返回非 JSON",
                context={"source": CORPORATE, "sample": text[:160]},
                cause=exc,
            ) from exc
        return self._parse_notices_payload(payload)

    def _parse_notices_payload(self, payload: Any) -> list[dict[str, Any]]:
        rows = ((payload or {}).get("data") or {}).get("list") or []
        out: list[dict[str, Any]] = []
        for r in rows:
            if not isinstance(r, Mapping):
                continue
            cols = r.get("columns") or []
            codes = r.get("codes") or []
            out.append(
                {
                    "art_code": _s(r.get("art_code")),
                    "title": _s(r.get("title")),
                    "notice_date": _s(r.get("notice_date")),
                    "display_time": _s(r.get("display_time")),
                    "categories": [c.get("column_name") for c in cols if isinstance(c, Mapping)],
                    "codes": [c.get("stock_code") for c in codes if isinstance(c, Mapping)],
                }
            )
        return out

    def parse(self, text: str, symbols: Sequence[str], **kwargs: Any) -> list[Any]:
        return self.parse_notices(text)


# --------------------------------------------------------------------------- #
# 研报
# --------------------------------------------------------------------------- #
class EastmoneyResearchSource(_EastmoneyJson):
    """东财个股研报（评级 / 盈利预测）。

    v9 Q4-1：由 ``BaseWebSource`` 手写取数改继承 :class:`_EastmoneyJson`，
    复用主机池 failover / 黑名单 / 失败计数（单主机池，行为等价）。
    """

    BASE = "https://reportapi.eastmoney.com/report/list"
    #: 研报接口为独立主机（非 push2/datacenter 系），单元素主机池
    HOSTS = ("https://reportapi.eastmoney.com",)
    JSON_LABEL = "研报"

    @property
    def source_name(self) -> str:
        return CORPORATE

    def build_url(self, symbols: Sequence[str], **kwargs: Any) -> str:
        return self.BASE + self._path(
            symbols[0] if symbols else "",
            page=int(kwargs.get("page", 1)),
            size=int(kwargs.get("size", 20)),
            begin=kwargs.get("begin", ""),
            end=kwargs.get("end", ""),
        )

    def _path(self, symbol: str, *, page: int, size: int, begin: str, end: str) -> str:
        from ..domain.symbol import split_symbol

        code = split_symbol(symbol)[1] if symbol else ""
        return (
            f"?pageSize={max(1, min(size, 500))}&beginTime={begin}"
            f"&endTime={end}&pageNo={max(1, page)}&qType=0&code={code}"
            "&industryCode=*&industry=*&rating=*&ratingChange=*"
        )

    def fetch_reports(
        self,
        symbol: str = "",
        *,
        page: int = 1,
        size: int = 20,
        begin: str = "",
        end: str = "",
    ) -> list[dict[str, Any]]:
        """拉取研报列表；``symbol`` 为空表示全市场最新研报。"""
        # 必须拼上 BASE 的路径段（/report/list）：``_get_json`` 仅补主机
        url = _base_path(self.BASE) + self._path(symbol, page=page, size=size, begin=begin, end=end)
        return self._parse_reports_payload(self._get_json(url))

    def parse_reports(self, text: str) -> list[dict[str, Any]]:
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise SourceDeprecated(
                "研报返回非 JSON",
                context={"source": CORPORATE, "sample": text[:160]},
                cause=exc,
            ) from exc
        return self._parse_reports_payload(payload)

    def _parse_reports_payload(self, payload: Any) -> list[dict[str, Any]]:
        rows = payload.get("data") or []
        out: list[dict[str, Any]] = []
        for r in rows:
            if not isinstance(r, Mapping):
                continue
            out.append(
                {
                    "info_code": _s(r.get("infoCode")),
                    "title": _s(r.get("title")),
                    "stock_code": _s(r.get("stockCode")),
                    "stock_name": _s(r.get("stockName")),
                    "org": _s(r.get("orgSName")),
                    "researcher": _s(r.get("researcher")),
                    "publish_date": _s(r.get("publishDate")),
                    "rating": _s(r.get("emRatingName")),
                    "industry": _s(r.get("indvInduName")),
                    "eps_this_year": _f(r.get("predictThisYearEps")),
                    "pe_this_year": _f(r.get("predictThisYearPe")),
                    "eps_next_year": _f(r.get("predictNextYearEps")),
                    "pe_next_year": _f(r.get("predictNextYearPe")),
                }
            )
        return out

    def parse(self, text: str, symbols: Sequence[str], **kwargs: Any) -> list[Any]:
        return self.parse_reports(text)


# --------------------------------------------------------------------------- #
# 十大流通股东 / 股东户数
# --------------------------------------------------------------------------- #
class EastmoneyShareholderSource(EastmoneyDataCenterSource):
    """十大流通股东与股东户数。"""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__("free_holders", **kwargs)

    def fetch_free_holders(
        self,
        symbol: str,
        *,
        page: int = 1,
        size: int = 20,
        end_date: str = "",
    ) -> list[dict[str, Any]]:
        """十大流通股东。

        Parameters
        ----------
        end_date:
            报告期（``2026-06-30``）；空串表示取最近一期。

        Returns
        -------
        ``[{"holder_name","holder_rank","hold_num","hold_ratio",
        "change","holder_type","shares_type","report_date"}, ...]``
        """
        from ..domain.symbol import split_symbol

        # 十大股东报表以 SECUCODE（600519.SH）过滤；北交所后缀为 .BJ（P1 #14）
        market, code = split_symbol(symbol)
        suffix = {"sh": "SH", "sz": "SZ", "bj": "BJ"}.get(market, "SZ")
        secucode = f"{code}.{suffix}"
        filters = [f'SECUCODE="{secucode}"']
        if end_date:
            filters.append(f'END_DATE="{end_date}"')
        rows = self.fetch_rows(
            filters=filters, sort_columns="HOLDER_RANK", sort_types="1", page=page, size=size
        )
        return [
            {
                "holder_name": _s(r.get("HOLDER_NAME")),
                "holder_rank": _i(r.get("HOLDER_RANK")),
                "hold_num": _f(r.get("HOLD_NUM")),
                "hold_ratio": _f(r.get("HOLD_RATIO")),
                "change": _s(r.get("HOLD_NUM_CHANGE")),
                "holder_type": _s(r.get("HOLDER_TYPE")),
                "shares_type": _s(r.get("SHARES_TYPE")),
                "report_date": _s(r.get("END_DATE")),
            }
            for r in rows
        ]

    def fetch_holder_num(
        self,
        symbol: str,
        *,
        page: int = 1,
        size: int = 20,
        all_pages: bool = True,
        max_pages: int = 50,
    ) -> list[dict[str, Any]]:
        """股东户数变动历史（v5 PG3：默认翻页取全量历史）。

        Returns
        -------
        ``[{"holder_num","prev_holder_num","change","change_ratio",
        "end_date","avg_market_cap","avg_hold_num","notice_date"}, ...]``
        """
        # 报表名经参数传递（不再运行时临时替换 self.report——线程不安全，P1 #14）
        rows = self.fetch_rows_by_code(
            symbol,
            sort_columns="HOLD_NOTICE_DATE",
            sort_types="-1",
            page=page,
            size=size,
            report=VALID_REPORTS["holder_num"],
            all_pages=all_pages,
            max_pages=max_pages,
        )
        return [
            {
                "holder_num": _i(r.get("HOLDER_NUM")),
                "prev_holder_num": _i(r.get("PRE_HOLDER_NUM")),
                "change": _i(r.get("HOLDER_NUM_CHANGE")),
                "change_ratio": _f(r.get("HOLDER_NUM_RATIO")),
                "end_date": _s(r.get("END_DATE")),
                "avg_market_cap": _f(r.get("AVG_MARKET_CAP")),
                "avg_hold_num": _f(r.get("AVG_HOLD_NUM")),
                "notice_date": _s(r.get("HOLD_NOTICE_DATE")),
            }
            for r in rows
        ]


# --------------------------------------------------------------------------- #
# 大宗交易
# --------------------------------------------------------------------------- #
class EastmoneyBlockTradeSource(EastmoneyDataCenterSource):
    """大宗交易明细。"""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__("block_trade", **kwargs)

    def fetch_block_trades(
        self,
        *,
        symbol: str = "",
        date: str = "",
        page: int = 1,
        size: int = 20,
        all_pages: bool = True,
        max_pages: int = 50,
    ) -> list[dict[str, Any]]:
        """大宗交易列表（v5 PG3：默认翻页取全量）。

        Parameters
        ----------
        symbol:
            指定证券；空串表示全市场。
        date:
            交易日（``2026-09-01``）；空串表示最近交易日。

        Returns
        -------
        ``[{"code","name","date","price","close_price","premium_ratio",
        "volume","amount","buyer","seller","turnover_rate"}, ...]``
        """
        filters: list[str] = []
        if symbol:
            from ..domain.symbol import split_symbol

            _, code = split_symbol(symbol)
            filters.append(f'SECURITY_CODE="{code}"')
        if date:
            filters.append(f'TRADE_DATE="{date}"')
        rows = self.fetch_rows(
            filters=filters,
            sort_columns="TRADE_DATE",
            sort_types="-1",
            page=page,
            size=size,
            all_pages=all_pages,
            max_pages=max_pages,
        )
        return [
            {
                "code": _s(r.get("SECURITY_CODE")),
                "name": _s(r.get("SECURITY_NAME_ABBR")),
                "date": _s(r.get("TRADE_DATE")),
                "price": _f(r.get("DEAL_PRICE")),
                "close_price": _f(r.get("CLOSE_PRICE")),
                "premium_ratio": _f(r.get("PREMIUM_RATIO")),
                "volume": _f(r.get("DEAL_VOLUME")),
                "amount": _f(r.get("DEAL_AMT")),
                "buyer": _s(r.get("BUYER_NAME")),
                "seller": _s(r.get("SELLER_NAME")),
                "turnover_rate": _f(r.get("TURNOVER_RATE")),
            }
            for r in rows
        ]


# --------------------------------------------------------------------------- #
# 限售股解禁
# --------------------------------------------------------------------------- #
class EastmoneyUnlockSource(EastmoneyDataCenterSource):
    """限售股解禁日程。"""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__("unlock", **kwargs)

    def fetch_unlocks(
        self,
        *,
        symbol: str = "",
        begin: str = "",
        end: str = "",
        page: int = 1,
        size: int = 20,
        all_pages: bool = True,
        max_pages: int = 50,
    ) -> list[dict[str, Any]]:
        """解禁批次列表（v5 PG3：默认翻页取全量）。

        Parameters
        ----------
        begin / end:
            解禁日区间（``YYYY-MM-DD``）；为空表示不限。

        Returns
        -------
        ``[{"code","name","free_date","free_shares","current_free_shares",
        "market_cap","shares_type","batch_holder_num"}, ...]``

        数量单位为**万股**，市值单位为**万元**（接口原始口径，保留不换算
        以便与东财页面对照）。
        """
        filters: list[str] = []
        if symbol:
            from ..domain.symbol import split_symbol

            _, code = split_symbol(symbol)
            filters.append(f'SECURITY_CODE="{code}"')
        if begin:
            filters.append(f"FREE_DATE>='{begin}'")
        if end:
            filters.append(f"FREE_DATE<='{end}'")
        rows = self.fetch_rows(
            filters=filters,
            sort_columns="FREE_DATE",
            sort_types="1",
            page=page,
            size=size,
            all_pages=all_pages,
            max_pages=max_pages,
        )
        return [
            {
                "code": _s(r.get("SECURITY_CODE")),
                "name": _s(r.get("SECURITY_NAME_ABBR")),
                "free_date": _s(r.get("FREE_DATE")),
                "free_shares": _f(r.get("FREE_SHARES")),
                "current_free_shares": _f(r.get("CURRENT_FREE_SHARES")),
                "market_cap": _f(r.get("LIFT_MARKET_CAP")),
                "shares_type": _s(r.get("FREE_SHARES_TYPE")),
                "batch_holder_num": _i(r.get("BATCH_HOLDER_NUM")),
            }
            for r in rows
        ]


# --------------------------------------------------------------------------- #
# 业绩报表
# --------------------------------------------------------------------------- #
class EastmoneyPerformanceSource(EastmoneyDataCenterSource):
    """定期报告业绩指标。"""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__("performance", **kwargs)

    def fetch_performance(
        self,
        *,
        symbol: str = "",
        report_date: str = "",
        page: int = 1,
        size: int = 20,
        all_pages: bool = True,
        max_pages: int = 50,
    ) -> list[dict[str, Any]]:
        """业绩报表（v5 PG3：默认翻页取全量）。

        Parameters
        ----------
        report_date:
            报告期（``2026-06-30``）；空串表示取最近一期。

        Returns
        -------
        ``[{"code","name","report_date","notice_date","eps",
        "deduct_eps","revenue","net_profit","roe","gross_margin",
        "bps","operate_cashflow_ps","yoy_revenue","yoy_net_profit"}, ...]``
        """
        filters: list[str] = []
        if symbol:
            from ..domain.symbol import split_symbol

            _, code = split_symbol(symbol)
            filters.append(f'SECURITY_CODE="{code}"')
        if report_date:
            filters.append(f'REPORTDATE="{report_date}"')
        rows = self.fetch_rows(
            filters=filters,
            sort_columns="NOTICE_DATE",
            sort_types="-1",
            page=page,
            size=size,
            all_pages=all_pages,
            max_pages=max_pages,
        )
        return [
            {
                "code": _s(r.get("SECURITY_CODE")),
                "name": _s(r.get("SECURITY_NAME_ABBR")),
                "report_date": _s(r.get("REPORTDATE")),
                "notice_date": _s(r.get("NOTICE_DATE")),
                "eps": _f(r.get("BASIC_EPS")),
                "deduct_eps": _f(r.get("DEDUCT_BASIC_EPS")),
                "revenue": _f(r.get("TOTAL_OPERATE_INCOME")),
                "net_profit": _f(r.get("PARENT_NETPROFIT")),
                "roe": _f(r.get("WEIGHTAVG_ROE")),
                "gross_margin": _f(r.get("XSMLL")),
                "bps": _f(r.get("BPS")),
                "operate_cashflow_ps": _f(r.get("MGJYXJJE")),
                "yoy_revenue": _f(r.get("SJLTZ")),
                "yoy_net_profit": _f(r.get("YSHZ")),
                "industry": _s(r.get("PUBLISHNAME")),
            }
            for r in rows
        ]


class EastmoneyForecastSource(EastmoneyDataCenterSource):
    """业绩预告（东财 datacenter-web ``RPT_PUBLIC_OP_NEWPREDICT``）。

    覆盖「业绩预告 / 快报」日历：预告类型（预增 / 预减 / 扭亏 / 首亏 …）、
    预告净利润区间、变动幅度区间、预告内容文本与去年同期基数。
    """

    def __init__(self, **kwargs: Any) -> None:
        super().__init__("forecast", **kwargs)

    def fetch_forecast(
        self,
        *,
        symbol: str = "",
        report_date: str = "",
        page: int = 1,
        size: int = 20,
        all_pages: bool = True,
        max_pages: int = 50,
    ) -> list[dict[str, Any]]:
        """业绩预告列表（v5 PG3：默认翻页取全量）。

        Parameters
        ----------
        symbol:
            6 位代码（``600519``）或带前缀（``sh600519``）；空串表示全市场。
        report_date:
            报告期（``2026-06-30``）；空串表示取最新一期。

        Returns
        -------
        ``[{"code","name","notice_date","report_date","predict_type",
        "predict_finance","profit_lower","profit_upper","amp_lower",
        "amp_upper","content","preyear_same_period","forecast_state",
        "market"}, ...]``

        金额单位为元、变动幅度为百分数（``50.0`` 即 +50.00%）。
        """
        filters: list[str] = []
        if symbol:
            from ..domain.symbol import split_symbol

            _, code = split_symbol(symbol)
            filters.append(f'SECURITY_CODE="{code}"')
        if report_date:
            # 报表侧报告期字段为完整字面值，含 00:00:00
            rd = report_date if "00:00:00" in report_date else f"{report_date} 00:00:00"
            filters.append(f'REPORT_DATE="{rd}"')
        rows = self.fetch_rows(
            filters=filters,
            sort_columns="NOTICE_DATE",
            sort_types="-1",
            page=page,
            size=size,
            all_pages=all_pages,
            max_pages=max_pages,
        )
        out: list[dict[str, Any]] = []
        for r in rows:
            out.append(
                {
                    "code": _s(r.get("SECURITY_CODE")),
                    "name": _s(r.get("SECURITY_NAME_ABBR")),
                    "notice_date": _s(r.get("NOTICE_DATE")),
                    "report_date": _s(r.get("REPORT_DATE")),
                    "predict_type": _s(r.get("PREDICT_TYPE")),
                    "predict_finance": _s(r.get("PREDICT_FINANCE")),
                    "profit_lower": _f(r.get("PREDICT_AMT_LOWER")),
                    "profit_upper": _f(r.get("PREDICT_AMT_UPPER")),
                    "amp_lower": _f(r.get("ADD_AMP_LOWER")),
                    "amp_upper": _f(r.get("ADD_AMP_UPPER")),
                    "content": _s(r.get("PREDICT_CONTENT")),
                    "preyear_same_period": _f(r.get("PREYEAR_SAME_PERIOD")),
                    "forecast_state": _s(r.get("FORECAST_STATE")),
                    "market": _s(r.get("TRADE_MARKET")),
                }
            )
        return out


class EastmoneyIpoSource(EastmoneyDataCenterSource):
    """IPO 申购日历（东财 datacenter-web ``RPTA_APP_IPOAPPLY``）。

    全市场新股/新债申购列表：申购日期与代码、发行价（未定价时给出
    预测价）、发行量、网上申购上限与顶格市值、中签号/缴款/上市日期、
    行业 PE 与发行后 PE。覆盖「今日申购 → 待上市 → 已上市」全生命周期
    字段，可按申购日过滤（「今日申购」视图）。
    """

    def __init__(self, **kwargs: Any) -> None:
        super().__init__("ipo", **kwargs)

    def fetch_ipo(
        self,
        *,
        apply_date: str = "",
        page: int = 1,
        size: int = 20,
        all_pages: bool = True,
        max_pages: int = 50,
    ) -> list[dict[str, Any]]:
        """IPO 申购日历（按申购日期降序；v5 PG3：默认翻页取全量）。

        Parameters
        ----------
        apply_date:
            申购日 ``YYYY-MM-DD``；非空时只取该日（「今日申购」视图），
            空串表示全量倒序分页。
        page, size:
            分页参数（datacenter 报表分页）。

        Returns
        -------
        ``[{"code","name","secucode","apply_code","apply_date",
        "listing_date","ballot_num_date","ballot_pay_date",
        "online_issue_date","trade_market","market_type","issue_price",
        "predict_issue_price","issue_num","online_issue_num",
        "online_apply_upper","top_apply_marketcap","industry_pe",
        "after_issue_pe","bvps","issue_way"}, ...]``

        单位：``issue_num`` 万股；``online_issue_num`` / ``online_apply_upper``
        股；``top_apply_marketcap`` 万元；价格为元。未定价/未上市字段为
        ``None``（与报表侧 ``null`` 一致，不填充为 0）。
        """
        filters: list[str] = []
        if apply_date:
            rd = apply_date if "00:00:00" in apply_date else f"{apply_date} 00:00:00"
            filters.append(f'APPLY_DATE="{rd}"')
        rows = self.fetch_rows(
            filters=filters,
            sort_columns="APPLY_DATE",
            sort_types="-1",
            page=page,
            size=size,
            all_pages=all_pages,
            max_pages=max_pages,
        )
        return [self._normalize(r) for r in rows]

    @staticmethod
    def _normalize(r: Mapping[str, Any]) -> dict[str, Any]:
        def _opt(value: Any) -> float | None:
            # null 语义保留：0 与「未定」不同（预测价 0 = 未定价）
            return _f(value) if value is not None else None

        return {
            "code": _s(r.get("SECURITY_CODE")),
            "name": _s(r.get("SECURITY_NAME")),
            "secucode": _s(r.get("SECUCODE")),
            "apply_code": _s(r.get("APPLY_CODE")),
            "apply_date": _s(r.get("APPLY_DATE"))[:10],
            "listing_date": _s(r.get("LISTING_DATE"))[:10],
            "ballot_num_date": _s(r.get("BALLOT_NUM_DATE"))[:10],
            "ballot_pay_date": _s(r.get("BALLOT_PAY_DATE"))[:10],
            "online_issue_date": _s(r.get("ONLINE_ISSUE_DATE"))[:10],
            "trade_market": _s(r.get("TRADE_MARKET")),
            "market_type": _s(r.get("MARKET_TYPE")),
            "issue_price": _opt(r.get("ISSUE_PRICE")),
            "predict_issue_price": _opt(r.get("PREDICT_ISSUE_PRICE")),
            "issue_num": _opt(r.get("ISSUE_NUM")),
            "online_issue_num": _opt(r.get("ONLINE_ISSUE_NUM")),
            "online_apply_upper": _opt(r.get("ONLINE_APPLY_UPPER")),
            "top_apply_marketcap": _opt(r.get("TOP_APPLY_MARKETCAP")),
            "industry_pe": _opt(r.get("INDUSTRY_PE")),
            "after_issue_pe": _opt(r.get("AFTER_ISSUE_PE")),
            "bvps": _opt(r.get("BVPS")),
            "issue_way": _s(r.get("ISSUE_WAY")),
        }
