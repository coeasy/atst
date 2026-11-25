# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""Regression: ``to_dicts`` / ``to_dicts_lazy`` on mixed object + dict inputs.

The lazy converter dispatches per element: objects exposing ``to_dict()`` use it,
everything else falls back to ``dict(x)``.  A mixed stream of real domain
objects and plain dicts must therefore produce the same dicts as converting
each element individually, and the generator must stay lazy (no element is
converted until it is pulled).
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from atst.domain.models import (
    AuctionSnapshot,
    Bar,
    CapitalChange,
    Quote,
    to_dicts,
    to_dicts_lazy,
)


class _CountingDictable:
    """Minimal ``to_dict()`` object that records when it is actually consumed."""

    def __init__(self, calls: list[str]) -> None:
        self._calls = calls

    def to_dict(self) -> dict[str, Any]:
        self._calls.append("converted")
        return {"counted": True}


def test_to_dicts_mixed_objects_and_plain_dicts() -> None:
    quote = Quote(code="sh600000", price=10.5, volume=100)
    bar = Bar(datetime="2026-01-02", open=1.0, close=2.0)
    change = CapitalChange(code="sh600000", rights_price=1.5)
    snapshot = AuctionSnapshot(code="sh600000", time="09:25", price=10.0)
    raw_a = {"code": "plain-a", "price": 1.0}
    raw_b = {"code": "plain-b", "nested": {"k": [1, 2]}}

    mixed: list[Any] = [quote, raw_a, bar, raw_b, change, snapshot]
    expected = [
        quote.to_dict(),
        dict(raw_a),
        bar.to_dict(),
        dict(raw_b),
        change.to_dict(),
        snapshot.to_dict(),
    ]

    actual = to_dicts(mixed)

    assert actual == expected
    # Key/value equivalence plus key order (dicts compare order-insensitively).
    for got, want in zip(actual, expected, strict=True):
        assert list(got.keys()) == list(want.keys())
    # Plain dicts are copied, not aliased: mutating the source cannot change output.
    raw_a["price"] = 999.0
    assert actual[1]["price"] == 1.0


def test_to_dicts_lazy_is_a_generator_and_defers_conversion() -> None:
    calls: list[str] = []
    stream = to_dicts_lazy([{"first": 1}, _CountingDictable(calls), {"last": 3}])

    assert isinstance(stream, Iterator)
    assert calls == [], "calling to_dicts_lazy must not convert anything yet"

    assert next(stream) == {"first": 1}
    assert calls == []

    assert next(stream) == {"counted": True}
    assert calls == ["converted"]

    assert next(stream) == {"last": 3}
    assert calls == ["converted"]

    try:
        next(stream)
    except StopIteration:
        pass
    else:
        raise AssertionError("generator should be exhausted after three items")
