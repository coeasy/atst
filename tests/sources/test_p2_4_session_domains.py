# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""P2-4 会话面瘦身的公开面守卫（F4）。

``WebQuoteSession`` 从直接组合 20 个能力 Mixin 改为聚合 4 个域 Mixin（P2-4a），
K 线路由改为查 providers 声明（P2-4b/F3）。组合层调整不许改变公开方法面：
本文件把「域类无遗漏无重复覆盖全部能力 Mixin、公开方法面等于能力 Mixin 并集、
分钟 K 线路由与 providers 声明一致」钉成不变量。
"""

from __future__ import annotations

import pytest

from atst.providers import default_provider_for
from atst.sources._session_astock import AstockToolkitMixin
from atst.sources._session_baidu import BaiduSessionMixin
from atst.sources._session_boards import BoardSessionMixin
from atst.sources._session_datacenter import DatacenterSessionMixin
from atst.sources._session_efinance import (
    DerivativeSessionMixin,
    FundMobSessionMixin,
    StockEfinanceMixin,
)
from atst.sources._session_esg_chip import EsgChipSessionMixin
from atst.sources._session_fund_v2 import (
    FundCompanySessionMixin,
    FundManagerSessionMixin,
    FundRankSessionMixin,
)
from atst.sources._session_fundamental import FundamentalSessionMixin
from atst.sources._session_holders import HoldersSessionMixin
from atst.sources._session_info import CorporateSessionMixin, FundFlowSessionMixin
from atst.sources._session_market import (
    KlineSessionMixin,
    QuoteSessionMixin,
    _capability_adapter,
)
from atst.sources._session_news import NewsSessionMixin
from atst.sources._session_research import ResearchSessionMixin
from atst.sources._session_sector_macro import SectorMacroSessionMixin
from atst.sources.session import (
    WebQuoteSession,
    _CorporateMixin,
    _FundMixin,
    _MarketDataMixin,
    _ResearchMixin,
    _SessionBase,
)

pytestmark = pytest.mark.unit

#: P4 拆分出的全部能力 Mixin（P2-4a 重组前 ``WebQuoteSession`` 的直接基座）。
LEAF_MIXINS: tuple[type, ...] = (
    QuoteSessionMixin,
    KlineSessionMixin,
    BoardSessionMixin,
    DatacenterSessionMixin,
    FundFlowSessionMixin,
    CorporateSessionMixin,
    FundamentalSessionMixin,
    EsgChipSessionMixin,
    SectorMacroSessionMixin,
    HoldersSessionMixin,
    BaiduSessionMixin,
    StockEfinanceMixin,
    FundMobSessionMixin,
    DerivativeSessionMixin,
    AstockToolkitMixin,
    NewsSessionMixin,
    ResearchSessionMixin,
    FundRankSessionMixin,
    FundManagerSessionMixin,
    FundCompanySessionMixin,
)

#: P2-4a 引入的 4 个域 Mixin。
DOMAIN_MIXINS: tuple[type, ...] = (_MarketDataMixin, _CorporateMixin, _ResearchMixin, _FundMixin)


# -- (a) 域聚合 ------------------------------------------------------------- #
def test_domain_mixins_cover_all_leaf_mixins() -> None:
    """域类聚合无遗漏、无重复——每个能力 Mixin 恰好进一个域。"""
    listed = [base for domain in DOMAIN_MIXINS for base in domain.__bases__]
    assert sorted(b.__name__ for b in listed) == sorted(b.__name__ for b in LEAF_MIXINS)
    assert len(listed) == len({b.__name__ for b in listed})


def test_webquotesession_mro_keeps_each_leaf_once() -> None:
    """组合层改为域类后，每个能力 Mixin 仍在 MRO 中且只出现一次。"""
    names = [b.__name__ for b in WebQuoteSession.__mro__]
    for leaf in LEAF_MIXINS:
        assert names.count(leaf.__name__) == 1


def test_leaf_mixins_have_no_public_name_collisions() -> None:
    """能力 Mixin 之间无公开方法名冲突——域聚合调整组合顺序不影响属性解析。"""
    seen: dict[str, str] = {}
    dups: list[str] = []
    for leaf in LEAF_MIXINS:
        for name in (n for n in dir(leaf) if not n.startswith("_")):
            if name in seen and seen[name] != leaf.__name__:
                dups.append(name)
            seen[name] = leaf.__name__
    assert not dups


def test_public_surface_is_union_of_leaf_mixins() -> None:
    """公开方法面（``dir()``）＝能力 Mixin 公开名并集——F4 实测 184 项不回归。"""
    expected: set[str] = set()
    for leaf in (*LEAF_MIXINS, _SessionBase):
        expected.update(n for n in dir(leaf) if not n.startswith("_"))
    actual = {n for n in dir(WebQuoteSession) if not n.startswith("_")}
    assert actual == expected
    assert len(actual) == 184  # reports/arch-review/sources.md F4 实测基数（含 close）


# -- (b) 路由查表 ----------------------------------------------------------- #
def test_minute_klines_routing_follows_provider_declarations() -> None:
    """P2-4b/F3：分钟 K 线市场路由查 providers 声明，不再手写市场硬编码。

    eastmoney.py 声明 ``MARKET_DEFAULT_CAPABILITIES["minute_klines"] = ("hk", "us")``，
    tencent.py 声明该能力总默认——会话层查表结果必须与之逐项一致。
    """
    assert _capability_adapter("minute_klines", symbol="sh600519").__name__ == "MinuteKlineSource"
    assert (
        _capability_adapter("minute_klines", symbol="hk00700").__name__
        == "EastmoneyHistoryKlineSource"
    )
    assert (
        _capability_adapter("minute_klines", symbol="usAAPL").__name__
        == "EastmoneyHistoryKlineSource"
    )
    assert default_provider_for("minute_klines", "sh600519") == "tencent"
    assert default_provider_for("minute_klines", "hk00700") == "eastmoney"


def test_history_source_resolved_from_binding_factories() -> None:
    """P2-4b/F3：history 的适配器类来自 capability="history" 的绑定 factory。"""
    assert _capability_adapter("history", provider="sina").__name__ == "SinaHistoryKlineSource"
    assert (
        _capability_adapter("history", provider="eastmoney").__name__
        == "EastmoneyHistoryKlineSource"
    )


def test_capability_adapter_fails_fast_on_unbound_provider() -> None:
    """providers 声明里没有绑定的 (provider, capability) 组合显式报错，不静默回退。"""
    from atst.errors import CompatibilityError

    with pytest.raises(CompatibilityError, match="providers 声明中"):
        _capability_adapter("history", provider="boc")
