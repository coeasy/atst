# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""``adjust`` / ``adjustment`` 别名契约：canonical 胜出、别名填充、下游收到同一值。"""

from __future__ import annotations

import pytest

from atst import Client, QuerySpec

pytestmark = pytest.mark.unit


def test_adjust_alias_fills_when_canonical_empty() -> None:
    spec = QuerySpec.build("bars", symbols="sh600519", count=2, adjust="qfq").normalized()
    assert spec.adjustment == "qfq"


def test_canonical_wins_over_alias() -> None:
    spec = QuerySpec.build(
        "bars", symbols="sh600519", count=2, adjustment="hfq", adjust="qfq"
    ).normalized()
    assert spec.adjustment == "hfq"


def test_adjust_alias_does_not_leak_into_options() -> None:
    """别名是签名形参，不是 options 键——不得被执行面的白名单检查拒绝。"""
    spec = QuerySpec.build("bars", symbols="sh600519", count=2, adjust="qfq").normalized()
    assert "adjust" not in spec.options


def test_client_bars_accepts_adjust_alias() -> None:
    """Client.bars 的 adjust 形参须透传到 QuerySpec（用 monkeypatch 截 execute 避免网络）。"""
    captured: dict[str, object] = {}

    def fake_execute(self: Client, spec: QuerySpec) -> object:  # type: ignore[no-untyped-def]
        captured["adjustment"] = spec.adjustment
        return None

    original = Client.execute
    Client.execute = fake_execute  # type: ignore[method-assign]
    try:
        Client().bars("sh600519", adjust="hfq")
    finally:
        Client.execute = original  # type: ignore[method-assign]
    assert captured["adjustment"] == "hfq"
