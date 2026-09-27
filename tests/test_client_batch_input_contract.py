from __future__ import annotations

from collections.abc import Sequence
from types import MethodType

import pytest

from atst.client import TdxClient
from atst.client.core import _normalize_symbols
from atst.errors import SymbolError, ValidationError


@pytest.mark.parametrize(
    "symbols",
    [None, 123, {"600519"}, {"symbol": "600519"}, b"600519", (item for item in ["600519"])],
)
def test_symbol_batch_rejects_non_sequence_or_ambiguous_containers(symbols) -> None:
    with pytest.raises(ValidationError, match="字符串或字符串 Sequence"):
        _normalize_symbols(symbols)


@pytest.mark.parametrize("symbols", [["600519", 1], ("600519", None)])
def test_symbol_batch_rejects_non_string_members(symbols: Sequence[object]) -> None:
    with pytest.raises(ValidationError, match=r"symbols\[1\]"):
        _normalize_symbols(symbols)


def test_symbol_batch_keeps_single_string_and_sequence_order() -> None:
    assert _normalize_symbols("600519") == ["600519"]
    assert _normalize_symbols(("000001", "600519")) == ["000001", "600519"]


def test_quotes_rejects_invalid_batch_before_pool_io() -> None:
    client = TdxClient(pool=object())

    with pytest.raises(ValidationError):
        client.quotes(None)  # type: ignore[arg-type]


def test_quotes_snapshot_rejects_invalid_member_before_pool_io() -> None:
    client = TdxClient(pool=object())

    with pytest.raises(ValidationError, match=r"symbols\[1\]"):
        client.quotes_snapshot(["600519", 1])  # type: ignore[list-item]


def test_quotes_still_isolates_bad_string_content_instead_of_failing_whole_batch() -> None:
    client = TdxClient(pool=object())

    rows = client.quotes(["not-a-symbol"], as_format="dict")

    assert rows == []
    assert len(client.last_errors) == 1
    assert client.last_errors[0][0] == "not-a-symbol"
    assert isinstance(client.last_errors[0][1], SymbolError)


@pytest.mark.parametrize("workers", [0, -1, 65, True, 1.5, "8"])
def test_quotes_concurrent_rejects_invalid_worker_budget_before_pool_io(workers) -> None:
    client = TdxClient(pool=object())

    with pytest.raises(ValidationError, match="workers"):
        client.quotes_concurrent([], workers=workers)  # type: ignore[arg-type]


def test_quotes_concurrent_workers_always_use_canonical_dict_then_convert_once() -> None:
    client = TdxClient(pool=object())
    seen_formats: list[str] = []

    def fake_quotes(
        self: TdxClient,
        symbols,
        *,
        as_format: str = "dict",
        _collect=None,
    ):
        del self, _collect
        seen_formats.append(as_format)
        return [{"code": symbols[0], "price": 1.0}]

    client.quotes = MethodType(fake_quotes, client)  # type: ignore[method-assign]

    result = client.quotes_concurrent(["600519", "000001"], workers=2, as_format="tuple")

    assert seen_formats == ["dict", "dict"]
    assert result == [("600519", 1.0), ("000001", 1.0)]


def test_quotes_concurrent_invalid_output_format_fails_before_worker_start() -> None:
    client = TdxClient(pool=object())

    with pytest.raises(ValidationError, match="未知输出格式"):
        client.quotes_concurrent(["600519"], workers=1, as_format="yaml")
