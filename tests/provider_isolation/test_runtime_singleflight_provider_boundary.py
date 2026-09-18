from tstdx.query import QueryPlanner, QuerySpec
from tstdx.runtime_identity import cache_identity_from_plan


def _identity(provider: str):
    plan = QueryPlanner(default_provider=provider).compile(
        QuerySpec.build(
            "bars",
            symbols="sh600519",
            provider=provider,
            period="day",
            count=10,
        )
    )
    return cache_identity_from_plan(plan)


def test_singleflight_identity_is_derived_from_real_provider_plan() -> None:
    tdx = _identity("tdx")
    eastmoney = _identity("eastmoney")

    assert tdx.key() != eastmoney.key()


def test_same_real_plan_has_stable_singleflight_identity() -> None:
    assert _identity("tdx").key() == _identity("tdx").key()
