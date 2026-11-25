# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""组合能力内核：复权事件窗口扩展 + 原始 K 线 + 富化三段组合。

``execute_composed`` 只做编排入口，真正的三段组合计算与窗口推进全部在本模块。
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

from ..diagnostics import WarningCode, record_warning

_ADJUSTABLE_PERIODS: frozenset[str] = frozenset({"day", "week", "month"})


_COARSE_DAYLIKE_PERIODS: frozenset[str] = frozenset({"week", "month", "season", "year"})


_ADJUST_EVENT_SOURCES: frozenset[str] = frozenset({"eastmoney", "tdx"})


_ADJUST_EVENT_PAGE_SIZE: int = 100


_ADJUST_EVENT_MAX_PAGES: int = 10


_ADJUST_WINDOW_GROWTH: int = 4


_ADJUST_HISTORY_MAX: int = 20_000


_ENRICH_ADJUSTMENTS: frozenset[str] = frozenset({"none", "qfq", "hfq"})


_ENRICH_MAX_COUNT: int = 500


def _day_in_range(day: str, start_date: str, end_date: str) -> bool:
    """闭区间判定。``day`` 取不到时**保留**该行：裁不掉的行宁可留着让调用方看见，
    也不要静默丢掉一条它没法解释的数据。"""
    if not day:
        return True
    if start_date and day < start_date:
        return False
    return not (end_date and day > end_date)


def _adjusted_raw_bars(
    executor, symbol: str, *, period: str, count: int, start: int, raw_provider: str, timeout: float
) -> list[Any]:
    """取复权用的**未复权**原始 K 线：本地 vipdoc 优先，没有就走 Provider。

    过去的 ``adjusted_bars`` 只认 ``vipdoc_root``，没配本地数据的调用方拿到的就是一句
    ``requires vipdoc_root``——复权因此事实上只是个离线功能，TDX 在线用户用不了。
    这里把"原始 bar 从哪来"变成一次正常的单内核查询：

    * 日线且本地 vipdoc 有这份文件 → 读本地（不产生网络 I/O，口径与离线一致）；
    * 其余情况 → 用 :class:`~atst.query.QueryPlanner` 编一条 ``bars`` 计划并交给
      :meth:`execute`。**复用同一条主链路**而不是在这里另写一份 Provider 分支，
      于是复权自动跟随 Provider 的能力面（TDX 的 10 个周期、Web 三家的 ``adjust``…
      这里刻意不传），也不会出现"复权能取、bars 取不到"的分叉。
    """
    from ..domain.models import Bar
    from ..domain.symbol import split_symbol
    from ..query import QuerySpec
    from ..reader import DayBarReader

    if period == "day" and executor.vipdoc_root:
        market, code = split_symbol(symbol)
        path = Path(executor.vipdoc_root) / market / "lday" / f"{market}{code}.day"
        if path.exists():
            bars = DayBarReader().read(path, output="model")
            end = max(0, len(bars) - start)
            begin = max(0, end - count)
            return list(bars[begin:end])

    spec = QuerySpec.build(
        "bars",
        symbols=symbol,
        provider=raw_provider,
        period=period,
        count=count,
        start=start,
        currentness="historical",
        # P1-3：预算继承——内层不再各自重置 5s deadline，吃外层剩余预算。
        deadline_ms=max(1, int(timeout * 1000)),
    )
    rows = executor.execute_subquery(spec).data
    return [row if isinstance(row, Bar) else Bar.from_dict(row) for row in rows]


def _adjusted_events(executor, symbol: str, event_source: str, *, timeout: float) -> list[Any]:
    """取复权事件。默认东财（已对拍），``tdx`` 需显式点名并过布局闸。"""
    from ..domain.finance import (
        reject_implausible_changes,
        to_capital_changes,
    )

    if event_source == "eastmoney":
        from ..diagnostics import WarningCode, record_warning
        from ..domain.finance import capital_changes_from_dividends_and_rights
        from ..query import QuerySpec

        def page_all(capability: str) -> list[Any]:
            """翻页到尽。``dividend_history`` 的 ``size`` 默认只有 20，
            长历史标的的早期事件会被静默截断（600519 实有 28 条）。

            逐行去重：``dividend_history`` 按 ``EX_DIVIDEND_DATE`` 降序排，
            该列存在并列值，服务端分页边界并不稳定——同一条记录可能落在
            相邻两页里都出现。下游 :func:`capital_changes_from_dividends_and_rights`
            是按日 **累加** ``bonus`` / ``dividend`` 的，重复行会直接把
            送转比例与派息金额翻倍，复权因子静默偏大且不报任何错。
            """
            collected: list[Any] = []

            def fingerprint(row: Any) -> tuple[tuple[str, str], ...] | str:
                if isinstance(row, dict):
                    return tuple(sorted((str(k), str(v)) for k, v in row.items()))
                return str(row)

            seen: set[Any] = set()
            for page in range(1, _ADJUST_EVENT_MAX_PAGES + 1):
                # P1-3：内层查询走主链（execute_subquery），预算吃外层剩余额度。
                batch = executor.execute_subquery(
                    QuerySpec.build(
                        capability,
                        symbols=symbol,
                        provider="eastmoney",
                        currentness="historical",
                        deadline_ms=max(1, int(timeout * 1000)),
                        options={
                            "args": [str(symbol)],
                            "kwargs": {
                                "page": page,
                                "size": _ADJUST_EVENT_PAGE_SIZE,
                            },
                        },
                    )
                ).data
                batch = list(batch or [])
                for row in batch:
                    mark = fingerprint(row)
                    if mark in seen:
                        continue
                    seen.add(mark)
                    collected.append(row)
                if len(batch) < _ADJUST_EVENT_PAGE_SIZE:
                    return collected
            #: 触顶不是"取完了"：最后一页仍是满页，说明还有没取到的事件。
            record_warning(
                WarningCode.WEB_EASTMONEY_PAGE_LIMIT,
                f"{symbol} 的 {capability} 在 {_ADJUST_EVENT_MAX_PAGES} 页内未取尽"
                f"（每页 {_ADJUST_EVENT_PAGE_SIZE} 条）：已取 {len(collected)} 条，"
                "更早期事件可能缺失。",
                stacklevel=2,
            )
            return collected

        #: 两路合成：分红送转（RPT_SHAREBONUS_DET）+ 配股（RPT_IPO_ALLOTMENT）。
        #: 配股不补，除权价公式里的 ``Pr·R`` 恒为 0，配股标的的因子系统性偏小。
        return capital_changes_from_dividends_and_rights(
            page_all("dividend_history"),
            page_all("rights_issue"),
            code=str(symbol),
        )

    from ..domain.models import CapitalChange

    with executor._client_session(executor._tdx_client(timeout)) as client:
        raw = list(client.capital_changes(symbol))
    #: ``TdxClient.capital_changes`` 已经返回 :class:`CapitalChange` 模型（解析行在
    #: 客户端里就转过了），而 :func:`to_capital_changes` 吃的是**解析行 dict**。
    #: 过去这里无条件套一层转换，于是 ``event_source="tdx"`` 每次都炸在
    #: ``row.get(...)`` 上、被兜成 E9000 InternalError——一条只对内部说得通的错，
    #: 调用方根本读不出"这条路不通，换 eastmoney"。这里按类型分派，让错误回到
    #: 它该在的地方：布局闸给出的 ValidationError。
    events = list(raw) if raw and isinstance(raw[0], CapitalChange) else to_capital_changes(raw)
    #: 布局未锁定 → 错位记录不许进引擎（错位的复权比不复权危险得多）。
    reject_implausible_changes(events)
    return events


def _extend_window_to_events(
    executor,
    bars: list[Any],
    events: Sequence[Any],
    *,
    symbol: str,
    period: str,
    start: int,
    target: int,
    raw_provider: str,
    timeout: float,
) -> list[Any]:
    """把 K 线窗口向后延伸，直到盖住最早的复权事件（或触到历史尽头）。

    为什么必须有这一步：后复权因子是 ``∏ 1/k_d``，``d`` 遍历**全部**事件日——
    包括窗口之前的那些。而每个 ``k_d`` 都需要该事件日的**前收盘价**，它只能从
    该事件日之前那根 K 线拿到。窗口盖不住那些事件时，引擎只能走 ``1/(1+S+R)``
    的降级口径，于是**同一根 bar 在 ``count=5`` 与 ``count=2000`` 下复权结果不同**：
    实测某标的 hfq 因子 1.045 与 1.642 并存。这既不可复现，也是静默错——拿短窗口
    做回测，早期因子的偏差会被当成真实价格。

    延伸用**倍数增长**（``_ADJUST_WINDOW_GROWTH``）而非线性步进，把请求数压在
    对数量级；一旦某次取回的根数不再增加，说明已经到历史尽头，就此收手——剩下的
    事件真的没有更早的 K 线可依，引擎会照旧记 ``ADJUST_PREV_CLOSE_MISSING``。
    ``_ADJUST_HISTORY_MAX`` 是防御上限：真到那儿说明上游在异常应答，不再无限翻。
    """

    def first_day(rows: Sequence[Any]) -> str:
        if not rows:
            return ""
        return str(getattr(rows[0], "datetime", "") or "")[:10]

    event_days = [str(getattr(ev, "date", "") or "")[:10] for ev in events]
    event_days = [d for d in event_days if d]
    if not event_days:
        return bars
    earliest_event = min(event_days)

    def uncovered(rows: Sequence[Any]) -> bool:
        day = first_day(rows)
        return bool(day) and earliest_event < day

    if not uncovered(bars):
        return bars

    grown = target
    while grown < _ADJUST_HISTORY_MAX:
        grown = min(grown * _ADJUST_WINDOW_GROWTH, _ADJUST_HISTORY_MAX)
        try:
            candidate = executor._adjusted_raw_bars(
                symbol,
                period=period,
                count=grown,
                start=start,
                raw_provider=raw_provider,
                timeout=timeout,
            )
        except Exception as exc:  # noqa: BLE001 - 延伸失败退回原窗口，但要留话
            #: 不能静默退回去：退回原窗口意味着早期事件拿不到前收盘价，因子会随
            #: 请求的 ``count`` 漂移（同一根 bar 短窗口/长窗口两个值）。调用方看到
            #: 的必须是"这次因子可能是漂移值"，而不是一个看着正常的数。
            record_warning(
                WarningCode.ADJUST_WINDOW_EXTEND_FAILED,
                f"{symbol} 的复权窗口向后延伸取数失败（{type(exc).__name__}: {exc}）；"
                f"已退回最近 {len(bars)} 根。若最早事件（{earliest_event}）早于窗口起点，"
                "它的前收盘价取不到，早期因子会随 count 变化。",
                stacklevel=2,
            )
            return bars
        #: 根数不再增加 = 历史取完了。再往下翻只是重复同一段。
        if len(candidate) <= len(bars):
            return candidate or bars
        bars = list(candidate)
        if not uncovered(bars):
            break
    return bars
