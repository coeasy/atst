# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""UnifiedQuoteAPI 三级路由单测（P0 #1/#5/#6 修复验证，P2 #13）。

覆盖：
* 空结果语义：合法空列表视为成功（不再误判失败降级）
* 异常驱动降级：某路由抛异常才尝试下一路由
* 全部失败抛 TdxError
* snapshot 尊重 default_route（不再硬编码 "tdx"）
* bars 含 web 兜底分支
"""

from __future__ import annotations

import pytest

from tstdx.errors import TdxError
from tstdx.facade.api import UnifiedQuoteAPI


class _Sentinel(Exception):
    """用于拦截 _try_routes 实际执行，捕获其入参。"""


def _capture(api: UnifiedQuoteAPI) -> dict:
    captured: dict = {}

    def fake(route, fn):  # noqa: ANN001
        captured["route"] = route
        captured["fn"] = set(fn.keys())
        raise _Sentinel()

    api._try_routes = fake  # type: ignore[assignment]
    return captured


class TestTryRoutes:
    def test_empty_list_is_success(self):
        """P0 #1：合法空结果不再被误判为失败。"""
        api = UnifiedQuoteAPI()
        assert api._try_routes("auto", {"tdx": lambda: []}) == []

    def test_exception_triggers_fallback(self):
        """异常才降级到下一可用路由。"""
        api = UnifiedQuoteAPI()

        def boom() -> list:
            raise TdxError("boom")

        assert api._try_routes("auto", {"tdx": boom, "web": lambda: [1, 2]}) == [1, 2]

    def test_all_fail_raises(self):
        api = UnifiedQuoteAPI()

        def boom() -> list:
            raise TdxError("boom")

        with pytest.raises(TdxError):
            api._try_routes("auto", {"tdx": boom, "web": boom})

    def test_explicit_route_single(self):
        api = UnifiedQuoteAPI()
        assert api._try_routes("tdx", {"tdx": lambda: [42]}) == [42]

    def test_import_error_not_caught(self):
        api = UnifiedQuoteAPI()

        def boom() -> list:
            raise ImportError("no module")

        with pytest.raises(ImportError):
            api._try_routes("auto", {"tdx": boom})


class TestDefaultRouteRespected:
    def test_snapshot_uses_default_route_auto(self):
        api = UnifiedQuoteAPI(route="auto")
        cap = _capture(api)
        with pytest.raises(_Sentinel):
            api.snapshot("sh600519")
        assert cap["route"] == "auto"

    def test_snapshot_uses_explicit_default_route(self):
        """P0 #5：snapshot 过去硬编码 'tdx'，现尊重 default_route。"""
        api = UnifiedQuoteAPI(route="web")
        cap = _capture(api)
        with pytest.raises(_Sentinel):
            api.snapshot("sh600519")
        assert cap["route"] == "web"


class TestBarsWebFallback:
    def test_bars_includes_web_branch(self):
        """P0 #6：bars 的 auto 路由必须含 web 兜底。"""
        api = UnifiedQuoteAPI(route="auto")
        cap = _capture(api)
        with pytest.raises(_Sentinel):
            api.bars("sh600519")
        assert cap["fn"] == {"local", "tdx", "web"}

    def test_bars_explicit_web(self):
        api = UnifiedQuoteAPI(route="auto")
        cap = _capture(api)
        with pytest.raises(_Sentinel):
            api.bars("sh600519", route="web")
        assert cap["route"] == "web"
        assert cap["fn"] == {"local", "tdx", "web"}
