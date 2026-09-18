"""P13 数据源补全离线测试：三大报表 / 治理 / 评级预测。

全部用罐头 JSON，不发起真实 HTTP（FakeHttpClient 按 URL 关键字路由）。

覆盖：
* datacenter-web URL 构造（SECUCODE 大写后缀 / source=WEB&client=WEB / sortColumns）
* 三大报表字段归一化 + 别名容错 + raw 透传
* 分页（短页提前终止）与错误分诊（code=9501 / 非 JSON）
* 治理四报表解析与一致预期聚合
* 门面整链（monkeypatch ``_shared_http``）与白名单扩展
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from tstdx.errors import SourceDeprecated, WebSourceError
from tstdx.web.base import HttpResponse
from tstdx.web.corporate import VALID_REPORTS
from tstdx.web.facade import WebQuoteSession
from tstdx.web.fin_report import (
    F10_REPORTS,
    EastmoneyF10ReportSource,
    to_eastmoney_secucode,
)
from tstdx.web.governance import (
    EastmoneyExecutiveHoldSource,
    EastmoneyOrgProfileSource,
    EastmoneyRatingForecastSource,
    EastmoneyShareholderChangeSource,
)

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _reset_em_blacklist():
    """跨测试隔离：清空东财主机池黑名单（R3 进程级状态）。"""
    from tstdx.web._base_em import reset_em_blacklist

    reset_em_blacklist()
    yield
    reset_em_blacklist()


# --------------------------------------------------------------------------- #
# 假客户端
# --------------------------------------------------------------------------- #
class FakeHttpClient:
    """按 URL 关键字返回预置响应的假客户端；``callable`` 体支持按 URL 路由。"""

    def __init__(self, **bodies: bytes | object) -> None:
        self.bodies = bodies
        self.calls: list[str] = []

    def get(self, url: str, *, headers: Any = None, timeout: float = 5.0) -> HttpResponse:
        self.calls.append(url)
        for key, body in self.bodies.items():
            if isinstance(body, bytes) and key in url:
                return HttpResponse(200, body, {})
        for key, fn in self.bodies.items():
            if callable(fn) and key in url:
                result = fn(url)
                if isinstance(result, HttpResponse):
                    return result
        return HttpResponse(404, b"not found", {})

    def close(self) -> None:
        pass


def _j(obj: dict) -> bytes:
    return json.dumps(obj).encode("utf-8")


# --------------------------------------------------------------------------- #
# 罐头：F10 三大报表（2026-09 抓包裁剪，茅台）
# --------------------------------------------------------------------------- #
BALANCE = _j(
    {
        "result": {
            "pages": 3,
            "count": 29,
            "data": [
                {
                    "SECUCODE": "600519.SH",
                    "SECURITY_CODE": "600519",
                    "SECURITY_NAME_ABBR": "贵州茅台",
                    "REPORT_DATE": "2026-06-30 00:00:00",
                    "REPORT_TYPE": "中报",
                    "NOTICE_DATE": "2026-08-15 00:00:00",
                    "TOTAL_ASSETS": 309050784569.31,
                    "TOTAL_LIABILITIES": 46954432394.95,
                    "TOTAL_EQUITY": 262096352174.36,
                    "MONETARYFUNDS": 53518798979.08,
                    "ACCOUNTS_RECE": "0",
                    "INVENTORY": 61317208371.3,
                    "GOODWILL": None,
                    "FIXED_ASSET": 22220890242.73,
                    "LONG_EQUITY_INVEST": 147198786.54,
                    "MINORITY_EQUITY": 10842757754.86,
                    "UNKNOWN_COL": "保留列",
                },
                {
                    "SECUCODE": "600519.SH",
                    "SECURITY_CODE": "600519",
                    "SECURITY_NAME_ABBR": "贵州茅台",
                    "REPORT_DATE": "2026-03-31 00:00:00",
                    "REPORT_TYPE": "一季报",
                    "NOTICE_DATE": "2026-04-25 00:00:00",
                    "TOTAL_ASSETS": 331020000000.0,
                    "TOTAL_LIABILITIES": 11000000000.0,
                    "TOTAL_EQUITY": 320020000000.0,
                    "MONETARYFUNDS": 155000000000.0,
                    "ACCOUNTS_RECE": "-",
                    "INVENTORY": 61000000000.0,
                },
            ],
        },
        "success": True,
        "message": "ok",
        "code": 0,
    }
)

INCOME = _j(
    {
        "result": {
            "pages": 1,
            "count": 29,
            "data": [
                {
                    "SECUCODE": "600519.SH",
                    "SECURITY_CODE": "600519",
                    "SECURITY_NAME_ABBR": "贵州茅台",
                    "REPORT_DATE": "2026-06-30 00:00:00",
                    "REPORT_TYPE": "中报",
                    "NOTICE_DATE": "2026-08-15 00:00:00",
                    "TOTAL_OPERATE_INCOME": 92278072083.21,
                    "OPERATE_COST": 9473762565.88,
                    "SALE_EXPENSE": 3206308341.53,
                    "MANAGE_EXPENSE": 3635355263.82,
                    "RESEARCH_EXPENSE": 114926219.6,
                    "FINANCE_EXPENSE": -243237716.96,
                    "TOTAL_PROFIT": 61438419177.29,
                    "PARENT_NETPROFIT": 44516880421.86,
                    "MINORITY_INTEREST": 1516450144.92,
                    "BASIC_EPS": 35.57,
                    "DILUTED_EPS": 35.57,
                }
            ],
        },
        "success": True,
        "message": "ok",
        "code": 0,
    }
)

CASHFLOW = _j(
    {
        "result": {
            "pages": 1,
            "count": 29,
            "data": [
                {
                    "SECUCODE": "600519.SH",
                    "SECURITY_CODE": "600519",
                    "SECURITY_NAME_ABBR": "贵州茅台",
                    "REPORT_DATE": "2026-06-30 00:00:00",
                    "REPORT_TYPE": "中报",
                    "NOTICE_DATE": "2026-08-15 00:00:00",
                    "SALES_SERVICES": 98421697395.39,
                    "TOTAL_OPERATE_INFLOW": 109245728301.02,
                    "TOTAL_OPERATE_OUTFLOW": 38554978181.96,
                    "NETCASH_OPERATE": 70690750119.06,
                    "NETCASH_INVEST": 25640543520.6,
                    "NETCASH_FINANCE": -37944297802.12,
                    "CCE_ADD": 58385486034.9,
                    "BEGIN_CASH": 117952629447.72,
                    "END_CASH": 184811095482.62,
                }
            ],
        },
        "success": True,
        "message": "ok",
        "code": 0,
    }
)

BALANCE_EMPTY = _j(
    {
        "result": {"pages": 3, "count": 29, "data": []},
        "success": True,
        "message": "ok",
        "code": 0,
    }
)

ERR_9501 = _j(
    {
        "result": None,
        "success": False,
        "message": "报表配置不存在,RPT_F10_FINANCE_GBALANCE",
        "code": 9501,
    }
)


# --------------------------------------------------------------------------- #
# 罐头：治理 / 评级预测（2026-09 真实抓包裁剪）
# --------------------------------------------------------------------------- #
EXEC_HOLD = _j(
    {
        "version": "v1",
        "result": {
            "pages": 1,
            "count": 2,
            "data": [
                {
                    "SECURITY_CODE": "300750",
                    "SECURITY_NAME_ABBR": "宁德时代",
                    "EXECUTIVE_NAME": "曾毓群",
                    "POSITION": "董事长",
                    "CHANGE_DATE": "2026-08-01",
                    "CHANGE_SHARES": -200000,
                    "CHANGE_PRICE": 210.5,
                    "CHANGE_RATIO": 0.012,
                    "HOLD_SHARES": 56000000,
                    "HOLD_RATIO": 0.326,
                    "RELATIONSHIP": "配偶",
                },
                {
                    "SECURITY_CODE": "300750",
                    "SECURITY_NAME_ABBR": "宁德时代",
                    "EXECUTIVE_NAME": "黄世霖",
                    "POSITION": "总裁",
                    "CHANGE_DATE": "2026-07-15",
                    "CHANGE_SHARES": 0,
                    "HOLD_SHARES": 8800000,
                    "HOLD_RATIO": 0.051,
                },
            ],
        },
        "success": True,
        "message": "ok",
        "code": 0,
    }
)

SHAREHOLDER_CHANGE = _j(
    {
        "version": "v1",
        "result": {
            "pages": 1,
            "count": 1,
            "data": [
                {
                    "SECURITY_CODE": "600519",
                    "SECURITY_NAME_ABBR": "贵州茅台",
                    "HOLDER_NAME": "中国贵州茅台酒厂(集团)有限责任公司",
                    "NOTICE_DATE": "2025-12-30 00:00:00",
                    "END_DATE": "2025-12-26 00:00:00",
                    "TRADE_DATE": "2025-12-26 00:00:00",
                    "START_DATE": "2025-10-21 00:00:00",
                    "CHANGE_NUM": 127.4234,
                    "CHANGE_NUM_SYMBOL": 127.4234,
                    "CHANGE_RATE": -0.0028,
                    "AFTER_HOLDER_NUM": 68128.2935,
                    "HOLD_RATIO": 54.4,
                    "AFTER_CHANGE_RATE": 0.101753917384,
                    "TRADE_AVERAGE_PRICE": 1443.1361,
                    "REAL_PRICE": 1443.1361,
                    "CLOSE_PRICE": 1414.13,
                    "DIRECTION": "增持",
                    "MARKET": "二级市场",
                }
            ],
        },
        "success": True,
        "message": "ok",
        "code": 0,
    }
)

ORG_PROFILE = _j(
    {
        "version": "v1",
        "result": {
            "pages": 1,
            "count": 1,
            "data": [
                {
                    "SECURITY_CODE": "600519",
                    "SECURITY_NAME_ABBR": "贵州茅台",
                    "SECUCODE": "600519.SH",
                    "ORG_NAME": "贵州茅台酒股份有限公司",
                    "ORG_NAME_EN": "Kweichow Moutai Co.,Ltd.",
                    "CHAIRMAN": "陈华",
                    "LEGAL_PERSON": "陈华",
                    "SECRETARY": "余思明",
                    "PRESIDENT": "王莉(代)",
                    "FOUND_DATE": "1999-11-20 00:00:00",
                    "LISTING_DATE": "2001-08-27 00:00:00",
                    "REG_ADDRESS": "贵州省仁怀市茅台镇",
                    "ADDRESS": "贵州省仁怀市茅台镇",
                    "ADDRESS_POSTCODE": "564501",
                    "ORG_TEL": "0851-22386002",
                    "ORG_FAX": "0851-22386193",
                    "ORG_WEB": "www.moutaichina.com",
                    "ORG_EMAIL": "mtdm@moutaichina.com",
                    "MAIN_BUSINESS": "茅台酒及系列酒的生产与销售",
                    "BUSINESS_SCOPE": "茅台酒及系列酒的生产与销售",
                    "EMP_NUM": 34992,
                    "PROVINCE": "贵州",
                    "INDUSTRYCSRC1": "制造业-酒、饮料和精制茶制造业",
                    "EM2016": "食品饮料-饮料-白酒",
                    "ORG_PROFILE": "贵州茅台酒股份有限公司成立于1999年...",
                    "ACTUAL_HOLDER": "贵州省人民政府国有资产监督管理委员会",
                    "ACCOUNTFIRM_NAME": "天健会计师事务所",
                    "LAW_FIRM": "北京市金杜律师事务所",
                    "REG_CAPITAL": 125008.1601,
                    "TRADE_MARKET": "上海证券交易所",
                    "INDEDIRECTORS": "盛雷 郭田勇 王鑫",
                    "SECURITY_TYPE": "上交所主板A股",
                }
            ],
        },
        "success": True,
        "message": "ok",
        "code": 0,
    }
)

ORG_PROFILE_BATCH = _j(
    {
        "version": "v1",
        "result": {
            "pages": 1,
            "count": 2,
            "data": [
                {
                    "SECURITY_CODE": "600519",
                    "SECURITY_NAME_ABBR": "贵州茅台",
                    "CHAIRMAN": "陈华",
                    "EMP_NUM": 34992,
                    "REG_CAPITAL": 125008.1601,
                },
                {
                    "SECURITY_CODE": "300750",
                    "SECURITY_NAME_ABBR": "宁德时代",
                    "CHAIRMAN": "曾毓群",
                    "EMP_NUM": 108000,
                    "REG_CAPITAL": 440300.56,
                },
            ],
        },
        "success": True,
        "message": "ok",
        "code": 0,
    }
)

RATING = _j(
    {
        "version": "v1",
        "result": {
            "pages": 1,
            "count": 1,
            "data": [
                {
                    "SECUCODE": "600519.SH",
                    "SECURITY_CODE": "600519",
                    "SECURITY_NAME_ABBR": "贵州茅台",
                    "RATING_ORG_NUM": 45,
                    "RATING_BUY_NUM": 36,
                    "RATING_ADD_NUM": 9,
                    "RATING_LONG_NUM": 45,
                    "RATING_NEUTRAL_NUM": None,
                    "RATING_REDUCE_NUM": None,
                    "RATING_SALE_NUM": None,
                    "YEAR1": 2025,
                    "YEAR2": 2026,
                    "YEAR3": 2027,
                    "YEAR4": 2028,
                    "YEAR_MARK1": "A",
                    "YEAR_MARK2": "E",
                    "YEAR_MARK3": "E",
                    "YEAR_MARK4": "E",
                    "EPS1": 65.85,
                    "EPS2": 67.66,
                    "EPS3": 71.52,
                    "EPS4": 75.21,
                    "DEC_AIMPRICEMAX": 2030,
                    "DEC_AIMPRICEMIN": 1430,
                    "INDUSTRY_BOARD": "白酒Ⅱ",
                    "CONCEPTINDEX_BOARD": "HS300_,MSCI中国,白酒",
                    "REGION_BOARD": "贵州板块",
                }
            ],
        },
        "success": True,
        "message": "ok",
        "code": 0,
    }
)


def _f10(**bodies: bytes | object) -> EastmoneyF10ReportSource:
    return EastmoneyF10ReportSource(client=FakeHttpClient(**bodies))


# --------------------------------------------------------------------------- #
# 符号 → SECUCODE
# --------------------------------------------------------------------------- #
class TestSecucode:
    @pytest.mark.parametrize(
        ("raw", "expect"),
        [
            ("sh600519", "600519.SH"),
            ("600519.SH", "600519.SH"),
            ("600519", "600519.SH"),
            ("sz000001", "000001.SZ"),
            ("000001.SZ", "000001.SZ"),
            ("300750", "300750.SZ"),
        ],
    )
    def test_variants(self, raw: str, expect: str) -> None:
        assert to_eastmoney_secucode(raw) == expect


# --------------------------------------------------------------------------- #
# URL 构造（datacenter-web 契约）
# --------------------------------------------------------------------------- #
class TestF10Path:
    def test_path_contains_dc_contract(self) -> None:
        """fetch_report 生成的 URL 应包含 datacenter-web 契约参数。"""
        src = EastmoneyF10ReportSource(client=FakeHttpClient(RPT_F10_FINANCE_GBALANCE=BALANCE))
        src.fetch_report("600519", "balance_sheet", page=2, size=20)
        url = src.client.calls[0]
        assert "/api/data/v1/get?" in url
        assert "reportName=RPT_F10_FINANCE_GBALANCE" in url
        assert "columns=ALL" in url
        assert "pageSize=20" in url
        assert "pageNumber=2" in url
        assert "source=WEB" in url
        assert "client=WEB" in url
        assert "sortColumns=REPORT_DATE" in url
        assert "sortTypes=-1" in url
        assert "SECUCODE%3D%22600519.SH%22" in url

    def test_report_date_filter_in_url(self) -> None:
        """report_date 过滤应追加 `` 00:00:00`` 后缀。"""
        src = _f10(RPT_F10_FINANCE_GBALANCE=BALANCE)
        src.fetch_report("600519", "balance_sheet", report_date="2025-12-31")
        url = src.client.calls[0]
        assert "SECUCODE%3D%22600519.SH%22" in url
        assert "REPORT_DATE" in url
        assert "2025-12-31" in url

    def test_page_size_capped(self) -> None:
        """size 超过 PAGE_CAP 时应被截断。"""
        src = _f10(RPT_F10_FINANCE_GBALANCE=BALANCE)
        src.fetch_report("600519", "balance_sheet", size=9999)
        url = src.client.calls[0]
        assert f"pageSize={src.PAGE_CAP}" in url
        assert "pageSize=9999" not in url

    def test_no_symbol_no_filter(self) -> None:
        """symbol 为空 → 不构造 filter。"""
        src = _f10(RPT_F10_FINANCE_GBALANCE=BALANCE)
        src.fetch_report("", "balance_sheet")
        url = src.client.calls[0]
        assert "filter=" not in url

    def test_raw_type_alias_fallback(self) -> None:
        """非别名视作原始报表名。"""
        src = _f10(RPT_F10_FINANCE_GBINF=BALANCE)
        src.fetch_report("600519", "RPT_F10_FINANCE_GBINF")
        url = src.client.calls[0]
        assert "reportName=RPT_F10_FINANCE_GBINF" in url

    def test_reports_table(self) -> None:
        assert set(F10_REPORTS) == {"balance_sheet", "income_sheet", "cash_flow"}


# --------------------------------------------------------------------------- #
# 三大报表解析
# --------------------------------------------------------------------------- #
class TestBalanceSheet:
    def test_normalize_and_alias(self) -> None:
        rows = _f10(RPT_F10_FINANCE_GBALANCE=BALANCE).fetch_balance_sheet("sh600519")
        assert len(rows) == 2
        r = rows[0]
        assert r["code"] == "600519"
        assert r["name"] == "贵州茅台"
        assert r["report_date"] == "2026-06-30 00:00:00"
        assert r["ann_date"] == "2026-08-15 00:00:00"
        assert r["report_type"] == "中报"
        assert r["total_assets"] == pytest.approx(3.0905078456931e11)
        assert r["total_liab"] == pytest.approx(4.695443239495e10)
        assert r["total_equity"] == pytest.approx(2.6209635217436e11)
        # 字符串数值容错（"0" → 0.0；"-" → None）
        assert rows[0]["accounts_recv"] == 0.0
        assert rows[1]["accounts_recv"] is None
        # None 原值 → None（不填 0）
        assert rows[0]["goodwill"] is None
        assert rows[0]["minority_equity"] == pytest.approx(1.084275775486e10)

    def test_raw_passthrough(self) -> None:
        rows = _f10(RPT_F10_FINANCE_GBALANCE=BALANCE).fetch_balance_sheet("600519", raw=True)
        assert rows[0]["raw"]["UNKNOWN_COL"] == "保留列"
        assert rows[0]["raw"]["TOTAL_ASSETS"] == pytest.approx(3.0905078456931e11)

    def test_all_pages_stops_on_short_page(self) -> None:
        def route(url: str) -> HttpResponse:
            if "pageNumber=1" in url:
                return HttpResponse(200, BALANCE, {})
            return HttpResponse(200, BALANCE_EMPTY, {})

        src = _f10(GBALANCE=route)
        rows = src.fetch_balance_sheet("600519", size=2, all_pages=True)
        assert len(rows) == 2
        # 两页请求：满页 → 空页
        assert len(src.client.calls) == 2


class TestIncomeSheet:
    def test_normalize(self) -> None:
        rows = _f10(RPT_F10_FINANCE_GINCOME=INCOME).fetch_income_sheet("600519")
        assert len(rows) == 1
        r = rows[0]
        assert r["report_date"] == "2026-06-30 00:00:00"
        assert r["report_type"] == "中报"
        assert r["operate_income"] == pytest.approx(9.227807208321e10)
        assert r["operate_cost"] == pytest.approx(9.47376256588e9)
        assert r["rd_expense"] == pytest.approx(1.149262196e8)
        # 负值财务费用（利息净收入）保留符号
        assert r["finance_expense"] == pytest.approx(-2.4323771696e8)
        assert r["profit_total"] == pytest.approx(6.143841917729e10)
        assert r["parent_net_profit"] == pytest.approx(4.451688042186e10)
        assert r["basic_eps"] == pytest.approx(35.57)
        assert r["diluted_eps"] == pytest.approx(35.57)
        assert r["minority_profit"] == pytest.approx(1.51645014492e9)


class TestCashFlow:
    def test_normalize(self) -> None:
        rows = _f10(RPT_F10_FINANCE_GCASHFLOW=CASHFLOW).fetch_cash_flow("600519")
        assert len(rows) == 1
        r = rows[0]
        assert r["report_date"] == "2026-06-30 00:00:00"
        assert r["report_type"] == "中报"
        assert r["sale_cash_in"] == pytest.approx(9.842169739539e10)
        assert r["cash_operate_net"] == pytest.approx(7.069075011906e10)
        # 投资净流入为正
        assert r["cash_invest_net"] == pytest.approx(2.56405435206e10)
        # 筹资净流出为负
        assert r["cash_finance_net"] == pytest.approx(-3.794429780212e10)
        assert r["cash_end"] == pytest.approx(1.8481109548262e11)


class TestF10Errors:
    def test_report_not_found_raises_web_source_error(self) -> None:
        with pytest.raises(WebSourceError) as exc:
            _f10(GBALANCE=ERR_9501).fetch_balance_sheet("600519")
        assert "9501" in str(exc.value)

    def test_non_json_raises_deprecated(self) -> None:
        """响应非 JSON（反爬页 / 结构变更）→ SourceDeprecated，触发库内下线检测。"""
        src = EastmoneyF10ReportSource(client=FakeHttpClient(GBALANCE=b"<html>anti-spider</html>"))
        with pytest.raises(SourceDeprecated):
            src.fetch_balance_sheet("600519")

    def test_empty_symbol_market_wide(self) -> None:
        """symbol 为空 → 不构造 SECUCODE 过滤（全市场最新一期）。"""
        src = _f10(RPT_F10_FINANCE_GBALANCE=BALANCE)
        src.fetch_report("", "balance_sheet")
        url = src.client.calls[0]
        assert "filter=" not in url

    def test_fetch_report_raw_passthrough(self) -> None:
        """通用直查：字段原样透传，不做单位翻译/重命名。"""
        rows = _f10(RPT_F10_FINANCE_GBALANCE=BALANCE).fetch_report("600519", "balance_sheet")
        r = rows[0]
        assert r["TOTAL_ASSETS"] == pytest.approx(3.0905078456931e11)
        assert "total_assets" not in r


# --------------------------------------------------------------------------- #
# 治理：董监高持股 / 股东增减持 / 公司概况
# --------------------------------------------------------------------------- #
class TestExecutiveHolds:
    def test_normalize(self) -> None:
        src = EastmoneyExecutiveHoldSource(
            client=FakeHttpClient(RPT_EXECUTIVE_HOLD_DETAILS=EXEC_HOLD)
        )
        rows = src.fetch_executive_holds("300750")
        assert len(rows) == 2
        r = rows[0]
        assert r["code"] == "300750"
        assert r["executive"] == "曾毓群"
        assert r["position"] == "董事长"
        assert r["change_date"] == "2026-08-01"
        assert r["change_shares"] == pytest.approx(-200000)
        assert r["change_price"] == pytest.approx(210.5)
        assert r["hold_ratio"] == pytest.approx(0.326)
        assert r["relationship"] == "配偶"
        # 第二条缺字段 → None（不填 0）
        assert rows[1]["relationship"] == ""
        assert rows[1]["change_price"] is None
        # URL 契约：datacenter-web 用 SECURITY_CODE（6 位纯码）
        url = src.client.calls[0]
        assert "RPT_EXECUTIVE_HOLD_DETAILS" in url
        assert "SECURITY_CODE%3D%22300750%22" in url
        assert "source=WEB" in url


class TestShareholderChanges:
    def test_normalize(self) -> None:
        src = EastmoneyShareholderChangeSource(
            client=FakeHttpClient(RPT_SHARE_HOLDER_INCREASE=SHAREHOLDER_CHANGE)
        )
        rows = src.fetch_shareholder_changes("600519")
        assert len(rows) == 1
        r = rows[0]
        assert r["holder"] == "中国贵州茅台酒厂(集团)有限责任公司"
        assert r["change_date"] == "2025-12-30 00:00:00"
        assert r["end_date"] == "2025-12-26 00:00:00"
        assert r["change_shares"] == pytest.approx(127.4234)
        assert r["change_ratio"] == pytest.approx(-0.0028)
        assert r["change_price"] == pytest.approx(1443.1361)
        assert r["hold_after"] == pytest.approx(68128.2935)
        assert r["hold_ratio_after"] == pytest.approx(54.4)
        assert r["direction"] == "增持"
        assert r["market"] == "二级市场"
        # URL 排序列应为 NOTICE_DATE（非 CHANGE_DATE）
        url = src.client.calls[0]
        assert "sortColumns=NOTICE_DATE" in url
        assert "RPT_SHARE_HOLDER_INCREASE" in url


class TestOrgProfile:
    def test_single(self) -> None:
        src = EastmoneyOrgProfileSource(client=FakeHttpClient(RPT_F10_BASIC_ORGINFO=ORG_PROFILE))
        rec = src.fetch_org_profile("600519")
        assert rec is not None
        assert rec["code"] == "600519"
        assert rec["chairman"] == "陈华"
        assert rec["legal_person"] == "陈华"
        assert rec["secretary"] == "余思明"
        assert rec["president"] == "王莉(代)"
        assert rec["founded_date"] == "1999-11-20 00:00:00"
        assert rec["main_business"] == "茅台酒及系列酒的生产与销售"
        # EMP_NUM 列名（非 EMPLOYEE_NUM）
        assert rec["employee_num"] == pytest.approx(34992)
        assert rec["province"] == "贵州"
        assert rec["actual_holder"] == "贵州省人民政府国有资产监督管理委员会"
        assert rec["official_site"] == "www.moutaichina.com"
        assert rec["reg_capital"] == pytest.approx(125008.1601)
        # URL 报表名应为 RPT_F10_BASIC_ORGINFO（非 RPT_F10_INFO_ORGPROFILE）
        url = src.client.calls[0]
        assert "RPT_F10_BASIC_ORGINFO" in url
        assert "RPT_F10_INFO_ORGPROFILE" not in url

    def test_empty_returns_none(self) -> None:
        empty = _j(
            {
                "result": {"pages": 0, "count": 0, "data": []},
                "success": True,
                "message": "ok",
                "code": 0,
            }
        )
        src = EastmoneyOrgProfileSource(client=FakeHttpClient(RPT_F10_BASIC_ORGINFO=empty))
        assert src.fetch_org_profile("600519") is None

    def test_batch(self) -> None:
        src = EastmoneyOrgProfileSource(
            client=FakeHttpClient(RPT_F10_BASIC_ORGINFO=ORG_PROFILE_BATCH)
        )
        rows = src.fetch_org_profiles(["600519", "300750"])
        assert [r["code"] for r in rows] == ["600519", "300750"]
        assert rows[0]["employee_num"] == pytest.approx(34992)
        assert rows[1]["employee_num"] == pytest.approx(108000)
        assert rows[0]["reg_capital"] == pytest.approx(125008.1601)
        # 批量用 OR 连接的 SECURITY_CODE 过滤
        url = src.client.calls[0]
        assert "600519" in url and "300750" in url


# --------------------------------------------------------------------------- #
# 券商评级与目标价
# --------------------------------------------------------------------------- #
class TestRatingForecast:
    def _src(self) -> EastmoneyRatingForecastSource:
        return EastmoneyRatingForecastSource(client=FakeHttpClient(RPT_WEB_RESPREDICT=RATING))

    def test_normalize(self) -> None:
        rows = self._src().fetch_rating_forecast("600519")
        assert len(rows) == 1
        r = rows[0]
        assert r["code"] == "600519"
        assert r["name"] == "贵州茅台"
        assert r["rating_org_num"] == pytest.approx(45)
        assert r["rating_buy_num"] == pytest.approx(36)
        assert r["rating_add_num"] == pytest.approx(9)
        assert r["rating_long_num"] == pytest.approx(45)
        assert r["rating_neutral_num"] is None
        assert r["eps1"] == pytest.approx(65.85)
        assert r["eps2"] == pytest.approx(67.66)
        assert r["eps3"] == pytest.approx(71.52)
        assert r["eps4"] == pytest.approx(75.21)
        assert r["target_price_max"] == pytest.approx(2030)
        assert r["target_price_min"] == pytest.approx(1430)
        assert r["year1"] == pytest.approx(2025)
        assert r["year_mark1"] == "A"
        assert r["year_mark2"] == "E"
        # URL 排序列应为 RATING_ORG_NUM（非 PUBLISH_DATE）
        self._src().client.calls[0] if self._src().client.calls else ""
        # 重新调用以捕获 URL
        src = self._src()
        src.fetch_rating_forecast("600519")
        assert "sortColumns=RATING_ORG_NUM" in src.client.calls[0]

    def test_sort_columns_passthrough(self) -> None:
        src = self._src()
        src.fetch_rating_forecast("600519", sort_columns="RATING_BUY_NUM")
        assert "sortColumns=RATING_BUY_NUM" in src.client.calls[0]

    def test_market_wide_without_symbol(self) -> None:
        src = self._src()
        rows = src.fetch_rating_forecast("")
        assert len(rows) == 1
        assert "filter=" not in src.client.calls[0]

    def test_consensus_aggregation(self) -> None:
        rec = self._src().fetch_rating_consensus("600519")
        assert rec is not None
        assert rec["code"] == "600519"
        assert rec["name"] == "贵州茅台"
        assert rec["sample"] == 1
        # 目标价区间
        assert rec["target_price_min"] == pytest.approx(1430)
        assert rec["target_price_max"] == pytest.approx(2030)
        # 目标价均值（上下限均值）
        assert rec["target_price_mean"] == pytest.approx((1430 + 2030) / 2, abs=0.01)
        # EPS 均值（4 年均值）
        eps_mean = (65.85 + 67.66 + 71.52 + 75.21) / 4
        assert rec["predict_eps_mean"] == pytest.approx(round(eps_mean, 2), abs=0.01)
        assert rec["rating_org_num"] == pytest.approx(45)
        # 评级分布（仅统计非 None）
        assert rec["rating_dist"]["买入"] == 36
        assert rec["rating_dist"]["增持"] == 9
        assert rec["rating_dist"]["长期"] == 45
        assert "中性" not in rec["rating_dist"]  # None → 不出现

    def test_consensus_empty_returns_none(self) -> None:
        empty = _j(
            {
                "result": {"pages": 0, "count": 0, "data": []},
                "success": True,
                "message": "ok",
                "code": 0,
            }
        )
        src = EastmoneyRatingForecastSource(client=FakeHttpClient(RPT_WEB_RESPREDICT=empty))
        assert src.fetch_rating_consensus("600519") is None


# --------------------------------------------------------------------------- #
# 白名单扩展 + 门面整链
# --------------------------------------------------------------------------- #
class TestWhitelistExtension:
    def test_valid_reports_contains_new_governance_reports(self) -> None:
        for alias, report in {
            "executive_hold": "RPT_EXECUTIVE_HOLD_DETAILS",
            "shareholder_change": "RPT_SHARE_HOLDER_INCREASE",
            "org_profile": "RPT_F10_BASIC_ORGINFO",
            "rating_forecast": "RPT_WEB_RESPREDICT",
        }.items():
            assert VALID_REPORTS[alias] == report

    def test_symbol_filter_keys_cover_new_reports(self) -> None:
        from tstdx.web._facade_mixin_info import CorporateSessionMixin

        for alias in ("executive_hold", "shareholder_change", "org_profile", "rating_forecast"):
            assert CorporateSessionMixin._DC_SYMBOL_FILTER_KEYS[alias] == "SECURITY_CODE"

    def test_dc_reports_exposes_new_aliases(self) -> None:
        from tstdx.web._facade_mixin_info import CorporateSessionMixin

        reports = CorporateSessionMixin.dc_reports()
        assert reports == VALID_REPORTS
        assert "rating_forecast" in reports


class TestFundamentalMixinChain:
    """整链离线验证：monkeypatch 共享 HTTP 客户端，驱动 FundamentalSessionMixin。"""

    @staticmethod
    def _bodies() -> dict[str, bytes | object]:
        return {
            "RPT_F10_FINANCE_GBALANCE": BALANCE,
            "RPT_F10_FINANCE_GINCOME": INCOME,
            "RPT_F10_FINANCE_GCASHFLOW": CASHFLOW,
            "RPT_EXECUTIVE_HOLD_DETAILS": EXEC_HOLD,
            "RPT_SHARE_HOLDER_INCREASE": SHAREHOLDER_CHANGE,
            "RPT_F10_BASIC_ORGINFO": ORG_PROFILE,
            "RPT_WEB_RESPREDICT": RATING,
        }

    def test_all_methods(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import tstdx.web._facade_mixin_fundamental as mix

        monkeypatch.setattr(mix, "_shared_http", lambda: FakeHttpClient(**self._bodies()))

        m = mix.FundamentalSessionMixin
        assert len(m.balance_sheet("600519")) == 2
        assert len(m.income_sheet("600519")) == 1
        assert len(m.cash_flow("600519")) == 1
        assert len(m.fin_report("600519", "balance_sheet")) == 2
        assert len(m.executive_holds("300750")) == 2
        assert len(m.shareholder_changes("600519")) == 1
        assert m.org_profile("600519")["code"] == "600519"
        assert len(m.org_profiles(["600519"])) == 1
        assert len(m.rating_forecast("600519")) == 1
        assert m.rating_consensus("600519")["sample"] == 1

    def test_session_class_exposes_methods(self) -> None:
        """WebQuoteSession 组合后应具备全部新方法（防 Mixin 注册遗漏）。"""
        from tstdx.web.facade import WebQuoteSession

        for name in (
            "balance_sheet",
            "income_sheet",
            "cash_flow",
            "fin_report",
            "executive_holds",
            "shareholder_changes",
            "org_profile",
            "org_profiles",
            "rating_forecast",
            "rating_consensus",
        ):
            assert callable(getattr(WebQuoteSession, name, None)), name


class TestUnifiedApiWiring:
    """WebQuoteSession 门面注册（防方法遗漏）。"""

    def test_all_methods_present(self) -> None:

        for name in (
            "balance_sheet",
            "income_sheet",
            "cash_flow",
            "fin_report",
            "executive_holds",
            "shareholder_changes",
            "org_profile",
            "org_profiles",
            "rating_forecast",
            "rating_consensus",
        ):
            assert callable(getattr(WebQuoteSession, name, None)), name
