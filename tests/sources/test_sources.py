"""数据源路由测试（§34 / v12 Provider 契约）。

原文件验证的是 ``tdx→web→reader→cache→synthetic`` 五级降级链。该架构在
v12 clean-break 中**已被移除**：一次查询绑定**恰好一个** Provider，失败绝不
跨 Provider 兜底（见 ``docs/REFACTOR_PLAN_v13_CLEAN_BREAK.md`` §5.1/§5.2 与
``tstdx/facade/routing.py`` 的 fail-closed 规则）。

本文件改为锁定：

* legacy ``order=`` 选择器 → **单一** Provider / channel / special mode 映射；
* 多元素 ``order``（编码跨 Provider 兜底语义）→ ``ValidationError``；
* 失败路径只记录**被选中** Provider 的 diagnostics，不尝试其它 Provider；
* replay / synthetic 特殊模式必须显式授权。
"""

from __future__ import annotations

import pytest

from tstdx.config.schema import SourcesConfig
from tstdx.errors import SourceUnavailable, ValidationError
from tstdx.sources import DataSourceRouter
from tstdx.sources import SourceUnavailable as RouterSourceUnavailable


@pytest.mark.unit
class TestLegacySelectorMapping:
    """legacy ``order=`` 选择器 → 单一 Provider / channel / special mode。"""

    def test_single_tdx_selector_binds_tdx_quotation(self) -> None:
        router = DataSourceRouter(order=["tdx"])
        try:
            assert router._selection(provider=None, source=None, order=None) == (
                "tdx",
                None,
                None,
            )
        finally:
            router.close()

    def test_reader_selector_binds_tdx_vipdoc_channel(self) -> None:
        """``reader``/``local``/``vipdoc`` → tdx 的 vipdoc channel（绝不变 Provider）。"""
        for selector in ("reader", "local", "vipdoc"):
            router = DataSourceRouter(order=[selector])
            try:
                assert router._selection(provider=None, source=None, order=None) == (
                    "tdx",
                    "vipdoc",
                    None,
                )
            finally:
                router.close()

    def test_web_selector_binds_exactly_one_web_provider(self) -> None:
        router = DataSourceRouter(order=["web"], web_sources=["sina"])
        try:
            pid, channel, special = router._selection(provider=None, source=None, order=None)
        finally:
            router.close()
        assert pid == "sina"
        assert channel is None
        assert special is None

    def test_default_selection_without_order_is_tdx(self) -> None:
        """无 ``order``（默认）→ 确定性 tdx 默认 Provider。"""
        router = DataSourceRouter()
        try:
            assert router._selection(provider=None, source=None, order=None) == (
                "tdx",
                None,
                None,
            )
        finally:
            router.close()

    def test_explicit_provider_wins_over_order(self) -> None:
        """显式 ``provider=`` 优先于 legacy ``order=``，且仍只选一个。"""
        router = DataSourceRouter(order=["tdx"])
        try:
            assert router._selection(provider="tencent", source=None, order=None) == (
                "tencent",
                None,
                None,
            )
        finally:
            router.close()

    def test_legacy_multi_order_is_rejected(self) -> None:
        """多元素 ``order`` 编码跨 Provider 兜底语义 → 直接拒绝。"""
        router = DataSourceRouter(order=["tdx", "web", "reader"])
        try:
            with pytest.raises(ValidationError) as caught:
                router._selection(provider=None, source=None, order=None)
            assert caught.value.context["fallback"] is False
        finally:
            router.close()

    def test_governed_config_order_is_not_consulted_for_selection(self) -> None:
        """配置里的多源 ``order`` 是 legacy 字段：既不生效也不静默降级。

        Provider-bound 选择只认显式 ``order=`` / ``provider=``；缺省即确定性
        tdx。多源配置不会把查询变成降级链。
        """
        cfg = SourcesConfig(
            order=["tdx", "web", "reader", "cache", "synthetic"],
            enabled={
                "tdx": True,
                "web": True,
                "reader": True,
                "cache": True,
                "synthetic": True,
            },
        )
        router = DataSourceRouter(config=cfg)
        try:
            assert router._selection(provider=None, source=None, order=None) == (
                "tdx",
                None,
                None,
            )
        finally:
            router.close()

    def test_empty_order_falls_back_to_deterministic_tdx(self) -> None:
        """空 ``order`` 不是「全部源都试过」，而是回到 tdx 默认选择。"""
        cfg = SourcesConfig(order=[], enabled={})
        router = DataSourceRouter(config=cfg)
        try:
            assert router._selection(provider=None, source=None, order=None) == (
                "tdx",
                None,
                None,
            )
        finally:
            router.close()


@pytest.mark.unit
class TestFailClosedExecution:
    """失败即失败：不换 Provider，只记录被选中 Provider 的 diagnostics。"""

    class _FakeService:
        """最小服务替身：记录被调用的 Provider，并按需失败。"""

        def __init__(self, *, fail: str | None = None, rows: list | None = None) -> None:
            self.fail = fail
            self.rows = rows if rows is not None else [{"datetime": "2024-01-01", "close": 1.0}]
            self.calls: list[tuple[str, str | None]] = []
            self.closed = False

        def bars(self, symbol, *, period="day", count=320, start=0, adjust="", provider=None):  # noqa: ANN001
            self.calls.append(("bars", provider))
            if provider == self.fail:
                raise SourceUnavailable(f"{provider} down")
            return list(self.rows)

        def quotes(self, symbols, *, provider=None, source=None, with_meta=False):  # noqa: ANN001
            self.calls.append(("quotes", provider))
            if provider == self.fail:
                raise SourceUnavailable(f"{provider} down")
            return list(self.rows)

        def close(self) -> None:
            self.closed = True

    def test_provider_failure_is_not_recovered_by_another_provider(self) -> None:
        service = self._FakeService(fail="tdx")
        router = DataSourceRouter(service=service)  # type: ignore[arg-type]
        try:
            with pytest.raises(SourceUnavailable, match="tdx down"):
                router.kline("600000", period="day", count=5, as_format="dict")
            # 只调用过一次，且只针对被选中的 tdx
            assert service.calls == [("bars", "tdx")]
            failed = [s for s, _ in router.last_errors]
            assert failed == ["tdx"]
            assert router.last_source is None
        finally:
            router.close()

    def test_quotes_provider_failure_is_not_recovered(self) -> None:
        service = self._FakeService(fail="tdx")
        router = DataSourceRouter(service=service)  # type: ignore[arg-type]
        try:
            with pytest.raises(SourceUnavailable, match="tdx down"):
                router.quotes(["600519"], as_format="dict")
            assert service.calls == [("quotes", "tdx")]
            assert [s for s, _ in router.last_errors] == ["tdx"]
        finally:
            router.close()

    def test_explicit_web_failure_does_not_touch_tdx(self) -> None:
        service = self._FakeService(fail="tencent")
        router = DataSourceRouter(service=service)  # type: ignore[arg-type]
        try:
            with pytest.raises(SourceUnavailable, match="tencent down"):
                router.kline("600000", provider="tencent", period="day", count=5)
            assert service.calls == [("bars", "tencent")]
            assert [s for s, _ in router.last_errors] == ["tencent"]
        finally:
            router.close()

    def test_diagnostics_are_reset_before_each_request(self) -> None:
        service = self._FakeService(fail="tdx")
        router = DataSourceRouter(service=service)  # type: ignore[arg-type]
        try:
            with pytest.raises(SourceUnavailable):
                router.kline("600000", period="day", count=3)
            assert len(router.last_errors) == 1

            # 第二次请求：旧错误被清空后重新记录（不存在累积）
            with pytest.raises(SourceUnavailable):
                router.kline("600000", period="day", count=3)
            assert len(router.last_errors) == 1
        finally:
            router.close()

    def test_success_records_single_last_source(self) -> None:
        service = self._FakeService()
        router = DataSourceRouter(service=service)  # type: ignore[arg-type]
        try:
            router.kline("600000", period="day", count=5, as_format="dict")
            assert router.last_errors == []
            assert router.last_source == "tdx"
        finally:
            router.close()

    def test_last_source_none_initially(self) -> None:
        router = DataSourceRouter(order=["tdx"])
        try:
            assert router.last_source is None
            assert router.last_errors == []
        finally:
            router.close()


@pytest.mark.unit
class TestSpecialModeAuthorisation:
    """replay / synthetic 必须显式授权，绝不在生产查询里隐式启用。"""

    def test_cache_selector_requires_explicit_replay_authorisation(self) -> None:
        router = DataSourceRouter(order=["cache"])
        try:
            with pytest.raises(SourceUnavailable) as caught:
                router._selection(provider=None, source=None, order=None)
            assert caught.value.context["mode"] == "replay"
            assert caught.value.context["real"] is False
        finally:
            router.close()

    def test_synthetic_selector_requires_explicit_authorisation(self) -> None:
        router = DataSourceRouter(order=["synthetic"])
        try:
            with pytest.raises(SourceUnavailable) as caught:
                router._selection(provider=None, source=None, order=None)
            assert caught.value.context["mode"] == "synthetic"
            assert caught.value.context["real"] is False
        finally:
            router.close()

    def test_authorised_replay_uses_tdx_quotation_replay_channel(self) -> None:
        router = DataSourceRouter(order=["cache"], allow_replay=True)
        try:
            assert router._selection(provider=None, source=None, order=None) == (
                "tdx",
                "quotation",
                "replay",
            )
        finally:
            router.close()

    def test_authorised_synthetic_uses_tdx_quotation_synthetic_channel(self) -> None:
        router = DataSourceRouter(order=["synthetic"], allow_synthetic=True)
        try:
            assert router._selection(provider=None, source=None, order=None) == (
                "tdx",
                "quotation",
                "synthetic",
            )
        finally:
            router.close()

    def test_synthetic_quotes_are_refused(self) -> None:
        """synthetic 仅提供 K 线；实时 quotes 必须显式拒绝。"""
        router = DataSourceRouter(order=["synthetic"], allow_synthetic=True)
        try:
            with pytest.raises(SourceUnavailable) as caught:
                router.quotes(["600519"], as_format="dict")
            assert caught.value.context["real"] is False
        finally:
            router.close()


@pytest.mark.unit
class TestReExportAndWiring:
    """兼容面再导出与默认服务绑定。"""

    def test_source_unavailable_is_the_canonical_error(self) -> None:
        assert RouterSourceUnavailable is SourceUnavailable

    def test_default_router_is_bound_to_exactly_one_service(self) -> None:
        router = DataSourceRouter()
        try:
            service = router._service
            assert service is not None
            # 默认自有服务；显式注入则不接管所有权。
            assert router._owns_service is True
        finally:
            router.close()

    def test_injected_service_is_not_owned(self) -> None:
        sentinel = object()
        router = DataSourceRouter(service=sentinel)  # type: ignore[arg-type]
        assert router._service is sentinel
        assert router._owns_service is False
        # close() 不得触碰外部注入的服务（所有权不在 router）。
        router.close()
        assert router._service is sentinel


@pytest.mark.unit
class TestKlinePeriodDelegation:
    """非 TDX Provider 的周期口径按 Provider 自身解析，不被 TDX category 表拦截。"""

    def test_sina_specific_period_is_passed_through(self) -> None:
        class FakeSinaService:
            def bars(self, symbol, *, period="day", count=320, start=0, adjust="", provider=None):  # noqa: ANN001
                assert provider == "sina"
                return [{"datetime": "2024-01-01", "close": 1.0, "period": period}]

        router = DataSourceRouter(service=FakeSinaService())  # type: ignore[arg-type]
        try:
            rows = router.kline("sh600519", provider="sina", period="120min", count=1)
        finally:
            router.close()
        assert rows[0]["period"] == "120min"
