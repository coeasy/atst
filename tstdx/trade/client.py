# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""TDX 交易协议客户端（P2-1 · 仅模拟器传输）。

**范围红线（务必阅读）**
------------------------
* **不连接真实券商交易通道**：:class:`SocketTransport` 仅作接入位占位，
  任何 ``connect`` 调用都会抛 :class:`TradingUnavailable`；
* **不下真实委托、不接真实资金账户**：默认传输 :class:`SimTransport` 为纯内存
  模拟券商（账本 / 持仓 / 委托均在进程内），用于协议回路研究与测试；
* **帧布局为洁净室推断（draft）**：真实券商侧布局封装于闭源 ``trade.dll``，
  本模块帧格式是自洽占位实现，待真机样本定标后翻转（见 PROTOCOL_SPEC/TRADE/）。

用法示例
--------
.. code-block:: python

    from tstdx.trade import TradeClient, ORDER_SIDE_BUY

    with TradeClient() as c:
        c.login("100001", "123456")
        print(c.query_cash())                          # {'available_cash': ...}
        c.send_order("sh600519", ORDER_SIDE_BUY, price=150000, quantity=100)
        c.cancel_order(1)
"""

from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING, Any

from ..errors import ValidationError
from .constants import (
    ORDER_SIDE_BUY,
    ORDER_SIDE_SELL,
    PRICE_TYPE_LIMIT,
    PRICE_TYPE_MARKET,
    QUERY_CATEGORY_CANCELABLE_ORDER,
    QUERY_CATEGORY_CASH,
    QUERY_CATEGORY_DEAL_OF_TODAY,
    QUERY_CATEGORY_ORDER_OF_TODAY,
    QUERY_CATEGORY_SHAREHOLDERS_CODE,
    QUERY_CATEGORY_STOCKS,
)
from .errors import TradingUnavailable
from .simulator import SimTransport

if TYPE_CHECKING:  # pragma: no cover
    from typing_extensions import Self

__all__ = [
    "TradeClient",
    "SocketTransport",
    "normalize_code",
]

#: 证券代码最小长度（支持裸代码 / 带市场前缀如 ``sh600519``）
_MIN_CODE_LEN = 6


def normalize_code(symbol: str) -> str:
    """把任意书写变种的证券代码归一为 6 位数字代码。

    >>> normalize_code("sh600519")
    '600519'
    >>> normalize_code("600519")
    '600519'
    >>> normalize_code("000001.SZ")
    '000001'
    """
    digits = "".join(ch for ch in symbol if ch.isdigit())
    if len(digits) < _MIN_CODE_LEN:
        raise ValidationError(f"证券代码至少 {_MIN_CODE_LEN} 位数字: {symbol!r}")
    return digits[-_MIN_CODE_LEN:]


class TradeClient:
    """TDX 交易协议客户端 —— **默认使用模拟器传输，绝不连接实盘**。"""

    def __init__(self, transport: SimTransport | None = None) -> None:
        self._transport = transport if transport is not None else SimTransport()

    @property
    def transport(self) -> SimTransport:
        return self._transport

    # --- 生命周期 ------------------------------------------------------------ #
    def connect(self) -> Self:
        self._transport.connect()
        return self

    def close(self) -> None:
        self._transport.close()

    def __enter__(self) -> Self:
        return self.connect()

    def __exit__(self, *exc: object) -> None:
        self.close()

    def __del__(self) -> None:  # pragma: no cover - 析构防御
        with contextlib.suppress(Exception):
            self.close()

    # --- 会话 ---------------------------------------------------------------- #
    def login(
        self, account: str, password: str, *, yyb_id: int = 0, version: int = 0
    ) -> dict[str, Any]:
        """登录模拟券商（默认账密表 ``100001 / 123456``）。"""
        return self._transport.login(account, password, yyb_id=yyb_id, version=version)

    def logout(self) -> None:
        self._transport.logout()

    def heartbeat(self) -> bool:
        return self._transport.heartbeat()

    # --- 查询 ---------------------------------------------------------------- #
    def query(self, category: int) -> list[dict[str, Any]]:
        """按类别查询（资金 / 持仓 / 当日委托 / 当日成交 / 可撤单 / 股东代码）。"""
        return self._transport.query(category)

    def query_cash(self) -> dict[str, int]:
        """查询资金账户。"""
        records = self.query(QUERY_CATEGORY_CASH)
        return records[0] if records else {}

    def query_stocks(self) -> list[dict[str, Any]]:
        """查询持仓列表。"""
        return self.query(QUERY_CATEGORY_STOCKS)

    def query_orders(self) -> list[dict[str, Any]]:
        """查询当日委托列表。"""
        return self.query(QUERY_CATEGORY_ORDER_OF_TODAY)

    def query_deals(self) -> list[dict[str, Any]]:
        """查询当日成交列表。"""
        return self.query(QUERY_CATEGORY_DEAL_OF_TODAY)

    def query_cancelable(self) -> list[dict[str, Any]]:
        """查询可撤单列表。"""
        return self.query(QUERY_CATEGORY_CANCELABLE_ORDER)

    def query_shareholders(self) -> list[dict[str, Any]]:
        """查询股东代码。"""
        return self.query(QUERY_CATEGORY_SHAREHOLDERS_CODE)

    # --- 委托 / 撤单 --------------------------------------------------------- #
    def send_order(
        self,
        symbol: str,
        side: int,
        *,
        price_type: int = PRICE_TYPE_LIMIT,
        price: int | None = None,
        quantity: int = 100,
    ) -> dict[str, Any]:
        """委托下单。

        :param symbol: 证券代码（``sh600519`` / ``600519`` 均可）
        :param side: :data:`ORDER_SIDE_BUY` 买入 / :data:`ORDER_SIDE_SELL` 卖出
        :param price_type: :data:`PRICE_TYPE_LIMIT` 限价 / :data:`PRICE_TYPE_MARKET` 市价
        :param price: 限价（**分**）；市价单可传 ``None``
        :param quantity: 委托数量（股）
        """
        code = normalize_code(symbol)
        if price_type == PRICE_TYPE_MARKET and price is None:
            price = 0
        if price is None:
            raise ValidationError("限价单缺少价格")
        return self._transport.send_order(
            code=code,
            side=side,
            price_type=price_type,
            price=price,
            quantity=quantity,
        )

    def buy(
        self,
        symbol: str,
        price: int,
        quantity: int = 100,
        *,
        price_type: int = PRICE_TYPE_LIMIT,
    ) -> dict[str, Any]:
        """买入（限价/市价）。"""
        return self.send_order(
            symbol, ORDER_SIDE_BUY, price_type=price_type, price=price, quantity=quantity
        )

    def sell(
        self,
        symbol: str,
        price: int,
        quantity: int = 100,
        *,
        price_type: int = PRICE_TYPE_LIMIT,
    ) -> dict[str, Any]:
        """卖出（限价/市价）。"""
        return self.send_order(
            symbol, ORDER_SIDE_SELL, price_type=price_type, price=price, quantity=quantity
        )

    def cancel_order(self, order_id: int) -> dict[str, Any]:
        """撤销委托。"""
        return self._transport.cancel_order(order_id=order_id)


class SocketTransport:
    """实盘网络传输 —— **红线占位：默认不可用**。

    仅保留真机定标后的接入位；任何连接尝试都会抛 :class:`TradingUnavailable`，
    确保 tstdx 不会意外连接真实券商交易通道。
    """

    connected = False
    client_id: int | None = None

    def connect(self) -> None:
        raise TradingUnavailable(
            "tstdx 不连接真实券商交易通道（P2-1 红线）：仅协议探测与本地模拟器。"
        )

    def close(self) -> None:
        self.connected = False

    def __enter__(self) -> SocketTransport:
        self.connect()
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
