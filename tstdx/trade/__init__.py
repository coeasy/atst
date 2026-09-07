# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""TDX 交易协议探测与模拟器（P2-1 · 独立可选模块）。

**范围红线（务必阅读）**
------------------------
* **不连接真实券商交易通道**：:class:`SocketTransport` 仅占位，任何 ``connect``
  调用抛 :class:`TradingUnavailable`；
* **不下真实委托、不接真实资金账户**：默认传输 :class:`SimTransport` 为纯内存
  模拟券商（账本 / 持仓 / 委托均在进程内），用于协议回路研究与测试；
* **帧布局为洁净室推断（draft）**：真实券商侧布局封装于闭源 ``trade.dll``，
  本模块的帧格式是自洽的占位实现，待真机样本定标后翻转状态
  （见 PROTOCOL_SPEC/TRADE/）。

用法示例
--------
.. code-block:: python

    from tstdx.trade import TradeClient, ORDER_SIDE_BUY

    with TradeClient() as c:
        c.login("100001", "123456")
        print(c.query_cash())   # {'available_cash': 1000000, ...}
        c.send_order("sh600519", ORDER_SIDE_BUY, price=150000, quantity=100)
        print(c.query_orders())
        c.cancel_order(1)
"""

from __future__ import annotations

from . import constants  # noqa: F401
from .client import SocketTransport, TradeClient, normalize_code
from .constants import (
    CMD_CANCEL_ORDER,
    CMD_HEARTBEAT,
    CMD_LOGIN,
    CMD_LOGOUT,
    CMD_QUERY,
    CMD_SEND_ORDER,
    DEFAULT_TRADE_PORT,
    ORDER_SIDE_BUY,
    ORDER_SIDE_SELL,
    ORDER_STATUS_CANCELLED,
    ORDER_STATUS_FILLED,
    ORDER_STATUS_PARTIAL,
    ORDER_STATUS_SUBMITTED,
    PRICE_SCALE,
    PRICE_TYPE_LIMIT,
    PRICE_TYPE_MARKET,
    QUERY_CATEGORY_CANCELABLE_ORDER,
    QUERY_CATEGORY_CASH,
    QUERY_CATEGORY_DEAL_OF_TODAY,
    QUERY_CATEGORY_MARGIN_LOAN_BALANCE,
    QUERY_CATEGORY_NEW_STOCK_HIT,
    QUERY_CATEGORY_NEW_STOCK_NUMBER,
    QUERY_CATEGORY_NEW_STOCKS,
    QUERY_CATEGORY_NEW_STOCKS_QUOTA,
    QUERY_CATEGORY_OPERABLE_MARGIN_STOCK,
    QUERY_CATEGORY_ORDER_OF_TODAY,
    QUERY_CATEGORY_SHAREHOLDERS_CODE,
    QUERY_CATEGORY_STOCK_LOAN_BALANCE,
    QUERY_CATEGORY_STOCKS,
    TRADE_FAMILY,
)
from .errors import TradeError, TradeNotLoggedIn, TradeRejected, TradingUnavailable
from .frames import (
    build_cancel_body,
    build_cancel_response,
    build_heartbeat_response,
    build_login_body,
    build_login_response,
    build_order_body,
    build_order_response,
    build_query_body,
    build_query_response,
    build_request,
    build_response,
    parse_cancel_body,
    parse_cancel_response,
    parse_heartbeat_response,
    parse_login_body,
    parse_login_response,
    parse_order_body,
    parse_order_response,
    parse_query_body,
    parse_query_response,
    parse_request,
    parse_response,
)
from .security import OBFUSCATION_KEY, deobfuscate_password, obfuscate_password
from .simulator import DEFAULT_ACCOUNTS, Deal, Order, Position, SimTransport, TradeSimulator

__all__ = [
    # 客户端 / 传输
    "TradeClient",
    "SimTransport",
    "SocketTransport",
    "TradeSimulator",
    "normalize_code",
    # 错误
    "TradeError",
    "TradeNotLoggedIn",
    "TradeRejected",
    "TradingUnavailable",
    # 常量
    "TRADE_FAMILY",
    "DEFAULT_TRADE_PORT",
    "CMD_LOGIN",
    "CMD_HEARTBEAT",
    "CMD_LOGOUT",
    "CMD_QUERY",
    "CMD_SEND_ORDER",
    "CMD_CANCEL_ORDER",
    "QUERY_CATEGORY_CASH",
    "QUERY_CATEGORY_STOCKS",
    "QUERY_CATEGORY_ORDER_OF_TODAY",
    "QUERY_CATEGORY_DEAL_OF_TODAY",
    "QUERY_CATEGORY_CANCELABLE_ORDER",
    "QUERY_CATEGORY_SHAREHOLDERS_CODE",
    "QUERY_CATEGORY_MARGIN_LOAN_BALANCE",
    "QUERY_CATEGORY_STOCK_LOAN_BALANCE",
    "QUERY_CATEGORY_OPERABLE_MARGIN_STOCK",
    "QUERY_CATEGORY_NEW_STOCKS",
    "QUERY_CATEGORY_NEW_STOCKS_QUOTA",
    "QUERY_CATEGORY_NEW_STOCK_NUMBER",
    "QUERY_CATEGORY_NEW_STOCK_HIT",
    "PRICE_TYPE_LIMIT",
    "PRICE_TYPE_MARKET",
    "ORDER_SIDE_BUY",
    "ORDER_SIDE_SELL",
    "ORDER_STATUS_SUBMITTED",
    "ORDER_STATUS_PARTIAL",
    "ORDER_STATUS_FILLED",
    "ORDER_STATUS_CANCELLED",
    "PRICE_SCALE",
    # 口令混淆
    "OBFUSCATION_KEY",
    "obfuscate_password",
    "deobfuscate_password",
    # 帧编解码
    "build_request",
    "parse_request",
    "build_response",
    "parse_response",
    "build_login_body",
    "parse_login_body",
    "build_login_response",
    "parse_login_response",
    "build_heartbeat_response",
    "parse_heartbeat_response",
    "build_query_body",
    "parse_query_body",
    "build_query_response",
    "parse_query_response",
    "build_order_body",
    "parse_order_body",
    "build_order_response",
    "parse_order_response",
    "build_cancel_body",
    "parse_cancel_body",
    "build_cancel_response",
    "parse_cancel_response",
    # 模拟券商领域对象
    "DEFAULT_ACCOUNTS",
    "Position",
    "Order",
    "Deal",
]
