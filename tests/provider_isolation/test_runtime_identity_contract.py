import pytest

from atst.query import QueryPlanner, QuerySpec
from atst.runtime.identity import RuntimeExecutionIdentity, execution_identity_from_plan


def test_runtime_execution_identity_is_provider_specific() -> None:
    tdx = RuntimeExecutionIdentity(provider="tdx", channel="quotation", capability="bars")
    eastmoney = RuntimeExecutionIdentity(provider="eastmoney", channel="push2", capability="bars")

    assert tdx != eastmoney


def test_execution_identity_key_normalizes_case_and_padding() -> None:
    identity = RuntimeExecutionIdentity(provider="  TDX ", channel="Quotation", capability="BARS")

    assert identity.key() == ("tdx", "quotation", "bars")


@pytest.mark.parametrize(
    ("capability", "kwargs"),
    [
        ("bars", {"period": "day", "count": 5}),
        ("quotes", {}),
    ],
)
def test_execution_identity_matches_the_compiled_plan_route(capability: str, kwargs: dict) -> None:
    """The guard identity must be the plan's own provider/channel, not the capability alone."""
    plan = QueryPlanner().compile(
        QuerySpec.build(capability=capability, symbols=["sh600519"], provider="tdx", **kwargs)
    )

    identity = execution_identity_from_plan(plan)

    assert (identity.provider, identity.channel, identity.capability) == (
        plan.provider,
        plan.channel,
        capability,
    )
