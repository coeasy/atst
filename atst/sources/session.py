# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""Web Provider 会话组合层（§33）。

在 :mod:`atst.sources` 注册表（``_ADAPTER_SPECS``，惰性 import 的零依赖适配器）之上
提供一套**原生命名**的便捷会话，
使「取实时行情 / 全市场快照 / K 线 / 指数 / 汇率」等操作统一收口，返回 atst
自有 :class:`~atst.domain.models.Quote` / :class:`~atst.domain.models.Bar`
模型（已归一化到「股 / 元」全局契约——外盘行情例外，其 ``volume`` 是合约手数，
单位由 ``extra["volume_unit"]`` 明示，见 :meth:`~atst.sources._session_market.QuoteSessionMixin.globals`）。

本模块不是路由层：它按精确 Provider 取数，**不会**在源不可用时自动换到别的源，
也不构成 v16 已删除的 ``UnifiedQuoteAPI`` 那类跨源聚合门面。

设计原则
--------
* **原生命名**：不沿用任何第三方库的字段或方法命名，所有输出走 atst 数据模型。
* **零反向语义**：A 股 / 港美股口径下 ``volume`` 恒为成交量（股）、``amount`` 恒为
  成交额（元），不再出现「turnover/volume 互换」这类反直觉约定。外盘行情是**明示的
  例外**：``volume`` 为合约手数并带 ``extra["volume_unit"] == "lot"``（见
  :mod:`atst.sources.global_market` 的"单位归一化"节），按股票股数用会差 100 倍。
* **懒加载**：底层源客户端按需创建，``close()`` 释放。

Quick start::

    from atst.sources.session import web_session
    sess = web_session("sina")
    quotes = sess.quotes(["600519", "000001"])   # -> list[Quote]
    market = sess.all_market(node="hs_a")          # -> list[Quote]
    bars   = sess.klines("sh600519", period="day") # -> list[Bar]
    sess.close()

P4 拆分说明：本模块只保留组合类 ``WebQuoteSession``、生命周期（``__init__`` /
``close`` / 懒加载 ``_c``）与工厂函数；方法体按域纯搬移到 ``atst/sources/_session_*.py``
的域 Mixin（行情/K 线/板块在 :mod:`atst.sources._session_market`，
资金流与基本面在 :mod:`atst.sources._session_info`，百度与扩展源在
:mod:`atst.sources._session_baidu`，其余按域名）。公开 API 不变。

P2-4 域聚合（F4）：全部能力 Mixin 再按 Provider 域聚为 4 个域 Mixin——
``_MarketDataMixin``（行情 / K 线 / 板块 / 资金流 / 行业宏观 / 东财 datacenter
报表 / 东财 EFinance 股票与衍生品 / 百度扩展行情）、``_CorporateMixin``
（公司资料 / 财报 / 股东治理 / 公司事件工具）、``_ResearchMixin``（ESG /
筹码 / 资讯 / 研报与宏观缺口补全）与 ``_FundMixin``（基金排行 / 经理 / 公司 /
移动端扩展）；``WebQuoteSession`` 只继承 ``_SessionBase`` 与这 4 个域类。
公开方法面（``dir()``，184 项）由 ``tests/sources/test_p2_4_session_domains.py``
钉住，不随组合层调整漂移。
"""

from __future__ import annotations

from typing import Any

from ..errors import CompatibilityError
from ._session_astock import AstockToolkitMixin
from ._session_baidu import BaiduSessionMixin
from ._session_boards import BoardSessionMixin
from ._session_datacenter import DatacenterSessionMixin
from ._session_efinance import (
    DerivativeSessionMixin,
    FundMobSessionMixin,
    StockEfinanceMixin,
)
from ._session_esg_chip import EsgChipSessionMixin
from ._session_fund_v2 import (
    FundCompanySessionMixin,
    FundManagerSessionMixin,
    FundRankSessionMixin,
)
from ._session_fundamental import FundamentalSessionMixin
from ._session_holders import HoldersSessionMixin
from ._session_info import CorporateSessionMixin, FundFlowSessionMixin
from ._session_market import (
    INDEX_SYMBOLS,
    KLINES_PERIOD_ALIASES,
    KlineSessionMixin,
    QuoteSessionMixin,
)
from ._session_news import NewsSessionMixin
from ._session_research import ResearchSessionMixin
from ._session_sector_macro import SectorMacroSessionMixin
from .sources import BOC, EASTMONEY, HK, JSL, KLINE, SINA, TENCENT

__all__ = [
    "web_session",
    "WebQuoteSession",
    "SOURCE_ALIASES",
    "KLINES_PERIOD_ALIASES",
    "INDEX_SYMBOLS",
]

#: 用户友好的源别名 → atst 内部源名。
SOURCE_ALIASES: dict[str, str] = {
    "sina": SINA,
    "tencent": TENCENT,
    "qq": TENCENT,
    "jsl": JSL,
    "hkquote": HK,
    "hk": HK,
    "daykline": KLINE,
    "kline": KLINE,
    "boc": BOC,
    "eastmoney": EASTMONEY,
    "em": EASTMONEY,
}


def web_session(source: str = SINA, **kwargs: Any) -> WebQuoteSession:
    """创建 HTTP Web 行情会话（原生命名工厂）。

    Example
    -------
    >>> from atst.sources.session import web_session
    >>> sess = web_session("sina")
    >>> sess.quotes(["600519"])
    """
    return WebQuoteSession(source, **kwargs)


class _SessionBase:
    """会话生命周期基类：源校验 / 懒加载客户端 / 释放 / 符号归一。

    供 :class:`WebQuoteSession` 的各域 Mixin 共享（多个 Mixin 依赖
    ``self._c`` / ``self.source_name`` / ``self._kwargs`` / ``self._normalize``）。
    """

    def __init__(self, source: str = SINA, **kwargs: Any) -> None:
        name = SOURCE_ALIASES.get(str(source).lower())
        if name is None:
            raise CompatibilityError(
                f"未知行情源: {source!r}；可选: {sorted(SOURCE_ALIASES)}",
                context={"source": source},
            )
        self.source_name = name
        self.alias = source
        self._kwargs = kwargs
        self._client: Any = None

    # -- 懒加载客户端 ------------------------------------------------------- #
    @property
    def _c(self):
        if self._client is None:
            from . import create_source

            self._client = create_source(self.source_name, **self._kwargs)
        return self._client

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None

    def __enter__(self) -> _SessionBase:
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()


# --------------------------------------------------------------------------- #
# 域 Mixin（P2-4a/F4）：按 Provider 域聚合能力 Mixin，WebQuoteSession 只组合域类。
# 每个域内的成员保持拆分前的相对顺序；各能力 Mixin 之间无公开方法名冲突
# （tests/sources/test_p2_4_session_domains.py 断言），故域聚合不改变属性解析。
# --------------------------------------------------------------------------- #
class _MarketDataMixin(
    QuoteSessionMixin,
    KlineSessionMixin,
    BoardSessionMixin,
    DatacenterSessionMixin,
    FundFlowSessionMixin,
    SectorMacroSessionMixin,
    BaiduSessionMixin,
    StockEfinanceMixin,
    DerivativeSessionMixin,
):
    """行情域：实时行情 / K 线 / 板块排行 / 资金流 / 行业与宏观 / datacenter 报表。

    聚 :mod:`atst.sources._session_market`、:mod:`atst.sources._session_boards`、
    :mod:`atst.sources._session_datacenter`（东财数据中心具名报表：龙虎榜席位 /
    融资融券 / 宏观 / 热点，以行情衍生数据为主）、:mod:`atst.sources._session_info`
    的资金流半边、:mod:`atst.sources._session_sector_macro`、
    :mod:`atst.sources._session_baidu`（百度行情与查询扩展）以及
    :mod:`atst.sources._session_efinance` 的股票 / 衍生品两半。
    """


class _CorporateMixin(
    CorporateSessionMixin,
    FundamentalSessionMixin,
    HoldersSessionMixin,
    AstockToolkitMixin,
):
    """公司域：F10 资料 / 公告 / 三大报表与评级 / 股东治理 / 公司事件工具。

    聚 :mod:`atst.sources._session_info` 的公司半边、
    :mod:`atst.sources._session_fundamental`（三大报表 / 治理 / 评级预测）、
    :mod:`atst.sources._session_holders`（北向持股 / 十大股东 / 解禁 / 业绩预告）
    与 :mod:`atst.sources._session_astock`（公告 / 分红 / 配股 / 财报摘要 /
    股东变动 / 估值等公司事件衍生）。
    """


class _ResearchMixin(EsgChipSessionMixin, NewsSessionMixin, ResearchSessionMixin):
    """研究域：ESG 评级 / 筹码分布 / 资讯 / 研报与宏观缺口补全。

    聚 :mod:`atst.sources._session_esg_chip`、:mod:`atst.sources._session_news`
    与 :mod:`atst.sources._session_research`（交易日历 / 宏量指标 / ST / 质押 /
    互动易 / 估值史等对标缺口）。
    """


class _FundMixin(
    FundMobSessionMixin,
    FundRankSessionMixin,
    FundManagerSessionMixin,
    FundCompanySessionMixin,
):
    """基金域：基金排行 / 快照 / 经理 / 公司 / 移动端扩展。

    聚 :mod:`atst.sources._session_fund_v2` 全部三支与
    :mod:`atst.sources._session_efinance` 的基金半边（天天基金移动端后端）。
    """


class WebQuoteSession(
    _SessionBase,
    _MarketDataMixin,
    _CorporateMixin,
    _ResearchMixin,
    _FundMixin,
):
    """HTTP Web 行情高层会话。

    收口实时行情、全市场快照、K 线、指数与汇率的便捷调用，底层复用
    :mod:`atst.sources` 的零依赖适配器，返回 atst 自有数据模型。

    这里没有"降级逻辑"可复用：本会话按精确 Provider 取数，源不可用即失败
    （见模块头）。多源切换只存在于内核的显式 ``FallbackPolicy``（§33.6），
    不在这一层。

    P4 起方法体按域拆分到各能力 Mixin（纯搬移）；P2-4 起再按 Provider 域
    聚合为 4 个域 Mixin 后组合（见模块头），公开方法面不变。
    """
