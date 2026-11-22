# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""bars 的市场感知缺省路由：港美股从 tdx 转到 operational Web 源。"""

from __future__ import annotations

import pytest

from atst import QuerySpec

pytestmark = pytest.mark.unit


def test_a_share_bars_default_stays_tdx() -> None:
    """A 股与北交所仍走默认 tdx，路由分支不得改变主路径。"""
    for symbol in ("sh600519", "sz000001", "bj430047"):
        spec = QuerySpec.build("bars", symbols=symbol, count=2).normalized()
        assert spec.provider == "tdx"


@pytest.mark.parametrize("symbol", ["hk00700", "usAAPL"])
def test_non_tdx_market_bars_routes_to_web_provider(symbol: str) -> None:
    """港美股 bars 缺省不能落到 tdx（执行期 E4040），须转 Web 源。"""
    spec = QuerySpec.build("bars", symbols=symbol, count=2).normalized()
    assert spec.provider in ("eastmoney", "tencent")
    assert spec.provider != "tdx"


@pytest.mark.parametrize("symbol", ["hk00700", "usAAPL"])
def test_explicit_provider_wins_over_market_routing(symbol: str) -> None:
    """显式 provider 是契约，市场路由分支完全不介入。"""
    spec = QuerySpec.build("bars", symbols=symbol, provider="tencent", count=2).normalized()
    assert spec.provider == "tencent"


def test_routing_is_idempotent() -> None:
    """已归一化的 spec 再归一化不改变 provider（无链式换源）。"""
    once = QuerySpec.build("bars", symbols="hk00700", count=2).normalized()
    twice = once.normalized()
    assert twice.provider == once.provider
