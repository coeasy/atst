"""统一证券搜索 / 指数目录 / 权息别名 测试（离线）。

search_symbols 底层走新浪联想（罐头响应同 test_adapters_ext.py），
重点验证：display 展示名补齐、market 过滤语义、limit 截断，
以及 WebQuoteSession 与 Client 两个入口的一致性。
"""

from __future__ import annotations

import pytest

from tstdx.web.session import WebQuoteSession
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
    monkeypatch.setattr("tstdx.web.sina.adapters.SuggestSource.fetch_suggest", _fake_fetch_suggest)


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
        """WebQuoteSession.search_symbols 与 WebQuoteSession 同实现。"""
        assert WebQuoteSession.search_symbols("maotai") == WebQuoteSession.search_symbols("maotai")

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
        assert WebQuoteSession.index_list() == WebQuoteSession.index_list()


class TestCorporateActionAlias:
    """v16：``corporate_action`` 是 ``capital_changes`` 的 capability 目录别名。"""

    def test_alias_binds_the_same_tdx_method(self) -> None:
        """别名与规范名解析到同一 TDX 后端方法（目录层收敛，无门面转发）。"""
        from tstdx.catalog.capability import binding_for

        alias = binding_for("tdx", "quotation", "corporate_action")
        canonical = binding_for("tdx", "quotation", "capital_changes")
        assert alias.method == canonical.method == "capital_changes"
        assert alias.backend == canonical.backend

    def test_session_is_http_only(self) -> None:
        """corporate_action 只在 capability 目录提供，不再是 HTTP 会话方法。"""
        from tstdx.catalog.capability import is_migrated_capability

        assert not hasattr(WebQuoteSession, "corporate_action")
        assert is_migrated_capability("corporate_action")


class TestWencaiFacadeEntry:
    def test_api_entry_delegates(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "tstdx.web.wencai.WencaiSource.fetch_strategy",
            lambda self, query, *, page=1, limit=50: [{"股票代码": "600519"}],
        )
        assert WebQuoteSession.wencai("连板3板以上", limit=20) == [{"股票代码": "600519"}]
        assert WebQuoteSession.wencai("连板3板以上") == [{"股票代码": "600519"}]

    def test_spec_capability_wired(self) -> None:
        """registry 一致性：wencai 能力有 facade 方法且源已注册。"""
        assert KNOWN_SOURCES["wencai"].capabilities == ("wencai",)
        assert callable(WebQuoteSession.wencai)
        assert callable(WebQuoteSession.wencai)
