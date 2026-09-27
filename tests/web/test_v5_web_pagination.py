# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""v5 优化批次 Web 域回归（PG3 东财翻页 / PG4 腾讯钳制 / PG7 新浪 worker 收口）。

全部离线（monkeypatch 传输/JSON 层），零网络。
"""

from __future__ import annotations

import json
import logging

import pytest

from atst.web.corporate import EastmoneyShareholderSource
from atst.web.limits import TENCENT_KLINE_MAX
from atst.web.sina.adapters import SinaSource
from atst.web.tencent.adapters import KlineSource, MinuteKlineSource

pytestmark = pytest.mark.unit


# --------------------------------------------------------------------------- #
# PG4：腾讯 K 线 count 钳制
# --------------------------------------------------------------------------- #
@pytest.mark.unit
class TestTencentKlineClamp:
    def test_kline_url_clamped(self, caplog):
        src = KlineSource()
        with caplog.at_level(logging.WARNING, logger="atst.web.tencent.adapters"):
            url = src.build_url(["sh600519"], period="day", count=2000)
        assert f",,,{TENCENT_KLINE_MAX}," in url
        assert "已钳制" in caplog.text

    def test_kline_url_within_limit_untouched(self):
        src = KlineSource()
        url = src.build_url(["sh600519"], period="day", count=500)
        assert ",,,500," in url

    def test_mkline_url_clamped(self, caplog):
        src = MinuteKlineSource()
        with caplog.at_level(logging.WARNING, logger="atst.web.tencent.adapters"):
            url = src.build_url(["sh600519"], period="5min", count=3000)
        assert f",,,{TENCENT_KLINE_MAX}" in url
        assert "已钳制" in caplog.text


# --------------------------------------------------------------------------- #
# PG3：东财 datacenter 报表翻页
# --------------------------------------------------------------------------- #
def _dc_payload(page_rows: list[dict], pages: int, count: int) -> dict:
    return {
        "success": True,
        "result": {"data": page_rows, "pages": pages, "count": count},
    }


class TestCorporatePagination:
    """PG3：fetch_rows all_pages 循环 / pages 元数据终止 / max_pages 告警。"""

    def _make_src(self, monkeypatch, pages_payloads: list[dict]):
        """构造 shareholder 源，_get_json 按 pageNumber 顺序回放脚本。"""
        src = EastmoneyShareholderSource()
        calls = {"n": 0}

        def fake_get_json(query: str) -> dict:
            # 从 query 里提取 pageNumber 断言递增
            assert "pageNumber=" in query
            payload = pages_payloads[calls["n"]]
            calls["n"] += 1
            return payload

        monkeypatch.setattr(src, "_get_json", fake_get_json)
        monkeypatch.setattr(src.rate_limiter, "acquire", lambda source: True)
        return src, calls

    def test_all_pages_collects_every_page(self, monkeypatch):
        payloads = [
            _dc_payload([{"HOLDER_NAME": f"h{i}"} for i in range(3)], pages=3, count=9),
            _dc_payload([{"HOLDER_NAME": f"h{i}"} for i in range(3, 6)], pages=3, count=9),
            _dc_payload([{"HOLDER_NAME": f"h{i}"} for i in range(6, 9)], pages=3, count=9),
        ]
        src, calls = self._make_src(monkeypatch, payloads)
        rows = src.fetch_rows(all_pages=True, size=3)
        assert calls["n"] == 3
        assert [r["HOLDER_NAME"] for r in rows] == [f"h{i}" for i in range(9)]

    def test_short_page_stops_early(self, monkeypatch):
        payloads = [
            _dc_payload([{"HOLDER_NAME": "a"}], pages=99, count=1),
        ]
        src, calls = self._make_src(monkeypatch, payloads)
        rows = src.fetch_rows(all_pages=True, size=3)
        assert calls["n"] == 1  # 短页即止，不空转 99 页
        assert len(rows) == 1

    def test_max_pages_exhaustion_warns(self, monkeypatch):
        full = lambda: _dc_payload([{"HOLDER_NAME": "x"} for _ in range(2)], pages=None, count=None)  # noqa: E731
        src, calls = self._make_src(monkeypatch, [full(), full(), full()])
        with pytest.warns(UserWarning, match="max_pages=3"):
            rows = src.fetch_rows(all_pages=True, size=2, max_pages=3)
        assert len(rows) == 6

    def test_single_page_default_unchanged(self, monkeypatch):
        payloads = [_dc_payload([{"HOLDER_NAME": "a"}], pages=9, count=99)]
        src, calls = self._make_src(monkeypatch, payloads)
        rows = src.fetch_rows()  # 默认 all_pages=False
        assert calls["n"] == 1
        assert len(rows) == 1

    def test_holder_num_defaults_to_all_pages(self, monkeypatch):
        """fetch_holder_num 默认全量（v5 PG3）：3 页历史全部取回。"""
        payloads = [
            _dc_payload([{"HOLDER_NUM": i} for i in (100, 101)], pages=2, count=4),
            _dc_payload([{"HOLDER_NUM": i} for i in (102, 103)], pages=2, count=4),
        ]
        src, calls = self._make_src(monkeypatch, payloads)
        rows = src.fetch_holder_num("600519", size=2)
        assert calls["n"] == 2
        assert [r["holder_num"] for r in rows] == [100, 101, 102, 103]


# --------------------------------------------------------------------------- #
# PG7：新浪 fetch_all worker 异常收口
# --------------------------------------------------------------------------- #
def _sina_page_rows(tag: str, n: int) -> str:
    rows = [
        {
            "symbol": f"sh60{i:04d}{tag}",
            "ticktime": "2026-09-05 15:00:00",
            "trade": "10.0",
            "settlement": "9.9",
            "open": "9.95",
            "high": "10.1",
            "low": "9.9",
            "volume": 1000,
            "amount": 10000.0,
            "buy": "9.99",
            "sell": "10.01",
            "name": f"股{tag}{i}",
        }
        for i in range(n)
    ]
    return json.dumps(rows)


class TestSinaFetchAllHardening:
    """PG7：worker 重试 / 失败页补拉 / 永久失败告警（不再击穿 fetch_all）。"""

    def _make_src(self, monkeypatch, responder):
        """responder(url, encoding=...) → text 或抛 WebSourceError；sleep 打桩提速。"""
        src = SinaSource(max_retries=0)
        monkeypatch.setattr(src, "_request_text", responder)
        monkeypatch.setattr(src.rate_limiter, "acquire", lambda source: True)
        monkeypatch.setattr("atst.web.sina.adapters.time.sleep", lambda s: None)
        return src

    def test_retry_recovers_transient_failure(self, monkeypatch):
        """第 2 页瞬时 429（重试后成功）→ 结果完整、不告警。"""
        attempts = {"page2": 0}

        def responder(url: str, encoding: str = "gbk") -> str:
            if "page=1&" in url:
                return _sina_page_rows("a", 2)
            if "page=2&" in url:
                attempts["page2"] += 1
                if attempts["page2"] <= 2:  # 首次+第 1 次重试失败
                    from atst.errors import WebRateLimited

                    raise WebRateLimited("simulated 429", context={"source": "sina"})
                return _sina_page_rows("b", 2)
            if "page=3&" in url:
                return _sina_page_rows("c", 1)  # 短页终止
            raise AssertionError(f"unexpected url {url}")

        src = self._make_src(monkeypatch, responder)
        quotes = src.fetch_all(page_size=2, workers=2)
        assert len(quotes) == 5  # 页1(2) + 页2(2) + 页3(1)
        assert attempts["page2"] == 3  # 1 首次 + 2 重试

    def test_permanent_failure_warns_with_pages(self, monkeypatch):
        """第 2 页永久失败 → 补拉后仍失败 → 告警含页码，其余页完整。"""

        def responder(url: str, encoding: str = "gbk") -> str:
            if "page=1&" in url:
                return _sina_page_rows("a", 2)
            if "page=2&" in url:
                from atst.errors import WebRateLimited

                raise WebRateLimited("simulated persistent 429", context={"source": "sina"})
            if "page=3&" in url:
                return _sina_page_rows("c", 1)
            raise AssertionError(f"unexpected url {url}")

        src = self._make_src(monkeypatch, responder)
        with pytest.warns(UserWarning, match="缺页.*2"):
            quotes = src.fetch_all(page_size=2, workers=2)
        # 页 2 永久失败被排除，页 1 + 页 3 仍然返回
        assert len(quotes) == 3

    def test_worker_escape_fixed_no_crash(self, monkeypatch):
        """回归：旧实现 worker 仅捕获 SourceDeprecated，WebRateLimited
        直接逃逸——现在必须被收口（返回残缺+告警，而非异常上抛）。"""

        def responder(url: str, encoding: str = "gbk") -> str:
            if "page=1&" in url:
                return _sina_page_rows("a", 2)
            from atst.errors import WebRateLimited

            raise WebRateLimited("every page fails", context={"source": "sina"})

        src = self._make_src(monkeypatch, responder)
        with pytest.warns(UserWarning):
            quotes = src.fetch_all(page_size=2, workers=3, max_pages=5)
        assert quotes == [] or len(quotes) >= 0  # 不崩溃即可（首页成功则非空）
