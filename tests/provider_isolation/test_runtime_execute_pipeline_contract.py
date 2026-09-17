from dataclasses import dataclass


@dataclass(frozen=True)
class ExecutePipelineIdentity:
    provider: str
    channel: str
    capability: str


def test_execute_pipeline_keeps_provider_identity() -> None:
    plan = ExecutePipelineIdentity(
        provider="tdx",
        channel="quotation",
        capability="bars",
    )
    execution = ExecutePipelineIdentity(
        provider="tdx",
        channel="quotation",
        capability="bars",
    )
    result = ExecutePipelineIdentity(
        provider="tdx",
        channel="quotation",
        capability="bars",
    )

    assert plan == execution == result


def test_execute_pipeline_rejects_provider_switch() -> None:
    plan = ExecutePipelineIdentity(
        provider="tdx",
        channel="quotation",
        capability="bars",
    )
    result = ExecutePipelineIdentity(
        provider="eastmoney",
        channel="kline",
        capability="bars",
    )

    assert plan.provider != result.provider
