"""统一证券搜索 / 指数目录 / 权息别名 测试（离线）。

search_symbols 底层走新浪联想（罐头响应同 test_adapters_ext.py），
重点验证：display 展示名补齐、market 过滤语义、limit 截断，
以及 UnifiedQuoteAPI / WebQuoteSession 两个入口的一致性。
"""

from __future__ import annotations

import pytest

from tstdx.facade.api import UnifiedQuoteAPI
from tstdx.web.facade import WebQuoteSession
from tstdx.web.sources import KNOWN_SOURCES

pytestmark = pytest.mark.unit

#: 两条候选（sh/sz 各一），供 monkeypatch fetch_suggest 返回
_CANDS = [
    {"code": "600519", "name": "贵州茅台", "market": "sh", "symbol": "sh600519"},
    {"code": "000858", "name": "五粮液", "market": "sz", "symbol": "sz000858"},
]


def _fake_fetch_suggest(self, key: str, *, limit: int = 10) -> list[dict[str, str]]:  # noqa: ARG001
    return [dict(c) for c in _CANDS[:limit]]


@pytest.fixture()
def patch_suggest(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("tstdx.web.adapters_ext.SuggestSource.fetch_suggest", _fake_fetch_suggest)


class TestSearchSymbols:
    def test_display_and_fields(self, patch_suggest) -> None:
        rows = WebQuoteSession.search_symbols("maotai")
        assert rows[0] == {
            "code": "600519",
            "name": "贵州茅台",
            "market": "sh",
            "symbol": "sh600519",
            "display": "贵州茅台(600519)",
        }

    def test_market_filter(self, patch_suggest) -> None:
        rows = WebQuoteSession.search_symbols("maotai", market="sz")
        assert [r["code"] for r in rows] == ["000858"]
        assert rows[0]["display"] == "五粮液(000858)"

    def test_market_filter_empty_result(self, patch_suggest) -> None:
        assert WebQuoteSession.search_symbols("maotai", market="hk") == []

    def test_limit_truncates(self, patch_suggest) -> None:
        assert len(WebQuoteSession.search_symbols("maotai", limit=1)) == 1

    def test_api_entry_matches_session(self, patch_suggest) -> None:
        """UnifiedQuoteAPI.search_symbols 与 WebQuoteSession 同实现。"""
        assert UnifiedQuoteAPI.search_symbols("maotai") == WebQuoteSession.search_symbols("maotai")

    def test_existing_candidates_have_display(self, patch_suggest) -> None:
        """候选即使源缺 display 也自动补齐；已有 display 不覆盖。"""
        rows = WebQuoteSession.search_symbols("maotai")
        assert all(it.get("display") for it in rows)


class TestIndexList:
    def test_static_catalog(self) -> None:
        rows = WebQuoteSession.index_list()
        names = {r["name"] for r in rows}
        assert {"上证指数", "深证成指", "创业板指", "沪深300"} <= names
        for r in rows:
            assert r["symbol"] == f"{r['market']}{r['code']}"
            assert r["market"] in ("sh", "sz")

    def test_api_entry_same(self) -> None:
        assert UnifiedQuoteAPI.index_list() == WebQuoteSession.index_list()


class TestCorporateActionAlias:
    def test_alias_delegates_to_capital_changes(self) -> None:
        """corporate_action 是 capital_changes 的语义别名（同一 TDX 通道）。"""
        calls: list[str] = []

        class FakeAPI(UnifiedQuoteAPI):
            def capital_changes(self, symbol: str, *, route=None):  # noqa: ANN001, ANN202
                calls.append(symbol)
                return [{"date": "2026-06-18", "text": "10转4派23.5"}]

        rows = FakeAPI().corporate_action("sh600519")
        assert calls == ["sh600519"]
        assert rows[0]["text"].startswith("10转")

    def test_session_is_http_only(self) -> None:
        """corporate_action 只在 UnifiedQuoteAPI（TDX 通道）提供。"""
        assert callable(getattr(UnifiedQuoteAPI, "corporate_action", None))
        assert not hasattr(WebQuoteSession, "corporate_action")


class TestWencaiFacadeEntry:
    def test_api_entry_delegates(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "tstdx.web.wencai.WencaiSource.fetch_strategy",
            lambda self, query, *, page=1, limit=50: [{"股票代码": "600519"}],
        )
        assert UnifiedQuoteAPI.wencai("连板3板以上", limit=20) == [{"股票代码": "600519"}]
        assert WebQuoteSession.wencai("连板3板以上") == [{"股票代码": "600519"}]

    def test_spec_capability_wired(self) -> None:
        """registry 一致性：wencai 能力有 facade 方法且源已注册。"""
        assert KNOWN_SOURCES["wencai"].capabilities == ("wencai",)
        assert callable(WebQuoteSession.wencai)
        assert callable(UnifiedQuoteAPI.wencai)
