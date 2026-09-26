# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""TradeClient API 与红线测试（P2-1）。

验证客户端语义（登录/查询/委托/撤单/心跳）经 :class:`SimTransport`
协议回路正确抵达模拟券商，并强制验证**实盘红线**（SocketTransport 不可用）。
"""

from __future__ import annotations

import pytest

from tstdx.errors import ValidationError
from tstdx.trade import (
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
    SocketTransport,
    TradeClient,
    TradeNotLoggedIn,
    TradeRejected,
    TradingUnavailable,
    normalize_code,
)

pytestmark = pytest.mark.unit


class TestNormalizeCode:
    def test_bare_code(self) -> None:
        assert normalize_code("600519") == "600519"

    def test_prefixed_code(self) -> None:
        assert normalize_code("sh600519") == "600519"

    def test_suffixed_code(self) -> None:
        assert normalize_code("000001.SZ") == "000001"

    def test_too_short_raises(self) -> None:
        with pytest.raises(ValidationError):
            normalize_code("6005")


class TestClientLifecycle:
    def test_full_lifecycle(self) -> None:
        with TradeClient() as c:
            c.login("100001", "123456")
            cash = c.query_cash()
            assert cash["available_cash"] == 1_000_000

            order = c.send_order("sh600519", ORDER_SIDE_BUY, price=1000, quantity=100)
            assert order["order_id"] == 1

            orders = c.query_orders()
            assert orders[0]["code"] == "600519"

            cancelable = c.query_cancelable()
            assert [o["order_id"] for o in cancelable] == [order["order_id"]]
            c.cancel_order(order["order_id"])
            assert c.query_cancelable() == []
            assert c.heartbeat() is True

    def test_operation_before_login_raises(self) -> None:
        with TradeClient() as c, pytest.raises(TradeNotLoggedIn):
            c.query_cash()

    def test_buy_helper(self) -> None:
        with TradeClient() as c:
            c.login("100001", "123456")
            order = c.buy("600519", 1000, quantity=100)
            assert order["error_code"] == 0
            assert c.query_orders()[0]["side"] == ORDER_SIDE_BUY

    def test_sell_helper(self) -> None:
        with TradeClient() as c:
            c.login("100001", "123456")
            c.transport.simulator.seed_position(
                code="600519", name="贵州茅台", qty=100, available_qty=100, last_price=1200
            )
            order = c.sell("600519", 1200, quantity=100)
            assert order["error_code"] == 0
            assert c.query_orders()[0]["side"] == ORDER_SIDE_SELL

    def test_insufficient_funds_raises(self) -> None:
        with TradeClient() as c:
            c.login("100001", "123456")
            # 金额远超默认额度（¥10,000），模拟券商应拒绝
            with pytest.raises(TradeRejected):
                c.send_order("600519", ORDER_SIDE_BUY, price=1000000, quantity=100)

    def test_limit_order_requires_price(self) -> None:
        with TradeClient() as c:
            c.login("100001", "123456")
            with pytest.raises(ValidationError):
                c.send_order(
                    "600519", ORDER_SIDE_BUY, price_type=PRICE_TYPE_LIMIT, price=None, quantity=100
                )

    def test_market_order_none_price(self) -> None:
        with TradeClient() as c:
            c.login("100001", "123456")
            c.transport.simulator.seed_position(
                code="600519", name="贵州茅台", qty=100, available_qty=100, last_price=1200
            )
            order = c.send_order(
                "600519", ORDER_SIDE_SELL, price_type=PRICE_TYPE_MARKET, price=None, quantity=100
            )
            assert order["error_code"] == 0


class TestQueryWrappers:
    """持仓 / 当日成交 / 股东代码三支查询便捷方法的调用侧证据（第 26 轮 F-113）。

    同族的 `query_cash` / `query_orders` / `query_cancelable` 在
    :class:`TestClientLifecycle` 里各有一条断言，这三支此前一条都没有：能 import、
    有 docstring，却没人按名调用过。改坏了不会有任何测试变红。
    """

    def test_query_stocks_reflects_seeded_positions(self) -> None:
        with TradeClient() as c:
            c.login("100001", "123456")
            assert c.query_stocks() == []
            c.transport.simulator.seed_position(
                code="600519",
                name="贵州茅台",
                qty=100,
                available_qty=100,
                cost_price=1000,
                last_price=1200,
            )
            stocks = c.query_stocks()
            assert [s["code"] for s in stocks] == ["600519"]
            assert stocks[0]["profit"] == (1200 - 1000) * 100

    def test_query_deals_is_empty_until_fill_order(self) -> None:
        with TradeClient() as c:
            c.login("100001", "123456")
            order = c.send_order("sh600519", ORDER_SIDE_BUY, price=1000, quantity=100)
            assert c.query_deals() == []  # 下单不产生成交：撮合不在本库范围内
            c.transport.simulator.fill_order(order_id=order["order_id"], qty=100, at="09:30:00")
            deals = c.query_deals()
            assert [d["order_id"] for d in deals] == [order["order_id"]]
            assert deals[0]["amount"] == deals[0]["price"] * 100

    def test_query_shareholders_reports_logged_in_account(self) -> None:
        with TradeClient() as c:
            c.login("100001", "123456")
            assert c.query_shareholders() == [{"code": "100001", "name": "模拟券商", "market": 0}]

    def test_all_six_query_wrappers_agree_with_query(self) -> None:
        with TradeClient() as c:
            c.login("100001", "123456")
            c.buy("600519", 1000, quantity=100)
            pairs = (
                ("query_cash", QUERY_CATEGORY_CASH),
                ("query_stocks", QUERY_CATEGORY_STOCKS),
                ("query_orders", QUERY_CATEGORY_ORDER_OF_TODAY),
                ("query_deals", QUERY_CATEGORY_DEAL_OF_TODAY),
                ("query_cancelable", QUERY_CATEGORY_CANCELABLE_ORDER),
                ("query_shareholders", QUERY_CATEGORY_SHAREHOLDERS_CODE),
            )
            for name, category in pairs:
                wrapper = getattr(c, name)()
                direct = c.query(category)
                assert wrapper == (direct[0] if name == "query_cash" else direct), name


class TestRedLines:
    def test_socket_transport_connect_raises(self) -> None:
        with pytest.raises(TradingUnavailable):
            SocketTransport().connect()

    def test_socket_transport_context_manager_raises(self) -> None:
        with pytest.raises(TradingUnavailable), SocketTransport():
            pass  # pragma: no cover
