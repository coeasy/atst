from __future__ import annotations


def test_semantic_cache_is_available_from_top_level() -> None:
    import tstdx

    assert tstdx.SemanticResultCache.__name__ == "SemanticResultCache"
