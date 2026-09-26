# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""TDX 交易协议常量（P2-1：协议探测 · 洁净室推断）。

命令号 / 查询类别 / 价格类型沿用 TDX 生态公开资料中稳定出现的约定
（如 pytdx :class:`TdxTradeApiParams` 的查询类别编号），本模块将其固化为
常量并统一标注 ``inferred`` 状态 —— 真实券商侧帧布局封装于闭源
``trade.dll``，在真机样本定标前**不得**把这些值当作协议事实使用
（红线详见 :mod:`tstdx.trade` 模块 docstring）。
"""

from __future__ import annotations

__all__ = [
    "CMD_LOGIN",
    "CMD_HEARTBEAT",
    "CMD_LOGOUT",
    "CMD_QUERY",
    "CMD_SEND_ORDER",
    "CMD_CANCEL_ORDER",
    "CMD_NAMES",
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
    "QUERY_CATEGORY_NAMES",
    "PRICE_TYPE_LIMIT",
    "PRICE_TYPE_MARKET",
    "ORDER_SIDE_BUY",
    "ORDER_SIDE_SELL",
    "ORDER_STATUS_SUBMITTED",
    "ORDER_STATUS_PARTIAL",
    "ORDER_STATUS_FILLED",
    "ORDER_STATUS_CANCELLED",
    "CMD_SET",
]


# --- 命令号（推断占位，待真机定标） ---------------------------------------- #
CMD_LOGIN = 0x0001  # 登录
CMD_HEARTBEAT = 0x0002  # 心跳保活
CMD_LOGOUT = 0x0003  # 登出
CMD_QUERY = 0x0100  # 账户/持仓/委托/成交查询
CMD_SEND_ORDER = 0x1000  # 委托下单
CMD_CANCEL_ORDER = 0x1001  # 撤单

CMD_NAMES: dict[int, str] = {
    CMD_LOGIN: "login",
    CMD_HEARTBEAT: "heartbeat",
    CMD_LOGOUT: "logout",
    CMD_QUERY: "query",
    CMD_SEND_ORDER: "send_order",
    CMD_CANCEL_ORDER: "cancel_order",
}

CMD_SET = frozenset(CMD_NAMES)

# --- 查询类别（生态公开约定，pytdx TdxTradeApiParams） --------------------- #
QUERY_CATEGORY_CASH = 0  # 资金
QUERY_CATEGORY_STOCKS = 1  # 股份/持仓
QUERY_CATEGORY_ORDER_OF_TODAY = 2  # 当日委托
QUERY_CATEGORY_DEAL_OF_TODAY = 3  # 当日成交
QUERY_CATEGORY_CANCELABLE_ORDER = 4  # 可撤单
QUERY_CATEGORY_SHAREHOLDERS_CODE = 5  # 股东代码
QUERY_CATEGORY_MARGIN_LOAN_BALANCE = 6  # 融资余额
QUERY_CATEGORY_STOCK_LOAN_BALANCE = 7  # 融券余额
QUERY_CATEGORY_OPERABLE_MARGIN_STOCK = 8  # 可融证券
QUERY_CATEGORY_NEW_STOCKS = 12  # 可申购新股
QUERY_CATEGORY_NEW_STOCKS_QUOTA = 13  # 新股申购额度
QUERY_CATEGORY_NEW_STOCK_NUMBER = 14  # 配号
QUERY_CATEGORY_NEW_STOCK_HIT = 15  # 中签

QUERY_CATEGORY_NAMES: dict[int, str] = {
    QUERY_CATEGORY_CASH: "cash",
    QUERY_CATEGORY_STOCKS: "stocks",
    QUERY_CATEGORY_ORDER_OF_TODAY: "order_of_today",
    QUERY_CATEGORY_DEAL_OF_TODAY: "deal_of_today",
    QUERY_CATEGORY_CANCELABLE_ORDER: "cancelable_order",
    QUERY_CATEGORY_SHAREHOLDERS_CODE: "shareholders_code",
    QUERY_CATEGORY_MARGIN_LOAN_BALANCE: "margin_loan_balance",
    QUERY_CATEGORY_STOCK_LOAN_BALANCE: "stock_loan_balance",
    QUERY_CATEGORY_OPERABLE_MARGIN_STOCK: "operable_margin_stock",
    QUERY_CATEGORY_NEW_STOCKS: "new_stocks",
    QUERY_CATEGORY_NEW_STOCKS_QUOTA: "new_stocks_quota",
    QUERY_CATEGORY_NEW_STOCK_NUMBER: "new_stock_number",
    QUERY_CATEGORY_NEW_STOCK_HIT: "new_stock_hit",
}

# --- 价格类型 -------------------------------------------------------------- #
PRICE_TYPE_LIMIT = 0  # 限价
PRICE_TYPE_MARKET = 1  # 市价

# --- 委托类别（side） ------------------------------------------------------ #
ORDER_SIDE_BUY = 0  # 买入
ORDER_SIDE_SELL = 1  # 卖出

# --- 委托状态 -------------------------------------------------------------- #
ORDER_STATUS_SUBMITTED = 0  # 已报
ORDER_STATUS_PARTIAL = 1  # 部分成交
ORDER_STATUS_FILLED = 2  # 全部成交
ORDER_STATUS_CANCELLED = 3  # 已撤
