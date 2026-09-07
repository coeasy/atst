# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""模拟券商语义测试（P2-1）。

覆盖登录/查询/委托/撤单的完整协议语义与状态转换（纯内存，无网络）。
"""

from __future__ import annotations

import pytest

from tstdx.trade.constants import (
    ORDER_SIDE_BUY,
    ORDER_SIDE_SELL,
    ORDER_STATUS_CANCELLED,
    PRICE_TYPE_LIMIT,
    PRICE_TYPE_MARKET,
    QUERY_CATEGORY_CASH,
    QUERY_CATEGORY_ORDER_OF_TODAY,
    QUERY_CATEGORY_STOCKS,
)
from tstdx.trade.errors import TradeNotLoggedIn, TradeRejected
from tstdx.trade.simulator import TradeSimulator

pytestmark = pytest.mark.unit


@pytest.fixture
def sim() -> TradeSimulator:
    return TradeSimulator(cash_cents=1_000_000)


class TestLogin:
    def test_login_success(self, sim: TradeSimulator) -> None:
        result = sim.login(yyb_id=32, version=0, account="100001", password="123456")
        assert result["client_id"] >= 1
        assert result["send_order_flag"] == 1

    def test_login_wrong_password_rejected(self, sim: TradeSimulator) -> None:
        with pytest.raises(TradeRejected):
            sim.login(yyb_id=0, version=0, account="100001", password="wrong")

    def test_login_unknown_account_rejected(self, sim: TradeSimulator) -> None:
        with pytest.raises(TradeRejected):
            sim.login(yyb_id=0, version=0, account="999999", password="123456")


class TestQueryGuard:
    def test_query_before_login_raises(self, sim: TradeSimulator) -> None:
        with pytest.raises(TradeNotLoggedIn):
            sim.query(QUERY_CATEGORY_CASH)

    def test_logout_requires_relogin(self, sim: TradeSimulator) -> None:
        sim.login(yyb_id=0, version=0, account="100001", password="123456")
        sim.logout()
        with pytest.raises(TradeNotLoggedIn):
            sim.query(QUERY_CATEGORY_CASH)


class TestCashAndPositions:
    def test_cash_record(self, sim: TradeSimulator) -> None:
        sim.login(yyb_id=0, version=0, account="100001", password="123456")
        cash = sim.query(QUERY_CATEGORY_CASH)[0]
        assert cash["available_cash"] == 1_000_000
        assert cash["total_asset"] == 1_000_000

    def test_stocks_record(self, sim: TradeSimulator) -> None:
        sim.login(yyb_id=0, version=0, account="100001", password="123456")
        sim.seed_position(
            code="600519",
            name="贵州茅台",
            qty=100,
            available_qty=100,
            cost_price=1000,
            last_price=1200,
        )
        stocks = sim.query(QUERY_CATEGORY_STOCKS)
        assert stocks[0]["code"] == "600519"
        assert stocks[0]["profit"] == (1200 - 1000) * 100


class TestOrderLifecycle:
    def test_buy_freezes_cash(self, sim: TradeSimulator) -> None:
        sim.login(yyb_id=0, version=0, account="100001", password="123456")
        result = sim.send_order(
            code="600519",
            side=ORDER_SIDE_BUY,
            price_type=PRICE_TYPE_LIMIT,
            price=1000,
            quantity=100,
        )
        assert result["error_code"] == 0
        cash = sim.query(QUERY_CATEGORY_CASH)[0]
        assert cash["available_cash"] == 1_000_000 - 1000 * 100

    def test_buy_insufficient_funds_rejected(self, sim: TradeSimulator) -> None:
        sim.login(yyb_id=0, version=0, account="100001", password="123456")
        with pytest.raises(TradeRejected):
            sim.send_order(
                code="600519",
                side=ORDER_SIDE_BUY,
                price_type=PRICE_TYPE_LIMIT,
                price=100000,
                quantity=100,
            )

    def test_sell_requires_position(self, sim: TradeSimulator) -> None:
        sim.login(yyb_id=0, version=0, account="100001", password="123456")
        with pytest.raises(TradeRejected):
            sim.send_order(
                code="600519",
                side=ORDER_SIDE_SELL,
                price_type=PRICE_TYPE_LIMIT,
                price=1200,
                quantity=100,
            )

    def test_sell_reserves_shares(self, sim: TradeSimulator) -> None:
        sim.login(yyb_id=0, version=0, account="100001", password="123456")
        sim.seed_position(
            code="600519", name="贵州茅台", qty=200, available_qty=200, last_price=1200
        )
        sim.send_order(
            code="600519",
            side=ORDER_SIDE_SELL,
            price_type=PRICE_TYPE_LIMIT,
            price=1200,
            quantity=50,
        )
        stocks = sim.query(QUERY_CATEGORY_STOCKS)
        assert stocks[0]["available_qty"] == 150

    def test_market_order_uses_reference_price(self, sim: TradeSimulator) -> None:
        sim.login(yyb_id=0, version=0, account="100001", password="123456")
        sim.seed_position(
            code="600519", name="贵州茅台", qty=100, available_qty=100, last_price=1200
        )
        result = sim.send_order(
            code="600519", side=ORDER_SIDE_SELL, price_type=PRICE_TYPE_MARKET, price=0, quantity=100
        )
        assert result["error_code"] == 0
        orders = sim.query(QUERY_CATEGORY_ORDER_OF_TODAY)
        assert orders[0]["price"] == 1200

    def test_market_order_without_reference_rejected(self, sim: TradeSimulator) -> None:
        sim.login(yyb_id=0, version=0, account="100001", password="123456")
        with pytest.raises(TradeRejected):
            sim.send_order(
                code="600519",
                side=ORDER_SIDE_BUY,
                price_type=PRICE_TYPE_MARKET,
                price=0,
                quantity=100,
            )

    def test_cancel_releases_frozen_cash(self, sim: TradeSimulator) -> None:
        sim.login(yyb_id=0, version=0, account="100001", password="123456")
        order = sim.send_order(
            code="600519",
            side=ORDER_SIDE_BUY,
            price_type=PRICE_TYPE_LIMIT,
            price=1000,
            quantity=100,
        )
        sim.cancel_order(order_id=order["order_id"])
        cash = sim.query(QUERY_CATEGORY_CASH)[0]
        assert cash["available_cash"] == 1_000_000
        orders = sim.query(QUERY_CATEGORY_ORDER_OF_TODAY)
        assert orders[0]["status"] == ORDER_STATUS_CANCELLED

    def test_cancel_releases_shares(self, sim: TradeSimulator) -> None:
        sim.login(yyb_id=0, version=0, account="100001", password="123456")
        sim.seed_position(
            code="600519", name="贵州茅台", qty=100, available_qty=100, last_price=1200
        )
        order = sim.send_order(
            code="600519",
            side=ORDER_SIDE_SELL,
            price_type=PRICE_TYPE_LIMIT,
            price=1200,
            quantity=30,
        )
        sim.cancel_order(order_id=order["order_id"])
        stocks = sim.query(QUERY_CATEGORY_STOCKS)
        assert stocks[0]["available_qty"] == 100

    def test_cancel_unknown_order_rejected(self, sim: TradeSimulator) -> None:
        sim.login(yyb_id=0, version=0, account="100001", password="123456")
        with pytest.raises(TradeRejected):
            sim.cancel_order(order_id=999)

    def test_heartbeat(self, sim: TradeSimulator) -> None:
        assert sim.heartbeat() is True
