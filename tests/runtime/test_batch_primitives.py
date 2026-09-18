# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""契约测试：canonical batch 请求（BatchSpec / BatchItem / BatchResult）。

batch 只做请求展开与逐项审计，不含任何合并（SingleFlight）或负缓存层，
因此每个 symbol 都是一次独立的 Provider 直连。
"""

from __future__ import annotations

import tstdx.batch as batch_module
from tstdx.batch import BatchItem, BatchResult, BatchSpec


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


def test_batch_module_exposes_no_coalescing_or_negative_cache() -> None:
    """零缓存契约：batch 原语不再携带 SingleFlight / NegativeCache。"""
    assert not hasattr(batch_module, "SingleFlight")
    assert not hasattr(batch_module, "NegativeCache")
    assert batch_module.__all__ == ["BatchSpec", "BatchItem", "BatchResult"]
