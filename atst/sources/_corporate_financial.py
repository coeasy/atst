# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""东财 datacenter / F10 / 公告 / 研报适配器（v2/W7 从 corporate.py 拆分）。"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any
from urllib.parse import quote

from ..diagnostics import WarningCode, record_warning
from ..errors import SourceDeprecated, WebSourceError
from ._corporate_common import (
    _HOSTS,
    VALID_REPORTS,
    _base_path,
    _s,
    is_empty_result,
    report_code_for,
)
from .base import _EastmoneyJson, to_eastmoney_secid
from .base import num_f as _f
from .sources import CORPORATE


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

    #: 服务端单页硬上限。``pageSize`` 超过这个值服务端仍按 500 返回，因此
    #: **必须**让「每页实到行数」与翻页终止判据用同一个数——否则
    #: ``size=1000`` 时每页只回 500 行，``500 < 1000`` 恒成立，循环在第 1 页
    #: 后就 break，静默截断且连 WEB_EASTMONEY_PAGE_LIMIT 告警都不会发。
    MAX_PAGE_SIZE = 500

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
            f"pageSize={max(1, min(size, self.MAX_PAGE_SIZE))}",
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
        allow_empty: bool = False,
    ) -> list[dict[str, Any]]:
        """查询报表并返回原始行（列名即报表字段名）。

        ``report`` 可临时指定报表名；优先于实例属性 ``self.report``，
        避免「运行时临时替换实例属性」的线程不安全写法（P1 #14）。

        v5 PG3：``all_pages=True`` 时按 ``pageNumber`` 循环拉全量——
        终止条件为「服务端报告页数取尽」「短页」或 ``max_pages`` 防御
        上限（耗尽仍满页时发 :class:`UserWarning`）。默认 ``False``
        保持旧的单页语义。

        ``allow_empty``：见 :meth:`_parse_rows_meta`——稀疏报表查不到记录是
        正常回答，不当故障抛。

        ``size`` 超过 :attr:`MAX_PAGE_SIZE` 时会被钳制并记一条告警：服务端
        单页最多给 500 行，钳制后「每页实到行数」才等于翻页终止判据用的
        ``size``，否则全量拉取会在第 1 页后误判短页而静默截断。
        """
        eff_size = max(1, min(int(size), self.MAX_PAGE_SIZE))
        if eff_size != size:
            record_warning(
                WarningCode.WEB_EASTMONEY_PAGE_LIMIT,
                f"东财报表 size={size} 超过服务端单页上限"
                f"{self.MAX_PAGE_SIZE}，已按 {eff_size} 拉取",
                stacklevel=2,
            )
        if not all_pages:
            self.rate_limiter.acquire(self.source_name)
            query = self._path(
                filters=list(filters),
                sort_columns=sort_columns,
                sort_types=sort_types,
                page=page,
                size=eff_size,
                report=report,
            )
            return self.parse_rows(self._get_json(query), allow_empty=allow_empty)
        return self._fetch_rows_all(
            filters=filters,
            sort_columns=sort_columns,
            sort_types=sort_types,
            page=page,
            size=eff_size,
            report=report,
            max_pages=max_pages,
            allow_empty=allow_empty,
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
        allow_empty: bool = False,
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
            rows, meta = self._parse_rows_meta(self._get_json(query), allow_empty=allow_empty)
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
        allow_empty: bool = False,
    ) -> list[dict[str, Any]]:
        """按证券代码过滤查询（``filter=(COLUMN="600519")``）。

        代码由 :func:`report_code_for` 取——它同时挡掉指数与深市股票的撞码
        （``sh000001`` / ``sz000001``），指数按空集处理而不是把平安银行的事实
        安到上证指数头上。
        """
        code = report_code_for(symbol)
        if code is None:
            return []
        return self.fetch_rows(
            filters=[f'{column}="{code}"'],
            sort_columns=sort_columns,
            sort_types=sort_types,
            page=page,
            size=size,
            report=report,
            all_pages=all_pages,
            max_pages=max_pages,
            allow_empty=allow_empty,
        )

    def parse_rows(self, payload: Any, *, allow_empty: bool = False) -> list[dict[str, Any]]:
        """校验 ``success`` 并取出 ``result.data``。"""
        return self._parse_rows_meta(payload, allow_empty=allow_empty)[0]

    def _parse_rows_meta(
        self,
        payload: Any,
        *,
        allow_empty: bool = False,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        """校验 ``success`` 并取出 ``result.data`` 与分页元数据（v5 PG3）。

        ``allow_empty``：服务端用 ``code=9201 返回数据为空`` 表示"这个 filter 下没有
        记录"。对配股一类**天然稀疏**的报表，绝大多数标的本来就没有记录，9201 是
        正常回答而不是故障——把它一律当错误抛，会让"没配过股"变成一次调用失败。
        开关是**显式**的：只有真的能接受空集的调用点才传 True，其余一律照旧抛。
        """
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
            if allow_empty and is_empty_result(payload):
                return [], {"pages": 0, "count": 0}
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
