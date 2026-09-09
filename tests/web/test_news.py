"""资讯 / 研报 / 机构调研 / 股东 源离线测试（niuniu 审计缺口补全）。

分两部分：
* 源级：直接用罐头客户端验证 :class:`EastmoneyNewsSource` /
  :class:`EastmoneyResearchVisitSource` / :class:`EastmoneyResearchSource` 的解析。
* 门面级：monkeypatch ``_shared_http`` 注入假客户端，验证
  ``UnifiedQuoteAPI`` → ``WebQuoteSession`` Mixin → Web 源 的完整调用链
  （覆盖 news_financial / research_reports / research_visits /
  free_holders / holder_num）。

全部罐头数据，不发起任何真实 HTTP。
"""

from __future__ import annotations

import json

import pytest

import tstdx.web._facade_mixin_info as mixin_info
import tstdx.web._facade_mixin_news as mixin_news
from tstdx.facade.api import UnifiedQuoteAPI
from tstdx.web.base import HttpResponse
from tstdx.web.corporate import EastmoneyResearchSource
from tstdx.web.news import EastmoneyNewsSource, EastmoneyResearchVisitSource


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


def _j(obj: dict) -> bytes:
    return json.dumps(obj).encode("utf-8")


# -- 罐头 payload ----------------------------------------------------------- #
NEWS_LIST = _j(
    {
        "error_code": 0,
        "data": {
            "list": [
                {
                    "id": "n1",
                    "title": "央行开展逆回购操作",
                    "content": "央行今日开展 2000 亿元逆回购操作。",
                    "summary": "央行逆回购",
                    "ctime": 1750000000,
                    "url": "https://kuaixun.eastmoney.com/n1",
                    "labels": "宏观",
                },
                {
                    "id": "n2",
                    "title": "A股收评：沪指震荡整理",
                    "digest": "A股收评摘要",
                    "summary": "收评摘要",
                    "update_time": "2026-08-14 15:00",
                },
            ],
            "total": 2,
        },
    }
)

# 研报（reportapi.eastmoney.com/report/list）
REPORTS = _j(
    {
        "data": [
            {
                "infoCode": "AP1234567",
                "title": "平安银行(000001)：基本面稳健，维持买入",
                "stockCode": "000001",
                "stockName": "平安银行",
                "orgSName": "中信建投",
                "researcher": "张三、李四",
                "publishDate": "2026-08-01",
                "emRatingName": "买入",
                "indvInduName": "银行",
                "predictThisYearEps": "1.5",
                "predictThisYearPe": "8.3",
                "predictNextYearEps": "1.7",
                "predictNextYearPe": "7.4",
            }
        ]
    }
)

# 机构调研（datacenter-web RPT_ORG_SURVEY_DET）
VISITS = _j(
    {
        "success": True,
        "result": {
            "data": [
                {
                    "SECURITY_CODE": "000001",
                    "SECURITY_NAME_ABBR": "平安银行",
                    "RECEIVED_DATE": "2026-07-15",
                    "ORG_NAME": "易方达基金",
                    "RESEARCH_TYPE": "现场调研",
                    "SURVEY_SUMMARY": "了解公司零售转型进展",
                    "CONTENT": "调研纪要全文内容",
                }
            ]
        },
    }
)

# 十大流通股东（RPT_F10_EH_FREEHOLDERS）
FREE_HOLDERS = _j(
    {
        "success": True,
        "result": {
            "data": [
                {
                    "HOLDER_NAME": "全国社保基金一一八组合",
                    "HOLDER_RANK": "1",
                    "HOLD_NUM": "12345678",
                    "HOLD_RATIO": "2.34",
                    "HOLD_NUM_CHANGE": "增持",
                    "HOLDER_TYPE": "机构",
                    "SHARES_TYPE": "流通A股",
                    "END_DATE": "2026-06-30",
                }
            ]
        },
    }
)

# 股东户数（RPT_HOLDERNUMLATEST）
HOLDER_NUM = _j(
    {
        "success": True,
        "result": {
            "data": [
                {
                    "HOLDER_NUM": "123456",
                    "PRE_HOLDER_NUM": "130000",
                    "HOLDER_NUM_CHANGE": "-6544",
                    "HOLDER_NUM_RATIO": "-5.03",
                    "END_DATE": "2026-06-30",
                    "AVG_MARKET_CAP": "1.2",
                    "AVG_HOLD_NUM": "812.45",
                    "HOLD_NOTICE_DATE": "2026-07-30",
                }
            ]
        },
    }
)


# -- 源级测试 --------------------------------------------------------------- #
class TestEastmoneyNewsSource:
    def test_parses_news_list(self) -> None:
        src = EastmoneyNewsSource(client=FakeHttpClient(kuaixun=NEWS_LIST))
        rows = src.fetch_news(page=1, size=30)
        assert len(rows) == 2
        assert rows[0]["title"] == "央行开展逆回购操作"
        assert rows[0]["url"] == "https://kuaixun.eastmoney.com/n1"
        assert rows[0]["labels"] == "宏观"
        # ctime（秒）→ 毫秒字符串
        assert rows[0]["time"].isdigit() and len(rows[0]["time"]) == 13
        # 第二条走 update_time 字符串 + digest 兜底标题
        assert rows[1]["title"] == "A股收评：沪指震荡整理"
        assert rows[1]["time"] == "2026-08-14 15:00"

    def test_empty_payload(self) -> None:
        src = EastmoneyNewsSource(client=FakeHttpClient(kuaixun=_j({"data": {}})))
        assert src.fetch_news() == []

    def test_build_url_raises(self) -> None:
        src = EastmoneyNewsSource(client=FakeHttpClient(kuaixun=NEWS_LIST))
        with pytest.raises(NotImplementedError):
            src.build_url([])


class TestEastmoneyResearchVisitSource:
    def test_parses_visits(self) -> None:
        src = EastmoneyResearchVisitSource(client=FakeHttpClient(RPT_ORG_SURVEY_DET=VISITS))
        rows = src.fetch_visits("000001")
        assert len(rows) == 1
        r = rows[0]
        assert r["code"] == "000001"
        assert r["name"] == "平安银行"
        assert r["date"] == "2026-07-15"
        assert r["org"] == "易方达基金"
        assert r["type"] == "现场调研"
        assert r["summary"] == "了解公司零售转型进展"
        assert r["content"] == "调研纪要全文内容"


class TestEastmoneyResearchSource:
    def test_parses_reports(self) -> None:
        src = EastmoneyResearchSource(client=FakeHttpClient(**{"/report/list": REPORTS}))
        rows = src.fetch_reports("000001")
        assert len(rows) == 1
        r = rows[0]
        assert r["info_code"] == "AP1234567"
        assert r["org"] == "中信建投"
        assert r["rating"] == "买入"
        assert r["eps_this_year"] == pytest.approx(1.5)
        assert r["pe_next_year"] == pytest.approx(7.4)


# -- 门面级测试（端到端调用链） --------------------------------------------- #
@pytest.fixture
def api(monkeypatch):
    """注入假客户端的 UnifiedQuoteAPI（同时覆盖两个 Mixin 模块）。"""
    bodies = {
        "kuaixun": NEWS_LIST,
        "/report/list": REPORTS,
        "RPT_ORG_SURVEY_DET": VISITS,
        "RPT_F10_EH_FREEHOLDERS": FREE_HOLDERS,
        "RPT_HOLDERNUMLATEST": HOLDER_NUM,
    }
    fake = FakeHttpClient(**bodies)

    def _shared():
        return fake

    monkeypatch.setattr(mixin_news, "_shared_http", _shared)
    monkeypatch.setattr(mixin_info, "_shared_http", _shared)
    return UnifiedQuoteAPI()


class TestNewsFacade:
    def test_news_financial(self, api) -> None:
        rows = api.news_financial(size=30)
        assert rows[0]["title"] == "央行开展逆回购操作"
        assert rows[1]["time"] == "2026-08-14 15:00"

    def test_research_reports(self, api) -> None:
        rows = api.research_reports("000001")
        assert rows[0]["rating"] == "买入"
        assert rows[0]["org"] == "中信建投"

    def test_research_visits(self, api) -> None:
        rows = api.research_visits("000001")
        assert rows[0]["org"] == "易方达基金"
        assert rows[0]["content"] == "调研纪要全文内容"


class TestHolderFacade:
    def test_free_holders(self, api) -> None:
        rows = api.free_holders("000001")
        assert rows[0]["holder_name"] == "全国社保基金一一八组合"
        assert rows[0]["hold_ratio"] == pytest.approx(2.34)
        assert rows[0]["report_date"] == "2026-06-30"

    def test_holder_num(self, api) -> None:
        rows = api.holder_num("000001")
        assert rows[0]["holder_num"] == 123456
        assert rows[0]["prev_holder_num"] == 130000
        assert rows[0]["change"] == -6544
        assert rows[0]["change_ratio"] == pytest.approx(-5.03)
        assert rows[0]["notice_date"] == "2026-07-30"
