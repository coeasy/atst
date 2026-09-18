"""扩展源测试：逐笔 / 分时 / 外盘 / 大盘统计 / 排行 / 资金流 / 涨跌停池 /
沪深港通 / 基本面（真实抓包样本罐头，全部离线）。"""

from __future__ import annotations

import json
from urllib.parse import unquote

import pytest

from tstdx.errors import SourceDeprecated, WebSourceError
from tstdx.web.adapters import HkSource, KlineSource, SinaHkSource, UsSource
from tstdx.web.base import HttpResponse
from tstdx.web.corporate import (
    EastmoneyBlockTradeSource,
    EastmoneyNoticeSource,
    EastmoneyPerformanceSource,
    EastmoneyProfileSource,
    EastmoneyResearchSource,
    EastmoneyShareholderSource,
    EastmoneyUnlockSource,
)
from tstdx.web.fundflow import (
    EastmoneyFundFlowSource,
    EastmoneyLimitPoolSource,
    EastmoneyNorthboundSource,
    EastmoneyRankSource,
    _hhmmss,
    _symbol_from_market,
)
from tstdx.web.global_market import TencentGlobalSource, TencentMarketStatSource
from tstdx.web.longhu import EastmoneyTopListSource, parse_lhb_row
from tstdx.web.news import SinaNewsSource, _clean
from tstdx.web.ticks import TICKS_PER_PAGE, EastmoneyTrendsSource, TencentTickSource


class FakeHttp:
    """按顺序返回预置响应，记录所有请求 URL。"""

    def __init__(self, *bodies: bytes):
        self.bodies = list(bodies)
        self.calls: list[str] = []

    def get(self, url, **kwargs):  # noqa: ARG002
        self.calls.append(url)
        body = self.bodies.pop(0) if len(self.bodies) > 1 else self.bodies[0]
        return HttpResponse(200, body, {})

    def close(self):
        pass


def _attach(source, *bodies: bytes):
    http = FakeHttp(*bodies)
    source.client = http
    return source, http


# --------------------------------------------------------------------------- #
# 样本
# --------------------------------------------------------------------------- #
TENCENT_TICK = (
    'v_detail_data_sh600519=[0,"0/09:25:01/1295.00/0.00/423/54778500/S'
    "|1/09:30:01/1292.22/-2.78/6/775848/B"
    '|2/09:30:04/1292.75/0.53/18/2326994/M"]'
).encode("gbk")

EM_TRENDS = json.dumps(
    {
        "rc": 0,
        "data": {
            "code": "600519",
            "market": 1,
            "name": "贵州茅台",
            "trends": [
                "2026-09-01 09:30,1295.00,1295.00,1295.00,1295.00,423,54778500.00,1295.000",
                "2026-09-01 09:31,1292.22,1289.24,1292.75,1286.20,794,102381280.00,1291.370",
            ],
        },
    }
).encode("utf-8")

GLOBAL_HF = (
    'v_hf_CL="87.84,2.43,87.82,87.83,88.13,86.13,18:53:08,85.76,86.31,0,3,1,'
    '2026-09-01,纽约原油"; '
    'v_hf_GC="4423.32,-1.30,4424.60,4424.70,4510.50,4413.00,18:53:10,4481.50,'
    '4498.70,0,1,3,2026-09-01,纽约黄金"; '
).encode("gbk")

MARKET_STAT = (
    'v_s_sh000001="1~上证指数~000001~3979.89~-6.41~-0.16~573538949~94430756~'
    '~704688.35~ZS~"; '
    'v_s_sz399001="51~深证成指~399001~13872.38~-142.62~-1.02~684714331~'
    '108909526~~447432.31~ZS~"; '
).encode("gbk")

RANK_CLIST = json.dumps(
    {
        "rc": 0,
        "data": {
            "total": 5555,
            "diff": [
                {
                    "f2": 26.0,
                    "f3": 290.98,
                    "f4": 19.35,
                    "f5": 779923,
                    "f6": 1978792312.0,
                    "f12": "601123",
                    "f13": 1,
                    "f14": "N马矿",
                },
                {
                    "f2": 35.9,
                    "f3": 196.69,
                    "f4": 23.8,
                    "f5": 342242,
                    "f6": 1344930207.99,
                    "f12": "301697",
                    "f13": 0,
                    "f14": "N贝特利",
                },
            ],
        },
    }
).encode("utf-8")

RANK_BOARD = json.dumps(
    {
        "rc": 0,
        "data": {
            "total": 496,
            "diff": [
                {
                    "f2": 15547.44,
                    "f3": 4.04,
                    "f5": 47052005,
                    "f6": 38703315718.0,
                    "f12": "BK0433",
                    "f13": 90,
                    "f14": "农林牧渔",
                    "f62": 2876480848.0,
                    "f184": 7.43,
                },
            ],
        },
    }
).encode("utf-8")

FLOW_REALTIME = json.dumps(
    {
        "rc": 0,
        "data": {
            "total": 2,
            "diff": [
                {
                    "f2": 1299.56,
                    "f3": 0.0,
                    "f12": "600519",
                    "f14": "贵州茅台",
                    "f62": 220531760.0,
                    "f66": 168048960.0,
                    "f69": 3.96,
                    "f72": 52482800.0,
                    "f75": 1.24,
                    "f78": -220434016.0,
                    "f81": -5.2,
                    "f84": -97733.0,
                    "f87": 0,
                    "f184": 5.2,
                },
            ],
        },
    }
).encode("utf-8")

FLOW_HISTORY = json.dumps(
    {
        "rc": 0,
        "data": {
            "klines": [
                "2026-09-01,220531760.0,-97733.0,-220434016.0,52482800.0,168048960.0",
            ]
        },
    }
).encode("utf-8")

ZT_POOL = json.dumps(
    {
        "rc": 0,
        "data": {
            "tc": 83,
            "qdate": 20260901,
            "pool": [
                {
                    "c": "000635",
                    "m": 0,
                    "n": "英 力 特",
                    "p": 7220,
                    "zdp": 10.06,
                    "amount": 22285339,
                    "ltsz": 2648439071.52,
                    "tshare": 2845644707.52,
                    "hs": 0.841,
                    "lbc": 1,
                    "fbt": 92500,
                    "lbt": 92500,
                    "fund": 100684257,
                    "zbc": 0,
                    "hybk": "化学原料",
                    "zttj": {"days": 1, "ct": 1},
                },
            ],
        },
    }
).encode("utf-8")

NORTHBOUND = json.dumps(
    {
        "rc": 0,
        "data": {
            "hk2sh": {
                "status": 3,
                "dayNetAmtIn": 0.0,
                "dayAmtRemain": 0.0,
                "dayAmtThreshold": 5200000.0,
                "date": "09-01",
                "date2": "2026-09-01",
            },
            "sh2hk": {
                "status": 1,
                "dayNetAmtIn": 4200000.0,
                "dayAmtRemain": 0.0,
                "dayAmtThreshold": 4200000.0,
                "date": "09-01",
                "date2": "2026-09-01",
            },
        },
    }
).encode("utf-8")

PROFILE = json.dumps(
    {
        "rc": 0,
        "data": {
            "f43": 129956,
            "f47": 32664,
            "f48": 4242440861.0,
            "f49": 15894,
            "f57": "600519",
            "f58": "贵州茅台",
            "f60": 129952,
            "f84": 1250081601.0,
            "f85": 1250081601.0,
            "f116": 1624556045395.5598,
            "f117": 1624556045395.5598,
            "f127": "白酒Ⅱ",
            "f128": "贵州板块",
            "f162": 1825,
            "f167": 647,
            "f173": 16.75,
            "f174": 153998,
            "f175": 115101,
        },
    }
).encode("utf-8")

NOTICE = json.dumps(
    {
        "data": {
            "list": [
                {
                    "art_code": "AN202608141827994407",
                    "codes": [{"stock_code": "600519", "short_name": "贵州茅台"}],
                    "columns": [{"column_code": "001002008", "column_name": "其他"}],
                    "display_time": "2026-08-14 20:41:29:380",
                    "notice_date": "2026-08-15 00:00:00",
                    "title": "贵州茅台:贵州茅台关于召开2026年半年度业绩说明会的公告",
                }
            ]
        }
    }
).encode("utf-8")

RESEARCH = json.dumps(
    {
        "hits": 46,
        "size": 1,
        "data": [
            {
                "title": "2026年中报点评：茅台酒稳健，系列酒主动调整",
                "stockName": "贵州茅台",
                "stockCode": "600519",
                "orgSName": "西南证券",
                "publishDate": "2026-08-21 00:00:00.000",
                "infoCode": "AP202608211828244348",
                "predictThisYearEps": "69.83",
                "predictThisYearPe": "18.59",
                "emRatingName": "买入",
                "researcher": "朱会振,舒尚立",
                "indvInduName": "白酒Ⅱ",
            }
        ],
    }
).encode("utf-8")

DC_SUCCESS = json.dumps(
    {
        "version": "fb6cf9d6",
        "result": {
            "pages": 35,
            "count": 1,
            "data": [
                {
                    "SECURITY_CODE": "600519",
                    "SECURITY_NAME_ABBR": "贵州茅台",
                    "REPORTDATE": "2026-06-30 00:00:00",
                    "BASIC_EPS": 35.57,
                    "DEDUCT_BASIC_EPS": 35.53,
                    "TOTAL_OPERATE_INCOME": 92278072083.21,
                    "PARENT_NETPROFIT": 44516880421.86,
                    "WEIGHTAVG_ROE": 16.75,
                    "XSMLL": 89.5552128279,
                    "BPS": 200.989754763617,
                    "MGJYXJJE": 56.54890853726,
                    "SJLTZ": -1.95,
                    "YSHZ": -31.3105,
                    "PUBLISHNAME": "白酒Ⅱ",
                    "NOTICE_DATE": "2026-08-15 00:00:00",
                }
            ],
        },
        "success": True,
        "message": "ok",
        "code": 0,
    }
).encode("utf-8")

DC_ERROR = json.dumps(
    {
        "version": None,
        "result": None,
        "success": False,
        "message": "报表配置不存在,RPT_IPO_INFO",
        "code": 9501,
    }
).encode("utf-8")

DC_HOLDERS = json.dumps(
    {
        "version": "7b5",
        "result": {
            "pages": 314,
            "count": 1,
            "data": [
                {
                    "SECUCODE": "600519.SH",
                    "SECURITY_CODE": "600519",
                    "HOLDER_NAME": "中国贵州茅台酒厂(集团)有限责任公司",
                    "HOLD_NUM": 728531955,
                    "HOLD_RATIO": 57.995,
                    "HOLD_NUM_CHANGE": "不变",
                    "HOLDER_TYPE": "其它",
                    "SHARES_TYPE": "A股",
                    "HOLDER_RANK": 1,
                    "END_DATE": "2020-06-30 00:00:00",
                }
            ],
        },
        "success": True,
        "message": "ok",
        "code": 0,
    }
).encode("utf-8")

DC_BLOCK = json.dumps(
    {
        "version": "abb",
        "result": {
            "pages": 136100,
            "count": 1,
            "data": [
                {
                    "SECURITY_CODE": "301571",
                    "SECURITY_NAME_ABBR": "国科天成",
                    "TRADE_DATE": "2026-09-01 00:00:00",
                    "DEAL_PRICE": 65.79,
                    "PREMIUM_RATIO": -0.019961269179,
                    "DEAL_VOLUME": 99000,
                    "DEAL_AMT": 6513200,
                    "CLOSE_PRICE": 67.13,
                    "BUYER_NAME": "中国中金财富证券有限公司上海浦东新区民生路证券营业部",
                    "SELLER_NAME": "华泰证券股份有限公司北京分公司",
                    "TURNOVER_RATE": 0.081420869768,
                }
            ],
        },
        "success": True,
        "message": "ok",
        "code": 0,
    }
).encode("utf-8")

DC_UNLOCK = json.dumps(
    {
        "version": "1d6",
        "result": {
            "pages": 10478,
            "count": 1,
            "data": [
                {
                    "SECURITY_CODE": "001388",
                    "SECURITY_NAME_ABBR": "信通电子",
                    "FREE_DATE": "2026-01-05 00:00:00",
                    "FREE_SHARES": 3120.0,
                    "CURRENT_FREE_SHARES": 62.7311,
                    "LIFT_MARKET_CAP": 2711.238142,
                    "FREE_SHARES_TYPE": "首发机构配售股份",
                    "BATCH_HOLDER_NUM": 1,
                }
            ],
        },
        "success": True,
        "message": "ok",
        "code": 0,
    }
).encode("utf-8")

DC_LHB = json.dumps(
    {
        "version": "e375de90",
        "result": {
            "pages": 2598,
            "count": 1,
            "data": [
                {
                    "SECURITY_INNER_CODE": "1000001536",
                    "SECURITY_CODE": "000001",
                    "SECUCODE": "000001.SZ",
                    "TRADE_DATE": "2026-09-01 00:00:00",
                    "SECURITY_NAME_ABBR": "平安银行",
                    "SUPERDEAL_INFLOW": 481167664,
                    "SUPERDEAL_OUTFLOW": 484846848,
                    "PRIME_INFLOW": 56309200,
                    "CLOSE_PRICE": 11.92,
                    "CHANGE_RATE": 1.7065,
                    "TRADE_MARKET_CODE": "069001002001",
                    "TURNOVERRATE": 0.7849,
                    "PRIME_COST": 11.865129270441,
                    "PE_DYNAMIC": 4.50106135,
                    "PRIME_COST_20DAYS": 11.394420260429,
                    "PRIME_COST_60DAYS": 11.027562011798,
                    "ORG_PARTICIPATE": 0.473204,
                    "PARTICIPATE_TYPE": "3",
                    "BIGDEAL_INFLOW": 421304528,
                    "BIGDEAL_OUTFLOW": 361316144,
                    "BUY_SUPERDEAL_RATIO": 0.2662,
                    "BUY_BIGDEAL_RATIO": 0.2331,
                    "RATIO": 0.4993,
                    "RATIO_3DAYS": 0.4749,
                    "RATIO_50DAYS": 0.40542,
                    "TOTALSCORE": 77.94636268,
                    "RANK_UP": 298,
                    "RANK": 186,
                    "FOCUS": 90,
                    "SECURITY_TYPE_CODE": "058001001",
                    "LISTING_STATE": "0",
                }
            ],
        },
        "success": True,
        "message": "ok",
        "code": 0,
    }
).encode("utf-8")

# 腾讯港股（hk00700，100 前缀布局，约 41 字段）—— 2026-09-01 真实抓包
TENCENT_HK = (
    'v_hk00700="100~腾讯控股~00700~441.400~453.000~446.400~20289781.0~0~0~'
    "441.400~0~0~0~0~0~0~0~0~0~441.400~0~0~0~0~0~0~0~0~0~20289781.0~"
    "2026/09/01 16:08:15~-11.600~-2.56~447.600~440.600~441.400~"
    '20289781.0~8990301596.734~0~16.14"'
).encode("gbk")

# 腾讯美股（usAAPL，200 前缀布局，约 40 字段）—— 2026-09-01 真实抓包
# 注意 [29] 为空的占位字段（两段 ~~），不可省略，否则后续索引整体错位
TENCENT_US = (
    'v_usAAPL="200~苹果~AAPL.OQ~316.85~319.70~319.60~41242724~0~0~'
    "316.98~120~0~0~0~0~0~0~0~0~317.00~280~0~0~0~0~0~0~0~0~"
    "~2026-08-31 16:00:01~-2.85~-0.89~321.24~312.80~USD~"
    '41242724~13038267059~0.28~36.34"'
).encode("gbk")

# 新浪港股（hk00700，逗号分隔约 19 字段）—— 2026-09-01 真实抓包
SINA_HK = (
    'var hq_str_hk00700="TENCENT,腾讯控股,446.400,453.000,447.600,440.600,'
    "441.400,-11.600,-2.561,441.39999,441.60001,8990301596,20289781,"
    '0.000,0.000,675.134,411.000,2026/09/01,16:08";'
).encode("gbk")


# --------------------------------------------------------------------------- #
# 逐笔成交（腾讯）
# --------------------------------------------------------------------------- #
class TestTencentTickSource:
    def test_parse_basic(self):
        ticks = TencentTickSource().parse_ticks(TENCENT_TICK.decode("gbk"), "sh600519")
        assert len(ticks) == 3
        first = ticks[0]
        assert first.time == "09:25:01"
        assert first.price == 1295.0
        assert first.volume == 42300  # 423 手 → 42300 股
        assert first.num == 0
        assert first.buyorsell == 1  # S = 卖

    def test_direction_mapping(self):
        ticks = TencentTickSource().parse_ticks(TENCENT_TICK.decode("gbk"), "sh600519")
        assert [t.buyorsell for t in ticks] == [1, 0, 2]  # S / B / M

    def test_volume_is_shares_not_lots(self):
        ticks = TencentTickSource().parse_ticks(TENCENT_TICK.decode("gbk"), "sh600519")
        # 第 2 笔 6 手 → 600 股；成交额 775848 元 ⇒ 均价 1293.08 元
        assert ticks[1].volume == 600
        assert ticks[1].price == 1292.22

    def test_pagination_requests_sequential_pages(self):
        src, http = _attach(TencentTickSource(), TENCENT_TICK)
        src.fetch_ticks("sh600519", max_pages=3)
        assert len(http.calls) == 3
        assert "p=0" in http.calls[0] and "p=1" in http.calls[1] and "p=2" in http.calls[2]

    def test_empty_response_returns_empty(self):
        body = b'v_detail_data_sh600519=[0,""]'
        assert TencentTickSource().parse_ticks(body.decode("gbk"), "sh600519") == []

    def test_malformed_raises_deprecated(self):
        with pytest.raises(SourceDeprecated):
            TencentTickSource().parse_ticks("<html>404</html>", "sh600519")

    def test_per_page_constant(self):
        assert TICKS_PER_PAGE == 70


# --------------------------------------------------------------------------- #
# 分时成交（东财）
# --------------------------------------------------------------------------- #
class TestEastmoneyTrendsSource:
    def test_parse_minutes(self):
        src = EastmoneyTrendsSource()
        pts = src.parse_minutes(EM_TRENDS.decode("utf-8"))
        assert len(pts) == 2
        assert pts[0].time == "2026-09-01 09:30"
        assert pts[0].price == 1295.0  # 该分钟收盘价
        assert pts[0].avg_price == 1295.0
        assert pts[0].volume == 42300  # 423 手 → 股
        assert pts[0].amount == 54778500.0

    def test_fetch_minutes(self):
        src, http = _attach(EastmoneyTrendsSource(), EM_TRENDS)
        pts = src.fetch_minutes("sh600519")
        assert len(pts) == 2
        assert "secid=1.600519" in http.calls[0]
        assert "trends2" in http.calls[0]

    def test_sz_secid(self):
        src, http = _attach(EastmoneyTrendsSource(), EM_TRENDS)
        src.fetch_minutes("000001.SZ")
        assert "secid=0.000001" in http.calls[0]


# --------------------------------------------------------------------------- #
# 外盘 / 大盘统计（腾讯）
# --------------------------------------------------------------------------- #
class TestTencentGlobalSource:
    def test_parse(self):
        quotes = TencentGlobalSource().parse(GLOBAL_HF.decode("gbk"), ["CL", "GC"])
        assert len(quotes) == 2
        cl = quotes[0]
        assert cl.code == "CL"
        assert cl.price == 87.84
        assert cl.last_close == 85.76
        assert cl.open == 86.31
        assert cl.high == 88.13
        assert cl.low == 86.13
        assert cl.extra["name"] == "纽约原油"
        assert cl.extra["pct_change"] == 2.43
        assert cl.extra["volume_unit"] == "lot"

    def test_pct_change_matches_formula(self):
        """字段 [1] 为涨跌幅%：(最新价 - 昨收) / 昨收。"""
        q = TencentGlobalSource().parse(GLOBAL_HF.decode("gbk"), ["CL"])[0]
        assert q.extra["pct_change"] == pytest.approx(
            (q.price - q.last_close) / q.last_close * 100, abs=0.01
        )

    def test_symbol_prefix_optional(self):
        src, http = _attach(TencentGlobalSource(), GLOBAL_HF)
        src.fetch(["hf_CL", "GC"])
        assert http.calls[0].endswith("q=hf_CL,hf_GC")

    def test_malformed_raises(self):
        with pytest.raises(SourceDeprecated):
            TencentGlobalSource().parse("no data", ["CL"])


class TestTencentMarketStatSource:
    def test_parse(self):
        quotes = TencentMarketStatSource().parse(
            MARKET_STAT.decode("gbk"), ["sh000001", "sz399001"]
        )
        assert len(quotes) == 2
        sh = quotes[0]
        assert sh.code == "sh000001"
        assert sh.price == 3979.89
        assert sh.volume == 57353894900  # 手 → 股
        assert sh.amount == 944307560000.0  # 万元 → 元
        assert sh.extra["name"] == "上证指数"
        assert sh.extra["pct_change"] == -0.16
        assert sh.extra["total_market_cap_yi"] == 704688.35

    def test_last_close_derived(self):
        sh = TencentMarketStatSource().parse(MARKET_STAT.decode("gbk"), ["sh000001"])[0]
        assert sh.last_close == pytest.approx(3979.89 + 6.41)


# --------------------------------------------------------------------------- #
# 排行
# --------------------------------------------------------------------------- #
class TestEastmoneyRankSource:
    def test_rows_field_renaming(self):
        src, _ = _attach(EastmoneyRankSource(), RANK_CLIST)
        rows = src.fetch_rows("all_a", limit=2)
        assert len(rows) == 2
        assert rows[0]["code"] == "601123"
        assert rows[0]["name"] == "N马矿"
        assert rows[0]["price"] == 26.0
        assert rows[0]["change_pct"] == 290.98
        assert rows[0]["symbol"] == "sh601123"
        assert rows[0]["total"] == 5555

    def test_market_bit_to_symbol(self):
        src, _ = _attach(EastmoneyRankSource(), RANK_CLIST)
        rows = src.fetch_rows("all_a", limit=2)
        assert rows[1]["symbol"] == "sz301697"  # f13=0 → 深

    def test_board_rows_keep_bk_code(self):
        src, _ = _attach(EastmoneyRankSource(), RANK_BOARD)
        rows = src.fetch_rows("industry", sort="main_net", extra_fields=["f62", "f184"])
        assert rows[0]["symbol"] == "BK0433"  # 板块不加 sh/sz 前缀
        assert rows[0]["main_net"] == 2876480848.0
        assert rows[0]["main_net_ratio"] == 7.43

    def test_sort_field_mapping(self):
        src, http = _attach(EastmoneyRankSource(), RANK_CLIST)
        src.fetch_rows("all_a", sort="main_net", limit=5)
        assert "fid=f62" in http.calls[0]

    def test_market_filter_mapping(self):
        src, http = _attach(EastmoneyRankSource(), RANK_CLIST)
        src.fetch_rows("star", limit=5)
        assert "fs=m:1+t:23" in http.calls[0]

    def test_as_quote_normalizes_volume(self):
        """clist 的 f5 是手，进 Quote 必须 ×100 → 股。"""
        src, _ = _attach(EastmoneyRankSource(), RANK_CLIST)
        quotes = src.fetch(limit=2)
        assert quotes[0].volume == 77992300
        assert quotes[0].price == 26.0
        assert quotes[0].amount == 1978792312.0

    def test_symbol_helper(self):
        assert _symbol_from_market("600519", 1) == "sh600519"
        assert _symbol_from_market("000001", 0) == "sz000001"
        assert _symbol_from_market("BK0433", 90) == "BK0433"


# --------------------------------------------------------------------------- #
# 资金流
# --------------------------------------------------------------------------- #
class TestEastmoneyFundFlowSource:
    def test_realtime_uses_fltt2(self):
        src, http = _attach(EastmoneyFundFlowSource(), FLOW_REALTIME)
        src.fetch_flow(["sh600519"])
        assert "fltt=2" in http.calls[0]
        assert "secids=1.600519" in http.calls[0]
        assert "1.600519" in http.calls[0]

    def test_realtime_values(self):
        src, _ = _attach(EastmoneyFundFlowSource(), FLOW_REALTIME)
        rows = src.fetch_flow(["sh600519"])
        row = rows[0]
        assert row["price"] == 1299.56  # fltt=2 已是元
        assert row["main_net_ratio"] == 5.2  # 已是百分数
        assert row["main_net"] == 220531760.0
        assert row["super_large_net"] == 168048960.0
        assert row["large_net"] == 52482800.0

    def test_main_equals_super_large_plus_large(self):
        src, _ = _attach(EastmoneyFundFlowSource(), FLOW_REALTIME)
        row = src.fetch_flow(["sh600519"])[0]
        assert row["main_net"] == pytest.approx(row["super_large_net"] + row["large_net"])

    def test_history_column_order(self):
        """历史资金流第 3 列是小单、第 4 列是中单（与实时接口顺序不同）。"""
        src, _ = _attach(EastmoneyFundFlowSource(), FLOW_HISTORY)
        rows = src.fetch_history("sh600519", count=5)
        row = rows[0]
        assert row["date"] == "2026-09-01"
        assert row["main_net"] == 220531760.0
        assert row["small_net"] == -97733.0
        assert row["medium_net"] == -220434016.0
        assert row["large_net"] == 52482800.0
        assert row["super_large_net"] == 168048960.0

    def test_history_conservation(self):
        src, _ = _attach(EastmoneyFundFlowSource(), FLOW_HISTORY)
        row = src.fetch_history("sh600519")[0]
        total = row["main_net"] + row["medium_net"] + row["small_net"]
        assert total == pytest.approx(0.0, abs=1000)

    def test_history_period_mapping(self):
        src, http = _attach(EastmoneyFundFlowSource(), FLOW_HISTORY)
        src.fetch_history("sh600519", period="15min", count=3)
        assert "klt=15" in http.calls[0]
        assert "lmt=3" in http.calls[0]


# --------------------------------------------------------------------------- #
# 涨跌停池
# --------------------------------------------------------------------------- #
class TestEastmoneyLimitPoolSource:
    def test_price_scale(self):
        """p 为价格 ×1000 整数（7220 → 7.22 元）。"""
        src = EastmoneyLimitPoolSource()
        rows = src.parse_pool(ZT_POOL.decode("utf-8"))
        assert rows[0]["price"] == pytest.approx(7.22)

    def test_fields(self):
        rows = EastmoneyLimitPoolSource().parse_pool(ZT_POOL.decode("utf-8"))
        r = rows[0]
        assert r["code"] == "000635"
        assert r["symbol"] == "sz000635"
        assert r["name"] == "英力特"  # 去掉名称中的全角空格
        assert r["change_pct"] == pytest.approx(10.06, abs=0.01)
        assert r["limit_up_days"] == 1
        assert r["industry"] == "化学原料"
        assert r["total"] == 83
        assert r["stat_days"] == 1

    def test_seal_time_formatting(self):
        rows = EastmoneyLimitPoolSource().parse_pool(ZT_POOL.decode("utf-8"))
        assert rows[0]["first_seal_time"] == "09:25:00"

    def test_hhmmss_helper(self):
        assert _hhmmss(92500) == "09:25:00"
        assert _hhmmss(143005) == "14:30:05"
        assert _hhmmss(None) == ""
        assert _hhmmss(0) == "00:00:00"

    def test_endpoint_variants(self):
        src, http = _attach(EastmoneyLimitPoolSource(), ZT_POOL)
        src.fetch_pool("zb", date="20260901")
        assert "getTopicZBPool" in http.calls[0]

    def test_page_is_zero_based(self):
        src, http = _attach(EastmoneyLimitPoolSource(), ZT_POOL)
        src.fetch_pool("zt", page=3)
        assert "Pageindex=2" in http.calls[0]


# --------------------------------------------------------------------------- #
# 沪深港通
# --------------------------------------------------------------------------- #
class TestEastmoneyNorthboundSource:
    def test_directions(self):
        src, _ = _attach(EastmoneyNorthboundSource(), NORTHBOUND)
        rows = src.fetch_northbound()
        assert {r["direction"] for r in rows} == {"hk2sh", "sh2hk"}
        assert rows[0]["name"] == "沪股通（北向）"
        assert rows[0]["net_amount"] == 0.0
        assert rows[0]["date"] == "2026-09-01"

    def test_closed_flag(self):
        src, _ = _attach(EastmoneyNorthboundSource(), NORTHBOUND)
        rows = {r["direction"]: r for r in src.fetch_northbound()}
        assert rows["hk2sh"]["closed"] is True  # status=3
        assert rows["sh2hk"]["closed"] is False  # status=1


# --------------------------------------------------------------------------- #
# 基本面：F10 / 公告 / 研报
# --------------------------------------------------------------------------- #
class TestEastmoneyProfileSource:
    def test_profile(self):
        src, http = _attach(EastmoneyProfileSource(), PROFILE)
        p = src.fetch_profile("sh600519")
        assert "secid=1.600519" in http.calls[0]
        assert p["code"] == "600519"
        assert p["name"] == "贵州茅台"
        assert p["price"] == pytest.approx(1299.56)  # ×100 → 元
        assert p["volume"] == 3266400  # 手 → 股
        assert p["amount"] == 4242440861.0
        assert p["outer_volume"] == 1589400
        assert p["pe_dynamic"] == pytest.approx(18.25)
        assert p["pb"] == pytest.approx(6.47)
        assert p["roe"] == pytest.approx(16.75)
        assert p["high_52w"] == pytest.approx(1539.98)
        assert p["low_52w"] == pytest.approx(1151.01)
        assert p["industry"] == "白酒Ⅱ"

    def test_empty_data_raises(self):
        src, _ = _attach(EastmoneyProfileSource(), b'{"rc":0,"data":null}')
        with pytest.raises(SourceDeprecated):
            src.fetch_profile("sh600519")


class TestEastmoneyNoticeSource:
    def test_notices(self):
        src, http = _attach(EastmoneyNoticeSource(), NOTICE)
        rows = src.fetch_notices(["sh600519"], size=3)
        assert "stock_list=600519" in http.calls[0]
        assert rows[0]["art_code"] == "AN202608141827994407"
        assert rows[0]["categories"] == ["其他"]
        assert rows[0]["codes"] == ["600519"]
        assert "业绩说明会" in rows[0]["title"]

    def test_batch_codes(self):
        src, http = _attach(EastmoneyNoticeSource(), NOTICE)
        src.fetch_notices(["sh600519", "sz000001"])
        assert "stock_list=600519,000001" in http.calls[0]


class TestEastmoneyResearchSource:
    def test_reports(self):
        src, http = _attach(EastmoneyResearchSource(), RESEARCH)
        rows = src.fetch_reports("sh600519", size=3)
        assert "code=600519" in http.calls[0]
        r = rows[0]
        assert r["stock_code"] == "600519"
        assert r["org"] == "西南证券"
        assert r["rating"] == "买入"
        assert r["eps_this_year"] == pytest.approx(69.83)
        assert r["pe_this_year"] == pytest.approx(18.59)
        assert r["researcher"] == "朱会振,舒尚立"

    def test_market_wide_when_no_symbol(self):
        src, http = _attach(EastmoneyResearchSource(), RESEARCH)
        src.fetch_reports()
        assert "code=" in http.calls[0]
        assert not http.calls[0].endswith("code=600519")


# --------------------------------------------------------------------------- #
# 基本面：datacenter-web 报表族
# --------------------------------------------------------------------------- #
class TestEastmoneyPerformanceSource:
    def test_rows(self):
        src, http = _attach(EastmoneyPerformanceSource(), DC_SUCCESS)
        rows = src.fetch_performance(symbol="sh600519", size=2)
        assert "reportName=RPT_LICO_FN_CPD" in http.calls[0]
        assert 'SECURITY_CODE="600519"' in http.calls[0].replace("%22", '"').replace("%3D", "=")
        r = rows[0]
        assert r["code"] == "600519"
        assert r["eps"] == pytest.approx(35.57)
        assert r["revenue"] == pytest.approx(92278072083.21)
        assert r["net_profit"] == pytest.approx(44516880421.86)
        assert r["roe"] == pytest.approx(16.75)
        assert r["industry"] == "白酒Ⅱ"

    def test_report_error_raises(self):
        src, _ = _attach(EastmoneyPerformanceSource(), DC_ERROR)
        with pytest.raises(WebSourceError, match="报表查询失败"):
            src.fetch_performance(symbol="sh600519")


class TestEastmoneyShareholderSource:
    def test_free_holders_filter_uses_secucode(self):
        src, http = _attach(EastmoneyShareholderSource(), DC_HOLDERS)
        rows = src.fetch_free_holders("sh600519", size=3)
        url = http.calls[0].replace("%22", '"').replace("%3D", "=")
        assert 'SECUCODE="600519.SH"' in url
        assert "sortColumns=HOLDER_RANK" in url
        assert rows[0]["holder_name"] == "中国贵州茅台酒厂(集团)有限责任公司"
        assert rows[0]["hold_ratio"] == pytest.approx(57.995)
        assert rows[0]["holder_rank"] == 1

    def test_holder_num_switches_report(self):
        body = json.dumps(
            {
                "version": "x",
                "result": {
                    "pages": 1,
                    "count": 1,
                    "data": [
                        {
                            "HOLDER_NUM": 296404,
                            "PRE_HOLDER_NUM": 243159,
                            "HOLDER_NUM_CHANGE": 53245,
                            "HOLDER_NUM_RATIO": 21.897,
                            "END_DATE": "2026-06-30 00:00:00",
                            "AVG_MARKET_CAP": 4999795.0,
                            "AVG_HOLD_NUM": 4217.49,
                            "HOLD_NOTICE_DATE": "2026-08-15 00:00:00",
                        }
                    ],
                },
                "success": True,
                "message": "ok",
                "code": 0,
            }
        ).encode("utf-8")
        src, http = _attach(EastmoneyShareholderSource(), body)
        src.fetch_holder_num("sh600519")
        assert "RPT_HOLDERNUMLATEST" in http.calls[0].replace("%20", " ")
        # 调用后必须还原报表名，避免实例被污染
        assert src.report == "RPT_F10_EH_FREEHOLDERS"


class TestEastmoneyBlockTradeSource:
    def test_block_trades(self):
        src, http = _attach(EastmoneyBlockTradeSource(), DC_BLOCK)
        rows = src.fetch_block_trades(symbol="sz301571", size=3)
        assert "RPT_DATA_BLOCKTRADE" in http.calls[0]
        r = rows[0]
        assert r["code"] == "301571"
        assert r["price"] == pytest.approx(65.79)
        assert r["premium_ratio"] == pytest.approx(-0.019961269179)
        assert r["volume"] == 99000.0
        assert "中金财富" in r["buyer"]

    def test_date_filter(self):
        src, http = _attach(EastmoneyBlockTradeSource(), DC_BLOCK)
        src.fetch_block_trades(date="2026-09-01")
        url = http.calls[0].replace("%22", '"').replace("%3D", "=")
        assert 'TRADE_DATE="2026-09-01"' in url


class TestEastmoneyUnlockSource:
    def test_unlocks(self):
        src, http = _attach(EastmoneyUnlockSource(), DC_UNLOCK)
        rows = src.fetch_unlocks(begin="2026-01-01", end="2026-12-31", size=3)
        assert "RPT_LIFT_STAGE" in http.calls[0]
        r = rows[0]
        assert r["code"] == "001388"
        assert r["free_shares"] == 3120.0
        assert r["shares_type"] == "首发机构配售股份"
        assert r["batch_holder_num"] == 1

    def test_range_filter(self):
        src, http = _attach(EastmoneyUnlockSource(), DC_UNLOCK)
        src.fetch_unlocks(begin="2026-01-01", end="2026-12-31")
        url = http.calls[0].replace("%3E%3D", ">=").replace("%3C%3D", "<=").replace("%27", "'")
        assert "FREE_DATE>='2026-01-01'" in url
        assert "FREE_DATE<='2026-12-31'" in url


# --------------------------------------------------------------------------- #
# 龙虎榜
# --------------------------------------------------------------------------- #
class TestEastmoneyTopListSource:
    def test_parse_mapping(self):
        raw = {
            "SECURITY_CODE": "000001",
            "SECUCODE": "000001.SZ",
            "SECURITY_NAME_ABBR": "平安银行",
            "TRADE_DATE": "2026-09-01 00:00:00",
            "CLOSE_PRICE": 11.92,
            "CHANGE_RATE": 1.7065,
            "TURNOVERRATE": 0.7849,
            "SUPERDEAL_INFLOW": 481167664,
            "SUPERDEAL_OUTFLOW": 484846848,
            "PRIME_INFLOW": 56309200,
            "BIGDEAL_INFLOW": 421304528,
            "BIGDEAL_OUTFLOW": 361316144,
            "PARTICIPATE_TYPE": "3",
        }
        r = parse_lhb_row(raw)
        # 主力净流入 = 超大单净 + 大单净，与 PRIME_INFLOW 一致
        assert r["prime_net"] == 56309200.0
        assert r["super_net"] == pytest.approx(481167664 - 484846848)
        assert r["big_net"] == pytest.approx(421304528 - 361316144)
        assert r["trade_date"] == "2026-09-01"
        assert r["reason"] == "日换手率达20%"
        assert r["name"] == "平安银行"

    def test_fetch_lhb(self):
        src, http = _attach(EastmoneyTopListSource(), DC_LHB)
        rows = src.fetch_lhb(date="2026-09-01", size=50)
        assert "RPT_DMSK_TS_STOCKNEW" in http.calls[0]
        # TRADE_DATE 过滤必须用完整字面值
        url = unquote(http.calls[0])
        assert "TRADE_DATE='2026-09-01 00:00:00'" in url
        assert "sortColumns=RANK" in http.calls[0]
        r = rows[0]
        assert r["code"] == "000001"
        assert r["prime_net"] == 56309200.0
        assert r["rank"] == 186
        assert r["focus"] == 90

    def test_fetch_lhb_by_code(self):
        src, http = _attach(EastmoneyTopListSource(), DC_LHB)
        rows = src.fetch_lhb_by_code("sh600519", date="2026-09-01")
        url = unquote(http.calls[0])
        assert 'SECURITY_CODE="600519"' in url
        assert rows[0]["code"] == "000001"

    def test_session_longhu(self, monkeypatch):
        import tstdx.web.longhu as _longhu
        from tstdx.web import session

        class _Fake(EastmoneyTopListSource):
            def fetch_lhb(self, date=None, *, symbol=None, page=1, size=50):
                return [
                    parse_lhb_row(
                        {
                            "SECURITY_CODE": "000001",
                            "SECUCODE": "000001.SZ",
                            "SECURITY_NAME_ABBR": "平安银行",
                            "TRADE_DATE": "2026-09-01 00:00:00",
                            "CLOSE_PRICE": 11.92,
                            "CHANGE_RATE": 1.7065,
                            "TURNOVERRATE": 0.7849,
                            "SUPERDEAL_INFLOW": 481167664,
                            "SUPERDEAL_OUTFLOW": 484846848,
                            "PRIME_INFLOW": 56309200,
                            "BIGDEAL_INFLOW": 421304528,
                            "BIGDEAL_OUTFLOW": 361316144,
                            "PARTICIPATE_TYPE": "3",
                        }
                    )
                ]

        monkeypatch.setattr(_longhu, "EastmoneyTopListSource", _Fake)
        rows = session.WebQuoteSession.longhu(date="2026-09-01", size=50)
        assert rows[0]["code"] == "000001"
        assert rows[0]["prime_net"] == 56309200.0


# --------------------------------------------------------------------------- #
# 港股 / 美股（腾讯外部市场布局 + 新浪港股）
# --------------------------------------------------------------------------- #
class TestExternalQuotes:
    def test_tencent_hk_parse(self):
        q = HkSource().parse(TENCENT_HK.decode("gbk"), ["hk00700"])[0]
        assert q.code == "hk00700"
        assert q.price == pytest.approx(441.4)
        assert q.last_close == pytest.approx(453.0)
        assert q.open == pytest.approx(446.4)
        assert q.high == pytest.approx(447.6)
        assert q.low == pytest.approx(440.6)
        # 港股成交量单位为「股」，volume_scale=1，无需 ×100
        assert q.volume == 20289781
        assert q.amount == pytest.approx(8990301596.734)
        # 外部市场无五档盘口，应为空而非垃圾值
        assert q.bid == [] and q.ask == []
        assert q.extra["currency"] == "HKD"
        assert q.extra["pct_change"] == pytest.approx(-2.56)
        assert q.extra["pe"] == pytest.approx(16.14)

    def test_tencent_us_parse(self):
        q = UsSource().parse(TENCENT_US.decode("gbk"), ["usAAPL"])[0]
        assert q.code == "usAAPL"
        assert q.price == pytest.approx(316.85)
        assert q.last_close == pytest.approx(319.70)
        assert q.open == pytest.approx(319.60)
        assert q.high == pytest.approx(321.24)
        assert q.low == pytest.approx(312.80)
        assert q.volume == 41242724
        assert q.amount == pytest.approx(13038267059)
        assert q.extra["currency"] == "USD"
        assert q.extra["pct_change"] == pytest.approx(-0.89)

    def test_sina_hk_parse(self):
        q = SinaHkSource().parse(SINA_HK.decode("gbk"), ["hk00700"])[0]
        assert q.code == "hk00700"
        assert q.extra["name"] == "腾讯控股"
        assert q.price == pytest.approx(441.4)
        assert q.last_close == pytest.approx(453.0)
        assert q.open == pytest.approx(446.4)
        assert q.high == pytest.approx(447.6)
        assert q.low == pytest.approx(440.6)
        assert q.volume == 20289781
        assert q.amount == pytest.approx(8990301596)
        assert q.extra["currency"] == "HKD"
        assert q.extra["change"] == pytest.approx(-11.6)
        assert q.extra["pct_change"] == pytest.approx(-2.561)
        assert q.extra["date"] == "2026/09/01"
        assert q.extra["time"] == "16:08"

    def test_tencent_hk_build_url(self):
        src = HkSource()
        url = src.build_url(["00700"])
        assert "hk00700" in url
        assert src.source_name == "hk"

    def test_tencent_us_build_url(self):
        src = UsSource()
        url = src.build_url(["AAPL"])
        assert "usAAPL" in url
        assert src.source_name == "us"

    def test_sina_hk_build_url(self):
        src = SinaHkSource()
        url = src.build_url(["00700"])
        assert "hk00700" in url

    def test_session_hk_us(self, monkeypatch):
        import tstdx.web.adapters as _adapters
        from tstdx.web import session

        class _FakeHk(HkSource):
            def fetch(self, symbols, **kw):
                return self.parse(TENCENT_HK.decode("gbk"), symbols)

        class _FakeUs(UsSource):
            def fetch(self, symbols, **kw):
                return self.parse(TENCENT_US.decode("gbk"), symbols)

        monkeypatch.setattr(_adapters, "HkSource", _FakeHk)
        monkeypatch.setattr(_adapters, "UsSource", _FakeUs)
        h = session.WebQuoteSession.hk_quotes(["00700"])[0]
        assert h.price == pytest.approx(441.4)
        assert h.extra["currency"] == "HKD"
        u = session.WebQuoteSession.us_quotes(["AAPL"])[0]
        assert u.price == pytest.approx(316.85)
        assert u.extra["currency"] == "USD"


# --------------------------------------------------------------------------- #
# 新浪个股新闻（vCB_AllNewsStock.php HTML 解析）
# --------------------------------------------------------------------------- #
SINA_NEWS_HTML = (
    "</a> <br>&nbsp;&nbsp;&nbsp;&nbsp;2026-09-01&nbsp;00:00&nbsp;&nbsp;"
    "<a target='_blank' href='https://cj.sina.cn/articles/view/7517400647/abc'>"
    "白酒行业进入存量博弈时代</a> <br>"
    "&nbsp;&nbsp;&nbsp;&nbsp;2026-08-31&nbsp;23:27&nbsp;&nbsp;"
    "<a target='_blank' href='https://finance.sina.com.cn/stock/a/xyz'>"
    "茅台王子酒在新疆大巴扎解锁C端触达</a> <br>"
    "&nbsp;&nbsp;&nbsp;&nbsp;2026-08-30&nbsp;09:00&nbsp;&nbsp;"
    "<font color='#ff0000'>研报</font>&nbsp;&nbsp;"
    "<a target='_blank' href='https://stock.finance.sina.com.cn/rep/zzz'>"
    "贵州茅台2026中报点评</a>"
)


class TestSinaNewsSource:
    def test_parse_news(self):
        items = SinaNewsSource().parse_news(SINA_NEWS_HTML, limit=10)
        assert len(items) == 3
        assert items[0]["datetime"] == "2026-09-01 00:00"
        assert items[0]["title"] == "白酒行业进入存量博弈时代"
        assert items[0]["url"].startswith("https://")
        assert items[2]["tag"] == "研报"  # 分类标签提取
        assert items[0]["tag"] == ""

    def test_parse_news_dedup_and_limit(self):
        # 重复条目去重
        dup = SINA_NEWS_HTML + SINA_NEWS_HTML
        items = SinaNewsSource().parse_news(dup, limit=2)
        assert len(items) == 2  # limit 生效
        # 全量去重后仍为 3
        all_items = SinaNewsSource().parse_news(dup, limit=100)
        assert len(all_items) == 3

    def test_parse_news_empty(self):
        assert SinaNewsSource().parse_news("<html>无新闻</html>") == []

    def test_clean(self):
        assert _clean("&nbsp;<font>研报</font>&nbsp;") == "研报"

    def test_session_news(self, monkeypatch):
        import tstdx.web.news as _news
        from tstdx.web import session

        class _Fake(SinaNewsSource):
            def fetch_news(self, symbol, *, page=1, num=20, tag=None):
                # 与真实实现一致的客户端分页 + tag 过滤语义
                return self.parse_news(SINA_NEWS_HTML, tag=tag, page=page, num=num)

        monkeypatch.setattr(_news, "SinaNewsSource", _Fake)
        rows = session.WebQuoteSession.news("sh600519", num=10)
        assert rows[0]["title"] == "白酒行业进入存量博弈时代"
        assert rows[2]["tag"] == "研报"

    def test_parse_news_pagination(self):
        """客户端分页：第 1 页取前 2 条，第 2 页取剩余 1 条。"""
        # 全量 3 条
        all_items = SinaNewsSource().parse_news(SINA_NEWS_HTML, page=1, num=20)
        assert len(all_items) == 3
        p1 = SinaNewsSource().parse_news(SINA_NEWS_HTML, page=1, num=2)
        assert len(p1) == 2
        assert p1[0]["datetime"] == "2026-09-01 00:00"
        p2 = SinaNewsSource().parse_news(SINA_NEWS_HTML, page=2, num=2)
        assert len(p2) == 1
        assert p2[0]["datetime"] == "2026-08-30 09:00"  # 第 3 条
        # 越界页返回空
        assert SinaNewsSource().parse_news(SINA_NEWS_HTML, page=3, num=2) == []

    def test_parse_news_tag_filter(self):
        """按 tag 过滤仅保留「研报」类。"""
        items = SinaNewsSource().parse_news(SINA_NEWS_HTML, tag="研报")
        assert len(items) == 1
        assert items[0]["title"] == "贵州茅台2026中报点评"
        # 不存在的 tag → 空
        assert SinaNewsSource().parse_news(SINA_NEWS_HTML, tag="公告") == []

    def test_session_news_pages_aggregation(self, monkeypatch):
        """pages>1 跨页聚合去重：3 条新闻分 2 页（num=2）拼接为 3 条。"""
        import tstdx.web.news as _news
        from tstdx.web import session

        class _Fake(SinaNewsSource):
            def fetch_news(self, symbol, *, page=1, num=20, tag=None):
                return self.parse_news(SINA_NEWS_HTML, tag=tag, page=page, num=num)

        monkeypatch.setattr(_news, "SinaNewsSource", _Fake)
        rows = session.WebQuoteSession.news("sh600519", num=2, pages=2)
        assert len(rows) == 3
        assert {r["datetime"] for r in rows} == {
            "2026-09-01 00:00",
            "2026-08-31 23:27",
            "2026-08-30 09:00",
        }


# --------------------------------------------------------------------------- #
# 腾讯 K 线：港股 / 美股（含成交量单位按市场区分）
# --------------------------------------------------------------------------- #
KLINE_HK_JSON = (
    '{"code":0,"msg":"","data":{"hk00700":{"day":'
    '[["2026-08-31","451.000","453.000","456.200","446.000","20149770.000",'
    '{"cqr":"2026-08-31"}]]}}}'
)
KLINE_US_JSON = (
    '{"code":0,"msg":"","data":{"usAAPL":{"day":'
    '[["2026-08-31","316.85","319.70","321.24","312.80","41242724.000",'
    '{"cqr":"x"}]]}}}'
)
KLINE_A_JSON = (
    '{"code":0,"msg":"","data":{"sh600519":{"qfqday":'
    '[["2026-08-31","1299.00","1299.56","1307.99","1286.10","32664.00",'
    '"4242440861.000"]]}}}'
)


class TestKlineExternal:
    def test_hk_volume_in_shares(self):
        bars = KlineSource().parse_bars(KLINE_HK_JSON, "hk00700")
        assert len(bars) == 1
        # 腾讯港股量已是「股」，不缩放
        assert bars[0].volume == 20149770
        assert bars[0].amount == 0.0  # 第 7 元素为元数据 dict，无成交额

    def test_us_volume_in_shares(self):
        bars = KlineSource().parse_bars(KLINE_US_JSON, "usAAPL")
        assert len(bars) == 1
        assert bars[0].volume == 41242724
        assert bars[0].amount == 0.0

    def test_a_volume_hand_in_lots(self):
        bars = KlineSource().parse_bars(KLINE_A_JSON, "sh600519")
        assert len(bars) == 1
        # A 股腾讯量返回「手」，×100 到股
        assert bars[0].volume == 32664 * 100
        assert bars[0].amount == 4242440861.0

    def test_session_klines_hk_us(self, monkeypatch):
        import tstdx.web.adapters as _adapters
        from tstdx.web import session

        store = {"hk00700": KLINE_HK_JSON, "usAAPL": KLINE_US_JSON}

        class _Fake(KlineSource):
            def fetch_bars(self, symbol, **kw):
                return self.parse_bars(store[symbol], symbol)

        monkeypatch.setattr(_adapters, "KlineSource", _Fake)
        hk = session.WebQuoteSession().klines("hk00700", period="day", count=1)
        assert hk[0].volume == 20149770
        us = session.WebQuoteSession().klines("usAAPL", period="day", count=1)
        assert us[0].volume == 41242724
