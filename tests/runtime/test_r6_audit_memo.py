# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""R-6: kernel and executor initialization share a single startup audit."""

from __future__ import annotations

from atst.runtime.audit import audit_runtime
from atst.runtime.kernel import UnifiedRuntime


def test_audit_runtime_is_memoized_across_kernel_and_executor() -> None:
    audit_runtime.cache_clear()
    try:
        before = audit_runtime.cache_info().misses
        runtime = UnifiedRuntime()
        # UnifiedRuntime.__init__ calls audit_runtime(), and its
        # DirectProviderExecutor.__init__ calls it again. One audit total.
        assert audit_runtime.cache_info().misses == before + 1
        first = audit_runtime()
        assert audit_runtime() is first
        assert audit_runtime.cache_info().misses == before + 1
        assert first.providers > 0
        assert runtime is not None
    finally:
        audit_runtime.cache_clear()


def test_audit_report_is_stable_for_repeated_calls() -> None:
    audit_runtime.cache_clear()
    try:
        first = audit_runtime()
        second = audit_runtime()
        assert first == second
        assert first is second
    finally:
        audit_runtime.cache_clear()
