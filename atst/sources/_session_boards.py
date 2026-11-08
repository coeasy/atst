# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""WebQuoteSession 的板块/排行域 Mixin（v2/W7 按领域拆分）。

从 :mod:`atst.sources._session_market` 的 ``BoardSessionMixin`` 抽出板块列表、成分、
排行与人气榜入口；datacenter 具名报表入口见 :mod:`atst.sources._session_datacenter`。
"""

from __future__ import annotations

from typing import Any

from ..domain.models import Quote
from ._session_market import _shared_http


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
    # :data:`atst.sources.corporate.VALID_REPORTS`，这里只负责把调用方的语义参数
    # 翻译成服务端 filter，并写清每张表的口径与坑。共用一个私有取数器，避免
    # 17 份重复的 try/finally。
