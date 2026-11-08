# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""``composed`` 后端执行入口：只做参数编排，三段组合内核在 :mod:`.composed`。"""

from __future__ import annotations

from typing import Any

from ..domain.symbol import normalize_symbol
from ..errors import ValidationError
from ..query import QueryPlan
from .composed import (
    _ADJUST_EVENT_SOURCES,
    _ADJUSTABLE_PERIODS,
    _ENRICH_ADJUSTMENTS,
    _ENRICH_MAX_COUNT,
)


def _composed_call(
    executor,
    plan: QueryPlan,
    capability: str,
    args: list[Any],
    kwargs: dict[str, Any],
    *,
    timeout: float,
) -> Any:
    if capability == "adjusted_bars":
        from ..domain.adjust import AdjustEngine, AdjustMethod
        from ..domain.finance import to_capital_changes
        from ..domain.models import CapitalChange

        symbol = str(args[0])
        #: ``adjustment`` 是 ``method`` 的别名：``Client.bars`` 用
        #: ``adjustment=``（被直接面拒之门外的那格），``daily_enriched`` 用
        #: ``adjust=``，同一个"复权方式"概念三个拼法，调用方只能靠翻源码。
        #: 两个都收，同时给出时以 ``method`` 为准（它名字更贴协议）。
        if "method" in kwargs and "adjustment" in kwargs:
            raise ValidationError(
                "adjusted_bars 同时给了 method 与 adjustment（同一个复权方式的两种拼法）：只留一个",
                context={
                    "method": kwargs.get("method"),
                    "adjustment": kwargs.get("adjustment"),
                },
            )
        method = str(kwargs.pop("method", kwargs.pop("adjustment", "qfq")))
        period = str(kwargs.pop("period", "day"))
        count = int(kwargs.pop("count", 320))
        start = int(kwargs.pop("start", 0))
        events = kwargs.pop("events", None)
        anchor_date = kwargs.pop("anchor_date", None)
        raw_provider = str(kwargs.pop("provider", "") or "").strip().lower() or "tdx"
        event_source = str(kwargs.pop("event_source", "") or "").strip().lower() or "eastmoney"
        if event_source not in _ADJUST_EVENT_SOURCES:
            raise ValidationError(
                "adjusted_bars 不认识的 event_source",
                context={
                    "event_source": event_source,
                    "supported": sorted(_ADJUST_EVENT_SOURCES),
                },
            )
        if period not in _ADJUSTABLE_PERIODS:
            raise ValidationError(
                "adjusted_bars 只支持按日期定位复权因子的周期",
                context={"period": period, "supported": sorted(_ADJUSTABLE_PERIODS)},
            )

        #: 多取一根（窗口**前**一根）再裁掉：复权引擎拿它当最早那批除权事件的
        #: 「前收盘价」，否则每股现金红利被整项忽略（ADJUST_PREV_CLOSE_MISSING）。
        #: 调用方看到的根数不变。
        lead = 1 if count > 0 else 0
        want = count + lead
        bars = executor._adjusted_raw_bars(
            symbol,
            period=period,
            count=want,
            start=start,
            raw_provider=raw_provider,
            timeout=timeout,
        )

        #: ``events`` 的三态必须分清：
        #:
        #: * ``None``（没给）→ 按 ``event_source`` 去取事件；
        #: * ``[]``（显式给了空表）→ **就是不调整**，不要偷偷去拉一份回来。
        #:   这里过去写的是 ``if not events:``，把 ``[]`` 和 ``None`` 混成一类，
        #:   于是 "我把事件集清空了" 被静默换成 "我去东财拉一份"——同一句话两种
        #:   结果，而且没有任何地方会说。
        #: * 非空 → 用调用方给的事件集（``CapitalChange`` 或等价 dict 行）。
        if events is None:
            events = executor._adjusted_events(symbol, event_source, timeout=timeout)
        event_list = list(events)
        if event_list and not isinstance(event_list[0], CapitalChange):
            event_list = to_capital_changes(event_list)

        #: 后复权/定点复权要把**窗口之前**的事件也累乘进去，而那批事件需要各自的
        #: 前收盘价。窗口盖不住它们时只能走降级口径，因子随 count 漂移（不可复现）。
        #: 这里按倍数向后延伸取数窗口，直到盖住最早的事件或触到历史尽头；返回给
        #: 调用方的仍然只有它要的那 ``count`` 根。
        if method in (AdjustMethod.HFQ, AdjustMethod.FIXED) and event_list and bars:
            bars = executor._extend_window_to_events(
                bars,
                event_list,
                symbol=symbol,
                period=period,
                start=start,
                target=want,
                raw_provider=raw_provider,
                timeout=timeout,
            )

        adjusted = AdjustEngine().apply(
            list(bars),
            event_list,
            method,
            anchor_date=anchor_date,
        )
        if count > 0 and len(adjusted) > count:
            return adjusted[len(adjusted) - count :]
        return adjusted

    if capability == "sync_daily":
        from ..sink import LocalDaySink

        symbols = args[0]
        root = kwargs.pop("root", None) or executor.vipdoc_root
        profile = kwargs.pop("profile", "a_share_day")
        #: 默认值取自 sink 自己的常量，不在这里抄第二份字面量——"同一事实只存在
        #: 一处"，否则改常量不改执行体，两处口径会静默分叉。
        from ..sink.local_day import MAX_CHUNK, MAX_WINDOWS

        chunk = int(kwargs.pop("chunk", MAX_CHUNK))
        max_windows = int(kwargs.pop("max_windows", MAX_WINDOWS))
        if not root:
            raise ValidationError("sync_daily requires root or vipdoc_root")
        sink = LocalDaySink(root, profile=profile)
        out: dict[str, Any] = {}
        with executor._client_session(executor._tdx_client(timeout)) as client:
            for symbol in symbols:
                requested = str(symbol)
                normalized = normalize_symbol(requested)

                def fetch(
                    offset: int,
                    count: int,
                    _symbol: str = normalized,
                ) -> list[Any]:
                    return client.bars(
                        _symbol,
                        period="day",
                        count=count,
                        start=offset,
                        as_format="dict",
                    )

                result = sink.sync(
                    normalized,
                    fetch,
                    chunk=chunk,
                    max_windows=max_windows,
                )
                out[requested] = {
                    "added": result.added,
                    "existed": result.existed,
                    "path": result.path,
                    "last_date": sink.last_date(normalized),
                }
        return out

    if capability == "daily_enriched":
        from ..domain.enrich import enrich_daily_bars
        from ..query import QueryPlanner, QuerySpec

        symbol = str(args[0])
        count = int(kwargs.pop("count", 250))
        start = int(kwargs.pop("start", 0))
        adjust = str(kwargs.pop("adjust", "none") or "none").strip().lower()
        bar_provider = str(kwargs.pop("provider", "") or "").strip().lower() or "tdx"
        #: 与 ``adjusted_bars`` 同款双旋钮：``provider`` 管未复权 K 线从哪来，
        #: ``event_source`` 管除权除息事件从哪来。默认东财（已对拍）。
        event_source = str(kwargs.pop("event_source", "") or "").strip().lower() or "eastmoney"
        if event_source not in _ADJUST_EVENT_SOURCES:
            raise ValidationError(
                "daily_enriched 不认识的 event_source",
                context={
                    "event_source": event_source,
                    "supported": sorted(_ADJUST_EVENT_SOURCES),
                },
            )
        if adjust not in _ENRICH_ADJUSTMENTS:
            raise ValidationError(
                "daily_enriched 不认识的 adjust",
                context={"adjust": adjust, "supported": sorted(_ENRICH_ADJUSTMENTS)},
            )
        if count < 1:
            raise ValidationError("daily_enriched 的 count 必须 >= 1", context={"count": count})
        if count > _ENRICH_MAX_COUNT:
            #: 不静默截断：老行的估值列会变成 None，调用方分不清"没有"和"没取到"。
            raise ValidationError(
                "daily_enriched 单次最多覆盖估值序列的单页上限 "
                f"{_ENRICH_MAX_COUNT} 个交易日；更长的区间请用 start 偏移分段",
                context={"count": count, "limit": _ENRICH_MAX_COUNT},
            )

        #: 多取一根（窗口前一根）让首行也拿到真实 pre_close，算完裁掉。
        lead = 1
        want = count + lead
        bars = executor._adjusted_raw_bars(
            symbol,
            period="day",
            count=want,
            start=start,
            raw_provider=bar_provider,
            timeout=timeout,
        )
        if adjust != "none":
            from ..domain.adjust import AdjustEngine, AdjustMethod

            events = executor._adjusted_events(symbol, event_source, timeout=timeout)
            if adjust == AdjustMethod.HFQ and events and bars:
                #: 与 ``adjusted_bars`` 同一条理由：hfq 的因子从史上第一个事件起
                #: 累乘，窗口盖不住早期事件时因子会随 ``count`` 漂移。宽表是批量
                #: 产物，同一根 bar 在不同批次的值不一致会让下游对不上账。
                bars = executor._extend_window_to_events(
                    bars,
                    events,
                    symbol=symbol,
                    period="day",
                    start=start,
                    target=want,
                    raw_provider=bar_provider,
                    timeout=timeout,
                )
            bars = AdjustEngine().apply(list(bars), events, adjust)
        valuation = executor.execute(
            QueryPlanner().compile(
                QuerySpec.build(
                    "valuation_history",
                    symbols=symbol,
                    provider="eastmoney",
                    currentness="historical",
                    options={
                        "args": [symbol],
                        "kwargs": {"count": min(_ENRICH_MAX_COUNT, count + lead)},
                    },
                )
            )
        ).data
        rows = enrich_daily_bars(list(bars), list(valuation or []))
        if len(rows) > count:
            return rows[len(rows) - count :]
        return rows

    raise ValidationError(
        "unknown composed capability",
        context={"capability": capability},
    )
