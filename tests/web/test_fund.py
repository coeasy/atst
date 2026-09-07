"""东财基金源（P0-1）测试。

罐头响应仿照真实接口结构（lsjz / fundgz jsonp / fundcode_search.js），
全部离线，不发起真实 HTTP。
"""

from __future__ import annotations

import pytest

from tstdx.errors import SourceDeprecated
from tstdx.web.adapters_fund import FundSource
from tstdx.web.base import HttpResponse
from tstdx.web.sources import FUND, get_source, list_sources


class FakeHttp:
    """按 URL 关键字返回预置响应的假 HTTP 客户端。"""

    def __init__(self, **bodies: bytes):
        self.bodies = bodies
        self.calls: list[str] = []

    def get(self, url, **kwargs):  # noqa: ARG002
        self.calls.append(url)
        for key, body in self.bodies.items():
            if key in url:
                return HttpResponse(200, body, {})
        return HttpResponse(404, b"not found", {})

    def close(self):
        pass


# --------------------------------------------------------------------------- #
# 罐头数据（仿真实结构）
# --------------------------------------------------------------------------- #
NAV = b"""{"Data": {"LSJZList": [
    {"FSRQ": "2026-09-01", "DWJZ": "0.8934", "LJJZ": "1.2874", "JZZZL": "-0.31", "JZZZBL": "0"},
    {"FSRQ": "2026-09-02", "DWJZ": "0.8948", "LJJZ": "1.2888", "JZZZL": "0.16", "JZZZBL": "0"},
    {"FSRQ": "2026-09-03", "DWJZ": "", "LJJZ": "", "JZZZL": "", "JZZZBL": ""}
]}, "ErrCode": 0, "ErrMsg": null}"""

ESTIMATE = (
    b'jsonpgz({"fundcode":"161725","name":"\\u62db\\u5546\\u4e2d\\u8bc1'
    b'\\u767d\\u9152\\u6307\\u6570(LOF)A","jzrq":"2026-09-03","dwjz":"0.8948",'
    b'"gsz":"0.8950","gszzl":"0.02","gztime":"2026-09-04 15:00"});'
)

FUND_LIST = (
    b'var r = [["161725","\\u62db\\u5546\\u4e2d\\u8bc1\\u767d\\u9152'
    b'\\u6307\\u6570(LOF)A","\\u767d\\u9152\\u6307\\u6570\\u578b","zszzbjzs","LOF"],'
    b'["110022","\\u6613\\u65b9\\u8fbe\\u6d88\\u8d39\\u884c\\u4e1a\\u80a1\\u7968",'
    b'"\\u80a1\\u7968\\u578b","yfdxfhygp","YF"]];'
)


def _src(bodies: dict[str, bytes]) -> tuple[FundSource, FakeHttp]:
    http = FakeHttp(**bodies)
    src = FundSource(client=http)
    return src, http


class TestFundSourceRegistration:
    def test_source_registered(self) -> None:
        spec = get_source(FUND)
        assert FUND in list_sources(capability="fund_nav_history")
        assert spec.capabilities == ("fund_nav_history", "fund_estimate", "fund_list")
        assert spec.default_rate == 3


class TestNavHistory:
    def test_parses_rows_oldest_first(self) -> None:
        src, http = _src({"lsjz": NAV})
        rows = src.fetch_nav_history("161725")
        assert len(rows) == 3
        assert rows[0] == {
            "date": "2026-09-01",
            "unit_nav": 0.8934,
            "accum_nav": 1.2874,
            "pct_change": -0.31,
            "bonus_ratio": 0.0,
        }
        # 空字符串净值 → None（新基金当日无净值）
        assert rows[2]["unit_nav"] is None
        assert rows[2]["pct_change"] is None
        assert "fundCode=161725" in http.calls[0]
        assert "pageSize=100" in http.calls[0]
        assert "pageIndex=1" in http.calls[0]

    def test_paging_params(self) -> None:
        src, http = _src({"lsjz": NAV})
        src.fetch_nav_history("161725", page_size=50, page_index=3)
        assert "pageSize=50" in http.calls[0]
        assert "pageIndex=3" in http.calls[0]

    def test_referer_header_set(self) -> None:
        src, _ = _src({"lsjz": NAV})
        assert src.headers.get("Referer") == "http://fundf10.eastmoney.com/"


class TestEstimate:
    def test_parses_jsonp(self) -> None:
        src, _ = _src({"1234567.com.cn/js": ESTIMATE})
        est = src.fetch_estimate("161725")
        assert est["code"] == "161725"
        assert est["name"] == "招商中证白酒指数(LOF)A"
        assert est["dwjz"] == pytest.approx(0.8948)
        assert est["gsz"] == pytest.approx(0.8950)
        assert est["gszzl"] == pytest.approx(0.02)
        assert est["gztime"] == "2026-09-04 15:00"
        assert est["jzrq"] == "2026-09-03"

    def test_non_jsonp_raises_deprecated(self) -> None:
        src, _ = _src({"js": b"<html>error</html>"})
        with pytest.raises(SourceDeprecated):
            src.fetch_estimate("161725")

    def test_dead_endpoint_404_page_raises_deprecated(self) -> None:
        """fundgz 下线（2026 实测返回东财 404 HTML）→ 显式 SourceDeprecated。"""
        dead = b"\n<!doctype html>\n<html>\n<head>\n<title>404</title>"
        src, _ = _src({"1234567.com.cn/js": dead})
        with pytest.raises(SourceDeprecated, match=".*"):
            src.fetch_estimate("161725")


class TestFundList:
    def test_parses_js_array(self) -> None:
        src, _ = _src({"fundcode_search.js": FUND_LIST})
        rows = src.fetch_fund_list()
        assert len(rows) == 2
        assert rows[0] == {
            "code": "161725",
            "name": "招商中证白酒指数(LOF)A",
            "type": "白酒指数型",
            "pinyin": "zszzbjzs",
            "py_abbr": "LOF",
        }
        assert rows[1]["code"] == "110022"

    def test_non_array_raises_deprecated(self) -> None:
        src, _ = _src({"fundcode_search.js": b"var r = {};"})
        with pytest.raises(SourceDeprecated):
            src.fetch_fund_list()


class TestSourceContract:
    def test_build_url_unavailable(self) -> None:
        """数据型源不通过基类 fetch 入口。"""
        src, _ = _src({"x": b"{}"})
        with pytest.raises(NotImplementedError):
            src.build_url(["161725"])
