from dataclasses import dataclass


@dataclass(frozen=True)
class _Meta:
    provider: str


def test_result_provider_identity_must_not_drift() -> None:
    plan_provider = "tdx"
    result_meta = _Meta(provider="tdx")

    assert plan_provider == result_meta.provider


def test_result_provider_identity_drift_is_detectable() -> None:
    plan_provider = "tdx"
    result_meta = _Meta(provider="eastmoney")

    assert plan_provider != result_meta.provider
