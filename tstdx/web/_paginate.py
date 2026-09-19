# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""腾讯 K 线族共享的分页拉取 / 行解析辅助（v9 Q4-1）。

此前 :class:`~tstdx.web.adapters.KlineSource`（fqkline 日/周/月/分线）与
:class:`~tstdx.web.adapters_ext.MinuteKlineSource`（mkline 分钟线）各自持有
一份结构雷同的 ``fetch_bars`` / ``parse_bars``：JSON 解码报错、按市场缩放
成交量、行 → :class:`~tstdx.domain.models.Bar` 组装，以及超上限时按日期
向前翻页拼接去重。本模块将其上收为一处实现，两源仅保留各自的差异点
（period 别名映射、amount=0 告警、翻页开关），方法签名与行为逐字等价。
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from typing import Any

from ..diagnostics import WarningCode, record_warning
from ..domain.models import Bar
from ..errors import SourceDeprecated
from .base import num_f as _f

logger = logging.getLogger(__name__)

__all__ = [
    "decode_kline_payload",
    "fetch_bars_paged",
    "market_vol_scale",
    "rows_to_bars",
]


def decode_kline_payload(text: str, *, msg: str, source: str) -> dict[str, Any]:
    """把 K 线响应文本解码为 JSON dict；非 JSON 抛 :class:`SourceDeprecated`。"""
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise SourceDeprecated(
            msg, context={"source": source, "sample": text[:200]}, cause=exc
        ) from exc


def market_vol_scale(symbol: str) -> float:
    """成交量单位按市场区分：A 股腾讯返回「手」需 ×100 到股。

    港股 / 美股腾讯直接返回「股」，不缩放。
    """
    return 1.0 if symbol[:2].lower() in ("hk", "us") else 100.0


def rows_to_bars(
    rows: list[Any],
    *,
    symbol: str,
    with_amount: bool,
) -> list[Bar]:
    """K 线行数组 → ``list[Bar]``。

    行结构 ``[datetime, open, close, high, low, volume, (amount | 元数据dict), ...]``。

    Parameters
    ----------
    with_amount:
        ``True``（fqkline）时第 7 列为成交额，缺失 / 非数值置 ``0.0``
        （不可得即置 0，不捏造），并跳过 ``len(r) < 6`` 的短行；
        ``False``（mkline）时无成交额列（``amount`` 恒 ``0.0``），
        且额外要求行必须为 list/tuple。
    """
    vol_scale = market_vol_scale(symbol)
    bars: list[Bar] = []
    for r in rows:
        if with_amount:
            if len(r) < 6:
                continue
            raw_vol = _f(r[5])
            raw_amt = r[6] if len(r) > 6 else None
            amount = _f(raw_amt) if isinstance(raw_amt, (int, float, str)) else 0.0
            bars.append(
                Bar(
                    datetime=str(r[0]),
                    open=_f(r[1]),
                    close=_f(r[2]),
                    high=_f(r[3]),
                    low=_f(r[4]),
                    volume=int(round(raw_vol * vol_scale)),
                    amount=amount,
                )
            )
        else:
            if not isinstance(r, (list, tuple)) or len(r) < 6:
                continue
            bars.append(
                Bar(
                    datetime=str(r[0]),
                    open=_f(r[1]),
                    close=_f(r[2]),
                    high=_f(r[3]),
                    low=_f(r[4]),
                    volume=int(round(_f(r[5]) * vol_scale)),
                )
            )
    return bars


def warn_amount_all_zero(bars: list[Bar], symbol: str) -> None:
    """A5：整批 ``amount`` 恒为 0（区别于个别缺失）时一次性 UserWarning。"""
    if bars and all(b.amount == 0.0 for b in bars):
        record_warning(
            WarningCode.WEB_TENCENT_AMOUNT_ALL_ZERO,
            f"腾讯 K 线 {symbol} 共 {len(bars)} 条 amount 均为 0"
            "（腾讯接口本周期不返回成交额字段），成交额不可用于计算，"
            "请改用分线或换源",
            stacklevel=3,
        )


def fetch_bars_paged(
    *,
    symbol: str,
    count: int,
    max_per_req: int,
    fetch_page: Callable[[int, str | None], list[Bar]],
    paging: bool,
    empty_error: str | None,
    source: str,
    log_label: str,
) -> list[Bar]:
    """K 线分页拉取器：单请求快路径 + 超上限按日期向前翻页拼接。

    Parameters
    ----------
    fetch_page:
        ``(seg, end) -> list[Bar]``：请求 ``seg`` 根、右边界 ``end``
        （YYYY-MM-DD，可为空）的一页 K 线。
    paging:
        是否支持 ``end`` 向前翻页。``False`` 时仅单请求快路径
        （如 mkline：上游不支持 ``end``，超限由调用方在
        ``build_url`` 钳制并告警）。
    empty_error:
        翻页路径全部失败（拼接结果为空）时抛出的
        :class:`SourceDeprecated` 消息；``None`` 表示空结果原样返回
        （mkline 语义：空数据由 ``parse_bars`` 的错误码分支处理）。
    log_label:
        翻页完成日志前缀（如 ``"腾讯 K 线"``）。

    C9：``end`` 为含边界（会重叠 1 根），请求 ``seg+1`` 补偿，
    上游若忽略 ``end``（不前进）则「无新增即停」，绝不死循环。
    """
    if not paging or count <= max_per_req:
        return fetch_page(count, None)

    merged: dict[str, Bar] = {}  # datetime → Bar（setdefault 保序去重）
    end: str | None = None
    while len(merged) < count:
        seg = min(count - len(merged) + (1 if end else 0), max_per_req)
        bars = fetch_page(seg, end)
        if not bars:
            break  # 历史耗尽（早于上市日）
        before = len(merged)
        for b in bars:
            merged.setdefault(str(b.datetime), b)
        if len(merged) == before:
            break  # 无新增 → 历史耗尽或上游不支持 end 翻页
        end = str(bars[0].datetime).split(" ")[0]  # 最早一根的日期
    out = sorted(merged.values(), key=lambda b: str(b.datetime))
    if len(out) > count:
        out = out[len(out) - count :]  # 重叠补偿可能超收，裁剪到最新 count 根
    if not out and empty_error is not None:
        raise SourceDeprecated(
            empty_error, context={"source": source, "symbol": symbol, "requested": count}
        )
    logger.info(
        "%s %s 超限自动分段完成：请求 %d 根，实际返回 %d 根",
        log_label,
        symbol,
        count,
        len(out),
    )
    return out
