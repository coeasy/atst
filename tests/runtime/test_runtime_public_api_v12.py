from __future__ import annotations


def test_top_level_v13_public_api_is_client_first() -> None:
    import tstdx

    assert tstdx.Client.__name__ == "Client"
    assert tstdx.AsyncClient.__name__ == "AsyncClient"
    assert tstdx.QuerySpec.__name__ == "QuerySpec"
    assert tstdx.StreamSpec.__name__ == "StreamSpec"
    assert tstdx.BatchSpec.__name__ == "BatchSpec"
    assert tstdx.UnifiedRuntime.__name__ == "UnifiedRuntime"
    assert tstdx.FallbackPolicy.__name__ == "FallbackPolicy"
    assert tstdx.ProviderOrchestrator.__name__ == "ProviderOrchestrator"
    assert tstdx.ErrorEnvelope.__name__ == "ErrorEnvelope"


def test_legacy_business_api_is_not_top_level_public_surface() -> None:
    """历史业务 API 不是顶层官方公开面（v13 §5.3）。

    官方公开面 = ``tstdx.__all__`` ∪ 惰性导出表 ``tstdx._LAZY``。
    这里刻意不检查 ``hasattr(tstdx, "facade")``：任何模块只要 import 过
    ``tstdx.facade`` 子模块，Python 就会把该属性挂到包上，断言会随测试收集
    顺序漂移（非契约）。
    """
    import tstdx

    for name in (
        "UnifiedQuoteAPI",
        "quote_api",
        "TdxClient",
        "AsyncTdxClient",
        "WebQuoteClient",
        "DirectProviderExecutor",
        "facade",
    ):
        assert name not in tstdx.__all__
        assert name not in tstdx._LAZY

    # 非子模块名还必须真的不可从包命名空间直达。
    for name in ("UnifiedQuoteAPI", "quote_api", "TdxClient", "WebQuoteClient"):
        assert not hasattr(tstdx, name)


def test_facade_namespace_is_a_deprecated_compat_layer_without_transport_classes() -> None:
    """v15：``tstdx.facade`` 是**已弃用**的兼容层，而非 runtime 入口。

    它保留历史业务门面（``UnifiedQuoteAPI`` 等）供迁移期使用，但不得暴露
    底层传输 / 执行实现，也不得再承担 canonical runtime 入口的角色
    （canonical 入口是 :class:`tstdx.Client` / :class:`tstdx.RuntimeGateway`）。
    """
    import tstdx.facade as facade

    assert "UnifiedQuoteAPI" in facade.__all__
    assert "LegacyUnifiedQuoteAPI" in facade.__all__
    for name in (
        "TdxClient",
        "AsyncTdxClient",
        "WebQuoteClient",
        "DirectProviderExecutor",
        "runtime_api",
        "UnifiedRuntime",
    ):
        assert name not in facade.__all__
        assert not hasattr(facade, name)
