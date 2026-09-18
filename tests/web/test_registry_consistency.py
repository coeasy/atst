"""Web 源三方一致性门禁（P2 #15）。

防「注册遗漏」：SourceSpec 注册表 (``KNOWN_SOURCES``) ↔ 适配器注册表
(``_ADAPTERS``) ↔ 归一化注册表 (``_NORMALIZER_REGISTRY``) ↔ facade 方法
四方一致。

不变量：
1. spec 名集合 == adapter 名集合（无孤儿合约 / 无孤儿实现）。
2. 每个 spec 名在归一化注册表中**显式**登记（P1 #7 显式化，弃用静默 identity 兜底）。
3. 每个 adapter 可无错实例化（``create_source`` 不抛、类引用有效）。
4. 每个面向用户的能力 (capability) 至少映射到一个 ``WebQuoteSession`` facade 方法。
"""

from __future__ import annotations

import pytest

from tstdx.web import _ADAPTERS, create_source
from tstdx.web.normalize import _NORMALIZER_REGISTRY
from tstdx.web.session import WebQuoteSession
from tstdx.web.sources import KNOWN_SOURCES

pytestmark = pytest.mark.unit

# 能力 → 期望存在的 facade 方法（回归守卫：方法被改名/删除即报警）
CAPABILITY_FACADE: dict[str, set[str]] = {
    "quote": {"quotes", "hk_quotes", "us_quotes"},
    "kline": {"klines", "history"},
    "minute_kline": {"klines"},
    "minute": {"minute", "intraday"},
    "history": {"history", "sina_fund_flow", "sina_board_fund_flow"},
    "rank": {"rank", "board_rank", "em_boards"},
    "fund_flow": {
        "fund_flow",
        "fund_flow_history",
        "big_order_flow",
        "sina_fund_flow",
        "sina_board_fund_flow",
    },
    "limit_pool": {"limit_pool"},
    "stock_changes": {"stock_changes"},
    "hot_rank": {"hot_rank"},
    "northbound": {"northbound"},
    "news": {"news"},
    "tick": {"ticks"},
    "suggest": {"suggest"},
    "longhu": {"longhu"},
    "global": {"globals"},
    "stat": {"market_stat"},
    "corporate": {
        "profile",
        "notices",
        "reports",
        "shareholders",
        "holder_num",
        "block_trades",
        "unlocks",
        "performance",
        "forecast",
    },
    "all_market": {"all_market"},
    "fx": {"rates"},
    "wencai": {"wencai"},
    "stock_boards": {"stock_boards"},
    "ipo": {"ipo_calendar"},
    "fund_nav_history": {"fund_nav_history"},
    "fund_estimate": {"fund_estimate"},
    "fund_list": {"fund_list"},
    "index_constituents": {"index_constituents"},
    "margin": {"margin"},
}
# 这些能力仅程序化访问（无专属 facade 方法），豁免 facade 覆盖断言
EXEMPT_CAPABILITIES: set[str] = {"bond", "etf"}


def test_spec_and_adapter_keys_match() -> None:
    """每个 SourceSpec 必须有对应 adapter，反之亦然。"""
    spec_keys = set(KNOWN_SOURCES)
    adapter_keys = set(_ADAPTERS)
    assert spec_keys == adapter_keys, (
        "SourceSpec 与 adapter 注册不一致：\n"
        f"spec 多: {spec_keys - adapter_keys}\n"
        f"adapter 多: {adapter_keys - spec_keys}"
    )


def test_every_spec_has_explicit_normalizer() -> None:
    """P1 #7：每个源都应显式登记 normalizer，不得依赖静默 identity 兜底。"""
    missing = set(KNOWN_SOURCES) - set(_NORMALIZER_REGISTRY)
    assert not missing, f"以下源缺少显式 normalizer 注册: {sorted(missing)}"


def test_every_adapter_instantiable() -> None:
    """每个注册适配器都应可被 ``create_source`` 无错实例化（类引用有效）。"""
    bad: dict[str, str] = {}
    for name in sorted(_ADAPTERS):
        spec = KNOWN_SOURCES.get(name)
        kwargs = {"cookie": "dummy"} if (spec and spec.needs_cookie) else {}
        try:
            create_source(name, **kwargs)
        except Exception as exc:  # noqa: BLE001
            bad[name] = repr(exc)
    assert not bad, f"以下 adapter 实例化失败: {bad}"


@pytest.mark.parametrize("capability,methods", sorted(CAPABILITY_FACADE.items()))
def test_capability_has_facade_method(capability: str, methods: set[str]) -> None:
    """每个面向用户的能力至少有一個 facade 方法（方法改名/删除即报警）。"""
    present = {m for m in methods if callable(getattr(WebQuoteSession, m, None))}
    assert present, f"能力 {capability!r} 无任何 facade 方法（期望 {methods}）"


def test_no_orphan_capability() -> None:
    """所有 spec 声明的能力应被 facade 覆盖或明确豁免。"""
    all_caps = {c for spec in KNOWN_SOURCES.values() for c in spec.capabilities}
    covered = set(CAPABILITY_FACADE) | EXEMPT_CAPABILITIES
    orphan = all_caps - covered
    assert not orphan, f"以下能力既无 facade 映射也未豁免: {sorted(orphan)}"
