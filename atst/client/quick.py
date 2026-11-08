# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""零配置快捷入口（最短路径，不是第二套业务入口）。

这些函数只做三件事：构造 :class:`~atst.client.api.Client`、调用既有业务方法、
把结果行摊平成 ``dict`` 列表。路由、参数校验、告警、降级与 provenance 仍全部
由 ``Client`` / ``UnifiedRuntime`` 承担；需要看 ``meta`` 或做跨源编排时请直接
用 ``Client``。

``provider`` 缺省是 ``"auto"``：它是本库显式的策略名，候选顺序按能力面与健康
反馈现算，跨源出数会带 ``auto_provider_fallback`` 告警——零配置起步不该被
"默认 Provider 恰好连不上"卡住。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, cast

from .api import Client, OrchestratedResult

__all__ = ["history", "minute", "quote", "quotes", "trades"]

Record = dict[str, Any]

_AUTO = "auto"


def _client() -> Client:
    """唯一构造点：测试可替换；业务方也可直接构造 ``Client`` 做同等调用。"""
    return Client()


def _data(result: Any) -> Any:
    """``bars`` / ``quotes`` 的返回在传 ``policy`` 时是编排结果；快捷入口不传。"""
    if isinstance(result, OrchestratedResult):
        return result.result.data
    return result.data


def _records(data: Any) -> list[Record]:
    """把 ``Bar`` / ``Quote`` / canonical dict 行统一成纯 ``dict`` 列表。"""
    if data is None:
        return []
    if isinstance(data, Mapping):
        return [dict(data)]
    out: list[Record] = []
    for row in data:
        if isinstance(row, Mapping):
            out.append(dict(row))
        elif hasattr(row, "to_dict"):
            out.append(row.to_dict())
        else:
            out.append(cast(Record, row))
    return out


def history(
    symbol: str,
    *,
    provider: str = _AUTO,
    period: str = "day",
    count: int = 320,
    start: int = 0,
    adjustment: str = "",
    currentness: str = "historical",
    strict: bool = False,
    start_date: str = "",
    end_date: str = "",
    index: bool | None = None,
) -> list[Record]:
    """取 K 线并直接返回 ``dict`` 行（``atst.history("600519")`` 一行起步）。

    参数与 :meth:`Client.bars` 逐字一致；``meta.warnings`` 不在这里返回，
    但发告警的通道没有关闭（``record_warning`` 仍会以 ``UserWarning`` 落到
    调用方进程）。需要严格拒收带瑕疵的结果时传 ``strict=True``。
    """
    with _client() as client:
        result = client.bars(
            symbol,
            provider=provider,
            period=period,
            count=count,
            start=start,
            adjustment=adjustment,
            currentness=currentness,
            strict=strict,
            start_date=start_date,
            end_date=end_date,
            index=index,
        )
    return _records(_data(result))


def quotes(
    symbols: str | Sequence[str],
    *,
    provider: str = _AUTO,
    currentness: str = "live",
    strict: bool = False,
) -> list[Record]:
    """批量实时行情（``atst.quotes(["600519", "000001"])``）。"""
    with _client() as client:
        result = client.quotes(
            symbols,
            provider=provider,
            currentness=currentness,
            strict=strict,
        )
    return _records(_data(result))


def quote(
    symbol: str,
    *,
    provider: str = _AUTO,
    currentness: str = "live",
    strict: bool = False,
) -> Record | None:
    """单只实时行情；源没有给出这一只时返回 ``None``（不是伪造空行）。"""
    rows = quotes(
        symbol,
        provider=provider,
        currentness=currentness,
        strict=strict,
    )
    return rows[0] if rows else None


def minute(
    symbol: str,
    *,
    provider: str = _AUTO,
    currentness: str = "live",
) -> list[Record]:
    """当日分时（``Client.minute`` 的行版本）。"""
    with _client() as client:
        result = client.minute(symbol, provider=provider, currentness=currentness)
    return _records(result.data)


def trades(
    symbol: str,
    *,
    provider: str = _AUTO,
    start: int = 0,
    count: int = 0,
    currentness: str = "live",
) -> list[Record]:
    """逐笔成交（``Client.trades`` 的行版本）。"""
    with _client() as client:
        result = client.trades(
            symbol,
            provider=provider,
            start=start,
            count=count,
            currentness=currentness,
        )
    return _records(result.data)
