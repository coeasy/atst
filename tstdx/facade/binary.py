# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""TDX 二进制协议高层门面（原生命名）。

定位：兼容门面（吸收自 pytdx 语义），生产建议用 :class:`~tstdx.facade.api.UnifiedQuoteAPI` / :class:`~tstdx.client.TdxClient`。

在 :class:`~tstdx.client.TdxClient` 之上提供一套**原生命名**的便捷门面，
覆盖实时行情、K 线、分时、成交明细与证券总数，不再沿用任何第三方客户端的
方法或字段命名。所有协议事实来自 tstdx 自有实现。

方法映射（tstdx 原生）
----------------------
* :meth:`BinaryClient.bars`          → :meth:`~tstdx.client.TdxClient.bars`
* :meth:`BinaryClient.quotes`        → :meth:`~tstdx.client.TdxClient.quotes`
* :meth:`BinaryClient.minute`        → :meth:`~tstdx.client.TdxClient.minute_today`
* :meth:`BinaryClient.trade_details` → :meth:`~tstdx.client.TdxClient.trade_today`
* :meth:`BinaryClient.security_count`→ :meth:`~tstdx.client.TdxClient.security_count`
* :meth:`BinaryClient.open`          → :meth:`~tstdx.client.TdxClient.open`（惰性建连）
* :meth:`BinaryClient.close`         → :meth:`~tstdx.client.TdxClient.close`

``TDX_CATEGORY_TO_PERIOD`` 记录 TDX 协议自身的周期类别码（非任何第三方库约定），
便于从整数类别切换到 tstdx 的 period 名称。
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from ..errors import CompatibilityWarning

__all__ = [
    "BinaryClient",
    "binary_client",
    "TDX_CATEGORY_TO_PERIOD",
]

#: TDX 协议周期类别码 → tstdx period 名称。
#: 0=5min 1=15min 2=30min 3=60min 4=day 5=week 6=month 7=1min
#: 8=1min 9=day 10=season 11=year
TDX_CATEGORY_TO_PERIOD: dict[int, str] = {
    0: "5min",
    1: "15min",
    2: "30min",
    3: "60min",
    4: "day",
    5: "week",
    6: "month",
    7: "1min",
    8: "1min",
    9: "day",
    10: "season",
    11: "year",
}


def binary_client(**client_kwargs: Any) -> BinaryClient:
    """返回 :class:`BinaryClient` 实例。"""
    return BinaryClient(**client_kwargs)


class BinaryClient:
    """TDX 二进制协议高层门面（原生命名）。

    .. deprecated:: v7
        兼容门面：能力与 :class:`~tstdx.client.TdxClient` 重叠且无 CLI 入口。
        新代码请直接使用 :class:`~tstdx.client.TdxClient`；本类仅保留
        原生（通达信二进制客户端）命名兼容，v8 评估删除。
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
    def open(self, host: str | None = None, port: int | None = None) -> BinaryClient:
        """建立连接（可选指定主站地址）。

        ``host=None`` 时使用 tstdx 内置候选池；``port=None`` 时默认 7709。
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

    def __enter__(self) -> BinaryClient:
        if self._client is None:
            _ = self._active_client  # 惰性建连副作用
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    # -- K 线 --------------------------------------------------------------- #
    def bars(
        self,
        symbol: str,
        period: str = "day",
        count: int = 80,
        start: int = 0,
    ) -> list[dict[str, Any]]:
        """获取 K 线 / 分钟线。

        Parameters
        ----------
        symbol:
            证券代码，如 ``"sh600519"`` / ``"600519"``。
        period:
            tstdx period 名称，如 ``"day"`` / ``"5min"`` / ``"1min"`` 等。
        count:
            返回根数，默认 80。
        start:
            起始偏移（0 = 最新 ``count`` 根），默认 0。
        """
        return self._active_client.bars(
            symbol, period=period, count=count, start=start, as_format="dict"
        )

    # -- 实时行情 ----------------------------------------------------------- #
    def quotes(self, symbols: Sequence[str]) -> list[dict[str, Any]]:
        """实时行情快照（多标的）。

        Parameters
        ----------
        symbols:
            证券代码序列，如 ``["sh600519", "sz000001"]``。
        """
        if isinstance(symbols, str):
            symbols = [symbols]
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

    # -- 成交明细 ----------------------------------------------------------- #
    def trade_details(
        self, symbols: str | Sequence[str], date: Any = None
    ) -> list[dict[str, Any]] | dict[str, list[dict[str, Any]]]:
        """当日 / 历史成交明细（命令 ``0x0FC5``）。

        Parameters
        ----------
        symbols:
            单只或批量证券代码。
        date:
            日期（``"YYYYMMDD"`` 字符串或 ``datetime.date``）；
            ``None`` 表示当日。历史日期暂不支持，返回空列表并发出警告。

        Returns
        -------
        单只 → ``list[dict]``；批量 → ``dict[symbol, list[dict]]``。
        """
        if isinstance(symbols, str):
            symbols = [symbols]

        out: dict[str, list[dict[str, Any]]] = {}
        for sym in symbols:
            if _is_today(date):
                out[sym] = self._active_client.trade_today(sym)
            else:
                import warnings

                warnings.warn(
                    f"历史成交明细暂不支持（date={date}），返回空列表",
                    CompatibilityWarning,
                    stacklevel=2,
                )
                out[sym] = []
        if len(out) == 1:
            return out[symbols[0]]
        return out

    # -- 元数据 ------------------------------------------------------------- #
    def security_count(self, market: int | str = 0) -> int:
        """某市场的证券总数（命令 ``0x044E``）。"""
        return self._active_client.security_count(market)


def _is_today(date: Any) -> bool:
    """判断 ``date`` 是否为当日（或 ``None``）。"""
    import datetime

    if date is None:
        return True
    if isinstance(date, datetime.date):
        return date == datetime.date.today()
    try:
        parsed = datetime.datetime.strptime(str(date), "%Y%m%d").date()
        return parsed == datetime.date.today()
    except (ValueError, TypeError):
        return False
