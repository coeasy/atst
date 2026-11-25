# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""中国银行外汇牌价适配器离线单测（覆盖率补强）。

全部离线：假 HTTP 客户端回放 HTML 罐头；覆盖表格解析的字段映射、
残缺行/空首列跳过、6 列无发布时间、HTTP 失败与 ``parse()`` 的空返回契约。
"""

from __future__ import annotations

import pytest

from atst.errors import WebSourceError
from atst.sources.base import HttpResponse
from atst.sources.boc.adapters import BocSource

pytestmark = pytest.mark.unit


RATES_HTML = """
<table>
<tr><th>货币名称</th><th>现汇买入价</th><th>现钞买入价</th><th>现汇卖出价</th>
<th>现钞卖出价</th><th>中行折算价</th><th>发布时间</th></tr>
<tr><td>美元</td><td>712.34</td><td>711.00</td><td>715.12</td><td>716.00</td>
<td>713.50</td><td>2026.11.24 10:00:00</td></tr>
<tr><td></td><td>1</td><td>2</td><td>3</td><td>4</td><td>5</td><td>2026.11.24</td></tr>
<tr><td>欧元</td><td>1</td><td>2</td><td>3</td></tr>
<tr><td>英镑</td><td>9.10</td><td>8.90</td><td>9.30</td><td>9.50</td><td>9.20</td></tr>
</table>
"""


class FakeHttp:
    """返回预置状态码/响应体的假 HTTP 客户端。"""

    def __init__(self, body: bytes, status: int = 200):
        self.body = body
        self.status = status
        self.calls: list[str] = []

    def get(self, url, **kwargs):  # noqa: ARG002
        self.calls.append(url)
        return HttpResponse(self.status, self.body, {})

    def close(self) -> None:
        pass


def _source(html: str = RATES_HTML, *, status: int = 200) -> tuple[BocSource, FakeHttp]:
    http = FakeHttp(html.encode("utf-8"), status=status)
    src = BocSource(client=http, max_retries=0)
    src.rate_limiter = type("RL", (), {"acquire": staticmethod(lambda *a, **k: None)})()
    return src, http


def test_build_url_is_fixed() -> None:
    """牌价端点固定，不受 symbols 影响。"""
    src, _ = _source()
    assert src.build_url(["USD"]) == BocSource.BASE


def test_parse_rates_maps_fields_and_skips_bad_rows() -> None:
    """7 列映射到具名字段；空首列与不足 6 列的行被跳过。"""
    rows = BocSource.parse_rates(RATES_HTML)
    assert [r["currency"] for r in rows] == ["美元", "英镑"]
    usd = rows[0]
    assert usd == {
        "currency": "美元",
        "buy_rate": pytest.approx(712.34),
        "cash_buy_rate": pytest.approx(711.00),
        "sell_rate": pytest.approx(715.12),
        "cash_sell_rate": pytest.approx(716.00),
        "middle_rate": pytest.approx(713.50),
        "publish_time": "2026.11.24 10:00:00",
    }
    # 6 列没有发布时间：字段仍存在，值为空串
    gbp = rows[1]
    assert gbp["currency"] == "英镑"
    assert gbp["publish_time"] == ""


def test_parse_rates_empty_or_noise_returns_empty() -> None:
    """无表格 / 空串不抛错，返回空列表。"""
    assert BocSource.parse_rates("") == []
    assert BocSource.parse_rates("<div>not a table</div>") == []


def test_fetch_rates_decodes_and_parses() -> None:
    """fetch_rates 走 _request_text(utf-8)，中文货币名不乱码。"""
    src, http = _source()
    rows = src.fetch_rates()
    assert rows[0]["currency"] == "美元"
    assert http.calls == [BocSource.BASE]


def test_fetch_rates_upstream_error() -> None:
    """HTTP 500 以 WebSourceError 干净失败（code E7000）。"""
    src, _ = _source(status=500)
    with pytest.raises(WebSourceError) as caught:
        src.fetch_rates()
    assert caught.value.code == "E7000"
    assert "HTTP 500" in str(caught.value)


def test_parse_returns_empty_quotes() -> None:
    """汇率不属于 Quote 语义：parse() 恒为空。"""
    src, _ = _source()
    assert src.parse(RATES_HTML, ["USD"]) == []
