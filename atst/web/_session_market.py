# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""WebQuoteSession 域 Mixin（行情 / K 线 / 板块）——P4 纯搬移拆分。

本模块只承载 :class:`atst.web.session.WebQuoteSession` 的方法**纯搬移**
（方法体逐字不变），按域拆为三个 Mixin：

* :class:`QuoteSessionMixin`：实时行情 / 港美股 / 全市场 / 大盘统计 / 指数 / 汇率
* :class:`KlineSessionMixin`：K 线 / 分时 / 历史行情 / 逐笔成交
* :class:`BoardSessionMixin`：板块 / 排行 / 人气榜

组合与 ``__init__`` / ``close`` 见 :mod:`atst.web.session`。
模块级共享常量（``SOURCE_ALIASES`` 除外）与进程级 HTTP 连接池助手也
收口于此，由 :mod:`atst.web.session` 再导出以保持公开 API 不变。
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
    "BoardSessionMixin",
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

    与 :func:`atst.web._base_http.shared_bucket` 同法：整段"取或建"在锁内完成。
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
            :func:`~atst.web.base.normalize_symbol` 归一（单一事实源），
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
            每页条数（新浪上限 100；腾讯上限 200）。
        max_pages:
            页数上限；``None`` **不是**"拉到底"——底层适配器把它取成
            :data:`~atst.web.tencent.adapters.DEFAULT_MAX_PAGES`（100 页）硬上限，
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
            可用代码见 :data:`~atst.web.global_market.GLOBAL_CODES`。
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

        # 方法内导入：保留测试对 atst.web.tencent.adapters.KlineSource 的注入 seam
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


class BoardSessionMixin:
    """板块 / 排行 / 人气榜。"""

    # -- 板块 --------------------------------------------------------------- #
    @staticmethod
    def industry_boards() -> list[dict[str, Any]]:
        """新浪行业板块列表（49 行业，含领涨股与板块成交量/额）。"""
        from .boards import SinaIndustryBoardSource

        src = SinaIndustryBoardSource(client=_shared_http())
        try:
            return src.fetch_boards()
        finally:
            src.close()

    @staticmethod
    def board_list(board: str = "concept") -> list[dict[str, Any]]:
        """新浪板块列表（``concept`` 概念约 175 个 / ``region`` 地域 31 个
        / ``industry`` 新版行业 84 个，含领涨股与板块量额统计）。

        与 :meth:`industry_boards`（旧口径 49 行业）、:meth:`board_rank`
        （腾讯排行）、:meth:`em_boards`（东财）互补。

        Returns
        -------
        ``[{"code": "gn_hwqc", "name": "华为汽车", "count": 97,
        "pct_change": -1.02, "volume": 1946565402, "amount": 34277211962,
        "leader": "sh605068", "leader_name": "明新旭腾"}, ...]``
        ``code`` 可直接用于 :meth:`board_members` 拉成分。
        """
        from .boards import SinaBoardListSource

        src = SinaBoardListSource(client=_shared_http())
        try:
            return src.fetch_boards(board)
        finally:
            src.close()

    @staticmethod
    def board_members(
        node: str, *, page_size: int = 100, max_pages: int | None = None
    ) -> list[Quote]:
        """新浪板块成分行情（node 来自 industry_boards / board_list 的 code 字段）。"""
        from .boards import SinaBoardMemberSource

        src = SinaBoardMemberSource(client=_shared_http())
        try:
            return src.fetch_members(node, page_size=page_size, max_pages=max_pages)
        finally:
            src.close()

    @staticmethod
    def board_rank(
        board: str = "industry", *, limit: int = 20, page: int = 1
    ) -> list[dict[str, Any]]:
        """腾讯板块排行（industry/concept/region，含领涨股与 5/20 日涨幅）。"""
        from .boards import TencentBoardRankSource

        src = TencentBoardRankSource(client=_shared_http())
        try:
            return src.fetch_boards(board, limit=limit, page=page)
        finally:
            src.close()

    @staticmethod
    def em_boards(
        board: str = "industry", *, limit: int = 100, page: int = 1
    ) -> list[dict[str, Any]]:
        """东财板块列表（industry/concept/region，代码形如 BK0475）。"""
        from .boards import EastmoneyBoardSource

        src = EastmoneyBoardSource(client=_shared_http())
        try:
            return src.fetch_boards(board, limit=limit, page=page)
        finally:
            src.close()

    @staticmethod
    def em_board_members(
        board_code: str, *, limit: int = 100, page: int = 1
    ) -> list[dict[str, Any]]:
        """东财板块成分（board_code 形如 BK0475）。"""
        from .boards import EastmoneyBoardSource

        src = EastmoneyBoardSource(client=_shared_http())
        try:
            return src.fetch_members(board_code, limit=limit, page=page)
        finally:
            src.close()

    @staticmethod
    def stock_boards(symbol: str) -> list[dict[str, Any]]:
        """个股所属板块（行业/概念/地域全量，按板块涨跌幅降序）。

        与 :meth:`em_board_members`（板块 → 成分）方向互补：
        个股 → 所属板块。返回的 ``code``（BKxxxx）可直接回查
        :meth:`em_board_members` 拉成分，形成板块网络遍历。

        Returns
        -------
        ``[{"code": "BK1102", "name": "空气能热泵", "pct_change": 0.84,
        "market": 90}, ...]``
        """
        from .boards import EastmoneyBoardSource

        src = EastmoneyBoardSource(client=_shared_http())
        try:
            return src.fetch_stock_boards(symbol)
        finally:
            src.close()

    @staticmethod
    def board_attribution(
        symbol: str = "",
        *,
        board_code: str = "",
        board_name: str = "",
        size: int = 100,
        page: int = 1,
        sort_columns: str = "BOARD_RANK",
        sort_types: str = "1",
        all_pages: bool = False,
        max_pages: int = 64,
    ) -> list[dict[str, Any]]:
        """东财 F10 核心题材「所属板块」：个股 → 板块 / 板块 → 成分双向查询。

        数据源是东财 datacenter 静态报表 ``RPT_F10_CORETHEME_BOARDTYPE``，
        全量 31681 行 = 全市场个股 × 所属板块（行业 / 概念 / 地域 / 指数族），
        服务端过滤后返回，不必整表下拉。

        **这是当前快照，不是历史截面**——报表没有任何时间维度字段，
        取不到「某只股票在 2024 年 1 月 2 日属于哪些板块」。要历史只能每日
        落盘快照后本地反推（与 `is_st` / `name` 历史快照同一模式）。

        与既有三个板块能力的分工：

        * :meth:`stock_boards`（push2 ``slist`` ``spt=3``）——同向，但走行情
          快照接口，受 push2 IP 级软限流影响；本方法走静态报表，更稳。
        * :meth:`em_board_members`（push2 ``clist`` ``fs=b:BKxxxx``）——板块 →
          成分，带实时价；本方法同向但**不带价**，只给归属关系。
        * :meth:`board_members`（新浪 ``node``）——另一套板块口径。

        Parameters
        ----------
        symbol:
            6 位代码或带前缀。给了就按个股过滤，返回它归属的**全部**板块
            （600519 实测 28 个，300750 实测 36 个），按 ``BOARD_RANK``
            升序——rank 1 即主营板块。指数按空集处理：``sh000001`` 与
            ``sz000001`` 在按 6 位代码过滤的报表里撞码，不能把平安银行的
            板块归属安到上证指数头上。
        board_code:
            板块代码。接受两种形状：F10 数字码（``896``）或 BK 码
            （``BK0896``）——后者过滤 ``NEW_BOARD_CODE``。给了就按板块过滤，
            返回该板块的全部成分（「白酒」实测 41 只）。
        board_name:
            板块中文名（``白酒`` / ``茅指数``）。适合人用的入口，内部转成
            ``BOARD_NAME`` 过滤。与 ``board_code`` 同时给出时以 ``symbol``
            → ``board_code`` → ``board_name`` 的优先级生效，只留一道过滤。
        size, page:
            分页。服务端单页上限 500。
        sort_columns, sort_types:
            排序键与方向。缺省 ``BOARD_RANK`` 升序（个股 → 板块时 rank 1
            在最前；板块 → 成分时 rank 是该股在板块内的排名）。
        all_pages, max_pages:
            ``all_pages=True`` 时翻页拉全量（全市场 31681 行 = 64 页）。
            ``max_pages`` 是防御上限，耗尽仍满页时发
            ``WEB_EASTMONEY_PAGE_LIMIT`` 告警而不是静默截断。

        Returns
        -------
        ``list[dict]``，列名即报表字段名::

            [{"SECUCODE": "600519.SH", "SECURITY_CODE": "600519",
              "SECURITY_NAME_ABBR": "贵州茅台",
              "BOARD_CODE": "896", "BOARD_NAME": "白酒",
              "BOARD_RANK": 2, "IS_PRECISE": "0",
              "NEW_BOARD_CODE": "BK0896",
              "DERIVE_BOARD_CODE": "BI0896",
              "SELECTED_BOARD_REASON": null,
              "BOARD_YIELD": null, "BOARD_TYPE": null,
              "BOARD_LEVEL": None}, ...]

        Examples
        --------
        >>> from atst import Client
        >>> res = Client.call("board_attribution", symbol="sh600519")     # doctest: +SKIP
        >>> [r["BOARD_NAME"] for r in res.data[:3]]                       # doctest: +SKIP
        ['食品饮料', '白酒Ⅱ', '白酒Ⅲ']
        >>> res = Client.call("board_attribution", board_code="BK0896")   # doctest: +SKIP
        >>> len(res.data)                                                 # doctest: +SKIP
        41
        """
        from .corporate import EastmoneyDataCenterSource, report_code_for

        filters: list[str] = []
        if symbol:
            code = report_code_for(symbol)
            if code is None:
                #: 指数不在 F10 覆盖范围内（也没有所属板块这回事）。
                #: 这是事实陈述，不是容错——与 dividend_history 同一口径。
                return []
            filters.append(f'SECURITY_CODE="{code}"')
        elif board_code:
            raw = board_code.strip().upper()
            if raw.startswith("BK"):
                filters.append(f'NEW_BOARD_CODE="{raw}"')
            else:
                filters.append(f'BOARD_CODE="{raw}"')
        elif board_name:
            filters.append(f'BOARD_NAME="{board_name.strip()}"')

        src = EastmoneyDataCenterSource(report="board_attribution", client=_shared_http())
        try:
            return src.fetch_rows(
                filters=filters,
                sort_columns=sort_columns,
                sort_types=sort_types,
                page=page,
                size=size,
                all_pages=all_pages,
                max_pages=max_pages,
                allow_empty=True,
            )
        finally:
            src.close()

    # -- 2026-10-14 批量补齐的 datacenter 报表能力 -------------------------- #
    #
    # 这一组全是「同一张 datacenter 静态报表的具名入口」：报表名登记在
    # :data:`atst.web.corporate.VALID_REPORTS`，这里只负责把调用方的语义参数
    # 翻译成服务端 filter，并写清每张表的口径与坑。共用一个私有取数器，避免
    # 17 份重复的 try/finally。

    @staticmethod
    def _dc_rows(
        report: str,
        filters: Sequence[str],
        *,
        sort_columns: str = "",
        sort_types: str = "-1",
        page: int = 1,
        size: int = 100,
        all_pages: bool = False,
        max_pages: int = 50,
        allow_empty: bool = True,
    ) -> list[dict[str, Any]]:
        """datacenter 报表取数的唯一出口（这一组能力共用）。"""
        from .corporate import EastmoneyDataCenterSource

        src = EastmoneyDataCenterSource(report=report, client=_shared_http())
        try:
            return src.fetch_rows(
                filters=list(filters),
                sort_columns=sort_columns,
                sort_types=sort_types,
                page=page,
                size=size,
                all_pages=all_pages,
                max_pages=max_pages,
                allow_empty=allow_empty,
            )
        finally:
            src.close()

    @staticmethod
    def _dc_symbol_filter(symbol: str) -> str | None:
        """个股 → 6 位代码；指数返回 ``None``（撞码守卫，见 ``report_code_for``）。

        返回 ``None`` 是**信号而不是错误**：调用方据此直接返回空集，一次 HTTP
        都不发——``sh000001``（上证指数）与 ``sz000001``（平安银行）在这些按
        6 位码过滤的报表里撞码，不挡就是把平安银行的事件安到指数头上。
        """
        from .corporate import report_code_for

        return report_code_for(symbol)

    @staticmethod
    def suspension(
        date: str,
        *,
        market: str = "全部",
        size: int = 200,
        page: int = 1,
        all_pages: bool = False,
        max_pages: int = 50,
    ) -> list[dict[str, Any]]:
        """停复牌信息（按**交易日**查询，不是按个股）。

        数据源 ``RPT_CUSTOM_SUSPEND_DATA_INTERFACE``。两个必须知道的口径：

        * **空 filter 会被服务端拒绝**（``9501 filter字段不能为空``），所以
          ``date`` 是必填。维度是交易日 ``DATETIME``——想查某只股票的停牌史，
          请拉多日全量后在本地按 ``SECURITY_CODE`` 筛。
        * ``MARKET`` 用中文面值（``全部`` / ``沪市`` / ``深市``）。

        回测里停牌是硬需求：停牌期间的 bar 不能当成「价格没动」，也不能拿
        假价格填。这条能力给的就是「哪天哪些票停了、为什么、停多久」。

        Returns
        -------
        ``list[dict]``，列名即报表字段名：``SECURITY_CODE`` /
        ``SECURITY_NAME_ABBR`` / ``SUSPEND_START_TIME`` / ``SUSPEND_END_TIME`` /
        ``SUSPEND_EXPIRE`` / ``SUSPEND_REASON`` / ``TRADE_MARKET`` /
        ``SUSPEND_START_DATE``。

        实测：``2026-10-13`` → 17 条，``2024-04-26`` → 927 条。
        """
        return BoardSessionMixin._dc_rows(
            "suspension",
            [f'MARKET="{market}"', f"DATETIME='{date}'"],
            sort_columns="SUSPEND_START_DATE",
            page=page,
            size=size,
            all_pages=all_pages,
            max_pages=max_pages,
        )

    @staticmethod
    def goodwill(
        symbol: str,
        *,
        size: int = 100,
        page: int = 1,
        all_pages: bool = False,
        max_pages: int = 50,
    ) -> list[dict[str, Any]]:
        """个股商誉明细（按报告期）。

        数据源 ``RPT_GOODWILL_STOCKDETAILS``。**稀疏报表**：多数公司没有商誉，
        查不到是正常的空集而不是故障（``allow_empty=True``；600519 实测回
        ``code=9201 返回数据为空``）。商誉排雷时真正的信号是「商誉/净资产
        占比高」而不是「有商誉」，``RPT_GOODWILL_MARKETSTATISTICS``
        （见 :meth:`goodwill_market`）直接给了这个比值。
        """
        code = BoardSessionMixin._dc_symbol_filter(symbol)
        if code is None:
            return []
        return BoardSessionMixin._dc_rows(
            "goodwill",
            [f'SECURITY_CODE="{code}"'],
            sort_columns="NOTICE_DATE",
            page=page,
            size=size,
            all_pages=all_pages,
            max_pages=max_pages,
        )

    @staticmethod
    def goodwill_market(
        *,
        size: int = 100,
        page: int = 1,
        all_pages: bool = False,
        max_pages: int = 50,
    ) -> list[dict[str, Any]]:
        """市场商誉统计（按板块 × 报告期，含商誉/净资产占比与减值状态）。

        数据源 ``RPT_GOODWILL_MARKETSTATISTICS``（实测 59 行，全市场按报告期
        汇总）。要「哪些板块商誉风险高」看这张，:meth:`goodwill` 是个股明细。
        """
        return BoardSessionMixin._dc_rows(
            "goodwill_market",
            [],
            sort_columns="REPORT_DATE",
            page=page,
            size=size,
            all_pages=all_pages,
            max_pages=max_pages,
        )

    @staticmethod
    def seo(
        symbol: str,
        *,
        size: int = 100,
        page: int = 1,
        all_pages: bool = False,
        max_pages: int = 50,
    ) -> list[dict[str, Any]]:
        """增发 / 再融资（SEO）明细。

        数据源 ``RPT_SEO_DETAIL``。与 :meth:`rights_issue`（配股）合起来才是
        完整的股本扩张事件集——漏掉增发，除权价里的股本项就少一块。

        **不指定排序列**：实测 ``NOTICE_DATE`` / ``ISSUE_START_DATE`` /
        ``UPDATE_DATE`` 均回 ``9501 排序列不存在``，因此这里用服务端默认
        顺序。需要稳定顺序的调用方请自行按 ``ISSUE_DATE`` 排序。
        """
        code = BoardSessionMixin._dc_symbol_filter(symbol)
        if code is None:
            return []
        return BoardSessionMixin._dc_rows(
            "seo",
            [f'SECURITY_CODE="{code}"'],
            page=page,
            size=size,
            all_pages=all_pages,
            max_pages=max_pages,
        )

    @staticmethod
    def equity_structure(
        symbol: str,
        *,
        size: int = 100,
        page: int = 1,
        all_pages: bool = False,
        max_pages: int = 50,
    ) -> list[dict[str, Any]]:
        """股本结构（有限售 / 无限售 / 总股本及占比，含变动原因）。

        数据源 ``RPT_F10_EH_EQUITY``。算真实换手率需要**流通股本**而不是
        总股本——这一列在这张表里（``LISTED_A_SHARES`` 等），按报告期给。
        """
        code = BoardSessionMixin._dc_symbol_filter(symbol)
        if code is None:
            return []
        return BoardSessionMixin._dc_rows(
            "equity_structure",
            [f'SECURITY_CODE="{code}"'],
            sort_columns="END_DATE",
            page=page,
            size=size,
            all_pages=all_pages,
            max_pages=max_pages,
        )

    @staticmethod
    def holder_num_detail(
        symbol: str,
        *,
        size: int = 100,
        page: int = 1,
        all_pages: bool = False,
        max_pages: int = 50,
    ) -> list[dict[str, Any]]:
        """股东户数**明细**（按报告期，含环比变动与户均市值）。

        区别于 :meth:`holder_num`（``RPT_HOLDERNUMLATEST``，只给最新一期）——
        户数收敛/发散是筹码集中度的常用代理指标，要看趋势必须走这张
        （``RPT_HOLDERNUM_DET``）。
        """
        code = BoardSessionMixin._dc_symbol_filter(symbol)
        if code is None:
            return []
        return BoardSessionMixin._dc_rows(
            "holder_num_detail",
            [f'SECURITY_CODE="{code}"'],
            sort_columns="END_DATE",
            page=page,
            size=size,
            all_pages=all_pages,
            max_pages=max_pages,
        )

    @staticmethod
    def org_seat_trades(
        symbol: str,
        *,
        size: int = 100,
        page: int = 1,
        all_pages: bool = False,
        max_pages: int = 50,
    ) -> list[dict[str, Any]]:
        """机构专用席位交易明细（龙虎榜里的「机构专用」席位）。

        数据源 ``RPT_ORGANIZATION_TRADE_DETAILS``。与 :meth:`longhu`
        （龙虎榜）互补：龙虎榜给当日全部席位，这张只给机构席位，适合判断
        「这波是机构在买还是游资在炒」。
        """
        code = BoardSessionMixin._dc_symbol_filter(symbol)
        if code is None:
            return []
        return BoardSessionMixin._dc_rows(
            "org_seat_trades",
            [f'SECURITY_CODE="{code}"'],
            sort_columns="TRADE_DATE",
            page=page,
            size=size,
            all_pages=all_pages,
            max_pages=max_pages,
        )

    @staticmethod
    def connect_deals(
        *,
        mutual_type: str = "",
        size: int = 100,
        page: int = 1,
        all_pages: bool = False,
        max_pages: int = 50,
    ) -> list[dict[str, Any]]:
        """沪深港通历史成交（逐日：净流入 / 成交额 / 额度余额 / 领涨股）。

        数据源 ``RPT_MUTUAL_DEAL_HISTORY``。**南北双向都在这一张表里**，
        ``mutual_type`` 给空串表示不过滤（六种通道全要）。

        ``MUTUAL_TYPE`` 的编号语义（2026-10-14 实测，按 ``LEAD_STOCKS_CODE``
        的交易所后缀判定，不是按命名猜的）：

        ====  ======================  ==========  =====================
        编号  通道                     方向        领涨股后缀（实测）
        ====  ======================  ==========  =====================
        001   沪股通                   北向        ``.SH``（A 股）
        002   沪市港股通               南向        ``.HK``
        003   深股通                   北向        ``.SZ``（A 股）
        004   深市港股通               南向        ``.HK``
        005   北向合计（沪股通+深股通）  北向        ``.SH``
        006   南向合计（沪+深港股通）   南向        ``.HK``
        ====  ======================  ==========  =====================

        只看外资买 A 股请传 ``mutual_type="001"``（沪股通）/
        ``"003"``（深股通）/ ``"005"``（北向合计）——不传会混入南向数据，
        净流入的方向含义正好相反。
        （沪股通/深股通/港股通全要）；常见的 ``001`` 是沪股通。

        与既有 :meth:`northbound`（当日快照）和 :meth:`northbound_hold`
        （持股明细）分工：这条是**逐日成交流水**，能做时间序列。
        """
        filters = [f'MUTUAL_TYPE="{mutual_type}"'] if mutual_type else []
        return BoardSessionMixin._dc_rows(
            "connect_deals",
            filters,
            sort_columns="TRADE_DATE",
            page=page,
            size=size,
            all_pages=all_pages,
            max_pages=max_pages,
        )

    @staticmethod
    def southbound_ranks(
        symbol: str,
        *,
        size: int = 100,
        page: int = 1,
        all_pages: bool = False,
        max_pages: int = 50,
    ) -> list[dict[str, Any]]:
        """**港股通（南向）**持股排行榜——按港股个股，含持股数/市值/占比与区间变动。

        数据源 ``RPT_MUTUAL_STOCK_HOLDRANKS``。

        .. warning::

           这张表**只有港股**，不是北向。实测全量样本 500 行里 ``SECUCODE``
           后缀 100% 是 ``.HK``，``MUTUAL_TYPE`` 只有 ``002``（沪市港股通）
           与 ``004``（深市港股通）——两个都是**南向**通道。A 股标的查这张表
           恒为空集（``SECURITY_CODE="600519"`` / ``SECUCODE="600519.SH"``
           实测均 0 行）。要看北向（外资持 A 股）请用 :meth:`northbound_hold`
           （``RPT_MUTUAL_HOLD``）。早先按表名里的 ``MUTUAL`` 想当然叫它
           ``northbound_ranks``，是错的——名字错了，调用方会拿着南向数据当
           北向用，方向正好相反。

        与 :meth:`northbound_hold` 的分工：那条是持仓明细，这里带
        ``HOLD_MARKETCAP_CHG1/5/10``（1/5/10 日市值变动）与
        ``PARTICIPANT_NUM``（参与机构数），适合做「南向在加仓还是减仓」。

        日期字段是 ``HOLD_DATE``；用 ``TRADE_DATE`` 过滤会报
        ``9501 filter字段中日期参数格式错误``。
        """
        from ..domain.symbol import split_symbol

        # 这里**不走** ``report_code_for``：那是指数/深市撞码守卫，而本表只
        # 收港股代码，用它反而会把合法港股挡在门外。
        _, code = split_symbol(symbol)
        return BoardSessionMixin._dc_rows(
            "southbound_ranks",
            [f'SECURITY_CODE="{code}"'],
            sort_columns="HOLD_DATE",
            page=page,
            size=size,
            all_pages=all_pages,
            max_pages=max_pages,
        )

    @staticmethod
    def margin_daily(
        *,
        size: int = 100,
        page: int = 1,
        all_pages: bool = False,
        max_pages: int = 50,
    ) -> list[dict[str, Any]]:
        """融资融券每日交易（市场级：融资余额 / 融券余额 / 两融余额）。

        数据源 ``RPTA_WEB_MARGIN_DAILYTRADE``（实测 3405 行，按
        ``STATISTICS_DATE`` 排序）。与既有 :meth:`margin`（个股两融明细）
        互补：这条是**全市场每日汇总**，看杠杆水位的时间序列用它。
        """
        return BoardSessionMixin._dc_rows(
            "margin_daily",
            [],
            sort_columns="STATISTICS_DATE",
            page=page,
            size=size,
            all_pages=all_pages,
            max_pages=max_pages,
        )

    @staticmethod
    def block_trade_stats(
        symbol: str,
        *,
        size: int = 100,
        page: int = 1,
        all_pages: bool = False,
        max_pages: int = 50,
    ) -> list[dict[str, Any]]:
        """大宗交易统计（按个股 × 交易日汇总，含折溢价率）。

        数据源 ``RPT_BLOCKTRADE_STA``。与既有 :meth:`block_trades`
        （``RPT_DATA_BLOCKTRADE`` 逐笔明细）分工：这条是**已按日聚合**的
        统计（笔数/总量/总额/均价/折溢价），省得自己 groupby。
        """
        code = BoardSessionMixin._dc_symbol_filter(symbol)
        if code is None:
            return []
        return BoardSessionMixin._dc_rows(
            "block_trade_stats",
            [f'SECURITY_CODE="{code}"'],
            sort_columns="TRADE_DATE",
            page=page,
            size=size,
            all_pages=all_pages,
            max_pages=max_pages,
        )

    # -- 宏观补齐（2026-10-14）--------------------------------------------- #

    @staticmethod
    def _macro_rows(
        report: str,
        *,
        size: int = 100,
        page: int = 1,
        all_pages: bool = False,
        max_pages: int = 50,
    ) -> list[dict[str, Any]]:
        """宏观报表的共用取数器：全部按 ``REPORT_DATE`` 降序（最新在前）。"""
        return BoardSessionMixin._dc_rows(
            report,
            [],
            sort_columns="REPORT_DATE",
            page=page,
            size=size,
            all_pages=all_pages,
            max_pages=max_pages,
        )

    @staticmethod
    def macro_money_supply(
        *,
        size: int = 100,
        page: int = 1,
        all_pages: bool = False,
        max_pages: int = 50,
    ) -> list[dict[str, Any]]:
        """货币供应量 M0/M1/M2（含同比、环比）。

        ``RPT_ECONOMY_CURRENCY_SUPPLY``，实测 225 期。M1-M2 剪刀差是常用的
        流动性观测口径，字段里 ``BASIC_CURRENCY``（M0）/ ``CURRENCY``（M1）
        之外还有 ``FREE_CASH``（M2）。
        """
        return BoardSessionMixin._macro_rows(
            "macro_money_supply", size=size, page=page, all_pages=all_pages, max_pages=max_pages
        )

    @staticmethod
    def macro_loan(
        *,
        size: int = 100,
        page: int = 1,
        all_pages: bool = False,
        max_pages: int = 50,
    ) -> list[dict[str, Any]]:
        """人民币贷款（含同比、环比、累计）。``RPT_ECONOMY_RMB_LOAN``，225 期。"""
        return BoardSessionMixin._macro_rows(
            "macro_loan", size=size, page=page, all_pages=all_pages, max_pages=max_pages
        )

    @staticmethod
    def macro_retail(
        *,
        size: int = 100,
        page: int = 1,
        all_pages: bool = False,
        max_pages: int = 50,
    ) -> list[dict[str, Any]]:
        """社会消费品零售总额（含同比、环比、累计）。

        ``RPT_ECONOMY_TOTAL_RETAIL``，209 期。
        """
        return BoardSessionMixin._macro_rows(
            "macro_retail", size=size, page=page, all_pages=all_pages, max_pages=max_pages
        )

    @staticmethod
    def macro_asset_invest(
        *,
        size: int = 100,
        page: int = 1,
        all_pages: bool = False,
        max_pages: int = 50,
    ) -> list[dict[str, Any]]:
        """固定资产投资（含同比、环比、累计）。``RPT_ECONOMY_ASSET_INVEST``，161 期。"""
        return BoardSessionMixin._macro_rows(
            "macro_asset_invest", size=size, page=page, all_pages=all_pages, max_pages=max_pages
        )

    @staticmethod
    def macro_industrial(
        *,
        size: int = 100,
        page: int = 1,
        all_pages: bool = False,
        max_pages: int = 50,
    ) -> list[dict[str, Any]]:
        """工业增加值（同比、累计）。``RPT_ECONOMY_INDUS_GROW``，205 期。"""
        return BoardSessionMixin._macro_rows(
            "macro_industrial", size=size, page=page, all_pages=all_pages, max_pages=max_pages
        )

    @staticmethod
    def macro_reserve_ratio(
        *,
        size: int = 100,
        page: int = 1,
        all_pages: bool = False,
        max_pages: int = 50,
    ) -> list[dict[str, Any]]:
        """存款准备金率（大型/中小金融机构，含变动前后与生效日）。

        ``RPT_ECONOMY_DEPOSIT_RESERVE``，58 期。降准是 A 股最直接的流动性
        事件之一，``INTEREST_RATE_BB``/``BA`` 是调整前/后的大型机构档。
        """
        return BoardSessionMixin._macro_rows(
            "macro_reserve_ratio", size=size, page=page, all_pages=all_pages, max_pages=max_pages
        )

    @staticmethod
    def macro_forex_reserve(
        *,
        size: int = 100,
        page: int = 1,
        all_pages: bool = False,
        max_pages: int = 50,
    ) -> list[dict[str, Any]]:
        """外汇储备 / 黄金储备（含同比、环比）。``RPT_ECONOMY_GOLD_CURRENCY``，225 期。"""
        return BoardSessionMixin._macro_rows(
            "macro_forex_reserve", size=size, page=page, all_pages=all_pages, max_pages=max_pages
        )

    # -- 排行 --------------------------------------------------------------- #
    @staticmethod
    def rank(
        market: str = "all_a",
        *,
        sort: str = "change_pct",
        limit: int = 20,
        page: int = 1,
        extra_fields: Sequence[str] = (),
    ) -> list[dict[str, Any]]:
        """通用排行（个股 / ETF / 指数 / 板块）。

        Parameters
        ----------
        market:
            ``all_a`` / ``sh_a`` / ``sz_a`` / ``gem`` / ``star`` / ``bse``
            / ``etf`` / ``index`` / ``industry`` / ``concept`` / ``region``
            （见 :data:`~atst.web.fundflow.MARKET_FILTERS`）。
        sort:
            ``change_pct`` / ``amount`` / ``turnover`` / ``main_net``
            / ``main_net_ratio`` 等（见
            :data:`~atst.web.fundflow.SORT_FIELDS`）。
        extra_fields:
            追加请求的东财原始字段（如 ``["f62", "f184"]`` 取资金流）。
        """
        from .fundflow import EastmoneyRankSource

        src = EastmoneyRankSource(client=_shared_http())
        try:
            return src.fetch_rows(
                market, sort=sort, limit=limit, page=page, extra_fields=extra_fields
            )
        finally:
            src.close()

    @staticmethod
    def hot_boards(limit: int = 10) -> list[dict[str, Any]]:
        """行业板块资金流排行（按主力净流入降序）。"""
        from .fundflow import EastmoneyRankSource

        src = EastmoneyRankSource(client=_shared_http())
        try:
            return src.fetch_rows(
                "industry",
                sort="main_net",
                limit=limit,
                extra_fields=["f62", "f184", "f66", "f72", "f78", "f84"],
            )
        finally:
            src.close()

    @staticmethod
    def sector_flow(
        board: str = "industry", *, sort: str = "main_net", limit: int = 20, page: int = 1
    ) -> list[dict[str, Any]]:
        """板块资金流排行（东财 push2 clist；金额单位元）。

        Parameters
        ----------
        board:
            ``industry`` 行业（默认）/ ``concept`` 概念 / ``region`` 地域。
        sort:
            ``main_net`` 主力净流入（默认）/ ``main_ratio`` 主力净占比 /
            ``change_pct`` 涨跌幅 / ``amount`` 成交额 等（见
            :data:`atst.web.fundflow.SORT_FIELDS`）；资金流排序自动附带
            f62/f184/f66/f72/f78/f84 字段（P12 修复：否则行值静默为 None）。

        Returns
        -------
        ``list[dict]``：code / name / symbol / main_net / main_net_ratio /
        super_large_net / large_net / medium_net / small_net / price /
        change_pct / amount 等。
        """
        from .fundflow import EastmoneyRankSource

        src = EastmoneyRankSource(client=_shared_http())
        try:
            return src.fetch_rows(board, sort=sort, limit=limit, page=page)
        finally:
            src.close()

    @staticmethod
    def hot_rank(*, page: int = 1, size: int = 100) -> list[dict[str, Any]]:
        """股吧个股人气榜（按名次升序，100 条/页）。

        Returns
        -------
        ``[{"rank": 1, "symbol": "sh600127", "code": "600127",
        "market": "sh", "rank_change": 0, "his_rank_change": 0}, ...]``

        榜单仅含排名与代码；如需行情请以 ``symbol`` 回查 :meth:`quotes`。
        """
        from .hot_rank import EastmoneyHotRankSource

        src = EastmoneyHotRankSource(client=_shared_http())
        try:
            return src.fetch_hot_rank(page=page, size=size)
        finally:
            src.close()
