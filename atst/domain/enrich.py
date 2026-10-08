"""宽表日线：把「K 线 + 估值序列」拼成一行一交易日的宽表。

为什么单独成模块：拼装规则里有**四条口径**是容易被写错、且错了不会报错的
（前收盘取自哪根、换手率的分母是谁、量比到底是什么、``is_st`` 是不是真值）。
把这四条写成**纯函数**放在域层，是为了让它们能被单测直接钉住——不需要网络、
不需要 Provider，给一组 K 线和一组估值行就出结果。

对齐基准：free-stockdb 的日线宽表（21 字段）。本模块产出 **24 列** = 21 个业务字段 +
3 列**来源标注**（``is_st_source`` / ``vol_ratio_basis``）。标注存在的意义是
"没有真值的地方不冒充真值"。

口径（写死在这里，别处不得另立一份）
------------------------------------
``pre_close``
    上一交易日的收盘价。取窗口**前一根** K 线，不是"循环里的前一根"，也不是
    当日 ``open``。首根没有前一根时为 ``None``（不猜）。

``pct_chg`` / ``amplitude``
    ``(close - pre_close) / pre_close * 100`` 与 ``(high - low) / pre_close * 100``，
    单位是**百分数**（与东财页面一致）。``pre_close`` 为 ``None`` 或 <= 0 时两者
    都是 ``None``——除零与"用错的基准价"都不静默发生。

``turnover``（换手率，%）
    ``raw_volume / float_share * 100``。``raw_volume`` 是**复权前**的成交量（**股**）、
    ``float_share``（``FREE_SHARES_A``）也是**股**，两者相除即流通盘换手比例，
    口径自洽，**不需要额外接口**。``float_share`` 缺失时为 ``None``。

    **为什么必须是 ``raw_volume`` 而不是 ``volume``**：``AdjustEngine.apply`` 会把
    ``volume`` 乘上成交量因子（后复权因子 < 1），而历史流通股本**不会**跟着变。
    拿复权后的量去除未复权的分母，得到的不是换手率。复权引擎因此把复权前的量留在
    ``bar.extra["raw_volume"]``；本模块优先读它，读不到（未复权输入）才退回 ``volume``。

``vol_ratio``（量比）
    ``raw_volume_t / mean(raw_volume_{t-5..t-1})``，**日频近似**。真实的量比是盘中概念
    （当日每分钟均量 / 过去 5 日每分钟均量），需要分钟数据；日频只能算成交量的
    相对倍数。因此每行的 ``vol_ratio_basis`` 恒为 ``"daily_approx"``，
    **不冒充**盘中量比。前 5 日不足时为 ``None``。分子分母同取 ``raw_volume``：
    跨除权日时复权因子逐根不同，用复权量会让量比在事件日附近跳变。

    **当日无成交时为 ``None``，不是 ``0.0``**：零成交（停牌、或盘中未开盘时上游回的
    那根占位 bar，实测 TDX ``bars`` 会回 ``volume=0`` 的当日 bar）意味着"量比"这件事
    没有定义，报 ``0.0`` 会被读成"只有正常水平的 0%"。这与 ``turnover`` 的口径一致
    （那里同样不产出 ``0.0``）。

``is_st``
    由估值行自带的证券简称（``SECURITY_NAME_ABBR``）判**前缀**是不是
    ``ST`` / ``*ST`` / ``SST`` / ``S*ST``。这是**启发式**，不是交易所的风险警示名单
    ——A 股没有免费的 ``is_st`` 历史基准日源（``st_list`` 自己就写着"最新快照，无历史
    基准日"）。所以 ``is_st_source`` 恒为 ``"name_heuristic"``，调用方必须能看见它。
    **拿不到简称时 ``is_st`` 是 ``None``（不是 ``False``）**，``is_st_source`` 为
    ``"unknown"``——"不知道"与"不是 ST"是两件事，不许混。

估值连接
--------
按 ``TRADE_DATE`` **精确等值**连接（不是 as-of 前向填充）。估值行与 K 行同属一个
交易日，等值连接既不会引入未来数据，也不会把昨天的估值当成今天的。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Any

__all__ = [
    "ENRICHED_FIELDS",
    "ST_PREFIXES",
    "VOL_RATIO_WINDOW",
    "enrich_daily_bars",
]

#: 输出列顺序（宽表的"列契约"：加列必须改这里，否则下游按位置取数会错位）。
ENRICHED_FIELDS: tuple[str, ...] = (
    "date",
    "open",
    "high",
    "low",
    "close",
    "pre_close",
    "pct_chg",
    "amplitude",
    "volume",
    "amount",
    "turnover",
    "vol_ratio",
    "pe_ttm",
    "pb",
    "ps_ttm",
    "pcf_ocf_ttm",
    "total_share",
    "float_share",
    "total_mv",
    "float_mv",
    "name",
    "is_st",
    "is_st_source",
    "vol_ratio_basis",
)

#: 量比的回看窗口（交易日）。日频近似的惯例是 5 日。
VOL_RATIO_WINDOW: int = 5

#: 风险警示股的简称前缀。只用**前缀**判，不用"名称里含 ST"——后者会把
#: ``STO`` 这类恰好以 ST 开头的英文串也算进去，且无法解释为什么。
ST_PREFIXES: tuple[str, ...] = ("*ST", "S*ST", "SST", "ST")

#: 估值列 → 输出列（真机列名在 2026-10-07 对拍过；大小写容错走 ``_pick``）。
_VALUATION_FIELDS: dict[str, tuple[str, ...]] = {
    "pe_ttm": ("PE_TTM",),
    "pb": ("PB_MRQ",),
    "ps_ttm": ("PS_TTM",),
    "pcf_ocf_ttm": ("PCF_OCF_TTM",),
    "total_share": ("TOTAL_SHARES",),
    "float_share": ("FREE_SHARES_A",),
    "total_mv": ("TOTAL_MARKET_CAP",),
    "float_mv": ("NOTLIMITED_MARKETCAP_A",),
    "name": ("SECURITY_NAME_ABBR", "SECURITY_NAME"),
}


def _pick(row: Mapping[str, Any], *names: str) -> Any:
    """取首个存在的列名（大小写不敏感，容忍上游改名）。"""
    lowered = {str(k).lower(): v for k, v in row.items()}
    for name in names:
        if name in row and row[name] is not None:
            return row[name]
        value = lowered.get(name.lower())
        if value is not None:
            return value
    return None


def _num(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number


def _day(value: Any) -> str:
    """``'2026-09-30 00:00:00'`` / ``'2026-09-30 15:00'`` → ``'2026-09-30'``。"""
    text = str(value or "").strip()
    if not text:
        return ""
    return text.split(" ")[0].split("T")[0]


def _close_of(bar: Any) -> float | None:
    value = getattr(bar, "close", None)
    if value is None and isinstance(bar, Mapping):
        value = bar.get("close")
    return _num(value)


def _field_of(bar: Any, name: str) -> Any:
    if isinstance(bar, Mapping):
        return bar.get(name)
    return getattr(bar, name, None)


def _extra_of(bar: Any) -> Mapping[str, Any]:
    extra = _field_of(bar, "extra")
    return extra if isinstance(extra, Mapping) else {}


def _raw_volume_of(bar: Any) -> float:
    """**复权前**的成交量；没有复权痕迹时就是 ``volume`` 本身。

    ``AdjustEngine.apply`` 把复权前的量留在 ``extra["raw_volume"]``，因为复权后的
    ``volume`` 与历史流通股本不是同一个尺度——拿它算换手率/量比得到的数没有含义。
    """
    raw = _extra_of(bar).get("raw_volume")
    value = _num(raw)
    if value is not None:
        return value
    return _num(_field_of(bar, "volume")) or 0.0


def _looks_like_st(name: str) -> bool:
    """简称是否带风险警示前缀（``ST`` / ``*ST`` / ``SST`` / ``S*ST``）。"""
    upper = name.strip().upper()
    return bool(upper) and upper.startswith(ST_PREFIXES)


def enrich_daily_bars(
    bars: Sequence[Any],
    valuation_rows: Iterable[Mapping[str, Any]] = (),
    *,
    float_share_fallback: float | None = None,
) -> list[dict[str, Any]]:
    """拼宽表日线。

    Parameters
    ----------
    bars:
        按时间**升序**的日 K 线（:class:`~atst.domain.models.Bar` 或等价 dict）。
        多传一根（窗口前一根）能让首行也拿到真实的 ``pre_close``，调用方负责
        事后裁掉那根——见 :mod:`atst.runtime.executor` 的 ``daily_enriched``。
    valuation_rows:
        东财 ``RPT_VALUEANALYSIS_DET`` 原始行，按 ``TRADE_DATE`` 降序。
    float_share_fallback:
        估值行缺 ``FREE_SHARES_A`` 时用它当分母（例如 ``stock_base_info`` 的当前
        流通股本）。**仅当调用方知道自己在做什么时才传**：历史行用当前股本算
        换手率是近似值。
    """
    #: 估值按交易日建索引：等值连接，不做前向填充（防未来数据）。
    val_by_day: dict[str, dict[str, Any]] = {}
    for row in valuation_rows:
        if not isinstance(row, Mapping):
            continue
        day = _day(_pick(row, "TRADE_DATE", "trade_date"))
        if day and day not in val_by_day:
            val_by_day[day] = dict(row)

    out: list[dict[str, Any]] = []
    for i, bar in enumerate(bars):
        date = _day(_field_of(bar, "datetime") or _field_of(bar, "date"))
        open_ = _num(_field_of(bar, "open"))
        high = _num(_field_of(bar, "high"))
        low = _num(_field_of(bar, "low"))
        close = _close_of(bar)
        volume = _num(_field_of(bar, "volume")) or 0.0
        #: 换手率 / 量比的分母分子都必须是**复权前**的量（见模块 docstring）。
        raw_volume = _raw_volume_of(bar)
        amount = _num(_field_of(bar, "amount"))

        pre_close = _close_of(bars[i - 1]) if i > 0 else None
        pct_chg = None
        amplitude = None
        if pre_close and pre_close > 0 and close is not None:
            pct_chg = (close - pre_close) / pre_close * 100.0
            if high is not None and low is not None:
                amplitude = (high - low) / pre_close * 100.0

        val = val_by_day.get(date, {})
        enriched_val: dict[str, Any] = {}
        for out_name, names in _VALUATION_FIELDS.items():
            if out_name == "name":
                enriched_val["name"] = str(_pick(val, *names) or "") if val else ""
                continue
            enriched_val[out_name] = _num(_pick(val, *names)) if val else None

        float_share = enriched_val.get("float_share")
        if float_share is None:
            float_share = float_share_fallback
        turnover = None
        if float_share and float_share > 0 and raw_volume:
            turnover = raw_volume / float_share * 100.0

        #: 量比：日频近似（前 VOL_RATIO_WINDOW 个交易日的均量）
        vol_ratio = None
        if i >= VOL_RATIO_WINDOW and raw_volume > 0:
            #: 前 5 日里出现停牌（0 成交）就不算——用含 0 的均值会把量比虚高。
            window_vols = [_raw_volume_of(bars[j]) for j in range(i - VOL_RATIO_WINDOW, i)]
            if min(window_vols) > 0:
                mean_vol = sum(window_vols) / len(window_vols)
                if mean_vol > 0:
                    vol_ratio = raw_volume / mean_vol

        name = enriched_val.get("name") or ""
        #: 拿不到简称 → ``None``（"不知道"），不是 ``False``（"不是 ST"）。
        is_st = _looks_like_st(name) if name else None

        out.append(
            {
                "date": date,
                "open": open_,
                "high": high,
                "low": low,
                "close": close,
                "pre_close": pre_close,
                "pct_chg": pct_chg,
                "amplitude": amplitude,
                "volume": volume,
                "amount": amount,
                "turnover": turnover,
                "vol_ratio": vol_ratio,
                "pe_ttm": enriched_val.get("pe_ttm"),
                "pb": enriched_val.get("pb"),
                "ps_ttm": enriched_val.get("ps_ttm"),
                "pcf_ocf_ttm": enriched_val.get("pcf_ocf_ttm"),
                "total_share": enriched_val.get("total_share"),
                "float_share": enriched_val.get("float_share"),
                "total_mv": enriched_val.get("total_mv"),
                "float_mv": enriched_val.get("float_mv"),
                "name": name,
                "is_st": is_st,
                "is_st_source": "name_heuristic" if name else "unknown",
                "vol_ratio_basis": "daily_approx",
            }
        )
    return out
