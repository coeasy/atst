# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""交易面词表判据（第 26 轮 F-83 / G40）。

交易面声明了三份词表——命令号（``CMD_SET``/``CMD_NAMES``）、查询类别
（``QUERY_CATEGORY_NAMES``）、每类的记录字段（``frames._QUERY_RECORD_FIELDS``）——
第 1 遍普查量到的却是：三份各说各话，且**没有任何一份有人按它行事**：
``query()`` 对没写到的类别一律返回 ``[]``（"融资余额查不了" 与 "今天没有委托"
在调用方看来是同一个答案），``roundtrip()`` 把任意 cmd 直接编上帧，而成交记录
只有读方没有产方（``_deals`` 全仓无人写入）。

本文件把这三条变成代价：声明的取值必须有对应的行为，行为必须有对应的声明。
分母全部取自声明自身与真实调用，不抄清单。
"""

from __future__ import annotations

import pytest

from atst.errors import ValidationError
from atst.trade import ORDER_SIDE_BUY, ORDER_SIDE_SELL, SimTransport, TradeClient
from atst.trade.constants import (
    CMD_NAMES,
    CMD_SET,
    ORDER_STATUS_CANCELLED,
    ORDER_STATUS_FILLED,
    ORDER_STATUS_PARTIAL,
    ORDER_STATUS_SUBMITTED,
    PRICE_TYPE_LIMIT,
    QUERY_CATEGORY_CANCELABLE_ORDER,
    QUERY_CATEGORY_CASH,
    QUERY_CATEGORY_DEAL_OF_TODAY,
    QUERY_CATEGORY_NAMES,
    QUERY_CATEGORY_NEW_STOCKS,
    QUERY_CATEGORY_ORDER_OF_TODAY,
    QUERY_CATEGORY_STOCK_LOAN_BALANCE,
    QUERY_CATEGORY_STOCKS,
)
from atst.trade.errors import TradeError, TradingUnavailable
from atst.trade.frames import _QUERY_RECORD_FIELDS, query_record_fields
from atst.trade.simulator import TradeSimulator

pytestmark = pytest.mark.unit


def _logged_in_simulator() -> TradeSimulator:
    simulator = TradeSimulator()
    simulator.login(yyb_id=0, version=0, account="100001", password="123456")
    return simulator


def _connected_transport() -> SimTransport:
    transport = SimTransport()
    transport.connect()
    transport.login("100001", "123456")
    return transport


def _buy(transport: SimTransport, *, code: str, price: int, quantity: int) -> int:
    order = transport.send_order(
        code=code, side=ORDER_SIDE_BUY, price_type=PRICE_TYPE_LIMIT, price=price, quantity=quantity
    )
    return int(order["order_id"])


# --- 一、查询类别：声明 ⇄ 帧表 ⇄ 行为 -------------------------------------- #
def test_categories_the_simulator_answers_are_exactly_the_frame_table() -> None:
    """能答的类别必须能编码，能编码的类别必须能答。

    帧表少一行 = 查得到却发不出去（记录在组响应时被丢掉）；帧表多一行 = 给调用方
    一个永远解不出记录的类别。两边都是现扫：可答集合靠逐个真调用得出，帧表取模块
    声明（``_QUERY_RECORD_FIELDS`` 没有公开列举口，而这张表正是本判据要对的对象）。
    """

    simulator = _logged_in_simulator()
    answered = set()
    for category in QUERY_CATEGORY_NAMES:
        try:
            simulator.query(category)
        except TradeError:
            continue
        answered.add(category)
    assert len(answered) >= 4, f"探测自身失明：只答得出 {sorted(answered)}"
    assert answered == set(_QUERY_RECORD_FIELDS), (
        f"可答类别与帧字段表不等：只有帧表有 {sorted(set(_QUERY_RECORD_FIELDS) - answered)}、"
        f"只有行为有 {sorted(answered - set(_QUERY_RECORD_FIELDS))}"
    )


def test_undeclared_category_is_a_caller_input_error() -> None:
    """类别号不在词表里 → ``ValidationError``（E1010，入参本身不成立）。"""

    with pytest.raises(ValidationError) as excinfo:
        _logged_in_simulator().query(4095)
    assert excinfo.value.code == "E1010"
    assert "QUERY_CATEGORY_NAMES" in str(excinfo.value)


def test_declared_but_unimplemented_category_fails_loudly() -> None:
    """在词表里、但模拟器没建账的类别：显式失败，绝不拿 ``[]`` 冒充"这一类没有记录"。

    同时锁住错误类别：``TradingUnavailable``（E4030）在本仓只表示红线"不连真实
    券商"，把范围缺口记成红线，调用方就会把一个可修的实现缺口读成不可逾越的边界。
    """

    for category in (QUERY_CATEGORY_STOCK_LOAN_BALANCE, QUERY_CATEGORY_NEW_STOCKS):
        assert category in QUERY_CATEGORY_NAMES, "判据自身失效：这类已不在词表"
        assert category not in _QUERY_RECORD_FIELDS, "判据自身失效：这类其实已有账本"
        with pytest.raises(TradeError) as excinfo:
            _logged_in_simulator().query(category)
        assert not isinstance(excinfo.value, TradingUnavailable)
        assert excinfo.value.code == "E4000"
        assert QUERY_CATEGORY_NAMES[category] in str(excinfo.value)


# --- 二、命令号：词表 ⇄ 服务端分派 ⇄ 发送侧闸门 ----------------------------- #
def test_cmd_set_matches_cmd_names() -> None:
    assert set(CMD_NAMES) == set(CMD_SET), "命令名表与命令集合不等"


def test_every_declared_command_has_a_server_branch() -> None:
    """``CMD_SET`` 里每个命令都要有 ``_serve`` 分支。

    判据是"报错的口径"而非"不报错"：未实现命令抛 ``未知命令``，声明过的命令即使
    带空请求体也应该已经走进分支（随后在解帧或语义处失败，那是另一回事）。
    """

    transport = SimTransport(simulator=_logged_in_simulator())
    with pytest.raises(TradeError, match="未知命令"):
        transport._serve(0x7FFF, b"")  # 负对照：先确认"未知"这个口径本身还在
    for cmd in sorted(CMD_SET):
        try:
            transport._serve(cmd, b"")
        except TradeError as exc:
            assert "未知命令" not in str(exc), (
                f"命令 {cmd:#06x}（{CMD_NAMES[cmd]}）声明了却没有服务端分支"
            )
        except Exception:
            pass  # 空请求体在解帧/语义处抱怨，说明已经走进分支


def test_transport_refuses_to_send_an_undeclared_command() -> None:
    """发送侧也要有闸门：帧编解码对 cmd 零检查，未声明的命令号曾经一路编上帧。"""

    transport = _connected_transport()
    seq_before = transport._seq
    with pytest.raises(TradeError) as excinfo:
        transport.roundtrip(0x7FFF, b"", lambda body: {})
    assert excinfo.value.code == "E4000"
    assert "CMD_SET" in str(excinfo.value)
    assert transport._seq == seq_before, "被拒的请求也取了序号：闸门排在了组帧之后"


# --- 三、成交：读方必须配产方（G40） --------------------------------------- #
def test_deal_records_need_an_explicit_fill_and_keep_their_frame_shape() -> None:
    """成交链的完整回路：下单 → 注入成交 → 经帧编解码读回记录。

    注入前 ``query(成交)`` 必空（撮合在交易所，模拟器不自作主张）；注入后记录键
    与帧声明的九个字段逐个对齐——``_deal_record`` 多加一列就会在这里先红，而不是
    到调用方那里变成"解出来的记录少一列"。
    """

    transport = _connected_transport()
    order_id = _buy(transport, code="600519", price=1500, quantity=300)
    assert transport.query(QUERY_CATEGORY_DEAL_OF_TODAY) == []

    transport.simulator.fill_order(order_id=order_id, qty=200, at="09:30:00")

    deals = transport.query(QUERY_CATEGORY_DEAL_OF_TODAY)
    assert len(deals) == 1
    declared = [name for name, _ in query_record_fields(QUERY_CATEGORY_DEAL_OF_TODAY)]
    assert set(deals[0]) == set(declared), "成交记录与帧字段表不等"
    assert deals[0] == {
        "deal_id": 1,
        "order_id": order_id,
        "code": "600519",
        "name": "600519",
        "side": ORDER_SIDE_BUY,
        "price": 1500,
        "qty": 200,
        "amount": 1500 * 200,
        "time": "09:30:00",
    }


def test_partial_fill_advances_order_status_and_cancelable_list() -> None:
    transport = _connected_transport()
    order_id = _buy(transport, code="600519", price=1000, quantity=300)
    assert transport.query(QUERY_CATEGORY_ORDER_OF_TODAY)[0]["status"] == ORDER_STATUS_SUBMITTED

    transport.simulator.fill_order(order_id=order_id, qty=100, at="09:30:00")
    record = transport.query(QUERY_CATEGORY_ORDER_OF_TODAY)[0]
    assert record["status"] == ORDER_STATUS_PARTIAL
    assert record["filled_qty"] == 100
    assert [o["order_id"] for o in transport.query(QUERY_CATEGORY_CANCELABLE_ORDER)] == [order_id]

    transport.simulator.fill_order(order_id=order_id, qty=200, at="09:31:00")
    record = transport.query(QUERY_CATEGORY_ORDER_OF_TODAY)[0]
    assert record["status"] == ORDER_STATUS_FILLED
    assert transport.query(QUERY_CATEGORY_CANCELABLE_ORDER) == []
    with pytest.raises(TradeError):
        transport.cancel_order(order_id=order_id)


def test_buy_fill_books_position_at_t_plus_one_and_keeps_ledger_consistent() -> None:
    """买入成交：资金按成交价结清（低于限价退差额），股份进持仓但当日不可用（T+1）。"""

    transport = _connected_transport()
    cash_before = transport.query(QUERY_CATEGORY_CASH)[0]["available_cash"]
    order_id = _buy(transport, code="600519", price=1000, quantity=300)
    frozen = transport.query(QUERY_CATEGORY_CASH)[0]["available_cash"]
    assert frozen == cash_before - 1000 * 300

    transport.simulator.fill_order(order_id=order_id, qty=300, at="09:30:00", price=900)
    cash = transport.query(QUERY_CATEGORY_CASH)[0]
    assert cash["available_cash"] == frozen + 100 * 300
    assert cash["total_asset"] == cash["available_cash"] + cash["market_value"]

    stock = transport.query(QUERY_CATEGORY_STOCKS)[0]
    assert (stock["qty"], stock["available_qty"]) == (300, 0)
    assert (stock["cost_price"], stock["last_price"]) == (900, 900)


def test_sell_fill_releases_proceeds_and_shrinks_position() -> None:
    transport = _connected_transport()
    transport.simulator.seed_position(
        code="600519", name="贵州茅台", qty=1000, available_qty=1000, last_price=1200
    )
    cash_before = transport.query(QUERY_CATEGORY_CASH)[0]["available_cash"]
    order = transport.send_order(
        code="600519",
        side=ORDER_SIDE_SELL,
        price_type=PRICE_TYPE_LIMIT,
        price=1300,
        quantity=400,
    )
    transport.simulator.fill_order(order_id=order["order_id"], qty=400, at="14:55:00")

    assert transport.query(QUERY_CATEGORY_CASH)[0]["available_cash"] == cash_before + 1300 * 400
    stock = transport.query(QUERY_CATEGORY_STOCKS)[0]
    assert (stock["qty"], stock["available_qty"]) == (600, 600)
    assert stock["name"] == "贵州茅台"


def test_cancel_after_partial_fill_only_releases_the_unfilled_part() -> None:
    transport = _connected_transport()
    cash_before = transport.query(QUERY_CATEGORY_CASH)[0]["available_cash"]
    order_id = _buy(transport, code="600519", price=1000, quantity=300)
    transport.simulator.fill_order(order_id=order_id, qty=100, at="09:30:00")
    transport.cancel_order(order_id=order_id)

    assert transport.query(QUERY_CATEGORY_CASH)[0]["available_cash"] == cash_before - 1000 * 100
    record = transport.query(QUERY_CATEGORY_ORDER_OF_TODAY)[0]
    assert record["status"] == ORDER_STATUS_CANCELLED
    assert record["filled_qty"] == 100
    assert transport.query(QUERY_CATEGORY_STOCKS)[0]["qty"] == 100


def test_fill_after_cancel_is_rejected_and_does_not_double_release() -> None:
    """撤单释放冻结之后，同一张委托不能再被注入成交。

    撤单把冻结原样退回（买：``price*qty``），而 ``fill_order`` 的 ``remaining`` 只按数量算、
    看不出撤单——少了终态守卫，撤单后仍能成交：买侧白送 ``(委托价-成交价)*qty`` 并凭空建仓。
    判据把钱、仓、成交记录三笔账都钉住。
    """
    transport = _connected_transport()
    cash_before = transport.query(QUERY_CATEGORY_CASH)[0]["available_cash"]
    order_id = _buy(transport, code="600519", price=1000, quantity=300)
    transport.cancel_order(order_id=order_id)

    with pytest.raises(TradeError):
        transport.simulator.fill_order(order_id=order_id, qty=300, at="09:30:00", price=900)

    assert transport.query(QUERY_CATEGORY_CASH)[0]["available_cash"] == cash_before
    assert transport.query(QUERY_CATEGORY_STOCKS) == []
    assert transport.query(QUERY_CATEGORY_DEAL_OF_TODAY) == []


def test_fill_rejects_impossible_executions() -> None:
    """成交注入也是语义边界：超量/非正/未知委托/零价都要显式拒绝。"""

    transport = _connected_transport()
    order_id = _buy(transport, code="600519", price=1000, quantity=100)
    for kwargs in (
        {"order_id": 999, "qty": 100, "at": "09:30:00"},
        {"order_id": order_id, "qty": 101, "at": "09:30:00"},
        {"order_id": order_id, "qty": 0, "at": "09:30:00"},
        {"order_id": order_id, "qty": -5, "at": "09:30:00"},
        {"order_id": order_id, "qty": 100, "at": "09:30:00", "price": 0},
    ):
        with pytest.raises(TradeError):
            transport.simulator.fill_order(**kwargs)
    assert transport.query(QUERY_CATEGORY_DEAL_OF_TODAY) == []
    assert transport.query(QUERY_CATEGORY_STOCKS) == []


# --- 四、客户端入参：不出网的判断与"券商拒绝"分属两类 ----------------------- #
def test_client_rejects_impossible_order_inputs_before_the_broker() -> None:
    """``side``/``price_type``/``quantity`` 是调用方入参 → E1010，不是 E4020 拒绝。

    曾经这些值一路发到模拟券商，由它抛 ``TradeRejected``（"服务端拒绝"），于是
    "我传了个不存在的委托类别" 和 "资金不足" 在调用方看来是同一类错误：前者改代码，
    后者改委托参数。
    """

    with TradeClient() as client:
        client.login("100001", "123456")
        for kwargs in (
            {"symbol": "600519", "side": 7, "price": 1000},
            {"symbol": "600519", "side": ORDER_SIDE_BUY, "price": 1000, "price_type": 9},
            {"symbol": "600519", "side": ORDER_SIDE_BUY, "price": 1000, "quantity": 0},
            {"symbol": "600519", "side": ORDER_SIDE_BUY, "price": -1},
        ):
            with pytest.raises(ValidationError) as excinfo:
                client.send_order(**kwargs)
            assert excinfo.value.code == "E1010"
        #: 入参判断不消耗序号：一次都没上帧。
        assert client.transport._seq == 1
