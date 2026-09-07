# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""easyquotation 能力对齐验证测试（P2）。

硬约束（project_memory）：**必须覆盖竞品 easyquotation 的所有功能**，包括
``all()`` / ``get_klines()`` / ``get_stock_market()`` / ``get_index()``。

easyquotation 垫片已于 v1.2.0 移除（docs/migration/README.md），能力面并入
tstdx 原生门面。本文件以**语义对齐契约**固化四入口映射（对照
docs/migration/easyquotation.md）：

===================  =============================  ============================
easyquotation        tstdx（UnifiedQuoteAPI）        语义
===================  =============================  ============================
``hq.all(node)``     :meth:`all_market`              全市场快照（web 独有）
``hq.get_klines``    :meth:`bars`                    K 线（local/tdx/web 三通路）
``hq.get_index()``   :meth:`index_list` + ``index``  指数目录 + 指数行情
``hq.get_stock_market`` / ``real``  :meth:`quotes`   实时行情（批量）
===================  =============================  ============================

全部离线，不触网。
"""

from __future__ import annotations

import inspect
from pathlib import Path
from typing import Any

import pytest

from tstdx.domain.models import Bar, Quote
from tstdx.facade.api import UnifiedQuoteAPI

#: easyquotation 四入口 → tstdx 门面方法（语义对齐契约，数据驱动）。
ALIGNMENT_TABLE: dict[str, str] = {
    "all": "all_market",
    "get_klines": "bars",
    "get_index": "index_list",
    "get_stock_market": "quotes",
}


class TestEntryPointPresence:
    """四入口在 UnifiedQuoteAPI 上必须全部存在且可调用。"""

    def test_all_four_entries_are_callable(self) -> None:
        for _eq_name, tstdx_name in ALIGNMENT_TABLE.items():
            method = getattr(UnifiedQuoteAPI, tstdx_name, None)
            assert method is not None, f"{tstdx_name} 缺失"
            assert callable(method), f"{tstdx_name} 不可调用"

    def test_table_matches_migration_doc(self) -> None:
        """迁移文档声明的 easyquotation 四入口与 tstdx web 目标一致（防漂移）。"""
        doc = Path(__file__).resolve().parents[2] / "docs" / "migration" / "easyquotation.md"
        text = doc.read_text(encoding="utf-8")
        # easyquotation 侧入口名（与 ALIGNMENT_TABLE 键一致）
        for eq_name in ALIGNMENT_TABLE:
            assert eq_name in text, f"迁移文档缺 easyquotation 入口 {eq_name}"
        # tstdx 侧目标（迁移文档用的 web 层命名）必须真实存在
        import tstdx.web as web_mod

        for target in ("get_quotes", "get_kline", "create_source", "WebQuoteSession"):
            assert target in text, f"迁移文档缺 tstdx 目标 {target}"
            assert callable(getattr(web_mod, target, None)), f"tstdx.web 缺 {target}"
        # create_source("sina") 必须具备 fetch_all/fetch（all()/real() 目标）
        sina = web_mod.create_source("sina")
        assert callable(sina.fetch_all) and callable(sina.fetch)
        # index() 目标：WebQuoteSession 必须有 index 方法
        assert callable(web_mod.WebQuoteSession.index)


class TestQuotesSemantics:
    """``quotes`` ↔ easyquotation ``real`` / ``get_stock_market``。"""

    def test_signature_accepts_batch_symbols(self) -> None:
        sig = inspect.signature(UnifiedQuoteAPI.quotes)
        params = list(sig.parameters)
        # (self, symbols, *, route)
        assert params[1] == "symbols"
        assert "route" in params

    def test_returns_quote_models(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Q1-b 合并后 quotes web 路由经 router（内部 from ..web import
        # WebQuoteClient 调用点解析），mock 目标改为 tstdx.web.WebQuoteClient；
        # 断言语义不变（返回 Quote 模型，离线验证）。
        import tstdx.web as web_mod

        class _FakeWeb:
            def __init__(self, **kw: Any) -> None:
                pass

            def close(self) -> None:
                pass

            def quotes(self, syms):  # noqa: ANN001
                return [Quote(code=s) for s in syms]

        monkeypatch.setattr(web_mod, "WebQuoteClient", _FakeWeb)
        api = UnifiedQuoteAPI()
        out = api.quotes("sh600519", route="web")
        assert isinstance(out, list) and all(isinstance(q, Quote) for q in out)
        assert out[0].code == "sh600519"

    def test_quote_field_alignment(self) -> None:
        """OHLC 核心字段与 easyquotation 语义一致（股/元已归一化）。"""
        for field in ("code", "price", "open", "high", "low", "volume", "amount"):
            assert field in Quote.__dataclass_fields__, f"Quote 缺 {field}"


class TestBarsSemantics:
    """``bars`` ↔ easyquotation ``get_klines(symbol, type)``。"""

    def test_signature_accepts_symbol_period(self) -> None:
        sig = inspect.signature(UnifiedQuoteAPI.bars)
        params = list(sig.parameters)
        assert params[1] == "symbol"
        assert "period" in params
        assert "count" in params

    def test_period_aliases_cover_easyquotation_klines_types(self) -> None:
        """easyquotation K 线 type（day/week/month/1min/5min/...）全覆盖。"""
        from tstdx.web.facade import KLINES_PERIOD_ALIASES

        for want in ("day", "week", "month", "1min", "5min", "15min", "30min", "60min"):
            assert want in KLINES_PERIOD_ALIASES, f"缺少周期别名 {want}"

    def test_returns_bar_models(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Q1-b 合并后 bars tdx 路由经 router（内部 from ..client import
        # TdxClient 调用点解析），mock 目标改为 tstdx.client.TdxClient；
        # 断言语义不变（返回 Bar 模型，离线验证）。
        import tstdx.client as client_mod

        class _FakeTdx:
            def __init__(self, **kw: Any) -> None:
                pass

            def __enter__(self) -> _FakeTdx:
                return self

            def __exit__(self, *exc: Any) -> None:
                self.close()

            def close(self) -> None:
                pass

            def bars(self, symbol, *, period="day", count=320, start=0, as_format="dict"):  # noqa: ANN001
                return [Bar(datetime="2026-09-03", close=1.0)]

        monkeypatch.setattr(client_mod, "TdxClient", _FakeTdx)
        api = UnifiedQuoteAPI()
        out = api.bars("sh600519", route="tdx")
        assert isinstance(out, list) and all(isinstance(b, Bar) for b in out)
        assert out[0].datetime == "2026-09-03"

    def test_bar_field_alignment(self) -> None:
        for field in ("datetime", "open", "high", "low", "close", "volume", "amount"):
            assert field in Bar.__dataclass_fields__, f"Bar 缺 {field}"


class TestAllMarketSemantics:
    """``all_market`` ↔ easyquotation ``all(node)``。"""

    def test_signature_accepts_node(self) -> None:
        sig = inspect.signature(UnifiedQuoteAPI.all_market)
        params = list(sig.parameters)
        assert "node" in params
        assert "source" in params
        assert "page_size" in params

    def test_unknown_source_rejected_at_construction(self) -> None:
        from tstdx.errors import CompatibilityError
        from tstdx.web.facade import WebQuoteSession

        with pytest.raises(CompatibilityError):
            WebQuoteSession("not-a-source")

    def test_n7_hk_us_rejected(self) -> None:
        """N7 能力边界：港股/美股整市场枚举未实装，显式 ValueError。"""
        from tstdx.web.facade import WebQuoteSession

        sess = WebQuoteSession("sina")
        for bad in ("hk", "us", "HK", "US", "hk_main", "us_main"):
            with pytest.raises(ValueError, match="未实装|N7"):
                sess.all_market(node=bad)


class TestIndexSemantics:
    """``index_list`` / ``index`` ↔ easyquotation ``get_index()``。"""

    def test_index_list_static_dir(self) -> None:
        out = UnifiedQuoteAPI.index_list()
        assert isinstance(out, list)
        assert out, "指数目录不应为空"
        item = out[0]
        for field in ("name", "symbol", "market", "code"):
            assert field in item, f"指数目录缺 {field}"

    def test_index_quotes_route(self) -> None:
        """``index``（WebQuoteSession）返回指数行情（Quote 序列）。"""
        from tstdx.web.facade import INDEX_SYMBOLS, WebQuoteSession

        assert "上证指数" in INDEX_SYMBOLS
        # 离线：注入 fake 客户端验证 index 委托 quotes
        sess = WebQuoteSession("sina")

        class _Fake:
            def fetch(self, codes):  # noqa: ANN001
                return [Quote(code=c) for c in codes]

        sess._client = _Fake()  # type: ignore[assignment]
        out = sess.index()
        assert len(out) == len(INDEX_SYMBOLS)
        assert all(isinstance(q, Quote) for q in out)
