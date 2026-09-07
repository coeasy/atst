# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""外部补充数据源桥接门面（原生命名）。

定位：兼容门面（吸收自 pytdx 语义），生产建议用 :class:`~tstdx.facade.api.UnifiedQuoteAPI` / :class:`~tstdx.client.TdxClient`。

在 :class:`~tstdx.client.TdxClient` 之上提供一套**原生命名**的桥接门面，
用于把外部补充行情 / 基本数据接入 tstdx 自有协议栈。方法名统一为 tstdx
原生命名，不再沿用任何第三方客户端的方法或字段约定。

方法映射（tstdx 原生）
----------------------
* :meth:`BridgeClient.bars`          → :meth:`~tstdx.client.TdxClient.bars`
* :meth:`BridgeClient.quotes`        → :meth:`~tstdx.client.TdxClient.quotes`
* :meth:`BridgeClient.minute`        → :meth:`~tstdx.client.TdxClient.minute_today`
* :meth:`BridgeClient.open`          → :meth:`~tstdx.client.TdxClient.open`（惰性建连）
* :meth:`BridgeClient.close`         → :meth:`~tstdx.client.TdxClient.close`

``FREQUENCY_ALIASES`` 记录常见周期字符串别名 → tstdx period 名称，便于迁移。
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from ..errors import CompatibilityWarning

__all__ = [
    "BridgeClient",
    "bridge_client",
    "FREQUENCY_ALIASES",
]

#: 周期字符串别名 → tstdx period 名称。
FREQUENCY_ALIASES: dict[str, str] = {
    "day": "day",
    "week": "week",
    "month": "month",
    "1m": "1min",
    "5m": "5min",
    "15m": "15min",
    "30m": "30min",
    "60m": "60min",
    # 兼容别名
    "d": "day",
    "w": "week",
    "m": "month",
}


def bridge_client(**client_kwargs: Any) -> BridgeClient:
    """返回 :class:`BridgeClient` 实例。"""
    return BridgeClient(**client_kwargs)


class BridgeClient:
    """外部补充数据源桥接门面（原生命名）。

    .. deprecated:: v7
        兼容门面：请改用 :class:`~tstdx.sources.DataSourceRouter`
        或 :class:`~tstdx.client.TdxClient` + Web 源组合；v8 评估删除。
    """

    def __init__(self, **client_kwargs: Any) -> None:
        self._client: Any = None
        self._host: str | None = None
        self._port: int | None = None
        self._client_kwargs = client_kwargs

    # -- 内部辅助 ----------------------------------------------------------- #
    @property
    def _active_client(self) -> Any:
        """惰性创建 / 返回 TdxClient。"""
        if self._client is None:
            from ..client import TdxClient

            self._client = TdxClient(**self._client_kwargs)
        return self._client

    # -- 生命周期 ----------------------------------------------------------- #
    def open(self, host: str | None = None, port: int | None = None) -> BridgeClient:
        """建立连接（可选指定主站地址）。

        无参数时复用 tstdx 内置候选池；本门面额外支持 ``host`` / ``port``。
        """
        self._host = host
        self._port = port or 7709
        from ..client import TdxClient

        if host:
            self._client = TdxClient(hosts=[f"{host}:{self._port}"], **self._client_kwargs)
        else:
            self._client = TdxClient(**self._client_kwargs)
        return self

    def close(self) -> None:
        """关闭连接。"""
        if self._client is not None:
            self._client.close()
        self._client = None

    @property
    def connected(self) -> bool:
        """客户端对象已创建（非已建连）。"""
        return self._client is not None

    @property
    def host(self) -> str | None:
        """当前主站地址（未指定时为 ``None``）。"""
        return self._host

    @property
    def port(self) -> int | None:
        """当前主站端口（未指定时为 ``None``）。"""
        return self._port

    def __enter__(self) -> BridgeClient:
        if self._client is None:
            _ = self._active_client  # 惰性建连副作用
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    # -- K 线 --------------------------------------------------------------- #
    def bars(
        self,
        symbols: str | Sequence[str],
        period: str = "day",
        count: int = 80,
    ) -> list[dict[str, Any]] | dict[str, list[dict[str, Any]]]:
        """获取 K 线 / 分钟线。

        Parameters
        ----------
        symbols:
            单只或批量证券代码。
        period:
            周期字符串，如 ``"day"`` / ``"week"`` / ``"1m"`` / ``"5m"`` /
            ``"15m"`` / ``"30m"`` / ``"60m"``，见 :data:`FREQUENCY_ALIASES`。
        count:
            返回根数，默认 80。

        Returns
        -------
        单只 → ``list[dict]``；批量 → ``dict[symbol, list[dict]]``。
        """
        client = self._active_client
        if isinstance(symbols, str):
            return client.bars(
                symbols, period=_resolve_period(period), count=count, as_format="dict"
            )
        out: dict[str, list[dict[str, Any]]] = {}
        for sym in symbols:
            out[sym] = client.bars(
                sym, period=_resolve_period(period), count=count, as_format="dict"
            )
        return out

    # -- 实时行情 ----------------------------------------------------------- #
    def quotes(self, symbols: str | Sequence[str]) -> dict[str, Any] | list[dict[str, Any]]:
        """实时行情快照。

        Parameters
        ----------
        symbols:
            单只或批量证券代码。

        Returns
        -------
        单只 → ``dict``（单个 Quote）；批量 → ``list[dict]``。
        """
        if isinstance(symbols, str):
            rows = self._active_client.quotes([symbols], as_format="dict")
            return rows[0] if rows else {}
        return self._active_client.quotes(list(symbols), as_format="dict")

    # -- 分时数据 ----------------------------------------------------------- #
    def minute(
        self, symbols: str | Sequence[str], count: int = 48
    ) -> list[dict[str, Any]] | dict[str, list[dict[str, Any]]]:
        """当日分时数据（命令 ``0x0537``）。

        Parameters
        ----------
        symbols:
            单只或批量证券代码。
        count:
            返回数据点数；0 表示全部。

        Returns
        -------
        单只 → ``list[dict]``；批量 → ``dict[symbol, list[dict]]``。
        """
        client = self._active_client
        if isinstance(symbols, str):
            rows = client.minute_today(symbols)
            return rows[:count] if count > 0 else rows
        out: dict[str, list[dict[str, Any]]] = {}
        for sym in symbols:
            rows = client.minute_today(sym)
            out[sym] = rows[:count] if count > 0 else rows
        return out


def _resolve_period(period: str) -> str:
    """把周期字符串别名解析为 tstdx period 名称。"""
    key = (period or "day").strip().lower()
    resolved = FREQUENCY_ALIASES.get(key)
    if resolved is None:
        import warnings

        warnings.warn(
            f"未知的 period 别名 {period!r}，回退到 day",
            CompatibilityWarning,
            stacklevel=2,
        )
        resolved = "day"
    return resolved
