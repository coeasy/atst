from __future__ import annotations

import pytest

from tstdx.provider import (
    LocalProvider,
    Provider,
    ProviderRouter,
    TdxProvider,
    WebProvider,
    adapter_for,
    default_adapters,
)
from tstdx.runtime import QueryRequest


class TestProviderUnifiedContract:
    def test_provider_exposes_phase3_surface(self) -> None:
        """统一接口: capabilities / execute / health / metadata / supports。"""
        for method in ("capabilities", "execute", "health", "metadata", "supports", "query"):
            assert callable(getattr(Provider, method, None)), f"Provider 缺少 {method}()"

    def test_default_adapters_cover_all_registered_providers(self) -> None:
        from tstdx.providers import PROVIDERS

        adapters = default_adapters()
        expected = set(PROVIDERS.ids())
        assert set(adapters) == expected
        assert all(isinstance(a, Provider) for a in adapters.values())

    def test_adapter_for_selects_correct_types(self) -> None:
        assert isinstance(adapter_for("tdx"), TdxProvider)
        assert isinstance(adapter_for("local_vipdoc"), LocalProvider)
        assert isinstance(adapter_for("eastmoney"), WebProvider)
        assert isinstance(adapter_for("tencent"), WebProvider)
        assert isinstance(adapter_for("jsl"), WebProvider)

    def test_capabilities_derived_from_registry(self) -> None:
        from tstdx.providers import PROVIDERS

        # 无 backend 时未配置，能力应为空（不声明可执行能力）
        for pid in ("tdx", "local_vipdoc", "eastmoney", "tencent", "boc"):
            adapter = adapter_for(pid)
            assert adapter.capabilities() == frozenset()

        # 有 backend 时能力 = 注册表能力 ∩ backend 方法
        class FullSource:
            def fund_manager(self, code: str = "", **kwargs):
                return {"code": code}

        adapter = adapter_for("eastmoney", FullSource())
        parser_adapter = adapter_for("tencent", FullSource())
        # eastmoney 注册表含 fund_manager
        assert "fund_manager" in adapter.capabilities()
        # tencent 注册表不含 fund_manager -> 即使 backend 有也不算
        assert "fund_manager" not in parser_adapter.capabilities()

    def test_web_capabilities_intersect_source(self) -> None:
        class FundOnlySource:
            def fund_manager(self, code: str = "", **kwargs):
                return {"code": code}

        adapter = adapter_for("eastmoney", FundOnlySource())
        caps = adapter.capabilities()
        assert "fund_manager" in caps
        # 仅 source 实现的方法才会暴露
        assert caps == frozenset({"fund_manager"})

    def test_metadata_reports_configured_state(self) -> None:
        empty = adapter_for("eastmoney")
        assert empty.metadata()["configured"] is False
        assert empty.metadata()["provider"] == "eastmoney"

        configured = adapter_for("eastmoney", object())
        assert configured.metadata()["configured"] is True
        assert configured.metadata()["adapter"] == "WebProvider"

    def test_execute_delegates_to_backend(self) -> None:
        class Source:
            def fund_manager(self, code: str = "", **kwargs):
                return {"kind": "fund_manager", "code": code}

        adapter = adapter_for("eastmoney", Source())
        request = QueryRequest("fund_manager", {"code": "000001"})
        result = adapter.execute(request)
        assert result == {"kind": "fund_manager", "code": "000001"}

    def test_execute_without_backend_raises(self) -> None:
        adapter = adapter_for("eastmoney")
        request = QueryRequest("fund_manager", {})
        with pytest.raises(RuntimeError, match="not configured"):
            adapter.execute(request)

    def test_tdx_execute_delegates_to_client(self) -> None:
        class Client:
            def bars(self, symbol: str = "", **kwargs):
                return {"kind": "bars", "symbol": symbol}

        adapter = TdxProvider(Client())
        request = QueryRequest("bars", {"symbol": "sh600000", "count": 10})
        result = adapter.execute(request)
        assert result == {"kind": "bars", "symbol": "sh600000"}


class TestProviderRouterIntegration:
    def test_router_uses_unified_execute_path(self) -> None:
        class Source:
            def fund_manager(self, code: str = "", **kwargs):
                return {"code": code}

        router = ProviderRouter()
        router.register(adapter_for("eastmoney", Source()))
        request = QueryRequest("fund_manager", {"code": "000001"})
        provider_id, result = router.query_first(request)
        assert provider_id == "eastmoney"
        assert result == {"code": "000001"}

    def test_router_capabilities_based_prefilter(self) -> None:
        class TdxClient:
            def f10(self, symbol: str = "", **kwargs):
                return {"symbol": symbol}

        tdx = TdxProvider(TdxClient())
        assert "f10" in tdx.capabilities()
        assert "fund_manager" not in tdx.capabilities()