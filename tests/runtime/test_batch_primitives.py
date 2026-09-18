# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""契约测试：canonical batch 审计原语（BatchItem / BatchResult）。

batch 只做请求展开与逐项审计，不含请求信封（v13 ``BatchSpec`` 已删除）、
合并（SingleFlight）或负缓存层，因此每个 symbol 都是一次独立的 Provider 直连。
"""

from __future__ import annotations

import tstdx.batch as batch_module
from tstdx.batch import BatchItem, BatchResult


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


def test_batch_module_exposes_no_coalescing_or_negative_cache() -> None:
    """零缓存契约：batch 原语不再携带 SingleFlight / NegativeCache。"""
    assert not hasattr(batch_module, "SingleFlight")
    assert not hasattr(batch_module, "NegativeCache")
    assert batch_module.__all__ == ["BatchItem", "BatchResult"]


def test_batch_spec_envelope_does_not_return() -> None:
    """v13 请求信封不复活：批量展开只有内核一条实现路径。"""
    import tstdx

    assert not hasattr(batch_module, "BatchSpec")
    assert not hasattr(tstdx, "BatchSpec")
