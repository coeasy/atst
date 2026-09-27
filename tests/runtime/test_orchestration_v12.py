from __future__ import annotations

import pytest

from atst.domain.models import Quote
from atst.errors import AllSourcesExhausted, ValidationError
from atst.query import QueryPlanner, QuerySpec
from atst.result import Provenance, QueryResult
from atst.runtime.orchestration import FallbackPolicy, ProviderOrchestrator


def _result(provider: str) -> QueryResult[list[Quote]]:
    plan = QueryPlanner().compile(
        QuerySpec.build(
            "quotes",
            symbols="sh600519",
            provider=provider,
            currentness="live",
        )
    )
    return QueryResult.from_plan(
        [Quote(code="600519", price=1.0)],
        plan=plan,
        provenance=Provenance.direct(plan),
    )


def test_fallback_policy_rejects_empty_duplicate_and_unknown_provider() -> None:
    # 第 10 轮：前三格原本抛裸 ``ValueError``，四面一致地把它报成 E9000/HTTP 500/
    # -32603/退出码 1——调用方写错的键被抹成服务器故障。原先只有最后一格（未知的
    # Provider）已经是 ValidationError，这一族判据本身就不自洽。
    with pytest.raises(ValidationError):
        FallbackPolicy.build()
    with pytest.raises(ValidationError):
        FallbackPolicy.build("tdx", "tdx")
    with pytest.raises(ValidationError):
        FallbackPolicy.build("unknown-provider")


def test_orchestrator_first_provider_success_is_not_fallback() -> None:
    class Runtime:
        def execute(self, spec):
            # ProviderOrchestrator rewrites the spec's provider per attempt and
            # delegates to the generic runtime execute() surface.
            assert spec.provider == "tdx"
            assert spec.capability == "quotes"
            return _result(spec.provider)

    output = ProviderOrchestrator(Runtime()).quotes(
        "sh600519", policy=FallbackPolicy.build("tdx", "eastmoney")
    )
    assert [(item.provider, item.status) for item in output.attempts] == [("tdx", "ok")]
    assert output.result.meta.provider == "tdx"
    assert output.result.meta.provenance.requested_provider == "tdx"
    assert output.result.meta.provenance.fallback is False


def test_orchestrator_fallback_is_explicit_and_auditable() -> None:
    class Runtime:
        def execute(self, spec):
            if spec.provider == "tdx":
                raise ValidationError("tdx unavailable", context={"provider": "tdx"})
            return _result(spec.provider)

    output = ProviderOrchestrator(Runtime()).quotes(
        "sh600519", policy=FallbackPolicy.build("tdx", "eastmoney")
    )
    assert [(item.provider, item.status, item.code) for item in output.attempts] == [
        ("tdx", "failed", "E1010"),
        ("eastmoney", "ok", None),
    ]
    assert output.result.meta.provider == "eastmoney"
    assert output.result.meta.provenance.requested_provider == "tdx"
    assert output.result.meta.provenance.fallback is True

    # 零缓存契约：回退结果不得被任何缓存层吞并，provenance 必须保留回退事实。
    assert output.result.meta.provenance.fallback is True


def test_orchestrator_all_failures_are_sanitized() -> None:
    class Runtime:
        def execute(self, spec):
            raise RuntimeError(f"secret token from {spec.provider}")

    with pytest.raises(AllSourcesExhausted) as info:
        ProviderOrchestrator(Runtime()).quotes(
            "sh600519", policy=FallbackPolicy.build("tdx", "eastmoney")
        )
    assert info.value.context["fallback"] is True
    assert info.value.context["errors"] == ["tdx:E9000", "eastmoney:E9000"]
    assert "secret" not in str(info.value.context)
