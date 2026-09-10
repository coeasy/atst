"""天天基金扩展源（排行 / 经理 / 公司 / 搜索）离线测试。

覆盖 :mod:`tstdx.web._mob_fund` 共享工具、:mod:`tstdx.web.fund_rank`、
:mod:`tstdx.web.fund_manager`、:mod:`tstdx.web.fund_company` 三个源，
以及 :class:`WebQuoteSession` → 源 的门面端到端链路。
全部用罐头 JSON，不发起真实 HTTP。
"""

from __future__ import annotations

import json

import pytest

from tstdx.errors import SourceDeprecated
from tstdx.facade.api import UnifiedQuoteAPI
from tstdx.web._mob_fund import apply_fields, mob_get_json, mob_rows, mob_rows_any
from tstdx.web.base import HttpResponse
from tstdx.web.fund_company import FundCompanySource
from tstdx.web.fund_manager import FundManagerSource
from tstdx.web.fund_rank import FundMobRankSource


class FakeHttpClient:
    """按 URL 关键字返回预置响应的假客户端。"""

    def __init__(self, **bodies: bytes):
        self.bodies = bodies
        self.calls: list[str] = []

    def get(self, url, *, headers=None, timeout=5.0):
        self.calls.append(url)
        for key, body in self.bodies.items():
            if key in url:
                return HttpResponse(200, body, {})
        return HttpResponse(404, b"not found", {})

    def close(self):
        pass


def _j(obj) -> bytes:
    return json.dumps(obj).encode("utf-8")


def _src(cls, bodies: dict[str, bytes]):
    return cls(client=FakeHttpClient(**bodies))


# -- _mob_fund 共享工具 --------------------------------------------------- #
class TestMobFundUtils:
    def test_apply_fields_types(self) -> None:
        out = apply_fields(
            {"A": "1.5", "B": "7", "C": "x", "D": None},
            {"a": ("A", "f"), "b": ("B", "i"), "c": ("C", "s"), "d": ("D", "s")},
        )
        assert out["a"] == pytest.approx(1.5)
        assert out["b"] == 7
        assert out["c"] == "x"
        assert out["d"] == ""

    def test_mob_rows_nested_and_empty(self) -> None:
        assert mob_rows({"Datas": {"fundStocks": [{"x": 1}]}})[0]["x"] == 1
        assert mob_rows({"Datas": {"a": 1, "b": [{"x": 2}]}})[0]["x"] == 2
        assert mob_rows({}) == []
        assert mob_rows({"Datas": "scalar"}) == []

    def test_mob_rows_any_prefers_first_nonempty(self) -> None:
        assert mob_rows_any(
            {"Datas": [], "data": [{"x": 1}]}, "Datas", "data"
        )[0]["x"] == 1
        assert mob_rows_any(
            {"Datas": [{"x": 1}], "data": [{"y": 2}]}, "Datas", "data"
        )[0]["x"] == 1
        assert mob_rows_any({}, "Datas", "data") == []

    def test_mob_get_json_raises_on_bad_payload(self) -> None:
        fc = FakeHttpClient(**{"X?": b"not json"})
        rt = lambda url, **kw: fc.get(url).text(kw.get("encoding", "utf-8"))
        with pytest.raises(SourceDeprecated, match="非 JSON"):
            mob_get_json(rt, "X?a=1")

    def test_mob_get_json_rejects_non_dict(self) -> None:
        fc = FakeHttpClient(**{"X?": _j([1, 2])})
        rt = lambda url, **kw: fc.get(url).text(kw.get("encoding", "utf-8"))
        with pytest.raises(SourceDeprecated, match="非 JSON 对象"):
            mob_get_json(rt, "X?a=1")


# -- fund_rank ------------------------------------------------------------ #
RANK = _j(
    {
        "Datas": [
            {
                "FCODE": "161725",
                "SHORTNAME": "招商中证白酒指数A",
                "JJGS": "招商基金",
                "FTYPE": "股票型",
                "ESTABDATE": "2015-05-27",
                "RZDF": "-6.03",
                "DWJZ": "1.1959",
                "LJJZ": "1.1959",
                "SYL_W": "-2.31",
                "SYL_M": "3.21",
                "SYL_Q": "5.55",
                "SYL_6Y": "8.10",
                "SYL_1N": "12.34",
                "SYL_3N": "45.67",
                "SYL_Z": "18.59",
                "RLEVEL_SZ": "120.5",
                "RISKLEVEL": "4",
            },
            {"FCODE": "005919", "SHORTNAME": "天弘中证500C", "RZDF": "1.20", "DWJZ": "0.9214"},
        ],
        "TotalCount": 12345,
    }
)

SNAP = _j(
    {
        "Datas": [
            {
                "FCODE": "161725",
                "SHORTNAME": "招商中证白酒指数A",
                "PDATE": "2026-07-30",
                "NAV": "1.1959",
                "ACCNAV": "1.1959",
                "NAVCHGRT": "-6.03",
                "GSZ": "1.1722",
                "GSZZL": "-1.98",
                "GZTIME": "2026-07-31 15:00",
                "NEWPRICE": "--",
                "CHANGERATIO": "--",
                "ISHAVEREDPACKET": "false",
            }
        ],
        "Expansion": {"GZTIME": "2026-07-31", "FSRQ": "2026-07-30"},
    }
)

NAV = _j(
    {
        "Datas": [
            {"FSRQ": "2026-07-30", "DWJZ": "1.1959", "JZZZL": "-6.03", "LJJZ": "1.1959",
             "NAVTYPE": "1", "RATE": "0.15", "SYI": "18.59"},
            {"FSRQ": "2026-07-29", "DWJZ": "1.2707", "JZZZL": "0.32", "LJJZ": "1.2707",
             "NAVTYPE": "1", "RATE": "0.15", "SYI": "25.11"},
        ]
    }
)

DETAIL = _j(
    {
        "Datas": {
            "FCODE": "161725",
            "SHORTNAME": "招商中证白酒指数A",
            "FULLNAME": "招商中证白酒指数分级证券投资基金",
            "FTYPE": "股票型",
            "ESTABDATE": "2015-05-27",
            "NETNAV": "189.5",
            "ENDNAV": "开放式",
            "RLEVEL_SZ": "100-150亿",
            "RISKLEVEL": "4",
            "BENCH": "中证白酒指数收益率*95%",
            "INDEXCODE": "399997",
            "INDEXNAME": "中证白酒",
            "INVTGT": "追求基金长期收益",
            "INVSTRA": "指数化投资为主",
            "JJGS": "招商基金",
            "JJGSID": "80084302",
            "JJJL": "王平",
            "MGREXP": "1.50%",
            "TRUSTEXP": "0.20%",
            "SALESEXP": "0.80%",
            "PRSVTYPE": "1",
            "PRSVDATE": "2015-05-27",
            "CYCLE": "长期",
        }
    }
)

RATING = _j(
    {
        "Datas": [
            {"RDATE": "2026-06-30", "HTPJ": "5", "ZSPJ": "A", "SZPJ3": "4", "JAPJ": ""},
            {"RDATE": "2026-03-31", "HTPJ": "5", "ZSPJ": "A", "SZPJ3": "4", "JAPJ": "4"},
        ]
    }
)

YIELD_CURVE = _j(
    {
        "Datas": [
            {"PDATE": "2026-07-30", "YIELD": "18.59", "INDEXYIELD": "15.02",
             "FUNDTYPEYIELD": "10.33", "BENCHQUOTE": "1.1859"},
            {"PDATE": "2026-07-29", "YIELD": "25.11", "INDEXYIELD": "20.10",
             "FUNDTYPEYIELD": "12.00", "BENCHQUOTE": "1.1200"},
        ]
    }
)

RANK_TREND = _j(
    {
        "Datas": [
            {"PDATE": "2026-07-30", "QRANK": "120", "QSC": "1500"},
            {"PDATE": "2026-07-29", "QRANK": "98", "QSC": "1500"},
        ]
    }
)


class TestFundMobRankSource:
    def test_fetch_rank_parses_and_pages(self) -> None:
        src = _src(FundMobRankSource, {"FundMNRank?": RANK})
        out = src.fetch_rank(sort_column="SYL_1N", page=2, size=30)
        assert out["total"] == 12345
        assert out["page"] == 2 and out["size"] == 30
        assert len(out["rows"]) == 2
        r = out["rows"][0]
        assert r["code"] == "161725"
        assert r["name"] == "招商中证白酒指数A"
        assert r["company"] == "招商基金"
        assert r["day_pct"] == pytest.approx(-6.03)
        assert r["unit_nav"] == pytest.approx(1.1959)
        assert r["return_1y"] == pytest.approx(12.34)
        assert r["return_total"] == pytest.approx(18.59)
        assert r["scale"] == pytest.approx(120.5)
        assert r["risk_level"] == "4"
        # 缺失字段归默认值
        assert out["rows"][1]["company"] == ""
        assert out["rows"][1]["return_1y"] == 0.0

    def test_fetch_rank_query_carries_filters(self) -> None:
        src = _src(FundMobRankSource, {"FundMNRank?": RANK})
        src.fetch_rank(
            fund_type=25, sort_column="SYL_Q", sort="asc", page=1, size=10,
            company_id="80084302", topic="12", risk_level="4", BUY="1",
        )
        url = src.client.calls[-1]
        for frag in [
            "FundMNRank?FundType=25", "SortColumn=SYL_Q", "Sort=asc",
            "pageIndex=1", "pageSize=10", "CompanyId=80084302",
            "TOPICAL=12", "RISKLEVEL=4", "DataConstraintType=0", "LevelTwo=",
            "BUY=1", "deviceid=3EA024C2", "plat=Iphone",
        ]:
            assert frag in url, frag

    def test_fetch_rank_omits_empty_filters(self) -> None:
        src = _src(FundMobRankSource, {"FundMNRank?": RANK})
        src.fetch_rank()
        url = src.client.calls[-1]
        assert "CompanyId=" not in url
        assert "TOPICAL=" not in url
        assert "RISKLEVEL=" not in url

    def test_fetch_snapshot_accepts_list_and_string(self) -> None:
        src = _src(FundMobRankSource, {"FundMNFInfo?": SNAP})
        rows = src.fetch_snapshot(["161725", "005919"])
        assert rows[0]["code"] == "161725"
        assert rows[0]["unit_nav"] == pytest.approx(1.1959)
        assert rows[0]["est_nav"] == pytest.approx(1.1722)
        assert rows[0]["est_pct"] == pytest.approx(-1.98)
        assert rows[0]["est_time"] == "2026-07-31 15:00"
        assert rows[0]["has_redpacket"] == "false"
        assert "Fcodes=161725,005919" in src.client.calls[-1]

        src.fetch_snapshot("005919,161725")
        assert "Fcodes=005919,161725" in src.client.calls[-1]

    def test_fetch_nav_history(self) -> None:
        src = _src(FundMobRankSource, {"FundMNHisNetList?": NAV})
        rows = src.fetch_nav_history("161725", page=1, size=49)
        assert len(rows) == 2
        assert rows[0]["date"] == "2026-07-30"
        assert rows[0]["unit_nav"] == pytest.approx(1.1959)
        assert rows[0]["pct_change"] == pytest.approx(-6.03)
        assert rows[0]["cum_return"] == pytest.approx(18.59)
        assert rows[0]["nav_type"] == "1"
        assert "pageSize=49" in src.client.calls[-1]

    def test_fetch_detail(self) -> None:
        src = _src(FundMobRankSource, {"FundMNDetailInformation?": DETAIL})
        d = src.fetch_detail("161725")
        assert d["code"] == "161725"
        assert d["name"] == "招商中证白酒指数A"
        assert d["fullname"] == "招商中证白酒指数分级证券投资基金"
        assert d["risk_level"] == "4"
        assert d["risk_scale"] == "100-150亿"
        assert d["benchmark"] == "中证白酒指数收益率*95%"
        assert d["index_code"] == "399997"
        assert d["invest_target"] == "追求基金长期收益"
        assert d["invest_strategy"] == "指数化投资为主"
        assert d["company_id"] == "80084302"
        assert d["management_exp"] == "1.50%"
        assert d["sales_exp"] == "0.80%"
        assert d["cycle"] == "长期"

    def test_fetch_detail_empty_payload(self) -> None:
        src = _src(FundMobRankSource, {"FundMNDetailInformation?": _j({"Datas": None})})
        d = src.fetch_detail("000000")
        assert d["code"] == "000000"
        assert d["name"] == ""
        assert d["net_nav"] == 0.0

    def test_fetch_rating(self) -> None:
        src = _src(FundMobRankSource, {"FundGradeDetail?": RATING})
        rows = src.fetch_rating("161725", page=1, size=20)
        assert len(rows) == 2
        assert rows[0]["date"] == "2026-06-30"
        assert rows[0]["rating_ht"] == "5"
        assert rows[0]["rating_zs"] == "A"
        assert rows[1]["rating_ja"] == "4"

    def test_fetch_yield_curve(self) -> None:
        src = _src(FundMobRankSource, {"FundVPageAcc?": YIELD_CURVE})
        rows = src.fetch_yield_curve("161725", index_code="399006")
        assert len(rows) == 2
        assert rows[0]["fund_yield"] == pytest.approx(18.59)
        assert rows[0]["index_yield"] == pytest.approx(15.02)
        assert rows[0]["peer_yield"] == pytest.approx(10.33)
        assert rows[0]["benchmark_quote"] == "1.1859"
        assert "INDEXCODE=399006" in src.client.calls[-1]

    def test_fetch_rank_trend(self) -> None:
        src = _src(FundMobRankSource, {"FundRankDiagram?": RANK_TREND})
        rows = src.fetch_rank_trend("161725", range_="y")
        assert len(rows) == 2
        assert rows[0]["rank"] == 120
        assert rows[0]["total"] == 1500
        assert "RANGE=y" in src.client.calls[-1]


# -- fund_manager --------------------------------------------------------- #
MGR_LIST = _j(
    {
        "Datas": [
            {"MGRID": "30634044", "MGRNAME": "王平", "FCODE": "161725", "DAYS": 4000,
             "FEMPDATE": "2015-05-27", "LEMPDATE": "", "PENAVGROWTH": "18.59",
             "ISINOFFICE": "1"},
            {"MGRID": "30000001", "MGRNAME": "李前", "FCODE": "161725", "DAYS": 900,
             "FEMPDATE": "2011-01-01", "LEMPDATE": "2015-05-26", "PENAVGROWTH": "32.10",
             "ISINOFFICE": "0"},
        ]
    }
)

MGR_PROFILE = _j(
    {
        "Datas": {
            "MGRID": "30634044", "MGRNAME": "王平", "JJGS": "招商基金",
            "JJGSID": "80084302", "SEX": "1",
            "RESUME": "2010 年加入招商基金",
            "INVESTMENTMETHOD": "指数化投资",
            "INVESTMENTIDEAR": "追求长期回报",
            "TOTALDAYS": 7300, "NETNAV": "189.5", "FCOUNT": 3, "TCOUNT": 8,
            "PRECODE": "217002", "PRENAME": "招商中证2000",
            "AWARDNUM": 5, "AWARDNUM_JN": 3, "AWARDNUM_MX": 2, "AWARDFNUM": 4,
            "MAXPENAVGROWTH": "45.6", "MFTYPE": "股票型",
            "FCODE": "161725", "SHORTNAME": "招商中证白酒指数A",
            "MAXRETRA1": "-22.1", "MAXEARN1": "60.0", "SDAY": "2010-06-01",
            "NEWPHOTOURL": "https://x/1.jpg",
        }
    }
)

MGR_YIELD = _j(
    {"Datas": [
        {"PDATE": "2026-07-30", "SYI": "45.6", "AVGSYI": "12.0", "INDEXSYI": "15.02"},
        {"PDATE": "2026-07-29", "SYI": "48.1", "AVGSYI": "11.8", "INDEXSYI": "14.90"},
    ]}
)

MGR_EVAL = _j(
    {
        "Datas": {
            "MAXRETRA_1": "-22.1", "MAXRETRA_3": "-30.5",
            "HCPCT_1": "68.0", "HCPCT_3": "55.2",
            "SHARP_1": "1.25", "SHARP_3": "0.87",
            "XPPCT_1": "90.1", "XPPCT_3": "88.0",
            "STDDEV_1": "22.3", "STDDEV_3": "21.0",
            "BDPCT_1": "30.0", "BDPCT_3": "40.0",
            "WIN_1": "110", "WIN_3": "320",
            "WINPCT_1": "65.5", "WINPCT_3": "61.2",
        }
    }
)

MGR_STYLE = _j(
    {
        "Datas": {
            "PosDate": "2026-03-31",
            "Pos": [
                {"GPDM": "600519", "GPJC": "贵州茅台", "NEWTEXCH": "1", "JZBL": "10.20",
                 "INDEXNAME": "中证白酒", "INDEXCODE": "399997", "PCTNVCHG": "增持"},
                {"GPDM": "600809", "GPJC": "山西汾酒", "NEWTEXCH": "1", "JZBL": "9.50",
                 "INDEXNAME": "中证白酒", "INDEXCODE": "399997", "PCTNVCHG": "新进"},
            ],
            "Style": {"FSCALE": "800.0", "FSTYLE": "大盘成长", "GZQK": "高估值",
                      "YLQK": "高收益"},
            "SubStyle": [
                {"DLMC": "食品饮料", "CCBL": "45.0", "AVRBL": "30.0"},
                {"DLMC": "医药生物", "CCBL": "12.0", "AVRBL": "18.0"},
            ],
        }
    }
)


class TestFundManagerSource:
    def test_fetch_list(self) -> None:
        src = _src(FundManagerSource, {"FundMNMangerList?": MGR_LIST})
        rows = src.fetch_list("161725")
        assert len(rows) == 2
        assert rows[0]["mgrid"] == "30634044"
        assert rows[0]["name"] == "王平"
        assert rows[0]["days"] == 4000
        assert rows[0]["start_date"] == "2015-05-27"
        assert rows[0]["end_date"] == ""
        assert rows[0]["nav_growth"] == pytest.approx(18.59)
        assert rows[0]["is_in_office"] == "1"
        assert rows[1]["is_in_office"] == "0"
        assert "FundMNMangerList?FCODE=161725" in src.client.calls[-1]

    def test_fetch_profile(self) -> None:
        src = _src(FundManagerSource, {"FundMSNMangerInfo?": MGR_PROFILE})
        d = src.fetch_profile("30634044")
        assert d["mgrid"] == "30634044"
        assert d["name"] == "王平"
        assert d["company"] == "招商基金"
        assert d["resume"] == "2010 年加入招商基金"
        assert d["invest_idea"] == "追求长期回报"
        assert d["total_days"] == 7300
        assert d["net_nav"] == pytest.approx(189.5)
        assert d["fund_count"] == 3
        assert d["award_num"] == 5
        assert d["max_nav_growth"] == pytest.approx(45.6)
        assert d["max_retra_1y"] == pytest.approx(-22.1)

    def test_fetch_profile_empty_fills_mgrid(self) -> None:
        src = _src(FundManagerSource, {"FundMSNMangerInfo?": _j({"Datas": None})})
        d = src.fetch_profile("999")
        assert d["mgrid"] == "999"
        assert d["name"] == ""

    def test_fetch_yield(self) -> None:
        src = _src(FundManagerSource, {"FundMSNMangerAcc?": MGR_YIELD})
        rows = src.fetch_yield("30634044", range_="ln")
        assert len(rows) == 2
        assert rows[0]["date"] == "2026-07-30"
        assert rows[0]["yield_"] == pytest.approx(45.6)
        assert rows[0]["avg_yield"] == pytest.approx(12.0)
        assert rows[0]["index_yield"] == pytest.approx(15.02)
        assert "mGRID=30634044" in src.client.calls[-1]
        assert "rANGE=ln" in src.client.calls[-1]

    def test_fetch_eval(self) -> None:
        src = _src(FundManagerSource, {"FundMSNMangerPerEval?": MGR_EVAL})
        d = src.fetch_eval("30634044")
        assert d["mgrid"] == "30634044"
        assert d["sharp_1y"] == pytest.approx(1.25)
        assert d["sharp_3y"] == pytest.approx(0.87)
        assert d["max_ret_1y"] == pytest.approx(-22.1)
        assert d["win_pct_1y"] == pytest.approx(65.5)
        assert d["stddev_1y"] == pytest.approx(22.3)
        assert d["win_1y"] == 110
        assert d["hc_pct_3y"] == pytest.approx(55.2)

    def test_fetch_style(self) -> None:
        src = _src(FundManagerSource, {"FundMSNMangerPosMark?": MGR_STYLE})
        d = src.fetch_style("30634044")
        assert d["mgrid"] == "30634044"
        assert d["pos_date"] == "2026-03-31"
        assert len(d["holdings"]) == 2
        assert d["holdings"][0]["code"] == "600519"
        assert d["holdings"][0]["name"] == "贵州茅台"
        assert d["holdings"][0]["ratio"] == pytest.approx(10.20)
        assert d["holdings"][0]["pct_change"] == "增持"
        assert d["style"]["fund_scale"] == pytest.approx(800.0)
        assert d["style"]["fund_style"] == "大盘成长"
        assert len(d["sub_style"]) == 2
        assert d["sub_style"][0]["name"] == "食品饮料"
        assert d["sub_style"][0]["avg_ratio"] == pytest.approx(30.0)

    def test_fetch_style_empty(self) -> None:
        src = _src(FundManagerSource, {"FundMSNMangerPosMark?": _j({"Datas": None})})
        d = src.fetch_style("999")
        assert d["mgrid"] == "999"
        assert d["holdings"] == []
        assert d["sub_style"] == []
        assert d["style"] == {}


# -- fund_company --------------------------------------------------------- #
COMPANIES = _j(
    {
        "Datas": [
            {"JJGSID": "80084302", "JJGS": "招商基金", "GSJJBName": "招商",
             "JJGSJP": "ZSJJ", "GSJJBID": "ZS", "QXJJ": "210"},
            {"JJGSID": "80000236", "JJGS": "易方达基金", "GSJJBName": "易方达",
             "JJGSJP": "YFDJJ"},
        ]
    }
)

ARCHIVES = _j(
    {
        "code": 0,
        "data": {
            "FDMC": "招商基金管理有限公司",
            "CLRQ": "2002-12-27",
            "ZCZB": "1200.5",
            "FRDB": "2003-06-01",
            "Manager": "1800",
            "ZCDZ": "深圳市福田区",
            "BGDZ": "www.cmfchina.com",
            "KFRX": "张三",
            "Count": 210,
            "ManagerCount": 45,
            "Level": "A",
        },
    }
)

COMPANY_FUNDS = _j(
    {
        "code": 0,
        "data": [
            {
                "FCODE": "161725", "FEATURE": "LOF", "FUNDTYPE": "股票型",
                "FULLNAME": "招商中证白酒指数A", "SHORTNAME": "招商中证白酒A",
                "JJGSID": "80084302", "DWJZ": "1.1959", "LJJZ": "1.1959",
                "SYL_D": "-6.03", "SYL_Z": "18.59", "SYL_Y": "12.34",
                "SYL_2N": "22.0", "SYL_3N": "45.67", "SYL_6Y": "8.10",
            },
        ],
    }
)

SCALE = _j(
    {"code": 0, "data": {"Datas": [
        {"FSRQ": "2026-03-31", "QMZFE": "1800.0", "QMJZC": "2150.5"},
        {"FSRQ": "2025-12-31", "QMZFE": "1600.0", "QMJZC": "1900.0"},
    ]}}
)

COMPANY_BASE = _j(
    {
        "code": 0,
        "data": {
            "FDMC": "招商基金", "GSJJBName": "招商", "Count": 210,
            "ManagerCount": 45, "fundmaxsyl": "68.9",
            "NewFundList": [
                {
                    "TypeName": "股票型", "TypeCount": 40, "Filds": "FCODE,SHORTNAME",
                    "fundlist": [
                        {"FCODE": "161725", "SHORTNAME": "招商中证白酒A",
                         "DWJZ": "1.1959", "SYL_Y": "12.34"},
                    ],
                },
                {"TypeName": "债券型", "TypeCount": 60, "Filds": "FCODE", "fundlist": []},
            ],
            "CompanyTopic": {
                "List": [
                    {"JJGSID": "12", "TTYPE": "0", "TTYPENAME": "白酒",
                     "PDATE": "2026-07-31", "W": "3.2", "M": "5.1", "Q": "8.0", "Y": "12.3"},
                ]
            },
        },
    }
)

SEARCH = _j(
    {
        "totalCount": 2,
        "data": [
            {"fcode": "161725", "showfcode": "161725", "ftype": "股票型",
             "shortname": "招商中证白酒指数A", "hightlight": "招商",
             "fcodetype": "1", "secondfcodetype": "2", "abbname": "ZSZZBJZSA",
             "abbtname": "ZSZZBJZ", "foreshortname": "招商基金", "newtexch": "1"},
            {"fcode": "161726", "showfcode": "161726", "ftype": "股票型",
             "shortname": "招商中证白酒指数C"},
        ],
    }
)


class TestFundCompanySource:
    def test_fetch_companies(self) -> None:
        src = _src(FundCompanySource, {"FundCompanyBaseList": COMPANIES})
        rows = src.fetch_companies()
        assert len(rows) == 2
        assert rows[0]["company_id"] == "80084302"
        assert rows[0]["name"] == "招商基金"
        assert rows[0]["name_abbrev"] == "招商"
        assert rows[0]["fund_count"] == "210"
        assert rows[1]["pinyin"] == "YFDJJ"
        assert "FundCompanyBaseList.ashx" in src.client.calls[-1]
        assert "FundMApi" in src.client.calls[-1]

    def test_fetch_companies_accepts_data_key(self) -> None:
        src = _src(
            FundCompanySource,
            {"FundCompanyBaseList": _j({"data": [{"JJGSID": "1", "JJGS": "X"}]})},
        )
        assert src.fetch_companies()[0]["name"] == "X"

    def test_fetch_archives(self) -> None:
        src = _src(FundCompanySource, {"action=companyarchives": ARCHIVES})
        d = src.fetch_archives("80084302")
        assert d["company_id"] == "80084302"
        assert d["company_name"] == "招商基金管理有限公司"
        assert d["establish_date"] == "2002-12-27"
        assert d["total_assets"] == "1200.5"
        assert d["first_fund_date"] == "2003-06-01"
        assert d["manager"] == "1800"
        assert d["website"] == "www.cmfchina.com"
        assert d["fund_count"] == 210
        assert d["manager_count"] == 45
        assert d["level"] == "A"
        assert "cc=80084302" in src.client.calls[-1]

    def test_fetch_funds(self) -> None:
        src = _src(FundCompanySource, {"action=fundlist": COMPANY_FUNDS})
        rows = src.fetch_funds("80084302", fund_type="1", page=2, size=10,
                               sort_field="SYL_Y", sort_dir="asc")
        assert len(rows) == 1
        r = rows[0]
        assert r["code"] == "161725"
        assert r["name"] == "招商中证白酒A"
        assert r["fund_type"] == "股票型"
        assert r["unit_nav"] == pytest.approx(1.1959)
        assert r["return_1y"] == pytest.approx(12.34)
        assert r["return_total"] == pytest.approx(18.59)
        assert r["company_id"] == "80084302"
        url = src.client.calls[-1]
        for frag in ["cc=80084302", "fundtype=1", "pi=2", "ps=10",
                     "sd=asc", "sf=SYL_Y"]:
            assert frag in url, frag

    def test_fetch_scale_change(self) -> None:
        src = _src(FundCompanySource, {"action=companygmbd": SCALE})
        rows = src.fetch_scale_change("80084302", page=1, size=10)
        assert len(rows) == 2
        assert rows[0]["date"] == "2026-03-31"
        assert rows[0]["share_total"] == pytest.approx(1800.0)
        assert rows[0]["nav_total"] == pytest.approx(2150.5)
        assert "ps=10" in src.client.calls[-1]

    def test_fetch_base_info(self) -> None:
        src = _src(FundCompanySource, {"action=fundcompanybaseinfo": COMPANY_BASE})
        d = src.fetch_base_info("80084302")
        assert d["company_id"] == "80084302"
        assert d["name"] == "招商基金"
        assert d["fund_count"] == 210
        assert d["manager_count"] == 45
        assert d["max_yield"] == pytest.approx(68.9)
        assert len(d["fund_types"]) == 2
        assert d["fund_types"][0]["type_name"] == "股票型"
        assert d["fund_types"][0]["type_count"] == 40
        assert d["fund_types"][0]["fund_list"][0]["code"] == "161725"
        assert d["fund_types"][1]["fund_list"] == []
        assert len(d["topics"]) == 1
        t = d["topics"][0]
        assert t["type_name"] == "白酒"
        assert t["ratio_1y"] == pytest.approx(12.3)

    def test_fetch_base_info_empty(self) -> None:
        src = _src(FundCompanySource, {"action=fundcompanybaseinfo": _j({"code": 1})})
        d = src.fetch_base_info("x")
        assert d["company_id"] == "x"
        assert d["fund_types"] == []
        assert d["topics"] == []

    def test_search_funds(self) -> None:
        src = _src(FundCompanySource, {"fundinfobynohigh": SEARCH})
        out = src.search_funds("招商", order_type=1, page=1, size=5)
        assert out["total"] == 2
        assert out["page"] == 1 and out["size"] == 5
        assert len(out["rows"]) == 2
        r = out["rows"][0]
        assert r["code"] == "161725"
        assert r["name"] == "招商中证白酒指数A"
        assert r["highlight"] == "招商"
        assert r["abb_tname"] == "ZSZZBJZ"
        assert out["rows"][1]["highlight"] == ""
        url = src.client.calls[-1]
        assert "fundinfobynohigh" in url
        assert "orderType=1" in url
        assert "key=招商" in url
        assert "pageindex=1" in url
        assert "pagesize=5" in url

    def test_search_uses_fundts_host(self) -> None:
        src = _src(FundCompanySource, {"fundinfobynohigh": SEARCH})
        src.search_funds("x")
        assert "fundts.eastmoney.com" in src.client.calls[-1]
        assert "fundmobapi" not in src.client.calls[-1]


# -- 门面端到端链路（monkeypatch _shared_http） --------------------------- #
import tstdx.web._facade_mixin_fund_v2 as mixin_mod  # noqa: E402

_ALL = {
    "FundMNRank?": RANK,
    "FundMNFInfo?": SNAP,
    "FundMNHisNetList?": NAV,
    "FundMNDetailInformation?": DETAIL,
    "FundGradeDetail?": RATING,
    "FundVPageAcc?": YIELD_CURVE,
    "FundRankDiagram?": RANK_TREND,
    "FundMNMangerList?": MGR_LIST,
    "FundMSNMangerInfo?": MGR_PROFILE,
    "FundMSNMangerAcc?": MGR_YIELD,
    "FundMSNMangerPerEval?": MGR_EVAL,
    "FundMSNMangerPosMark?": MGR_STYLE,
    "FundCompanyBaseList": COMPANIES,
    "action=companyarchives": ARCHIVES,
    "action=fundlist": COMPANY_FUNDS,
    "action=companygmbd": SCALE,
    "action=fundcompanybaseinfo": COMPANY_BASE,
    "fundinfobynohigh": SEARCH,
}


@pytest.fixture
def api(monkeypatch):
    """注入假客户端的 UnifiedQuoteAPI（18 个新门面方法全链验证）。"""
    fc = FakeHttpClient(**_ALL)
    monkeypatch.setattr(mixin_mod, "_shared_http", lambda: fc)
    return UnifiedQuoteAPI(), fc


class TestFundFacadeChain:
    def test_fund_rank(self, api) -> None:
        obj, _ = api
        out = obj.fund_rank(sort_column="SYL_1N", size=20)
        assert out["total"] == 12345
        assert out["rows"][0]["code"] == "161725"
        assert out["rows"][0]["return_1y"] == pytest.approx(12.34)

    def test_fund_snapshot(self, api) -> None:
        obj, _ = api
        rows = obj.fund_snapshot(["161725", "005919"])
        assert rows[0]["est_nav"] == pytest.approx(1.1722)

    def test_fund_nav_history_mob(self, api) -> None:
        obj, _ = api
        rows = obj.fund_nav_history_mob("161725", size=49)
        assert rows[0]["cum_return"] == pytest.approx(18.59)

    def test_fund_detail(self, api) -> None:
        obj, _ = api
        d = obj.fund_detail("161725")
        assert d["risk_level"] == "4"
        assert d["benchmark"] == "中证白酒指数收益率*95%"

    def test_fund_rating(self, api) -> None:
        obj, _ = api
        rows = obj.fund_rating("161725")
        assert rows[0]["rating_ht"] == "5"

    def test_fund_yield_curve(self, api) -> None:
        obj, _ = api
        rows = obj.fund_yield_curve("161725", index_code="399006")
        assert rows[0]["index_yield"] == pytest.approx(15.02)

    def test_fund_rank_trend(self, api) -> None:
        obj, _ = api
        rows = obj.fund_rank_trend("161725")
        assert rows[0]["rank"] == 120

    def test_fund_manager_list(self, api) -> None:
        obj, _ = api
        rows = obj.fund_manager_list("161725")
        assert rows[0]["mgrid"] == "30634044"
        assert rows[0]["is_in_office"] == "1"

    def test_fund_manager_profile(self, api) -> None:
        obj, _ = api
        d = obj.fund_manager_profile("30634044")
        assert d["name"] == "王平"
        assert d["award_num"] == 5

    def test_fund_manager_yield(self, api) -> None:
        obj, _ = api
        rows = obj.fund_manager_yield("30634044")
        assert rows[0]["yield_"] == pytest.approx(45.6)

    def test_fund_manager_eval(self, api) -> None:
        obj, _ = api
        d = obj.fund_manager_eval("30634044")
        assert d["sharp_1y"] == pytest.approx(1.25)
        assert d["win_pct_1y"] == pytest.approx(65.5)

    def test_fund_manager_style(self, api) -> None:
        obj, _ = api
        d = obj.fund_manager_style("30634044")
        assert d["holdings"][0]["code"] == "600519"
        assert d["style"]["fund_style"] == "大盘成长"

    def test_fund_companies(self, api) -> None:
        obj, _ = api
        rows = obj.fund_companies()
        assert rows[0]["company_id"] == "80084302"

    def test_fund_company_archives(self, api) -> None:
        obj, _ = api
        d = obj.fund_company_archives("80084302")
        assert d["company_name"] == "招商基金管理有限公司"

    def test_fund_company_funds(self, api) -> None:
        obj, _ = api
        rows = obj.fund_company_funds("80084302", size=10)
        assert rows[0]["code"] == "161725"

    def test_fund_company_scale(self, api) -> None:
        obj, _ = api
        rows = obj.fund_company_scale("80084302")
        assert rows[0]["nav_total"] == pytest.approx(2150.5)

    def test_fund_company_base_info(self, api) -> None:
        obj, _ = api
        d = obj.fund_company_base_info("80084302")
        assert d["fund_types"][0]["type_name"] == "股票型"
        assert d["topics"][0]["type_name"] == "白酒"

    def test_fund_search(self, api) -> None:
        obj, _ = api
        out = obj.fund_search("招商", size=5)
        assert out["total"] == 2
        assert out["rows"][0]["code"] == "161725"

    def test_all_18_methods_present(self, api) -> None:
        obj, _ = api
        methods = [
            "fund_rank", "fund_snapshot", "fund_nav_history_mob", "fund_detail",
            "fund_rating", "fund_yield_curve", "fund_rank_trend",
            "fund_manager_list", "fund_manager_profile", "fund_manager_yield",
            "fund_manager_eval", "fund_manager_style",
            "fund_companies", "fund_company_archives", "fund_company_funds",
            "fund_company_scale", "fund_company_base_info", "fund_search",
        ]
        missing = [m for m in methods if not callable(getattr(obj, m, None))]
        assert not missing, missing
        assert len(methods) == 18
