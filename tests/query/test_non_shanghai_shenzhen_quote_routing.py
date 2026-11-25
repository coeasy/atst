# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""非沪深标的的缺省 quotes 路由（2026-11-24 本地真机实测回归）。

真机症状：``Client().quotes(["hk00700"])`` 在缺省 provider 下先被翻译成
``hk_quotes``，能力目录的缺省 provider（derived / tencent）都把这条能力挂在
``catalog`` 上（live=False），于是 ``currentness='live'`` 直接 E1010
「所选 Channel 不是 live channel」；北交所则钉死 tdx 的未验证 market=2。
修复：与 bars 一样保留 canonical ``quotes`` capability，只把缺省 provider
换成有 live quote 通道的 Web 源（tencent 优先），显式 provider 不介入。
"""

from __future__ import annotations

import pytest

from atst.providers import PROVIDERS
from atst.query import QueryPlanner, QuerySpec


@pytest.mark.parametrize("symbol", ["hk00700", "usAAPL", "bj833171"])
def test_non_mainland_symbols_route_to_live_web_quotes(symbol: str) -> None:
    plan = QueryPlanner().compile(QuerySpec.build("quotes", symbols=[symbol]))
    assert plan.spec.capability == "quotes", "不能再翻译成 catalog 上的 hk_quotes/us_quotes"
    assert plan.provider == "tencent"
    assert plan.channel == "quote"
    channel = next(ch for ch in PROVIDERS.get(plan.provider).channels if ch.id == plan.channel)
    assert channel.live is True


def test_mixed_market_batch_routes_as_one_live_web_quote() -> None:
    plan = QueryPlanner().compile(
        QuerySpec.build("quotes", symbols=["sh600519", "hk00700", "usAAPL", "bj833171"])
    )
    assert plan.provider == "tencent"
    assert plan.channel == "quote"
    assert plan.spec.symbols == ("sh600519", "hk00700", "usAAPL", "bj833171")


def test_mainland_only_batch_still_uses_tdx() -> None:
    plan = QueryPlanner().compile(QuerySpec.build("quotes", symbols=["sh600519", "sz000001"]))
    assert plan.provider == "tdx"
    assert plan.channel == "quotation"


@pytest.mark.parametrize("symbol", ["hk00700", "usAAPL", "bj833171"])
def test_explicit_tdx_provider_is_not_rerouted(symbol: str) -> None:
    """调用方钉死 tdx 时仍由二进制层 fail-closed，不能被市场路由悄悄换源。"""
    plan = QueryPlanner().compile(QuerySpec.build("quotes", symbols=[symbol], provider="tdx"))
    assert plan.provider == "tdx"
    assert plan.channel == "quotation"


def test_non_tdx_bars_routing_remains_intact() -> None:
    plan = QueryPlanner().compile(QuerySpec.build("bars", symbols=["hk00700"], count=3))
    assert plan.provider == "eastmoney"
    assert plan.channel == "kline"
