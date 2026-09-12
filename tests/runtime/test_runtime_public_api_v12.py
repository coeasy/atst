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
        assert not hasattr(tstdx, name)


def test_facade_namespace_has_no_business_exports() -> None:
    import tstdx.facade as facade

    assert facade.__all__ == []
    assert not hasattr(facade, "UnifiedQuoteAPI")
    assert not hasattr(facade, "runtime_api")
