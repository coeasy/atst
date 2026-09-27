# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""Web Provider 会话组合层（§33）。

在 :mod:`atst.web` 注册表（``_ADAPTER_SPECS``，惰性 import 的零依赖适配器）之上
提供一套**原生命名**的便捷会话，
使「取实时行情 / 全市场快照 / K 线 / 指数 / 汇率」等操作统一收口，返回 atst
自有 :class:`~atst.domain.models.Quote` / :class:`~atst.domain.models.Bar`
模型（已归一化到「股 / 元」全局契约——外盘行情例外，其 ``volume`` 是合约手数，
单位由 ``extra["volume_unit"]`` 明示，见 :meth:`~atst.web._session_market.QuoteSessionMixin.globals`）。

本模块不是路由层：它按精确 Provider 取数，**不会**在源不可用时自动换到别的源，
也不构成 v16 已删除的 ``UnifiedQuoteAPI`` 那类跨源聚合门面。

设计原则
--------
* **原生命名**：不沿用任何第三方库的字段或方法命名，所有输出走 atst 数据模型。
* **零反向语义**：A 股 / 港美股口径下 ``volume`` 恒为成交量（股）、``amount`` 恒为
  成交额（元），不再出现「turnover/volume 互换」这类反直觉约定。外盘行情是**明示的
  例外**：``volume`` 为合约手数并带 ``extra["volume_unit"] == "lot"``（见
  :mod:`atst.web.global_market` 的"单位归一化"节），按股票股数用会差 100 倍。
* **懒加载**：底层源客户端按需创建，``close()`` 释放。

Quick start::

    from atst.web.session import web_session
    sess = web_session("sina")
    quotes = sess.quotes(["600519", "000001"])   # -> list[Quote]
    market = sess.all_market(node="hs_a")          # -> list[Quote]
    bars   = sess.klines("sh600519", period="day") # -> list[Bar]
    sess.close()

P4 拆分说明：本模块只保留组合类 ``WebQuoteSession``、生命周期（``__init__`` /
``close`` / 懒加载 ``_c``）与工厂函数；方法体按域纯搬移到 ``atst/web/_session_*.py``
的域 Mixin（行情/K 线/板块在 :mod:`atst.web._session_market`，
资金流与基本面在 :mod:`atst.web._session_info`，百度与扩展源在
:mod:`atst.web._session_baidu`，其余按域名）。公开 API 不变。
"""

from __future__ import annotations

from typing import Any

from ..errors import CompatibilityError
from ._session_astock import AstockToolkitMixin
from ._session_baidu import BaiduSessionMixin
from ._session_efinance import (
    DerivativeSessionMixin,
    FundMobSessionMixin,
    StockEfinanceMixin,
)
from ._session_fund_v2 import (
    FundCompanySessionMixin,
    FundManagerSessionMixin,
    FundRankSessionMixin,
)
from ._session_fundamental import FundamentalSessionMixin
from ._session_info import CorporateSessionMixin, FundFlowSessionMixin
from ._session_market import (
    INDEX_SYMBOLS,
    KLINES_PERIOD_ALIASES,
    BoardSessionMixin,
    KlineSessionMixin,
    QuoteSessionMixin,
)
from ._session_news import NewsSessionMixin
from ._session_p1 import P1SessionMixin
from ._session_p2 import P2SessionMixin
from ._session_p3 import P3SessionMixin
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
    >>> from atst.web.session import web_session
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


class WebQuoteSession(
    _SessionBase,
    QuoteSessionMixin,
    KlineSessionMixin,
    BoardSessionMixin,
    FundFlowSessionMixin,
    CorporateSessionMixin,
    FundamentalSessionMixin,
    P1SessionMixin,
    P2SessionMixin,
    P3SessionMixin,
    BaiduSessionMixin,
    StockEfinanceMixin,
    FundMobSessionMixin,
    DerivativeSessionMixin,
    AstockToolkitMixin,
    NewsSessionMixin,
    FundRankSessionMixin,
    FundManagerSessionMixin,
    FundCompanySessionMixin,
):
    """HTTP Web 行情高层会话。

    收口实时行情、全市场快照、K 线、指数与汇率的便捷调用，底层复用
    :mod:`atst.web` 的零依赖适配器，返回 atst 自有数据模型。

    这里没有"降级逻辑"可复用：本会话按精确 Provider 取数，源不可用即失败
    （见模块头）。多源切换只存在于内核的显式 ``FallbackPolicy``（§33.6），
    不在这一层。

    P4 起方法体按域拆分到各 Mixin（纯搬移），本类负责组合与生命周期。
    """
