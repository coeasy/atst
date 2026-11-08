# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""WebQuoteSession 域 Mixin（行情 / K 线）——v2/W7 按领域拆分。

本模块承载 :class:`atst.sources.session.WebQuoteSession` 的行情与 K 线两域：

* :class:`QuoteSessionMixin`：实时行情 / 港美股 / 全市场 / 大盘统计 / 指数 / 汇率
* :class:`KlineSessionMixin`：K 线 / 分时 / 历史行情 / 逐笔成交

板块/排行见 :mod:`atst.sources._session_boards`，东财 datacenter 具名报表见
:mod:`atst.sources._session_datacenter`。组合与 ``__init__`` / ``close`` 见
:mod:`atst.sources.session`。模块级共享常量（``SOURCE_ALIASES`` 除外）与进程级
HTTP 连接池助手收口于此，供各域 Mixin 与 ``session`` 复用。
"""

from __future__ import annotations

import atexit
import contextlib
import threading
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

from ..domain.models import Bar, MinutePoint, Quote
from ..domain.period import PERIOD_ALIASES, normalize_bar_period
from ..errors import CompatibilityError
from .base import normalize_symbol
from .sources import BOC, SINA, TENCENT

__all__ = [
    "QuoteSessionMixin",
    "KlineSessionMixin",
    "KLINES_PERIOD_ALIASES",
    "INDEX_SYMBOLS",
    "shared_http",
    "close_shared_http",
]

#: 本面（腾讯 ifzq K 线）能服务的**规范**周期。这里只登记"服务得起哪几档"这一件真差异，
#: 别名一律取自 :mod:`atst.domain.period` 那份唯一词表——``tick``/``season``/``year``
#: 上游没有对应参数，所以它们不在名单里，写它们仍按"未知周期"显式报错。
_KLINE_SERVABLE: tuple[str, ...] = (
    "1min",
    "5min",
    "15min",
    "30min",
    "60min",
    "day",
    "week",
    "month",
)

#: K 线周期别名 → atst period（KlineSource）：由域内词表派生，不再手抄别名。
KLINES_PERIOD_ALIASES: dict[str, str] = {
    **{alias: canon for alias, canon in PERIOD_ALIASES.items() if canon in _KLINE_SERVABLE},
    **{canon: canon for canon in _KLINE_SERVABLE},
}

#: 常用大盘指数代码（用于 :meth:`WebQuoteSession.index`）。
INDEX_SYMBOLS: dict[str, str] = {
    "上证指数": "sh000001",
    "深证成指": "sz399001",
    "创业板指": "sz399006",
    "沪深300": "sh000300",
}

#: B6：进程级共享 HTTP 连接池（惰性创建）——门面各方法按调用新建的
#: 轻量源实例统一注入该 client，消除「每次调用新建 + 关闭」的 TCP 开销。
#: client 归进程所有：源实例 ``close()`` 对注入 client 不生效（见
#: ``BaseWebSource._owns_client``），连接随 keep-alive 池复用。
_SHARED_HTTP: list[Any] = []
_SHARED_HTTP_LOCK = threading.Lock()


def shared_http():
    """返回进程级共享 :class:`HttpClient`（线程安全惰性单例）。

    与 :func:`atst.sources._base_http.shared_bucket` 同法：整段"取或建"在锁内完成。
    早先的无锁 check-then-append 会让并发首建各自造一个 client，其中一个连同
    其 keep-alive 连接池一起泄漏（进程级对象，再没人引用也永不关闭）。

    这份 client 归进程所有，所以它的释放点也必须挂在进程上：首建时登记
    :func:`atexit`，一次性进程（``atst web ...`` 那批命令、脚本）退出时
    keep-alive 连接池被确定地关掉，而不是等解释器把整个模块一起丢弃
    （第 26 轮 F-103）。长期进程里想提前收尾就直接调 :func:`close_shared_http`。
    """
    with _SHARED_HTTP_LOCK:
        if not _SHARED_HTTP:
            from .base import build_client

            _SHARED_HTTP.append(build_client())
            atexit.register(close_shared_http)
        return _SHARED_HTTP[0]


def close_shared_http() -> bool:
    """关掉 :func:`shared_http` 那份进程级 client（幂等；由 atexit 兑现）。

    Returns
    -------
    ``True`` 表示确有一份被关掉；``False`` 表示还没人建过、或已经关过。
    """
    with _SHARED_HTTP_LOCK:
        client = _SHARED_HTTP.pop(0) if _SHARED_HTTP else None
    if client is None:
        return False
    with contextlib.suppress(Exception):
        client.close()
    return True


# 旧名别名：会话组合层内部沿用原私有名。
_shared_http = shared_http


class QuoteSessionMixin:
    """实时行情 / 港美股 / 全市场快照 / 大盘统计 / 指数 / 汇率。"""

    # These members are provided by ``session._SessionBase`` at runtime.  Keep
    # the composition contract explicit for type checkers without introducing
    # a second runtime inheritance hierarchy between mixins.
    if TYPE_CHECKING:
        source_name: str
        _kwargs: dict[str, Any]
        _c: Any

    # -- 实时行情 ----------------------------------------------------------- #
    def quotes(self, codes: Sequence[str]) -> list[Quote]:
        """查询指定标的的实时行情，返回 ``list[Quote]``。

        Parameters
        ----------
        codes:
            6 位代码（``000001``）或带市场前缀（``sh600519``）。入参一律经
            :func:`~atst.sources.base.normalize_symbol` 归一（单一事实源），
            不存在"关掉前缀归一"的开关。
        """
        if isinstance(codes, str):
            codes = [codes]
        symbols = [self._normalize(c) for c in codes]
        if self.source_name == BOC:
            raise CompatibilityError(
                "中行汇率源不提供 quotes()，请改用 rates()", context={"source": BOC}
            )
        try:
            return self._c.fetch(symbols)
        except Exception as exc:  # noqa: BLE001
            from ..errors import TdxError

            if isinstance(exc, TdxError):
                raise
            raise CompatibilityError(
                f"底层源调用失败: {exc}", context={"source": self.source_name}, cause=exc
            ) from exc

    # -- 港股 / 美股 -------------------------------------------------------- #
    @staticmethod
    def hk_quotes(codes: Sequence[str], *, provider: str = "tencent") -> list[Quote]:
        """港股实时行情。

        Parameters
        ----------
        codes:
            港股代码，如 ``["00700"]`` / ``["hk00700"]`` / ``["腾讯"]``（联想结果）。
        provider:
            ``"tencent"``（默认，qt.gtimg.cn）或 ``"sina"``（hq.sinajs.cn）。

        返回价格 / 成交额以原生币种港元(HKD)计，``extra["currency"]`` 标记；
        成交量单位为「股」（已与全局契约对齐）。
        """
        from .sina.adapters import SinaHkSource
        from .tencent.adapters import HkSource

        src_cls = SinaHkSource if provider == "sina" else HkSource
        src = src_cls(client=_shared_http())
        try:
            seq = [codes] if isinstance(codes, str) else list(codes)
            return src.fetch(seq)
        finally:
            src.close()

    @staticmethod
    def us_quotes(codes: Sequence[str]) -> list[Quote]:
        """美股实时行情（腾讯 qt.gtimg.cn q=usXXXXX）。

        Parameters
        ----------
        codes:
            美股代码，如 ``["AAPL"]`` / ``["usAAPL"]``。

        返回价格 / 成交额以原生币种美元(USD)计，``extra["currency"]`` 标记；
        成交量单位为「股」（已与全局契约对齐）。
        """
        from .tencent.adapters import UsSource

        src = UsSource(client=_shared_http())
        try:
            seq = [codes] if isinstance(codes, str) else list(codes)
            return src.fetch(seq)
        finally:
            src.close()

    # -- 全市场快照 --------------------------------------------------------- #
    def all_market(
        self,
        *,
        node: str = "hs_a",
        page_size: int = 80,
        max_pages: int | None = None,
    ) -> list[Quote]:
        """分页拉取全市场行情摘要（新浪 / 腾讯）。

        新浪 ``Market_Center.getHQNodeData`` 接口直接返回行情摘要；腾讯走
        ``getBoardRankList`` 枚举代码 + ``qt.gtimg.cn`` 批量行情（U5），
        无需先取代码表再逐批查询。

        Parameters
        ----------
        node:
            新浪市场节点：``hs_a`` 沪深 A 股 / ``hs_b`` B 股 / ``cyb`` 创业板
            / ``sh_a`` 沪 A / ``sz_a`` 深 A 等；腾讯支持 ``hs_a`` / ``cyb``。
            **港股（``hk``）/ 美股（``us``）整市场枚举未验证，显式拒绝**——
            需要港股/美股行情请用 :meth:`hk_quotes` / :meth:`us_quotes`
            （按代码批量）或 :meth:`klines` / :meth:`minute_klines`（K 线）。
        page_size:
            每页条数（新浪上限 100——超限由适配器钳制并告警；腾讯上限 200）。
        max_pages:
            页数上限；``None`` **不是**"拉到底"——底层适配器把它取成
            :data:`~atst.sources.tencent.adapters.DEFAULT_MAX_PAGES`（100 页）硬上限，
            与显式传值同样受"防失控"钳制。要更大范围请显式给更大的 ``max_pages``。
        """
        if node.lower() in ("hk", "us", "hk_main", "us_main"):
            raise ValueError(
                f"all_market: 港股/美股整市场枚举未实装（N7 标注），node={node!r}；"
                "请用 hk_quotes()/us_quotes() 按代码批量取行情，或 klines()/minute_klines() 取 K 线"
            )
        if self.source_name in (SINA, TENCENT):
            return self._c.fetch_all(node=node, page_size=page_size, max_pages=max_pages)
        raise CompatibilityError(
            f"全市场快照 all_market() 仅新浪/腾讯源支持；当前源 {self.source_name!r}，"
            f"请改用 web_session('sina') 或 web_session('tencent').all_market()",
            context={"source": self.source_name},
        )

    # -- 外盘 / 大盘统计 ----------------------------------------------------- #
    @staticmethod
    def globals(codes: Sequence[str] | None = None) -> list[Quote]:
        """外盘行情（贵金属 / 能源 / 有色 / 农产品）。

        Parameters
        ----------
        codes:
            ``["CL", "GC"]``；``None`` 表示全部 13 个可用品种。
            可用代码见 :data:`~atst.sources.global_market.GLOBAL_CODES`。
        """
        from .global_market import TencentGlobalSource

        src = TencentGlobalSource(client=_shared_http())
        try:
            return src.fetch(list(codes) if codes else [])
        finally:
            src.close()

    @staticmethod
    def market_stat(codes: Sequence[str] | None = None) -> list[Quote]:
        """大盘统计（点位 + 全市场成交量/额 + 总市值）。

        ``codes`` 形如 ``["sh000001", "sz399001"]``；``None`` 表示主要指数。
        """
        from .global_market import TencentMarketStatSource

        src = TencentMarketStatSource(client=_shared_http())
        try:
            return src.fetch(list(codes) if codes else [])
        finally:
            src.close()

    # -- 大盘指数 ----------------------------------------------------------- #
    def index(self) -> list[Quote]:
        """常用大盘指数行情（上证 / 深成 / 创业板 / 沪深 300）。"""
        return self.quotes(list(INDEX_SYMBOLS.values()))

    # -- 外汇牌价 ----------------------------------------------------------- #
    def rates(self) -> list[dict[str, Any]]:
        """中国银行外汇牌价。"""
        from .boc.adapters import BocSource

        src = BocSource(**self._kwargs, client=_shared_http())
        try:
            return src.fetch_rates()
        finally:
            src.close()  # P1 #14: 释放连接池

    # -- 辅助 --------------------------------------------------------------- #
    @staticmethod
    def _normalize(code: str) -> str:
        # 委托统一符号引擎（单一事实源），不再手动拼接市场前缀，
        # 避免与 domain.symbol 推断逻辑漂移（P1 #9）
        try:
            return normalize_symbol(code)
        except Exception:  # noqa: BLE001
            return str(code).strip().lower()


class KlineSessionMixin:
    """K 线 / 当日分时 / 历史行情 / 逐笔成交。"""

    # -- K 线 --------------------------------------------------------------- #
    def klines(
        self,
        symbol: str,
        period: str = "day",
        *,
        count: int = 320,
        adjust: str = "qfq",
    ) -> list[Bar]:
        """日 / 周 / 月 / 分钟 K 线，返回 ``list[Bar]``。

        分钟周期（``m1``/``m5``/``m15``/``m30``/``m60``）：A 股走腾讯
        mkline；港股 / 美股走东财 push2his（腾讯 mkline 不支持 hk/us）。
        日线及以上走腾讯 fqkline（hk/us 亦支持）。

        Parameters
        ----------
        symbol:
            6 位代码（``600519``）或带前缀（``sh600519`` / ``hk00700``
            / ``usAAPL``）。
        period:
            ``day`` / ``week`` / ``month`` / ``m1`` / ``m5`` / ``m15``
            / ``m30`` / ``m60``（见 :data:`KLINES_PERIOD_ALIASES`）。
        """
        p = KLINES_PERIOD_ALIASES.get(str(period).lower())
        if p is None:
            # P1 #13: 未知周期显式报错，不再静默回退 day（周/月请求被偷换成
            # 日线属于「帧合法但内容错误」级缺陷）
            raise ValueError(f"未知 K 线周期 {period!r}；可选: {sorted(KLINES_PERIOD_ALIASES)}")
        if p.endswith("min"):
            from ..domain.symbol import parse_symbol

            mkt = parse_symbol(symbol).market
            # src 在三分支被赋不同源类型——显式 Any 化统一推断
            src: Any
            if mkt in ("hk", "us"):
                from .eastmoney.adapters import EastmoneyHistoryKlineSource

                src = EastmoneyHistoryKlineSource(client=_shared_http())
                try:
                    # 东财 push2his 是 hk/us 分钟 K 线唯一数据源：
                    # 港股 secid=116.x 确定性映射；美股 105/106/107 逐个探测。
                    return src.fetch_bars(symbol, period=p, count=count, adjust=adjust)
                finally:
                    src.close()
            from .tencent.adapters import MinuteKlineSource

            src = MinuteKlineSource(client=_shared_http())
            try:
                # A 股分钟 K 线走腾讯 mkline（量「手」×100 到股）。
                return src.fetch_bars(symbol, period=p, count=count)
            finally:
                src.close()

        # 方法内导入：保留测试对 atst.sources.tencent.adapters.KlineSource 的注入 seam
        from .tencent.adapters import KlineSource as _KlineSource

        src = _KlineSource(client=_shared_http())
        try:
            return src.fetch_bars(symbol, period=p, count=count, adjust=adjust)
        finally:
            src.close()

    # -- 当日分时 ----------------------------------------------------------- #
    def minute(self, symbol: str) -> list[MinutePoint]:
        """当日 1 分钟分时（价格 / 分钟增量成交量 / 增量成交额）。"""
        from .tencent.adapters import MinuteSource

        src = MinuteSource(client=_shared_http())
        try:
            return src.fetch_minute(symbol)
        finally:
            src.close()

    # -- 历史行情 ----------------------------------------------------------- #
    @staticmethod
    def history(
        symbol: str,
        *,
        period: str = "day",
        count: int = 320,
        adjust: str = "qfq",
        source: str = "sina",
    ) -> list[Bar]:
        """历史 K 线（新浪 / 东财双源；东财含成交额与复权选项）。

        周期先经 :func:`~atst.domain.period.normalize_bar_period` 规范，再交给
        所选源自己的周期表：新浪最细到 ``5min``（没有 1 分钟档），另含 ``120min`` /
        ``1200min``；东财 ``1min``–``day``。请求该源不服务的档位是**显式报错**，
        不会被换成另一个周期。

        Parameters
        ----------
        source:
            ``"sina"``（``5min``/``15min``/``30min``/``60min``/``120min``/``day``/
            ``1200min``）或 ``"eastmoney"``（``1min``–``day``，支持 adjust="" / "qfq" / "hfq"）。
        """
        from .eastmoney.adapters import EastmoneyHistoryKlineSource
        from .sina.adapters import SinaHistoryKlineSource

        period = normalize_bar_period(period)
        src: Any
        # 深审 M9：新浪只提供原始价——复权请求自动路由到东财源
        if source == "eastmoney" or adjust not in ("", None):
            src = EastmoneyHistoryKlineSource(client=_shared_http())
            try:
                return src.fetch_bars(symbol, period=period, count=count, adjust=adjust)
            finally:
                src.close()
        src = SinaHistoryKlineSource(client=_shared_http())
        try:
            return src.fetch_bars(symbol, period=period, count=count, adjust=adjust)
        finally:
            src.close()

    # -- 逐笔成交 ----------------------------------------------------------- #
    @staticmethod
    def ticks(symbol: str, *, max_pages: int = 1) -> list[Any]:
        """当日逐笔成交明细（腾讯，每页 70 条）。

        Returns
        -------
        ``list[Tick]``：``volume`` 为股、``price`` 为元、``buyorsell``
        为 0=买 / 1=卖 / 2=中性。
        """
        from .ticks import TencentTickSource

        src = TencentTickSource(client=_shared_http())
        try:
            return src.fetch_ticks(symbol, max_pages=max_pages)
        finally:
            src.close()

    @staticmethod
    def intraday(symbol: str) -> list[MinutePoint]:
        """当日 1 分钟分时成交（东财，含均价；分钟数多于腾讯分时）。"""
        from .ticks import EastmoneyTrendsSource

        src = EastmoneyTrendsSource(client=_shared_http())
        try:
            return src.fetch_minutes(symbol)
        finally:
            src.close()
