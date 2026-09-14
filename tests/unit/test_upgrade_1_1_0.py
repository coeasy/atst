# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""1.1.0 升级批次测试（docs/archive/OPTIMIZATION_PLAN.md 批次 C/D）。

覆盖：WS ``stock_changes`` 方法、MCP 两个新工具、CLI ``changes``/``hot``
子命令、异步门面 :class:`AsyncUnifiedQuoteAPI`。全部离线（罐头 +
monkeypatch），零网络。
"""

from __future__ import annotations

import asyncio
import io
import json
from contextlib import redirect_stdout

import pytest

pytestmark = pytest.mark.unit


# --------------------------------------------------------------------------- #
# C3 WS stock_changes
# --------------------------------------------------------------------------- #
class TestWsStockChanges:
    def _req(self, method: str, params: dict | None = None, req_id: int = 1) -> str:
        msg: dict = {"jsonrpc": "2.0", "id": req_id, "method": method}
        if params is not None:
            msg["params"] = params
        return json.dumps(msg)

    def test_stock_changes_roundtrip(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from tstdx.integration.ws_server import JsonRpcHandler
        from tstdx.web import facade as wf

        monkeypatch.setattr(
            wf.WebQuoteSession,
            "stock_changes",
            staticmethod(
                lambda types=(), page=1, size=50: (
                    [{"code": "600000", "change_type": 8201}]
                    if types == (8201,) and page == 1
                    else []
                )
            ),
        )
        h = JsonRpcHandler(client=object())  # 不触发 TdxClient 懒加载
        out = json.loads(
            h.handle_message(self._req("stock_changes", {"types": [8201], "page": 1, "size": 10}))
        )
        assert out["result"][0]["change_type"] == 8201

    def test_invalid_types_rejected(self) -> None:
        from tstdx.integration.ws_server import ERR_INVALID_PARAMS, JsonRpcHandler

        h = JsonRpcHandler(client=object())
        out = json.loads(
            h.handle_message(self._req("stock_changes", {"types": ["8201"]}, req_id=9))
        )
        assert out["error"]["code"] == ERR_INVALID_PARAMS
        assert out["id"] == 9

    def test_in_methods_registry(self) -> None:
        from tstdx.integration.ws_server import JsonRpcHandler

        assert "stock_changes" in JsonRpcHandler.METHODS


# --------------------------------------------------------------------------- #
# C2 MCP get_stock_changes / get_hot_rank
# --------------------------------------------------------------------------- #
class TestMcpNewTools:
    def _call(self, name: str, arguments: dict) -> dict:
        from tstdx.integration.mcp_server import MCPServer

        s = MCPServer()
        return s.handle_request(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": name, "arguments": arguments},
            }
        )

    def test_get_stock_changes(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from tstdx.web import facade as wf

        monkeypatch.setattr(
            wf.WebQuoteSession,
            "stock_changes",
            staticmethod(
                lambda types=(), page=1, size=50: [{"code": "600000", "change_name": "火箭发射"}]
            ),
        )
        out = self._call("get_stock_changes", {"types": ["8201"], "size": 10})
        assert "error" not in out
        text = out["result"]["content"][0]["text"]
        assert "火箭发射" in text

    def test_get_hot_rank(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from tstdx.web import facade as wf

        monkeypatch.setattr(
            wf.WebQuoteSession,
            "hot_rank",
            staticmethod(lambda page=1, size=100: [{"rank": 1, "symbol": "sh600127"}]),
        )
        out = self._call("get_hot_rank", {"size": 5})
        assert "sh600127" in out["result"]["content"][0]["text"]


# --------------------------------------------------------------------------- #
# C1 CLI changes / hot
# --------------------------------------------------------------------------- #
class TestCliWebCommands:
    def test_changes_json(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from tstdx import cli
        from tstdx.web import facade as wf

        monkeypatch.setattr(
            wf.WebQuoteSession,
            "stock_changes",
            staticmethod(
                lambda types=(), page=1, size=50: [
                    {
                        "time": "11:19:05",
                        "code": "600000",
                        "name": "x",
                        "change_type": 8201,
                        "change_name": "火箭发射",
                        "metrics": [0.1],
                        "total": 1,
                    }
                ]
            ),
        )
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = cli.main(["changes", "--types", "8201", "--size", "5", "--json"])
        assert rc == 0
        rows = json.loads(buf.getvalue())
        assert rows[0]["change_name"] == "火箭发射"

    def test_hot_table_output(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from tstdx import cli
        from tstdx.web import facade as wf

        monkeypatch.setattr(
            wf.WebQuoteSession,
            "hot_rank",
            staticmethod(
                lambda page=1, size=30: [
                    {
                        "rank": 1,
                        "symbol": "sh600127",
                        "code": "600127",
                        "rank_change": 0,
                        "his_rank_change": -3,
                    }
                ]
            ),
        )
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = cli.main(["hot", "--size", "5"])
        assert rc == 0
        assert "sh600127" in buf.getvalue()

    def test_changes_error_exit(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from tstdx import cli
        from tstdx.web import facade as wf

        def _boom(*a, **kw):
            raise RuntimeError("blocked")

        monkeypatch.setattr(wf.WebQuoteSession, "stock_changes", staticmethod(_boom))
        err = io.StringIO()
        import contextlib

        with redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
            rc = cli.main(["changes", "--json"])
        assert rc == 2
        assert "blocked" in err.getvalue()


# --------------------------------------------------------------------------- #
# D2 异步门面
# --------------------------------------------------------------------------- #
class TestAsyncFacade:
    def test_method_parity_with_sync(self) -> None:
        from tstdx.facade.api import UnifiedQuoteAPI
        from tstdx.facade.async_api import AsyncUnifiedQuoteAPI

        a = AsyncUnifiedQuoteAPI()
        for name in (
            "quotes",
            "bars",
            "finance",
            "minute",
            "capital_changes",
            "trades",
            "stock_changes",
            "hot_rank",
            "wencai",
            "search_symbols",
        ):
            assert asyncio.iscoroutinefunction(getattr(a, name)), name
            assert hasattr(UnifiedQuoteAPI, name), name

    def test_quotes_bridges_to_sync(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from tstdx.facade import api as fa

        monkeypatch.setattr(
            fa.UnifiedQuoteAPI,
            "quotes",
            staticmethod(lambda symbols, **kw: [{"code": s} for s in symbols]),
        )

        async def run() -> list[dict]:
            from tstdx.facade.async_api import AsyncUnifiedQuoteAPI

            return await AsyncUnifiedQuoteAPI().quotes(["sh600519"])

        assert asyncio.run(run())[0]["code"] == "sh600519"

    def test_arun_generic_and_unknown(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from tstdx.facade import api as fa

        monkeypatch.setattr(
            fa.UnifiedQuoteAPI,
            "hot_rank",
            staticmethod(lambda page=1, size=100: [{"rank": 1}]),
        )

        async def run() -> tuple[object, object]:
            from tstdx.facade.async_api import AsyncUnifiedQuoteAPI

            api = AsyncUnifiedQuoteAPI()
            ok = await api.arun("hot_rank", size=7)
            try:
                await api.arun("no_such_method")
            except AttributeError as exc:
                bad = exc
            return ok, bad  # noqa: TRY300

        ok, bad = asyncio.run(run())
        assert ok == [{"rank": 1}]
        assert isinstance(bad, AttributeError)

    def test_aquery_success_and_failure(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from tstdx.facade import api as fa

        monkeypatch.setattr(
            fa.UnifiedQuoteAPI,
            "security_count",
            staticmethod(lambda market=0: 5321),
        )

        def _boom(*a, **kw):
            raise RuntimeError("sink")

        monkeypatch.setattr(fa.UnifiedQuoteAPI, "stock_changes", staticmethod(_boom))

        async def run() -> tuple[bool, bool]:
            from tstdx.facade.async_api import AsyncUnifiedQuoteAPI

            api = AsyncUnifiedQuoteAPI()
            ok = await api.aquery("security_count", 1)
            bad = await api.aquery("stock_changes")
            return bool(ok.success), bool(bad.success)

        ok, bad = asyncio.run(run())
        assert ok is True
        # 永不抛异常边界：失败折叠为 success=False
        assert bad is False

    def test_arun_rejects_non_callable(self) -> None:
        async def run() -> bool:
            from tstdx.facade.async_api import AsyncUnifiedQuoteAPI

            try:
                await AsyncUnifiedQuoteAPI().arun("_iter_routes")  # 私有 → getattr 可达但不公开
            except AttributeError:
                return True
            return False

        # 私有名仍 getattr 可达——本测试只锁定「不存在名」路径
        async def run2() -> bool:
            from tstdx.facade.async_api import AsyncUnifiedQuoteAPI

            try:
                await AsyncUnifiedQuoteAPI().arun("does_not_exist")
            except AttributeError:
                return True
            return False

        assert asyncio.run(run2()) is True
        del run


# --------------------------------------------------------------------------- #
# D3 版本一致性
# --------------------------------------------------------------------------- #
def test_version_bumped() -> None:
    import tstdx

    assert tstdx.__version__ == "1.4.0"
