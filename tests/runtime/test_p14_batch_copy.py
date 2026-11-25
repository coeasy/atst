# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""P-14: BatchResult copies containers, not every audited value."""

from __future__ import annotations

import pytest

from atst.batch import BatchItem, BatchResult
from atst.error_envelope import to_error_envelope
from atst.errors import ValidationError


def test_items_and_errors_keep_value_identity_while_meta_is_deep_copied() -> None:
    exc = ValidationError("bad caller option")
    item = BatchItem("failed", error=exc)
    envelope = to_error_envelope(exc)
    errors = {"sh600000": envelope}
    meta = {"nested": {"count": 1}}

    result = BatchResult.build(
        {"sh600000": item},
        errors=errors,
        requested=("sh600000",),
        meta=meta,
    )

    assert result.items["sh600000"] is item
    assert result.errors["sh600000"] is envelope
    meta["nested"]["count"] = 99
    assert result.meta["nested"]["count"] == 1


def test_caller_container_mutations_do_not_change_the_audit() -> None:
    item = BatchItem("ok", value="payload")
    items = {"sh600519": item}
    errors: dict[str, object] = {}
    result = BatchResult.build(items, errors=errors, requested=("sh600519",))

    items["extra"] = item
    assert set(result.items) == {"sh600519"}
    with pytest.raises(TypeError):
        result.items["extra"] = item  # type: ignore[index]
    with pytest.raises(TypeError):
        result.errors["extra"] = object()  # type: ignore[index]
