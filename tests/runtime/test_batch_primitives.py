# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""契约测试：v13 batch 原语（SingleFlight / NegativeCache / BatchSpec）在 v14 生态下的独立行为。

从 PR #6 (refactor/runtime-integration-v12) 提取的独立工程原语。
测试不依赖 v13 UnifiedRuntime，仅验证原语自身与 QueryPlan 的集成契约。
"""

from __future__ import annotations

import concurrent.futures
import threading
import time

from tstdx.batch import BatchItem, BatchResult, BatchSpec, NegativeCache, SingleFlight
from tstdx.errors import RetryAdvice, TdxError
from tstdx.query import QueryPlanner, QuerySpec


def _plan(capability: str = "quotes", symbol: str = "sh600000") -> QueryPlan:
    spec = QuerySpec(capability=capability, symbols=(symbol,), provider="tdx")
    return QueryPlanner().compile(spec)


# --------------------------------------------------------------------------- #
# BatchSpec
# --------------------------------------------------------------------------- #
class TestBatchSpec:
    def test_quotes_builder(self) -> None:
        spec = BatchSpec.quotes(["sh600000", "sh600519"])
        assert spec.capability == "quotes"
        assert spec.symbols == ("sh600000", "sh600519")
        assert spec.provider == "tdx"
        assert spec.currentness == "live"

    def test_quotes_normalizes_symbols(self) -> None:
        spec = BatchSpec.quotes(["600000", "SH600000"])
        assert spec.symbols == ("sh600000",)

    def test_quotes_empty_rejected(self) -> None:
        from tstdx.errors import ValidationError

        try:
            BatchSpec.quotes([])
            raise AssertionError("empty symbols should be rejected")
        except ValidationError:
            pass

    def test_quotes_negative_max_age_rejected(self) -> None:
        from tstdx.errors import ValidationError

        try:
            BatchSpec.quotes(["sh600000"], max_age=-1)
            raise AssertionError("negative max_age should be rejected")
        except ValidationError:
            pass


# --------------------------------------------------------------------------- #
# BatchItem / BatchResult
# --------------------------------------------------------------------------- #
class TestBatchItem:
    def test_ok_item_requires_value(self) -> None:
        try:
            BatchItem(status="ok")
            raise AssertionError("ok item must carry value")
        except ValueError:
            pass

    def test_ok_item_cannot_carry_error(self) -> None:
        try:
            BatchItem(status="ok", value=1, error=RuntimeError("x"))
            raise AssertionError("ok item cannot carry error")
        except ValueError:
            pass

    def test_non_ok_cannot_carry_value(self) -> None:
        try:
            BatchItem(status="failed", value=1)
            raise AssertionError("failed item cannot carry value")
        except ValueError:
            pass

    def test_invalid_status_rejected(self) -> None:
        try:
            BatchItem(status="bogus")
            raise AssertionError("invalid status should be rejected")
        except ValueError:
            pass


class TestBatchResult:
    def test_build_and_failed(self) -> None:
        result = BatchResult.build(
            {
                "a": BatchItem(status="ok", value=1),
                "b": BatchItem(status="failed", error=RuntimeError("x")),
                "c": BatchItem(status="missing"),
            }
        )
        assert result.failed == ("b",)
        assert result.missing == ("c",)
        assert result.not_attempted == ()

    def test_deepcopy_roundtrip(self) -> None:
        import copy

        result = BatchResult.build({"a": BatchItem(status="ok", value={"x": [1]})})
        cloned = copy.deepcopy(result)
        assert cloned.items["a"].value == {"x": [1]}
        assert cloned is not result


# --------------------------------------------------------------------------- #
# SingleFlight
# --------------------------------------------------------------------------- #
class TestSingleFlight:
    def test_coalesces_concurrent_calls(self) -> None:
        plan = _plan()
        sf = SingleFlight()
        calls: dict[str, int] = {"n": 0}
        lock = threading.Lock()

        def slow() -> dict[str, int]:
            with lock:
                calls["n"] += 1
            time.sleep(0.05)
            return {"value": calls["n"]}

        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as ex:
            futures = [ex.submit(sf.do, plan, slow) for _ in range(4)]
            results = [f.result() for f in futures]

        assert calls["n"] == 1, "concurrent identical work must execute once"
        assert all(r == results[0] for r in results)

    def test_distinct_plans_run_independently(self) -> None:
        plan_a = _plan(capability="quotes", symbol="sh600000")
        plan_b = _plan(capability="quotes", symbol="sz000001")
        sf = SingleFlight()
        calls: dict[str, int] = {"n": 0}
        lock = threading.Lock()

        def slow_a() -> str:
            with lock:
                calls["n"] += 1
            time.sleep(0.05)
            return "a"

        def slow_b() -> str:
            with lock:
                calls["n"] += 1
            time.sleep(0.05)
            return "b"

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as ex:
            fa = [ex.submit(sf.do, plan_a, slow_a) for _ in range(2)]
            fb = [ex.submit(sf.do, plan_b, slow_b) for _ in range(2)]
            results_a = [f.result() for f in fa]
            results_b = [f.result() for f in fb]

        assert calls["n"] == 2
        assert set(results_a) == {"a"}
        assert set(results_b) == {"b"}

    def test_failure_cloned_to_followers(self) -> None:
        plan = _plan()
        sf = SingleFlight()
        attempts: dict[str, int] = {"n": 0}

        def failing() -> None:
            attempts["n"] += 1
            time.sleep(0.05)
            raise RuntimeError("boom")

        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as ex:
            futures = [ex.submit(sf.do, plan, failing) for _ in range(3)]
            errors = []
            for fut in futures:
                try:
                    fut.result()
                except RuntimeError as exc:
                    errors.append(str(exc))

        assert attempts["n"] == 1
        assert errors == ["boom", "boom", "boom"]


# --------------------------------------------------------------------------- #
# NegativeCache
# --------------------------------------------------------------------------- #
class TestNegativeCache:
    def test_roundtrip_and_invalidate(self) -> None:
        plan = _plan()
        nc = NegativeCache(ttl=1.0, maxsize=16)
        assert nc.get(plan) is None

        exc = TdxError("failure", code="E5000", advice=RetryAdvice(retryable=False))
        assert nc.put(plan, exc)
        assert nc.get(plan) is not None

        assert nc.invalidate(plan)
        assert nc.get(plan) is None

    def test_retryable_errors_not_cached(self) -> None:
        plan = _plan()
        nc = NegativeCache(ttl=1.0, maxsize=16)
        exc = TdxError("retryable", code="E5001", advice=RetryAdvice(retryable=True))
        assert not nc.put(plan, exc)
        assert nc.get(plan) is None

    def test_non_tdx_errors_not_cached(self) -> None:
        plan = _plan()
        nc = NegativeCache(ttl=1.0, maxsize=16)
        assert not nc.put(plan, RuntimeError("raw"))
        assert nc.get(plan) is None

    def test_ttl_expiry(self) -> None:
        plan = _plan()
        nc = NegativeCache(ttl=0.05, maxsize=16)
        exc = TdxError("failure", code="E5000", advice=RetryAdvice(retryable=False))
        now = time.time_ns()
        assert nc.put(plan, exc, now_ns=now)
        assert nc.get(plan, now_ns=now) is not None
        # 1s later: expired
        assert nc.get(plan, now_ns=now + 1_000_000_000) is None

    def test_lru_eviction(self) -> None:
        nc = NegativeCache(ttl=1.0, maxsize=2)
        exc = TdxError("failure", code="E5000", advice=RetryAdvice(retryable=False))
        p1 = _plan(symbol="sh600000")
        p2 = _plan(symbol="sh600519")
        p3 = _plan(symbol="sz000001")
        nc.put(p1, exc)
        nc.put(p2, exc)
        nc.put(p3, exc)
        # maxsize=2, so one of the first two was evicted
        metrics = nc.metrics()
        assert metrics["size"] == 2
        assert metrics["evictions"] == 1