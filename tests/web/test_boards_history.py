"""板块与历史行情适配器测试（真实抓包样本罐头，全部离线）。"""

from __future__ import annotations

import json

import pytest

from atst.domain.models import Bar
from atst.errors import SourceDeprecated
from atst.web.base import HttpResponse, RateLimiter
from atst.web.boards import (
    EastmoneyBoardSource,
    SinaBoardListSource,
    SinaIndustryBoardSource,
    TencentBoardRankSource,
)
from atst.web.eastmoney.adapters import EastmoneyHistoryKlineSource
from atst.web.sina.adapters import SinaHistoryKlineSource


class FakeHttp:
    def __init__(self, body: bytes):
        self.body = body
        self.calls: list[str] = []

    def get(self, url, **kwargs):  # noqa: ARG002
        self.calls.append(url)
        return HttpResponse(200, self.body, {})

    def close(self):
        pass


# --------------------------------------------------------------------------- #
# 新浪行业板块列表
# --------------------------------------------------------------------------- #
SINA_BOARDS = (
    'var S_Finance_bankuai_sinaindustry = {"new_blhy":"new_blhy,玻璃行业,19,'
    "13.049166,0.093333,0.720396,452830284,10610274008,sh600552,2.852,"
    '18.390,0.510,凯盛科技","new_cbzz":"new_cbzz,船舶制造,8,19.40,'
    '-0.285,-1.447,130367043,3550526362,sh601890,0.687,8.800,0.060,亚星锚链"};'
).encode("gbk")


class TestSinaBoards:
    def test_parse(self):
        src = SinaIndustryBoardSource(max_retries=0)
        src.client = FakeHttp(SINA_BOARDS)
        src.rate_limiter = RateLimiter()
        boards = src.fetch_boards()
        assert len(boards) == 2
        b0 = boards[0]
        assert b0["code"] == "new_blhy"
        assert b0["name"] == "玻璃行业"
        assert b0["count"] == 19
        assert b0["pct_change"] == pytest.approx(0.720396, abs=1e-4)
        assert b0["volume"] == 452830284  # 股
        assert b0["amount"] == 10610274008  # 元
        assert b0["leader"] == "sh600552"
        assert b0["leader_name"] == "凯盛科技"

    def test_format_change(self):
        """前缀缺失 → SourceDeprecated。"""
        src = SinaIndustryBoardSource(max_retries=0)
        src.client = FakeHttp(b"var something_else = {};")
        src.rate_limiter = RateLimiter()
        with pytest.raises(SourceDeprecated):
            src.fetch_boards()


# --------------------------------------------------------------------------- #
# 新浪板块列表（newFLJK：概念 / 地域 / 新版行业）
# --------------------------------------------------------------------------- #
#: 真实抓包样本（2026-09-01）：13 列 CSV，量=股、额=元
SINA_CONCEPT_BOARDS = (
    'var S_Finance_bankuai_class = {"gn_hwqc":"gn_hwqc,华为汽车,97,'
    "25.035473684211,-0.25684210526316,-1.01549461663,1946565402,"
    '34277211962,sh605068,9.984,20.380,1.850,明新旭腾",'
    '"gn_BCdc":"gn_BCdc,BC电池,26,62.931923,1.901231,3.125691,'
    '89123456,5678901234,sh688778,12.310,88.880,9.730,厦钨新能"};'
).encode("gbk")

SINA_REGION_BOARDS = (
    'var S_Finance_bankuai_area = {"diyu_650000":"diyu_650000,'
    "新疆维吾尔自治区,62,10.752131147541,0.10131147540984,0.95120824996152,"
    '2393852982,21951925533,sh600540,10.072,6.120,0.560,新赛股份"};'
).encode("gbk")

SINA_INDUSTRY_NEW = (
    'var S_Finance_bankuai_industry = {"hangye_ZA01":"hangye_ZA01,农业,16,'
    "11.604375,1.17875,11.306276602122,1673422509,13947219156,bj920403,"
    '21.305,25.850,4.540,康农种业"};'
).encode("gbk")


class TestSinaBoardList:
    def _src(self, body: bytes) -> SinaBoardListSource:
        src = SinaBoardListSource(max_retries=0)
        src.client = FakeHttp(body)
        src.rate_limiter = RateLimiter()
        return src

    def test_concept_parse(self):
        """概念列表：13 列语义 + 量=股/额=元 + 领涨股。"""
        boards = self._src(SINA_CONCEPT_BOARDS).fetch_boards("concept")
        assert len(boards) == 2
        b0 = boards[0]
        assert b0["code"] == "gn_hwqc"
        assert b0["name"] == "华为汽车"
        assert b0["count"] == 97
        assert b0["pct_change"] == pytest.approx(-1.01549461663, abs=1e-6)
        assert b0["volume"] == 1946565402  # 股
        assert b0["amount"] == 34277211962  # 元
        assert b0["leader"] == "sh605068"
        assert b0["leader_name"] == "明新旭腾"
        assert b0["leader_pct"] == pytest.approx(9.984, abs=1e-6)

    def test_region_and_industry(self):
        """地域 / 新版行业共用同一解析（含北交所领涨股 bj 前缀）。"""
        regions = self._src(SINA_REGION_BOARDS).fetch_boards("region")
        assert regions[0]["code"] == "diyu_650000"
        assert regions[0]["name"] == "新疆维吾尔自治区"
        inds = self._src(SINA_INDUSTRY_NEW).fetch_boards("industry")
        assert inds[0]["code"] == "hangye_ZA01"
        assert inds[0]["leader"] == "bj920403"

    def test_board_url_param(self):
        """board → param 映射：concept→class / region→area / industry→industry。"""
        src = SinaBoardListSource(max_retries=0)
        assert "param=class" in src.build_url([], board="concept")
        assert "param=area" in src.build_url([], board="region")
        assert "param=industry" in src.build_url([], board="industry")

    def test_format_change(self):
        """前缀缺失 → SourceDeprecated。"""
        src = self._src(b"var S_Finance_other = {};")
        with pytest.raises(SourceDeprecated):
            src.fetch_boards("concept")


# --------------------------------------------------------------------------- #
# 腾讯板块排行
# --------------------------------------------------------------------------- #
TENCENT_RANK = json.dumps(
    {
        "code": 0,
        "data": [
            {
                "bd_name": "数字媒体",
                "bd_code": "pt01801767",
                "bd_zxj": "1633.43",
                "bd_zd": "109.51",
                "bd_zdf": "7.19",
                "bd_zs": "0.05",
                "nzg_code": "sz300413",
                "nzg_name": "芒果超媒",
                "nzg_zxj": "16.98",
                "nzg_zd": "2.83",
                "nzg_zdf": "20.00",
                "bd_zdf5": "8.92",
                "bd_zdf20": "2.58",
            }
        ],
    }
).encode("utf-8")


class TestTencentRank:
    def test_parse(self):
        src = TencentBoardRankSource(max_retries=0)
        src.client = FakeHttp(TENCENT_RANK)
        src.rate_limiter = RateLimiter()
        boards = src.fetch_boards("industry")
        assert len(boards) == 1
        b = boards[0]
        assert b["code"] == "pt01801767"
        assert b["name"] == "数字媒体"
        assert b["pct_change"] == 7.19
        assert b["leader"] == "sz300413"
        assert b["leader_name"] == "芒果超媒"
        assert b["leader_pct"] == 20.0
        assert b["pct_change_5d"] == 8.92

    def test_url_board_type(self):
        src = TencentBoardRankSource(max_retries=0)
        src.client = FakeHttp(TENCENT_RANK)
        src.rate_limiter = RateLimiter()
        src.fetch_boards("concept")
        assert "t=02/averatio" in src.client.calls[0]

    def test_error_code(self):
        src = TencentBoardRankSource(max_retries=0)
        src.client = FakeHttp(b'{"code": -1, "data": []}')
        src.rate_limiter = RateLimiter()
        with pytest.raises(SourceDeprecated):
            src.fetch_boards()


# --------------------------------------------------------------------------- #
# 东财板块
# --------------------------------------------------------------------------- #
EM_BOARDS = json.dumps(
    {
        "data": {
            "diff": [
                {"f12": "BK1300", "f14": "院线", "f2": "-", "f3": "-"},
                {"f12": "BK0740", "f14": "教育", "f2": "1024.5", "f3": "1.5"},
            ]
        },
    }
).encode("utf-8")


class TestEastmoneyBoards:
    def test_boards_dash_safe(self):
        """盘后 f2/f3 为 '-' 字符串 → 按 0 处理。"""
        src = EastmoneyBoardSource(max_retries=0)
        src.client = FakeHttp(EM_BOARDS)
        src.rate_limiter = RateLimiter()
        boards = src.fetch_boards("concept")
        assert len(boards) == 2
        assert boards[0] == {"code": "BK1300", "name": "院线", "pct_change": 0.0}
        assert boards[1]["pct_change"] == 1.5

    def test_members(self):
        members_payload = json.dumps(
            {
                "data": {
                    "diff": [
                        {"f12": "603323", "f14": "苏农银行", "f2": "5.1", "f3": "0.9"},
                    ]
                },
            }
        ).encode("utf-8")
        src = EastmoneyBoardSource(max_retries=0)
        src.client = FakeHttp(members_payload)
        src.rate_limiter = RateLimiter()
        members = src.fetch_members("BK0475")
        assert members == [{"code": "603323", "name": "苏农银行", "price": 5.1, "pct_change": 0.9}]
        assert "fs=b:BK0475" in src.client.calls[0]


# --------------------------------------------------------------------------- #
# 新浪历史 K 线
# --------------------------------------------------------------------------- #
SINA_KLINES = json.dumps(
    [
        {
            "day": "2026-08-31 14:55:00",
            "open": "1289.500",
            "high": "1289.730",
            "low": "1288.700",
            "close": "1289.010",
            "volume": "63000",
            "amount": "81221914.1958",
        },
        {
            "day": "2026-08-27",
            "open": "1304.000",
            "high": "1305.000",
            "low": "1288.000",
            "close": "1292.300",
            "volume": "2476747",
        },
    ]
).encode("utf-8")


class TestSinaHistory:
    def test_parse(self):
        """分线含 amount；日线无 amount 字段 → 0。"""
        src = SinaHistoryKlineSource(max_retries=0)
        src.client = FakeHttp(SINA_KLINES)
        src.rate_limiter = RateLimiter()
        bars = src.fetch_bars("sh600519", period="day", count=2)
        assert len(bars) == 2
        assert all(isinstance(b, Bar) for b in bars)
        assert bars[0].close == 1289.010
        assert bars[0].volume == 63000  # 已是股
        assert bars[0].amount == pytest.approx(81221914.1958)
        assert bars[1].amount == 0.0  # 日线无 amount 字段

    def test_scale_in_url(self):
        src = SinaHistoryKlineSource(max_retries=0)
        src.client = FakeHttp(SINA_KLINES)
        src.rate_limiter = RateLimiter()
        src.fetch_bars("600519", period="5min")
        assert "scale=5" in src.client.calls[0]


# --------------------------------------------------------------------------- #
# 东财历史 K 线
# --------------------------------------------------------------------------- #
EM_KLINES = json.dumps(
    {
        "data": {
            "klines": [
                "2026-08-31,1297.99,1299.52,1305.00,1286.00,23248,3003033720.00",
                "2026-08-31 15:00,1289.01,1299.52,1299.52,1288.90,2573,334006421.00",
            ]
        },
    }
).encode("utf-8")


class TestEastmoneyHistory:
    def test_parse_shares_and_amount(self):
        """手→股换算 + 成交额（元）保留。"""
        src = EastmoneyHistoryKlineSource(max_retries=0)
        src.client = FakeHttp(EM_KLINES)
        src.rate_limiter = RateLimiter()
        bars = src.fetch_bars("600519", period="day")
        assert len(bars) == 2
        assert bars[0].datetime == "2026-08-31"
        assert bars[0].volume == 23248 * 100  # 手 → 股
        assert bars[0].amount == 3003033720.00
        assert bars[1].datetime == "2026-08-31 15:00"

    def test_adjust_in_url(self):
        src = EastmoneyHistoryKlineSource(max_retries=0)
        src.client = FakeHttp(EM_KLINES)
        src.rate_limiter = RateLimiter()
        src.fetch_bars("600519", adjust="hfq")
        assert "fqt=2" in src.client.calls[0]
        src.fetch_bars("600519", adjust="")
        assert "fqt=0" in src.client.calls[1]


# --------------------------------------------------------------------------- #
# 东财历史 K 线：港股 / 美股（2026-09-01 真实抓包样本）
# --------------------------------------------------------------------------- #
#: 港股 secid=116.00700 klt=5（8 列 = dt,o,c,h,l,vol(股),amount,pct）
EM_HK_KLINES = json.dumps(
    {
        "rc": 0,
        "data": {
            "code": "00700",
            "market": 116,
            "name": "腾讯控股",
            "decimal": 3,
            "klines": [
                "2026-08-25 09:35,438.400,440.600,444.600,438.400,1713100,754887020.000,1.41",
                "2026-08-25 09:40,440.600,439.600,440.800,438.000,572200,251227960.000,0.64",
            ],
        },
    }
).encode("utf-8")

#: 美股 secid=105.AAPL klt=5（8 列同上，量已是股）
EM_US_KLINES = json.dumps(
    {
        "rc": 0,
        "data": {
            "code": "AAPL",
            "market": 105,
            "name": "苹果",
            "decimal": 3,
            "klines": [
                "2026-08-25 21:35,310.935,311.260,313.580,310.750,1648619,513643470.000,0.91",
                "2026-08-25 21:40,311.260,311.080,311.720,310.530,444875,138384922.000,0.38",
            ],
        },
    }
).encode("utf-8")

#: 美股市场段未命中（105/106/107 探测）：空市场返回 data=None
EM_US_MISS = json.dumps({"rc": 100, "rt": 1, "data": None}).encode("utf-8")


class TestEastmoneyHistoryExternal:
    """东财 push2his 港股/美股分钟 K 线（腾讯 mkline 不支持 hk/us 的替代源）。"""

    def _src(self, bodies: list[bytes]) -> EastmoneyHistoryKlineSource:
        src = EastmoneyHistoryKlineSource(max_retries=0)
        src.client = _QueueHttp(bodies)
        src.rate_limiter = RateLimiter()
        return src

    def test_hk_secid_and_volume_shares(self):
        """港股 secid=116.00700；量已是「股」不 ×100。"""
        src = self._src([EM_HK_KLINES])
        bars = src.fetch_bars("hk00700", period="5min", count=2)
        assert "secid=116.00700" in src.client.calls[0]
        assert len(bars) == 2
        assert bars[0].datetime == "2026-08-25 09:35"
        assert bars[0].volume == 1713100  # 股，不缩放
        assert bars[0].amount == 754887020.000  # 港元

    def test_us_market_segment_probe(self):
        """美股字母代码市场段未知：105 未命中（data=None 空 klines）自动换 106。"""
        src = self._src([EM_US_MISS, EM_US_MISS, EM_US_KLINES])
        bars = src.fetch_bars("usAAPL", period="5min", count=2)
        assert "secid=105.AAPL" in src.client.calls[0]
        assert "secid=106.AAPL" in src.client.calls[1]
        assert "secid=107.AAPL" in src.client.calls[2]
        assert len(bars) == 2
        assert bars[0].volume == 1648619  # 股，不缩放
        assert bars[0].amount == 513643470.000  # 美元

    def test_us_uppercase_code_in_secid(self):
        """小写输入 usAAPL → 归一后 code 大写进入 secid。"""
        src = self._src([EM_US_MISS, EM_US_MISS, EM_US_KLINES])
        src.fetch_bars("usaapl", period="5min")
        assert "secid=105.AAPL" in src.client.calls[0]

    def test_a_share_scale_unchanged(self):
        """回归：A 股仍走 手→股 ×100（外部市场扩展不得影响原口径）。"""
        src = self._src([EM_KLINES])
        bars = src.fetch_bars("sh600519", period="day", count=2)
        assert bars[0].volume == 23248 * 100

    def test_empty_then_return(self):
        """最后一候选市场命中空数据直接返回 []（不无限探测）。"""
        src = self._src([EM_US_MISS, EM_US_MISS, EM_US_MISS])
        bars = src.fetch_bars("usZZZZ", period="5min")
        assert bars == []
        assert len(src.client.calls) == 3  # 105/106/107 各试一次

    def test_parse_market_aware_scale(self):
        """parse_bars 带 symbol 时量口径按市场；缺省（旧签名）保持 ×100。"""
        src = EastmoneyHistoryKlineSource(max_retries=0)
        assert src.parse_bars(EM_HK_KLINES.decode("utf-8"), "hk00700")[0].volume == 1713100
        assert src.parse_bars(EM_KLINES.decode("utf-8"))[0].volume == 23248 * 100


class _QueueHttp:
    """按序返回多个罐头响应的 Fake HTTP（市场段探测场景）。"""

    def __init__(self, bodies: list[bytes]):
        self.bodies = list(bodies)
        self.calls: list[str] = []

    def get(self, url, **kwargs):  # noqa: ARG002
        self.calls.append(url)
        body = self.bodies.pop(0) if self.bodies else b"{}"
        return HttpResponse(200, body, {})

    def close(self):
        pass
