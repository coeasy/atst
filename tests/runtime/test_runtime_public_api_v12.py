from __future__ import annotations


def test_top_level_runtime_exports_are_available() -> None:
    import tstdx

    assert tstdx.UnifiedRuntime.__name__ == "UnifiedRuntime"
    assert tstdx.DirectProviderExecutor.__name__ == "DirectProviderExecutor"
    assert tstdx.SemanticResultCache.__name__ == "SemanticResultCache"
    assert tstdx.PersistentSemanticCache.__name__ == "PersistentSemanticCache"
    assert tstdx.SingleFlight.__name__ == "SingleFlight"
    assert tstdx.NegativeCache.__name__ == "NegativeCache"
    assert tstdx.ErrorEnvelope.__name__ == "ErrorEnvelope"


def test_facade_exposes_strict_runtime_without_removing_legacy_api() -> None:
    from tstdx.facade import UnifiedQuoteAPI, UnifiedRuntime, runtime_api

    runtime = runtime_api()
    assert isinstance(runtime, UnifiedRuntime)
    assert UnifiedQuoteAPI.__name__ == "UnifiedQuoteAPI"
