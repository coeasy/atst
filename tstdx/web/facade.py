# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""HTTP Web 行情高层门面（§33）。

在 :mod:`tstdx.web` 的 7 个零依赖适配器之上提供一套**原生命名**的便捷会话，
使「取实时行情 / 全市场快照 / K 线 / 指数 / 汇率」等操作统一收口，返回 tstdx
自有 :class:`~tstdx.domain.models.Quote` / :class:`~tstdx.domain.models.Bar`
模型（已归一化到「股 / 元」全局契约）。

设计原则
--------
* **原生命名**：不沿用任何第三方库的字段或方法命名，所有输出走 tstdx 数据模型。
* **零反向语义**：``volume`` 恒为成交量（股）、``amount`` 恒为成交额（元），
  不再出现「turnover/volume 互换」这类反直觉约定。
* **懒加载**：底层源客户端按需创建，``close()`` 释放。

Quick start::

    from tstdx.web.facade import web_session
    sess = web_session("sina")
    quotes = sess.quotes(["600519", "000001"])   # -> list[Quote]
    market = sess.all_market(node="hs_a")          # -> list[Quote]
    bars   = sess.klines("sh600519", period="day") # -> list[Bar]
    sess.close()

P4 拆分说明：方法体按域纯搬移到 :mod:`tstdx.web._facade_mixin_market`
（行情 / K 线 / 板块）与 :mod:`tstdx.web._facade_mixin_info`
（资金流 / 基本面 / 百度与扩展源）；本模块保留组合类 ``WebQuoteSession``、
生命周期（``__init__`` / ``close`` / 懒加载 ``_c``）与工厂函数，公开 API 不变。
"""

from __future__ import annotations

from typing import Any

from ..errors import CompatibilityError
from ._facade_mixin_baidu import BaiduSessionMixin
from ._facade_mixin_astock import AstockToolkitMixin
from ._facade_mixin_efinance import (
    DerivativeSessionMixin,
    FundMobSessionMixin,
    StockEfinanceMixin,
)
from ._facade_mixin_news import NewsSessionMixin
from ._facade_mixin_fund_v2 import (
    FundCompanySessionMixin,
    FundManagerSessionMixin,
    FundRankSessionMixin,
)
from ._facade_mixin_fundamental import FundamentalSessionMixin
from ._facade_mixin_info import CorporateSessionMixin, FundFlowSessionMixin
from ._facade_mixin_p1 import P1SessionMixin
from ._facade_mixin_p2 import P2SessionMixin
from ._facade_mixin_p3 import P3SessionMixin
from ._facade_mixin_market import (
    INDEX_SYMBOLS,
    KLINES_PERIOD_ALIASES,
    BoardSessionMixin,
    KlineSessionMixin,
    QuoteSessionMixin,
)
from .sources import BOC, EASTMONEY, HK, JSL, KLINE, SINA, TENCENT

__all__ = [
    "web_session",
    "WebQuoteSession",
    "SOURCE_ALIASES",
    "KLINES_PERIOD_ALIASES",
    "INDEX_SYMBOLS",
]

#: 用户友好的源别名 → tstdx 内部源名。
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
    >>> from tstdx.web.facade import web_session
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
    :mod:`tstdx.web` 的零依赖适配器与降级逻辑，返回 tstdx 自有数据模型。

    P4 起方法体按域拆分到各 Mixin（纯搬移），本类负责组合与生命周期。
    """
