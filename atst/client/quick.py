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

import threading
from collections.abc import Mapping, Sequence
from typing import Any, cast

from .api import Client, OrchestratedResult

__all__ = ["history", "minute", "quote", "quotes", "trades"]

Record = dict[str, Any]

_AUTO = "auto"

#: T-4：进程级共享 Client。每次快捷调用都 ``Client()`` 会重跑一遍 audit_runtime /
#: \"默认 Provider 恰好连不上\" 的现算路由；而快捷入口从不给 Client 传 ``hosts`` /
#: ``timeout``，所以只复用**默认构造**这一种情形最安全。需要自定义 hosts / timeout
#: 的调用方请自己构造 ``Client``（本模块不提供相应 kwarg，也就没有按 kwargs 分桶
#: 的必要）。初始化走双检锁，多线程首次并发调用也只会造一个实例。
#: 生命周期：这份 Client **故意不随某次调用 close()**——它会让常驻连接池跨调用
#: 复用，这正是快捷入口提速的来源；进程退出时由解释器收尾（与 Client 的所有权
#: 口径一致）。测试仍可 monkeypatch ``_client`` 换成假实例。
_client_lock = threading.Lock()
_shared_client: Client | None = None


def _client() -> Client:
    """唯一构造点：进程内懒加载、线程安全的共享 ``Client``；测试可替换。"""
    global _shared_client
    if _shared_client is None:
        with _client_lock:
            if _shared_client is None:
                _shared_client = Client()
    return _shared_client


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
    adjust: str = "",
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
    client = _client()
    result = client.bars(
        symbol,
        provider=provider,
        period=period,
        count=count,
        start=start,
        adjustment=adjustment,
        adjust=adjust,
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
    client = _client()
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
    client = _client()
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
    client = _client()
    result = client.trades(
        symbol,
        provider=provider,
        start=start,
        count=count,
        currentness=currentness,
    )
    return _records(result.data)
