from tstdx.query import QueryPlanner, QuerySpec
from tstdx.runtime_identity import cache_identity_from_plan


def _bars_plan(provider: str):
    return QueryPlanner(default_provider=provider).compile(
        QuerySpec.build(
            "bars",
            symbols="sh600519",
            provider=provider,
            period="day",
            count=10,
        )
    )


def test_query_fingerprint_is_provider_aware() -> None:
    tdx = _bars_plan("tdx")
    eastmoney = _bars_plan("eastmoney")

    assert tdx.fingerprint.value != eastmoney.fingerprint.value


def test_runtime_cache_identity_matches_real_query_plan() -> None:
    tdx = cache_identity_from_plan(_bars_plan("tdx"))
    eastmoney = cache_identity_from_plan(_bars_plan("eastmoney"))

    assert tdx.key() != eastmoney.key()
    assert tdx.provider == "tdx"
    assert eastmoney.provider == "eastmoney"
