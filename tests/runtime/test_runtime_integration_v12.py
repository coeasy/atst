from __future__ import annotations

import threading
import time

import pytest

from tstdx.batch import BatchItem, BatchResult, NegativeCache, SingleFlight
from tstdx.direct_provider import DIRECT_BINDINGS, DirectProviderExecutor, audit_direct_bindings
from tstdx.error_envelope import to_error_envelope
from tstdx.errors import SourceUnavailable, ValidationError
from tstdx.query import QueryPlanner, QuerySpec
from tstdx.result import Provenance, QueryResult
from tstdx.runtime_v13 import UnifiedRuntime


def _plan(provider: str = "tdx"):
    return QueryPlanner().compile(
        QuerySpec.build("quotes", symbols=["sh600519"], provider=provider, currentness="live")
    )


def test_direct_binding_audit_covers_planner_visible_core_contracts() -> None:
    bindings = audit_direct_bindings()
    assert bindings == DIRECT_BINDINGS
    keys = {item.key for item in bindings}
    for capability in (
        "quotes",
        "bars",
        "snapshot",
        "minute",
        "trades",
        "security_count",
        "security_list",
    ):
        assert ("tdx", "quotation", capability) in keys
    assert ("local_vipdoc", "vipdoc", "bars") in keys
    assert ("eastmoney", "kline", "bars") in keys
    assert ("baidu", "quote", "quotes") in keys


def test_direct_executor_missing_binding_fails_before_io() -> None:
    plan = _plan("tdx")
    executor = DirectProviderExecutor()
    executor._bindings.clear()
    with pytest.raises(ValidationError) as info:
        executor.execute(plan)
    assert info.value.context["fallback"] is False
    assert info.value.context["provider_switch_allowed"] is False


def test_error_envelope_hides_native_details_and_preserves_domain_identity() -> None:
    native = to_error_envelope(RuntimeError("secret path /tmp/token"))
    assert native.code == "E9000"
    assert native.message == "internal error"
    assert "secret" not in str(native.to_dict())

    exc = ValidationError(
        "bad request",
        context={"provider": "tdx", "secret_token": "do-not-leak"},
    )
    domain = to_error_envelope(exc)
    assert domain.code == "E1010"
    assert domain.http_status == 422
    assert domain.context["provider"] == "tdx"
    assert "secret_token" not in domain.context


def test_batch_result_is_read_only_and_auditable() -> None:
    result = BatchResult.build(
        {
            "a": BatchItem("ok", value={"x": 1}),
            "b": BatchItem("failed", error=ValidationError("bad")),
            "c": BatchItem("missing"),
            "d": BatchItem("not_attempted"),
        }
    )
    assert result.failed == ("b",)
    assert result.missing == ("c",)
    assert result.not_attempted == ("d",)
    with pytest.raises(TypeError):
        result.items["x"] = BatchItem("ok", value={"x": 2})  # type: ignore[index]


def test_negative_cache_is_full_fingerprint_and_rejects_transient_failure() -> None:
    plan = _plan()
    cache = NegativeCache(ttl=1.0)
    assert cache.put(plan, SourceUnavailable("temporary")) is False
    assert cache.get(plan) is None

    terminal = ValidationError("terminal", context={"provider": "tdx"})
    assert cache.put(plan, terminal, now_ns=1_000_000_000) is True
    hit = cache.get(plan, now_ns=1_500_000_000)
    assert isinstance(hit, ValidationError)
    assert hit is not terminal
    assert hit.context is not terminal.context
    assert cache.get(plan, now_ns=2_000_000_000) is None


def test_singleflight_followers_get_independent_values() -> None:
    plan = _plan()
    sf = SingleFlight()
    calls = 0
    lock = threading.Lock()
    start = threading.Barrier(3)
    outputs: list[dict[str, list[int]]] = []

    def work():
        nonlocal calls
        with lock:
            calls += 1
        time.sleep(0.05)
        return {"rows": [1]}

    def runner():
        start.wait()
        outputs.append(sf.do(plan, work))

    threads = [threading.Thread(target=runner) for _ in range(2)]
    for thread in threads:
        thread.start()
    start.wait()
    for thread in threads:
        thread.join()

    assert calls == 1
    assert len(outputs) == 2
    outputs[0]["rows"].append(2)
    assert outputs[1]["rows"] == [1]


