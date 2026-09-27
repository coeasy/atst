from __future__ import annotations

from typing import Any

import pytest

from atst.errors import ValidationError
from atst.query import QueryPlanner, QuerySpec


@pytest.mark.parametrize(
    ("capability", "channel", "period"),
    [
        ("quotes", "extended", ""),
        ("quotes", "goods", ""),
        ("bars", "extended", "day"),
        ("bars", "goods", "day"),
        ("bars", "vipdoc", "day"),
    ],
)
def test_tdx_provider_specific_channels_are_not_silently_executed_by_unified_query(
    capability: str,
    channel: str,
    period: str,
) -> None:
    kwargs: dict[str, Any] = {
        "symbols": ("sh600519",),
        "provider": "tdx",
        "channel": channel,
    }
    if capability == "bars":
        kwargs.update(period=period, count=10)

    with pytest.raises(ValidationError) as caught:
        QueryPlanner().compile(QuerySpec.build(capability, **kwargs))

    assert caught.value.context["provider"] == "tdx"
    assert caught.value.context["channel"] == channel
    assert caught.value.context["canonical_channel"] == "quotation"
    assert caught.value.context["channel_switch_allowed"] is False
    assert caught.value.context["provider_switch_allowed"] is False


@pytest.mark.parametrize(
    ("provider", "capability", "channel", "period"),
    [
        ("tdx", "quotes", "quotation", ""),
        ("tencent", "quotes", "quote", ""),
        ("sina", "quotes", "quote", ""),
        ("eastmoney", "quotes", "quote", ""),
        ("baidu", "quotes", "quote", ""),
        ("tdx", "bars", "quotation", "day"),
        ("tencent", "bars", "minute_kline", "5min"),
        ("tencent", "bars", "kline", "day"),
        ("sina", "bars", "history_kline", "day"),
        ("eastmoney", "bars", "kline", "day"),
        ("baidu", "bars", "kline", "day"),
    ],
)
def test_explicit_canonical_channel_matches_the_actual_unified_executor(
    provider: str,
    capability: str,
    channel: str,
    period: str,
) -> None:
    kwargs: dict[str, Any] = {
        "symbols": ("sh600519",),
        "provider": provider,
        "channel": channel,
    }
    if capability == "bars":
        kwargs.update(period=period, count=10)

    plan = QueryPlanner().compile(QuerySpec.build(capability, **kwargs))

    assert plan.provider == provider
    assert plan.channel == channel
    assert plan.spec.channel == channel


class NoIoExecutor:
    """Proves an invalid plan never reaches Provider I/O."""

    def __init__(self) -> None:
        self.calls = 0

    def execute(self, plan: Any) -> Any:
        self.calls += 1
        raise AssertionError(f"invalid channel plan must fail before {plan.channel} I/O")

    def close(self) -> None:
        return None


def test_kernel_rejects_channel_mismatch_before_any_io() -> None:
    from atst.runtime.kernel import UnifiedRuntime

    runtime = UnifiedRuntime()
    runtime.executor = NoIoExecutor()
    spec = QuerySpec.build(
        "bars",
        symbols=("sh600519",),
        provider="tdx",
        channel="vipdoc",
        period="day",
        count=10,
    )

    with pytest.raises(ValidationError):
        runtime.execute(spec)

    assert runtime.executor.calls == 0
