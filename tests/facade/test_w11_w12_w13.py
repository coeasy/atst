# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""W11/W12/W13（门面一致性域）回归：路由熔断、route 治理、bars 口径守卫。

对应 docs/INDUSTRIAL_OPTIMIZATION_PLAN.md F3 批次：

* W11：``_try_routes`` 聚合 ``route_errors`` 进最终异常 context（仍重抛最后
  一路由异常）+ 每路由失败一条 warning + 连续失败 ≥3 次 30s 冷却熔断。
* W12：仅 tdx 能力的方法显式传非 tdx 路由 → ``ValueError``；web 有对应能力
  的方法（minute/trades）接双通路；无实现路由的明确错误文案。
* W13：bars web 兜底透传 ``adjust``；``start`` 仅 tdx/local——显式 web →
  ``ValueError``，auto → 剔除该路由并告警，绝不静默变口径。

测试纪律：不打桩被测方法本身；仅注入 ``_tdx``/``_web`` 客户端替身或对
``_try_routes`` 打桩捕获入参（与既有 test_routing.py 同纪律）。
"""

from __future__ import annotations

import logging
from typing import Any

import pytest

from tstdx.errors import CommandOffline, SourceUnavailable, TdxError
from tstdx.facade.api import UnifiedQuoteAPI


class _Sentinel(Exception):
    """拦截 _try_routes 实际执行时使用（对齐 test_routing.py）。"""


def _capture_try_routes(api: UnifiedQuoteAPI) -> dict:
    captured: dict = {}

    def fake(route, fn):  # noqa: ANN001
        captured["route"] = route
        captured["fn"] = set(fn.keys())
        raise _Sentinel()

    api._try_routes = fake  # type: ignore[assignment, method-assign]
    return captured


# --------------------------------------------------------------------------- #
# W11：错误聚合 + 逐路由告警
# --------------------------------------------------------------------------- #
class TestW11ErrorAggregation:
    def test_auto_failure_contains_only_selected_provider_path(self) -> None:
        """v13 契约：auto 只选定一路 Provider，失败绝不跨 Provider 兜底。

        因此只有被选中的 tdx 被调用一次；异常 context 的 ``route_errors``
        仅包含该路径，不会出现 web。
        """
        api = UnifiedQuoteAPI()
        calls: list[str] = []

        def boom_tdx() -> list:
            calls.append("tdx")
            raise TdxError("tdx down")

        def boom_web() -> list:
            calls.append("web")
            raise RuntimeError("web down")

        with pytest.raises(TdxError) as ei:
            api._try_routes("auto", {"tdx": boom_tdx, "web": boom_web})
        assert calls == ["tdx"]  # web 绝不被尝试
        errs = ei.value.context["route_errors"]
        assert set(errs) == {"tdx"}
        assert errs["tdx"].startswith("TdxError: ")

    def test_route_errors_attached_to_non_tdx_exception(self) -> None:
        """非 TdxError 的最终异常也能挂上 route_errors。"""
        api = UnifiedQuoteAPI()

        def boom() -> list:
            raise RuntimeError("x")

        with pytest.raises(RuntimeError) as ei:
            api._try_routes("tdx", {"tdx": boom})
        assert ei.value.context["route_errors"]["tdx"].startswith("RuntimeError: ")

    def test_one_warning_for_selected_route_failure(self, caplog: pytest.LogCaptureFixture) -> None:
        """每路由失败一条 warning——但 auto 只选定一路，故仅 1 条。"""
        api = UnifiedQuoteAPI()

        def boom() -> list:
            raise TdxError("boom")

        with caplog.at_level(logging.WARNING, logger="tstdx.facade"), pytest.raises(TdxError):
            api._try_routes("auto", {"tdx": boom, "web": boom})
        records = [r for r in caplog.records if r.name == "tstdx.facade"]
        assert len(records) == 1  # 只对选中的 Provider 路径告警
        assert all(r.levelno == logging.WARNING for r in records)
        assert "不会跨 Provider" in records[0].getMessage()


# --------------------------------------------------------------------------- #
# W11：熔断
# --------------------------------------------------------------------------- #
class TestW11CircuitBreaker:
    def test_opens_after_three_failures_without_switching_provider(self) -> None:
        """连续失败 ≥3 次 → auto 熔断；第 4 次直接 SourceUnavailable，不换 Provider。

        注意：web 虽然登记在路由映射里，但 auto 只选定 tdx，故 web 调用次数恒为 0。
        """
        api = UnifiedQuoteAPI()
        calls = {"tdx": 0, "web": 0}

        def boom_tdx() -> list:
            calls["tdx"] += 1
            raise TdxError("tdx down")

        def ok_web() -> list:
            calls["web"] += 1
            return ["web"]

        for _ in range(3):
            with pytest.raises(TdxError):
                api._try_routes("auto", {"tdx": boom_tdx, "web": ok_web})
        assert calls == {"tdx": 3, "web": 0}  # 绝不跨 Provider 兜底

        # 第 4 次：tdx 已熔断 → 抛出 SourceUnavailable，且仍不尝试 web
        with pytest.raises(SourceUnavailable) as ei:
            api._try_routes("auto", {"tdx": boom_tdx, "web": ok_web})
        assert calls == {"tdx": 3, "web": 0}
        assert ei.value.context["provider_path"] == "tdx"
        assert ei.value.context["fallback"] is False
        assert ei.value.context["cooldown_seconds"] == api._ROUTE_COOLDOWN_SECONDS

    def test_cooldown_error_when_no_other_route(self) -> None:
        """仅一路可用时熔断同样生效：冷却期内不再调用，且明示 fallback=False。"""
        api = UnifiedQuoteAPI()
        calls = {"n": 0}

        def boom() -> list:
            calls["n"] += 1
            raise TdxError("tdx down")

        for _ in range(3):
            with pytest.raises(TdxError):
                api._try_routes("auto", {"tdx": boom})
        assert calls["n"] == 3
        with pytest.raises(SourceUnavailable) as ei:
            api._try_routes("auto", {"tdx": boom})
        assert calls["n"] == 3  # 冷却期内不再尝试
        assert "冷却" in str(ei.value)
        assert ei.value.context["provider_path"] == "tdx"
        assert ei.value.context["route"] == "auto"
        assert ei.value.context["fallback"] is False

    def test_success_resets_failure_count(self) -> None:
        """成功清零：熔断只看「连续」失败。"""
        api = UnifiedQuoteAPI()
        calls = {"n": 0}

        def flaky() -> list:
            calls["n"] += 1
            if calls["n"] == 3:
                return ["ok"]  # 第 3 次成功 → 计数清零
            raise TdxError("flaky")

        for _ in range(2):
            with pytest.raises(TdxError):
                api._try_routes("auto", {"tdx": flaky})
        assert api._try_routes("auto", {"tdx": flaky}) == ["ok"]
        for _ in range(2):
            with pytest.raises(TdxError):
                api._try_routes("auto", {"tdx": flaky})
        # 此前成功清零过 → 当前仅连续失败 2 次 → 第 6 次仍尝试 tdx
        with pytest.raises(TdxError):
            api._try_routes("auto", {"tdx": flaky})
        assert calls["n"] == 6

    def test_explicit_route_still_attempts_during_cooldown(self) -> None:
        """冷却只作用于 auto 序；显式指定路由仍尝试。"""
        api = UnifiedQuoteAPI()
        calls = {"n": 0}

        def boom() -> list:
            calls["n"] += 1
            raise TdxError("x")

        for _ in range(3):
            with pytest.raises(TdxError):
                api._try_routes("auto", {"tdx": boom})
        with pytest.raises(TdxError):
            api._try_routes("tdx", {"tdx": boom})
        assert calls["n"] == 4

    def test_reset_circuit_reopens_route(self) -> None:
        api = UnifiedQuoteAPI()
        calls = {"n": 0}

        def boom() -> list:
            calls["n"] += 1
            raise TdxError("x")

        for _ in range(3):
            with pytest.raises(TdxError):
                api._try_routes("auto", {"tdx": boom})
        with pytest.raises(TdxError) as ei:
            api._try_routes("auto", {"tdx": boom})
        assert "冷却" in str(ei.value)
        api.reset_circuit()
        with pytest.raises(TdxError):
            api._try_routes("auto", {"tdx": boom})
        assert calls["n"] == 4  # 复位后重新尝试

    def test_close_keeps_circuit_state(self) -> None:
        """close() 不复位熔断（进程级体验）。"""
        api = UnifiedQuoteAPI()
        calls = {"n": 0}

        def boom() -> list:
            calls["n"] += 1
            raise TdxError("x")

        for _ in range(3):
            with pytest.raises(TdxError):
                api._try_routes("auto", {"tdx": boom})
        api.close()
        with pytest.raises(TdxError) as ei:
            api._try_routes("auto", {"tdx": boom})
        assert calls["n"] == 3
        assert "冷却" in str(ei.value)


# --------------------------------------------------------------------------- #
# W12：route 参数治理
# --------------------------------------------------------------------------- #
class TestW12RouteGovernance:
    @pytest.mark.parametrize(
        ("method", "args"),
        [
            ("snapshot", ("sh600519",)),
            ("minute_history", ("sh600519", 20260901)),
            ("security_count", ()),
            ("finance", ("sh600519",)),
            ("capital_changes", ("sh600519",)),
            # block_quotes 自 P13-A 起支持 web 近似兜底（board_rank），
            # 移出「仅 tdx」列表；新行为见 TestP13ABlockQuotesWebFallback。
            ("auction", ("sh600519",)),
            ("volume_price", ("sh600519",)),
            ("ex_bars", ("hk00700",)),
            ("goods_quotes", ("m2509",)),
            ("f10", ("sh600519", "gsgk.dat")),
            ("f10_catalog", ("sh600519",)),
            # ex_market_list / ex_instruments 自 P13-A 起仍仅 tdx 路由
            # （web 无对应目录/品种列举能力），保持契约不变。
            ("ex_market_list", ()),
            ("ex_instruments", (0,)),
        ],
    )
    def test_tdx_only_methods_reject_explicit_web(self, method: str, args: tuple) -> None:
        """仅 tdx 能力的方法：显式传非 tdx 路由 → ValueError（收缩承诺）。"""
        api = UnifiedQuoteAPI()
        with pytest.raises(ValueError, match="仅支持 tdx"):
            getattr(api, method)(*args, route="web")

    def test_corporate_action_rejects_route_via_delegation(self) -> None:
        api = UnifiedQuoteAPI()
        with pytest.raises(ValueError, match="仅支持 tdx"):
            api.corporate_action("sh600519", route="web")

    def test_f10_catalog_delegates_to_f10_client(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """N1：f10_catalog 经 F10Client.catalog 委托，且调用后关闭客户端（fake 注入）。"""

        class FakeF10:
            def __init__(self, **kw: Any) -> None:
                self.closed = False

            def catalog(self, symbol: str) -> list[dict[str, Any]]:
                return [{"title": "公司概况", "filename": "gsgk.dat"}]

            def close(self) -> None:
                self.closed = True

        captured: list[FakeF10] = []

        def _factory(**kw: Any) -> FakeF10:
            inst = FakeF10(**kw)
            captured.append(inst)
            return inst

        import tstdx.client as client_mod

        monkeypatch.setattr(client_mod, "F10Client", _factory)
        rows = UnifiedQuoteAPI().f10_catalog("sh600519")
        assert rows == [{"title": "公司概况", "filename": "gsgk.dat"}]
        assert captured and captured[0].closed is True  # 关闭客户端，避免泄漏

    def test_minute_tdx_failure_does_not_call_web(self) -> None:
        """(b) v13 契约：minute 的双通路是「显式选择」，不是兜底链。

        auto 只选定 tdx；tdx 抛异常时异常直接上抛，web 绝不被调用。
        """

        class _Tdx:
            def minute_today(self, sym: str) -> list:
                raise RuntimeError("tdx down")

        api = UnifiedQuoteAPI()
        api._tdx = _Tdx()
        web_called: list[str] = []

        def _web(sym: str) -> list:
            web_called.append(sym)
            return [{"price": 1.0}, {"price": 2.0}]

        api.minute_web = _web  # type: ignore[method-assign]
        with pytest.raises(RuntimeError, match="tdx down"):
            api.minute("sh600519")
        assert web_called == []

    def test_minute_prefers_tdx_when_healthy(self) -> None:
        class _Tdx:
            def minute_today(self, sym: str) -> list:
                return [{"price": 9.9}]

        api = UnifiedQuoteAPI()
        api._tdx = _Tdx()

        def _no_web(sym: str) -> list:
            raise AssertionError("web 不应在 tdx 健康时被调用")

        api.minute_web = _no_web  # type: ignore[method-assign]
        assert api.minute("sh600519") == [{"price": 9.9}]

    def test_minute_count_slice_after_routing(self) -> None:
        class _Tdx:
            def minute_today(self, sym: str) -> list:
                return [{"i": i} for i in range(10)]

        api = UnifiedQuoteAPI()
        api._tdx = _Tdx()
        assert len(api.minute("sh600519", count=3)) == 3
        assert len(api.minute("sh600519")) == 10

    def test_trades_tdx_path_and_web_start_guard(self) -> None:
        class _Tdx:
            def trade_today(self, sym: str, *, start: int = 0, count: int = 0) -> list:
                return [{"price": float(start + i)} for i in range(3)]

        api = UnifiedQuoteAPI()
        api._tdx = _Tdx()
        rows = api.trades("sh600519", start=1)
        assert [r["price"] for r in rows] == [1.0, 2.0, 3.0]
        with pytest.raises(ValueError, match="start"):
            api.trades("sh600519", start=1, route="web")

    def test_trades_excludes_web_when_start_set(self) -> None:
        api = UnifiedQuoteAPI()
        cap = _capture_try_routes(api)
        with pytest.raises(_Sentinel):
            api.trades("sh600519", start=5)
        assert cap["fn"] == {"tdx"}  # web 因 start 被剔除

    def test_route_without_impl_gets_explicit_message(self) -> None:
        """无实现路由：明确文案而非误导性「全部路由均无数据」。"""
        api = UnifiedQuoteAPI()
        with pytest.raises(TdxError) as ei:
            api.minute("sh600519", route="local")
        msg = str(ei.value)
        assert "无对应实现" in msg and "minute" in msg
        assert "tdx, web" in msg

    def test_default_route_web_on_tdx_only_method(self) -> None:
        """default_route='web'（非显式传参）落到无实现路由 → 明确文案。"""
        api = UnifiedQuoteAPI(route="web")
        with pytest.raises(TdxError) as ei:
            api.snapshot("sh600519")
        assert "无对应实现" in str(ei.value) and "snapshot" in str(ei.value)


# --------------------------------------------------------------------------- #
# W13：bars 口径守卫
# --------------------------------------------------------------------------- #
class TestW13BarsSemantics:
    def test_explicit_web_with_start_raises(self) -> None:
        api = UnifiedQuoteAPI()
        with pytest.raises(ValueError, match="start"):
            api.bars("sh600519", start=5, route="web")

    def test_auto_excludes_web_when_start_set(self) -> None:
        api = UnifiedQuoteAPI()
        cap = _capture_try_routes(api)
        with pytest.raises(_Sentinel):
            api.bars("sh600519", start=5)
        assert "web" not in cap["fn"]
        assert {"local", "tdx"} <= cap["fn"]

    def test_auto_excludes_tdx_when_adjust_requested(self) -> None:
        """显式复权请求 → 仅 web 可满足（tdx/local 只能原始价）。"""
        api = UnifiedQuoteAPI()
        cap = _capture_try_routes(api)
        with pytest.raises(_Sentinel):
            api.bars("sh600519", adjust="qfq")
        assert cap["fn"] == {"web"}

    def test_explicit_tdx_with_adjust_raises(self) -> None:
        api = UnifiedQuoteAPI()
        with pytest.raises(ValueError, match="adjust"):
            api.bars("sh600519", adjust="qfq", route="tdx")

    def test_no_route_satisfies_semantics(self) -> None:
        api = UnifiedQuoteAPI()
        with pytest.raises(TdxError, match="口径"):
            api.bars("sh600519", start=5, adjust="qfq")

    def test_web_adjust_passthrough_and_default_raw(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """``adjust`` 原样透传到 web 通路；默认（None）→ 原始价 ``""``。

        Q1-b 合并后 bars 三通路均经 ``router.kline``（Provider-bound），
        mock 目标由 ``api._web`` 改为注入 router 替身；断言语义不变。
        """
        seen: list[str] = []

        class _Router:
            def kline(
                self,
                sym: str,
                *,
                period: str = "day",
                count: int = 320,
                start: int = 0,
                order: Any = None,
                adjust: str = "",
                default_empty_ok: bool = False,
            ) -> list:
                seen.append(adjust)
                return []

        api = UnifiedQuoteAPI()
        monkeypatch.setattr(api, "_get_router", lambda: _Router())
        assert api.bars("sh600519", route="web") == []
        assert seen[-1] == ""  # 默认 → 原始价，与 tdx/local 口径一致
        api.bars("sh600519", route="web", adjust="hfq")
        assert seen[-1] == "hfq"  # 显式复权原样透传

    def test_web_skip_warning_logged(self, caplog: pytest.LogCaptureFixture) -> None:
        api = UnifiedQuoteAPI()
        # This contract only verifies that ``start`` removes the web route.
        # Capture the route map instead of opening a real TDX connection; the
        # test suite must remain deterministic in offline CI.
        _capture_try_routes(api)
        with pytest.raises(_Sentinel), caplog.at_level(logging.WARNING, logger="tstdx.facade"):
            api.bars("sh600519", start=5)
        assert any("web" in r.getMessage() and "start" in r.getMessage() for r in caplog.records)

    def test_default_bars_include_all_three_routes(self) -> None:
        """默认参数口径下 local/tdx/web 三路由齐备（口径一致：原始价）。"""
        api = UnifiedQuoteAPI()
        cap = _capture_try_routes(api)
        with pytest.raises(_Sentinel):
            api.bars("sh600519")
        assert cap["fn"] == {"local", "tdx", "web"}


# --------------------------------------------------------------------------- #
# P13-A：兜底路径补齐（block_quotes web 近似 + SourceUnavailable 转换）
# --------------------------------------------------------------------------- #
class TestP13ABlockQuotesWebFallback:
    """block_quotes 自 P13-A 起支持 web 近似兜底（腾讯板块排行）。"""

    def test_explicit_web_uses_web_route(self) -> None:
        """显式 route="web" + start=0 → fns 含 web 且 tdx 被跳过（_iter_routes 单路由）。"""
        api = UnifiedQuoteAPI()
        cap = _capture_try_routes(api)
        with pytest.raises(_Sentinel):
            api.block_quotes(route="web")
        assert cap["route"] == "web"
        # _try_routes 收到的 fns 应含 web；由于 resolved=="web"，_iter_routes
        # 只执行 web 单路由，tdx 不会被执行
        assert "web" in cap["fn"]

    def test_auto_route_includes_web_for_mapped_block_type(self) -> None:
        """auto 路由 + 可映射 block_type（0/1/2）→ fns 含 web 兜底。"""
        api = UnifiedQuoteAPI()
        cap = _capture_try_routes(api)
        for bt in (0, 1, 2):
            cap.clear()
            with pytest.raises(_Sentinel):
                api.block_quotes(block_type=bt)
            assert "web" in cap["fn"], f"block_type={bt} 应含 web 兜底"

    def test_auto_route_excludes_web_for_unmappable_block_type(self) -> None:
        """auto 路由 + block_type=3（指数）→ fns 不含 web（无对应映射）。"""
        api = UnifiedQuoteAPI()
        cap = _capture_try_routes(api)
        with pytest.raises(_Sentinel):
            api.block_quotes(block_type=3)
        assert "web" not in cap["fn"]

    def test_auto_route_excludes_web_when_start_nonzero(self) -> None:
        """auto 路由 + start>0 → fns 不含 web（web 排行无偏移语义，W13 守卫）。"""
        api = UnifiedQuoteAPI()
        cap = _capture_try_routes(api)
        with pytest.raises(_Sentinel):
            api.block_quotes(start=100)
        assert "web" not in cap["fn"]

    def test_explicit_web_rejects_nonzero_start(self) -> None:
        """显式 route="web" + start>0 → ValueError（拒绝改变数据窗口）。"""
        api = UnifiedQuoteAPI()
        with pytest.raises(ValueError, match="start=100"):
            api.block_quotes(start=100, route="web")

    def test_explicit_web_rejects_unmappable_block_type(self) -> None:
        """显式 route="web" + block_type=3（指数）→ ValueError（无映射）。"""
        api = UnifiedQuoteAPI()
        with pytest.raises(ValueError, match="block_type=3"):
            api.block_quotes(block_type=3, route="web")

    def test_local_route_still_rejected(self) -> None:
        """显式 route="local" → ValueError（vipdoc 无板块行情本地文件）。"""
        api = UnifiedQuoteAPI()
        with pytest.raises(ValueError, match="block_quotes"):
            api.block_quotes(route="local")


class TestP13ASourceUnavailableConversion:
    """auction / volume_price / f10_catalog / ex_market_list / ex_instruments
    在 auto 路由下把 CommandOffline/AllHostsUnreachable 转换为 SourceUnavailable。
    """

    @pytest.mark.parametrize(
        ("method", "args", "cmd"),
        [
            ("auction", ("sh600519",), "0x056A"),
            ("volume_price", ("sh600519",), "0x051A"),
            ("f10_catalog", ("sh600519",), "0x0001"),
            ("ex_market_list", (), "0x0100"),
            ("ex_instruments", (0,), "0x0103"),
        ],
    )
    def test_auto_route_converts_to_source_unavailable(
        self, method: str, args: tuple, cmd: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """auto 路由下 tdx 抛 CommandOffline → 转成 SourceUnavailable，context 含 alternatives。"""
        from tstdx.errors import CommandOffline, SourceUnavailable

        api = UnifiedQuoteAPI()
        # 注入假 _tdx：调用相关方法时抛 CommandOffline

        def _boom():
            raise CommandOffline(f"{cmd} offline")

        # 拦截 tdx 属性：按方法调用返回或抛
        _tdx_call_map = {
            "auction": lambda: _boom(),
            "volume_price": lambda: _boom(),
            "f10_catalog": lambda: _boom(),
            "ex_market_list": lambda: _boom(),
            "ex_instruments": lambda: _boom(),
        }
        # monkeypatch UnifiedQuoteAPI.tdx 属性为直接抛的 fake
        import tstdx.facade.api as _api_mod

        orig_tdx_prop = _api_mod.UnifiedQuoteAPI.tdx

        class _FakeTdx:
            def auction_snapshot(self, sym: str):
                raise CommandOffline(f"{cmd} offline")

            def volume_price_dist(self, sym: str):
                raise CommandOffline(f"{cmd} offline")

        # 对 ex_market_list/ex_instruments/f10_catalog，走 self._with(...) 会构造真实客户端
        # 因此 monkeypatch 这些构造函数
        import tstdx.client as _client_mod

        def _fake_client_factory(*_a, **_kw):
            class _C:
                def ex_market_list(self):
                    raise CommandOffline(f"{cmd} offline")

                def ex_instrument_list(self, m, start=0):
                    raise CommandOffline(f"{cmd} offline")

                def catalog(self, sym):
                    raise CommandOffline(f"{cmd} offline")

                def close(self):
                    pass

            return _C()

        # 用 monkeypatch 替换 ExMarketClient / F10Client / TdxClient.auction_snapshot 等
        if method in ("auction", "volume_price"):
            # 替换 TdxClient 相关方法
            _api_mod.UnifiedQuoteAPI.tdx = property(lambda self: _FakeTdx())  # type: ignore[method-assign]
        else:
            # 替换 client 模块构造函数
            if method in ("ex_market_list", "ex_instruments"):
                monkeypatch.setattr(_client_mod, "ExMarketClient", _fake_client_factory)
            elif method == "f10_catalog":
                monkeypatch.setattr(_client_mod, "F10Client", _fake_client_factory)

        try:
            with pytest.raises(SourceUnavailable) as ei:
                getattr(api, method)(*args)  # auto 路由
            # context 校验
            ctx = ei.value.context
            assert ctx["method"] == method
            assert cmd.split()[0] in ctx["command"]  # 命令号前缀匹配
            assert "alternatives" in ctx and ctx["alternatives"]
            assert ctx["cause"] == "CommandOffline"
            assert ei.value.cause is not None
            assert isinstance(ei.value.cause, CommandOffline)
        finally:
            _api_mod.UnifiedQuoteAPI.tdx = orig_tdx_prop  # type: ignore[misc]

    @pytest.mark.parametrize(
        ("method", "args"),
        [
            ("auction", ("sh600519",)),
            ("volume_price", ("sh600519",)),
        ],
    )
    def test_explicit_tdx_transparent(self, method: str, args: tuple) -> None:
        """显式 route="tdx" → 透传 CommandOffline，不转成 SourceUnavailable。"""
        from tstdx.errors import CommandOffline

        api = UnifiedQuoteAPI()
        import tstdx.facade.api as _api_mod

        orig_tdx_prop = _api_mod.UnifiedQuoteAPI.tdx

        class _FakeTdx:
            def auction_snapshot(self, sym: str):
                raise CommandOffline("0x056A offline")

            def volume_price_dist(self, sym: str):
                raise CommandOffline("0x051A offline")

        _api_mod.UnifiedQuoteAPI.tdx = property(lambda self: _FakeTdx())  # type: ignore[method-assign]
        try:
            with pytest.raises(CommandOffline):
                getattr(api, method)(*args, route="tdx")
        finally:
            _api_mod.UnifiedQuoteAPI.tdx = orig_tdx_prop  # type: ignore[misc]

    def test_source_unavailable_registered_in_retry_advice(self) -> None:
        """SourceUnavailable 已加入 RETRY_ADVICE 且 code=http_status 正确。"""
        from tstdx.errors import RETRY_ADVICE, SourceUnavailable

        assert SourceUnavailable in RETRY_ADVICE
        adv = SourceUnavailable.default_advice
        assert adv.retryable is False
        assert SourceUnavailable.code == "E7050"
        assert SourceUnavailable.http_status == 503


# --------------------------------------------------------------------------- #
# P14-E：异步门面继承 SourceUnavailable 转换语义
# --------------------------------------------------------------------------- #
class TestP14EAsyncFacadeSourceUnavailable:
    """AsyncUnifiedQuoteAPI 经 asyncio.to_thread 桥接 UnifiedQuoteAPI，
    因此同步门面的 CommandOffline/AllHostsUnreachable → SourceUnavailable
    转换**自动继承**，无需在异步层重复实现。本组测试把该契约锁定。

    同时锁定 aquery 的「永不抛异常」边界：SourceUnavailable 被折叠为
    失败 ApiResponse，与同步门面 query 的语义一致。
    """

    def test_async_facade_propagates_source_unavailable(self) -> None:
        """AsyncUnifiedQuoteAPI.arun 在 auto 路由下收到 SourceUnavailable。"""
        import asyncio

        import tstdx.facade.api as _api_mod
        from tstdx.facade.async_api import AsyncUnifiedQuoteAPI

        api = AsyncUnifiedQuoteAPI()

        class _FakeTdx:
            def auction_snapshot(self, sym: str):
                raise CommandOffline("0x056A offline")

        orig_tdx_prop = _api_mod.UnifiedQuoteAPI.tdx
        _api_mod.UnifiedQuoteAPI.tdx = property(lambda self: _FakeTdx())  # type: ignore[method-assign]
        try:
            with pytest.raises(SourceUnavailable) as exc_info:
                asyncio.run(api.arun("auction", "sh600519"))
            assert exc_info.value.code == "E7050"
            assert exc_info.value.context.get("method") == "auction"
            assert "auction" in str(exc_info.value)
        finally:
            _api_mod.UnifiedQuoteAPI.tdx = orig_tdx_prop  # type: ignore[misc]

    def test_aquery_folds_source_unavailable_to_failure(self) -> None:
        """aquery 承诺「永不抛异常」——SourceUnavailable 折叠为失败 ApiResponse。"""
        import asyncio

        import tstdx.facade.api as _api_mod
        from tstdx.facade.async_api import AsyncUnifiedQuoteAPI

        api = AsyncUnifiedQuoteAPI()

        class _FakeTdx:
            def auction_snapshot(self, sym: str):
                raise CommandOffline("0x056A offline")

        orig_tdx_prop = _api_mod.UnifiedQuoteAPI.tdx
        _api_mod.UnifiedQuoteAPI.tdx = property(lambda self: _FakeTdx())  # type: ignore[method-assign]
        try:
            resp = asyncio.run(api.aquery("auction", "sh600519"))
            assert resp.success is False
            assert resp.error is not None
            assert "auction" in str(resp.error) or "unavailable" in str(resp.error).lower()
        finally:
            _api_mod.UnifiedQuoteAPI.tdx = orig_tdx_prop  # type: ignore[misc]

    def test_async_facade_docstring_mentions_inheritance(self) -> None:
        """async_api 模块 docstring 显式声明 SourceUnavailable 语义继承（P14-E 契约）。"""
        import tstdx.facade.async_api as _async_mod

        doc = _async_mod.__doc__ or ""
        assert "SourceUnavailable" in doc, "P14-E：异步门面 docstring 应显式声明继承语义"
