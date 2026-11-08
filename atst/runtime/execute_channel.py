# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""显式通道执行体：tdx 协议族 / 本地 vipdoc / 四个 web Provider 的行情通道。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..diagnostics import WarningCode, record_warning
from ..errors import ValidationError
from ..query import QueryPlan


def _tdx_quotes(executor, plan: QueryPlan) -> Any:
    """``quotes`` 的失败必须显形：全败即抛，部分失败随结果携带。

    子客户端对逐只失败做了隔离，默认把它们只写进 ``client.last_errors``——
    一个每次调用都被整体覆盖的实例属性。内核既不读那个属性也不另作声明，
    于是"所有主机都连不上"在 wire 上的形状与"这只代码没有行情"完全相同
    （``data=[]`` + ``warnings=()`` + HTTP 200）。失败袋在此显式取走，
    整体与部分按 :meth:`_tdx_bars` 的同一条界线分家：连不上就抛，缺几只需报。

    ``options['strict']``：逐只开启解析器自校验判据（哨兵 / 成交额值域），
    发现字段漂移的标的计入失败而不是返回可疑行情。
    """
    symbols = list(plan.spec.symbols)
    strict = bool(plan.spec.options.get("strict"))
    errors: list[tuple[str, BaseException]] = []
    with executor._client_session(executor._tdx_client(executor._hop_timeout(plan))) as client:
        rows = client.quotes(symbols, strict=strict, _collect=errors)
    if errors and not rows:
        raise errors[0][1]
    if errors:
        record_warning(
            WarningCode.QUOTES_PARTIAL_FAILURE,
            f"quotes 有 {len(errors)}/{len(symbols)} 只标的未取得行情："
            + "；".join(f"{sym} → {type(exc).__name__}({exc})" for sym, exc in errors[:3]),
        )
    return rows


def _tdx_bars(executor, plan: QueryPlan) -> Any:
    if plan.spec.adjustment:
        raise ValidationError(
            "TDX Direct bars 不支持在原始 bars 调用中静默复权",
            context={
                "provider": "tdx",
                "channel": "quotation",
                "adjustment": plan.spec.adjustment,
            },
        )
    with executor._client_session(executor._tdx_client(executor._hop_timeout(plan))) as client:
        return client.bars(
            plan.spec.symbols[0],
            period=plan.spec.period,
            count=plan.spec.count,
            start=plan.spec.start,
            index=executor._index_bit(plan),
        )


def _index_bit(plan: QueryPlan) -> bool:
    """``0x052D`` 的指数位：按代码段推断，``options["index"]`` 可显式覆盖。

    指数 K 线的每条记录尾部多 4 字节（上涨/下跌家数），股票没有。这个位不开，
    解码器就不消费那 4 字节，于是**下一条**记录的 ``datetime`` 从尾巴上开始读——
    整条记录流从此错位：日期变成 ``10112-28-10`` / ``428119-93-66``，成交量变成
    1e40 量级。错位还是混沌的（LEB128 变长，偶尔会自己对齐回来），实测
    ``sh000001`` 取 3 根时"第 1、3 根全错、第 2 根恰好对"——比整页全错更难发现。

    历史缺陷：这一位只能由调用方显式传，而门面 :meth:`Client.bars` 根本没有这个
    形参，于是**通过主入口取指数日线必然错位**，且错得不响（除非值域尺子恰好抓到
    那个日期）。:func:`atst.universe.classify_with_index` 早就按同一套段表判好了
    这个位，却是全仓无人调用的孤儿逻辑——这里把它接上。
    """
    from ..universe import classify_with_index

    explicit = plan.spec.options.get("index")
    if explicit is None:
        return classify_with_index(plan.spec.symbols[0])[1]
    if not isinstance(explicit, bool):
        raise ValidationError(
            f"options['index'] 必须是 bool，收到 {type(explicit).__name__}",
            context={"option": "index", "value_type": type(explicit).__name__},
        )
    return explicit


def _tdx_snapshot(executor, plan: QueryPlan) -> Any:
    with executor._client_session(executor._tdx_client(executor._hop_timeout(plan))) as client:
        return client.snapshot(plan.spec.symbols[0], as_format="dict")


def _tdx_minute(executor, plan: QueryPlan) -> Any:
    with executor._client_session(executor._tdx_client(executor._hop_timeout(plan))) as client:
        return client.minute_today(plan.spec.symbols[0])


def _tdx_trades(executor, plan: QueryPlan) -> Any:
    with executor._client_session(executor._tdx_client(executor._hop_timeout(plan))) as client:
        return client.trade_today(
            plan.spec.symbols[0],
            start=plan.spec.start,
            count=plan.spec.count,
        )


def _tdx_security_count(executor, plan: QueryPlan) -> Any:
    with executor._client_session(executor._tdx_client(executor._hop_timeout(plan))) as client:
        return client.security_count(plan.spec.options.get("market", 0))


def _tdx_security_list(executor, plan: QueryPlan) -> Any:
    with executor._client_session(executor._tdx_client(executor._hop_timeout(plan))) as client:
        return client.security_list(plan.spec.options.get("market", 0), plan.spec.start)


def _local_bars(executor, plan: QueryPlan) -> Any:
    if not executor.vipdoc_root:
        raise ValidationError(
            "local_vipdoc Provider 需要显式 vipdoc_root",
            context={"provider": "local_vipdoc", "channel": "vipdoc"},
        )
    if plan.spec.adjustment:
        raise ValidationError(
            "local_vipdoc Direct bars 不支持静默复权",
            context={
                "provider": "local_vipdoc",
                "channel": "vipdoc",
                "adjustment": plan.spec.adjustment,
            },
        )

    from ..domain.symbol import split_symbol
    from ..reader import DayBarReader, MinBarReader

    market, code = split_symbol(plan.spec.symbols[0])
    if market not in {"sh", "sz", "bj"}:
        raise ValidationError("local_vipdoc Direct bars 仅支持沪深北本地目录")

    root = Path(executor.vipdoc_root)
    canonical = f"{market}{code}"
    if plan.spec.period == "day":
        rows = DayBarReader().read(
            root / market / "lday" / f"{canonical}.day",
            output="model",
        )
    elif plan.spec.period == "1min":
        rows = MinBarReader(profile="a_share_min", interval=1).read(
            root / market / "minline" / f"{canonical}.lc1",
            output="model",
        )
    elif plan.spec.period == "5min":
        rows = MinBarReader(profile="a_share_min", interval=5).read(
            root / market / "fzline" / f"{canonical}.lc5",
            output="model",
        )
    else:
        raise ValidationError("local_vipdoc Direct bars 仅支持 day/1min/5min")

    if plan.spec.start:
        end = max(0, len(rows) - plan.spec.start)
        begin = max(0, end - plan.spec.count) if plan.spec.count else 0
        return rows[begin:end]
    return rows[-plan.spec.count :] if plan.spec.count else rows


def _web_quotes(executor, plan: QueryPlan) -> Any:
    """Web 报价一跳：源名取 `plan.provider`，构造即单源，不存在换源下一步。

    刻意不复用公开便捷函数 `atst.sources.get_quotes`——它在 `source` 缺省时会 new 一个
    `WebQuoteClient`，按 `web.enabled_sources` 顺序降级，而有序降级是内核禁止的形状。

    走 :meth:`~atst.sources._base_core.BaseWebSource.fetch_quotes` 而不是
    :meth:`~atst.sources._base_core.BaseWebSource.fetch`：后者不传参时按子类默认
    出口解析，百度财经的默认分组是 K 线——`quotes(provider="baidu")` 曾合法
    返回 250 根日线冒充五档快照（帧合法、内容错能力）。``fetch_quotes`` 显式
    下发 ``kind="quote"``，URL 构造与解析两侧同口径路由到行情快照。
    """
    from ..sources import create_source, normalize_symbol

    src = create_source(plan.provider, timeout=executor._hop_timeout(plan))
    try:
        return src.fetch_quotes([normalize_symbol(s) for s in plan.spec.symbols])
    finally:
        src.close()


def _tencent_bars(executor, plan: QueryPlan) -> Any:
    from ..sources import create_source

    source_name = "minute_kline" if plan.channel == "minute_kline" else "kline"
    src = create_source(source_name, timeout=executor._hop_timeout(plan))
    try:
        if plan.channel == "minute_kline":
            if plan.spec.adjustment:
                raise ValidationError("Tencent minute_kline 不支持复权参数")
            return src.fetch_bars(  # type: ignore[attr-defined]
                plan.spec.symbols[0],
                period=plan.spec.period,
                count=plan.spec.count,
            )
        return src.fetch_bars(  # type: ignore[attr-defined]
            plan.spec.symbols[0],
            period=plan.spec.period,
            count=plan.spec.count,
            adjust=plan.spec.adjustment,
        )
    finally:
        src.close()


def _sina_bars(executor, plan: QueryPlan) -> Any:
    from ..sources.sina.adapters import SinaHistoryKlineSource

    src = SinaHistoryKlineSource(timeout=executor._hop_timeout(plan))
    try:
        return src.fetch_bars(
            plan.spec.symbols[0],
            period=plan.spec.period,
            count=plan.spec.count,
            adjust=plan.spec.adjustment,
        )
    finally:
        src.close()


def _eastmoney_bars(executor, plan: QueryPlan) -> Any:
    from ..sources.eastmoney.adapters import EastmoneyHistoryKlineSource

    src = EastmoneyHistoryKlineSource(timeout=executor._hop_timeout(plan))
    try:
        return src.fetch_bars(
            plan.spec.symbols[0],
            period=plan.spec.period,
            count=plan.spec.count,
            adjust=plan.spec.adjustment,
        )
    finally:
        src.close()


def _baidu_bars(executor, plan: QueryPlan) -> Any:
    from ..sources.baidu.adapters import BaiduSource

    if plan.spec.adjustment:
        raise ValidationError("Baidu Direct bars 不支持复权参数")
    src = BaiduSource(timeout=executor._hop_timeout(plan))
    try:
        return src.fetch_kline(
            plan.spec.symbols[0],
            period=plan.spec.period,
            count=plan.spec.count,
        )
    finally:
        src.close()
