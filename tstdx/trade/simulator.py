# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""交易模拟器与协议回路传输（P2-1）。

:class:`TradeSimulator` —— 纯内存模拟券商：账本（资金 / 持仓 / 委托 / 成交）
均在进程内，支持登录 / 查询 / 委托 / 撤单 / 心跳的完整协议语义，用于
协议研究与测试，**不连接任何真实券商**。

:class:`SimTransport` —— 协议回路传输：把客户端语义调用经帧编解码
（:mod:`tstdx.trade.frames`）接到 :class:`TradeSimulator`，全链路走
「组帧 → 解帧 → 语义 → 组响应 → 解帧」，确保编解码器在双端都被验证。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from ..errors import ValidationError
from .constants import (
    CMD_CANCEL_ORDER,
    CMD_HEARTBEAT,
    CMD_LOGIN,
    CMD_LOGOUT,
    CMD_QUERY,
    CMD_SEND_ORDER,
    CMD_SET,
    ORDER_SIDE_BUY,
    ORDER_SIDE_SELL,
    ORDER_STATUS_CANCELLED,
    ORDER_STATUS_FILLED,
    ORDER_STATUS_PARTIAL,
    ORDER_STATUS_SUBMITTED,
    PRICE_TYPE_LIMIT,
    PRICE_TYPE_MARKET,
    QUERY_CATEGORY_CANCELABLE_ORDER,
    QUERY_CATEGORY_CASH,
    QUERY_CATEGORY_DEAL_OF_TODAY,
    QUERY_CATEGORY_NAMES,
    QUERY_CATEGORY_ORDER_OF_TODAY,
    QUERY_CATEGORY_SHAREHOLDERS_CODE,
    QUERY_CATEGORY_STOCKS,
)
from .errors import TradeError, TradeNotLoggedIn, TradeRejected
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
from .security import deobfuscate_password, obfuscate_password

__all__ = [
    "Position",
    "Order",
    "Deal",
    "TradeSimulator",
    "SimTransport",
    "DEFAULT_ACCOUNTS",
]

#: 模拟券商默认账密表（仅用于演示与测试，非真实凭证）。
DEFAULT_ACCOUNTS: dict[str, str] = {"100001": "123456"}


# --------------------------------------------------------------------------- #
# 领域对象
# --------------------------------------------------------------------------- #
@dataclass
class Position:
    """持仓。``cost_price`` / ``last_price`` 以「分」为单位的整数记账。"""

    code: str
    name: str
    qty: int
    available_qty: int
    cost_price: int
    last_price: int


@dataclass
class Order:
    """委托。"""

    order_id: int
    code: str
    name: str
    side: int
    price: int
    qty: int
    filled_qty: int
    status: int
    cancelable: bool


@dataclass
class Deal:
    """成交。"""

    deal_id: int
    order_id: int
    code: str
    name: str
    side: int
    price: int
    qty: int
    time: str


# --------------------------------------------------------------------------- #
# 模拟券商
# --------------------------------------------------------------------------- #
class TradeSimulator:
    """纯内存模拟券商（协议语义层，不做任何网络 IO）。"""

    def __init__(
        self,
        *,
        cash_cents: int = 1_000_000,
        accounts: Mapping[str, str] | None = None,
        branch: str = "1000",
        name: str = "模拟券商",
    ) -> None:
        self._accounts: dict[str, str] = dict(accounts or DEFAULT_ACCOUNTS)
        self._cash_cents = cash_cents
        self._branch = branch
        self._name = name
        self._positions: dict[str, Position] = {}
        self._orders: list[Order] = []
        self._deals: list[Deal] = []
        self._last_prices: dict[str, int] = {}
        self._logged_in = False
        self._account_no = ""
        self._yyb_id = 0
        self._client_id = 0
        self._order_seq = 0
        self._deal_seq = 0

    # --- 会话 ---------------------------------------------------------------- #
    def login(self, *, yyb_id: int, version: int, account: str, password: str) -> dict[str, Any]:
        """登录：校验账密（默认表 :data:`DEFAULT_ACCOUNTS`），失败抛 :class:`TradeRejected`。"""
        expected = self._accounts.get(account)
        if expected is None or expected != password:
            raise TradeRejected(f"账号或交易密码错误: {account}")
        self._logged_in = True
        self._account_no = account
        self._yyb_id = yyb_id
        self._client_id += 1
        return {
            "client_id": self._client_id,
            "account": account,
            "branch": self._branch,
            "name": self._name,
            "yyb_id": yyb_id,
            "login_flag": 0,
            "send_order_flag": 1,
        }

    def _require_login(self) -> None:
        if not self._logged_in:
            raise TradeNotLoggedIn("尚未登录")

    def logout(self) -> None:
        self._logged_in = False

    # --- 测试辅助 ------------------------------------------------------------ #
    def seed_position(
        self,
        *,
        code: str,
        name: str | None = None,
        qty: int,
        available_qty: int | None = None,
        cost_price: int = 0,
        last_price: int = 0,
    ) -> None:
        """预置持仓（测试用），并登记参考价。"""
        self._positions[code] = Position(
            code=code,
            name=name or code,
            qty=qty,
            available_qty=qty if available_qty is None else available_qty,
            cost_price=cost_price,
            last_price=last_price,
        )
        self._last_prices[code] = last_price

    # --- 查询 ---------------------------------------------------------------- #
    def query(self, category: int) -> list[dict[str, Any]]:
        """按类别返回记录列表（:data:`QUERY_CATEGORY_*`）。"""
        self._require_login()
        if category == QUERY_CATEGORY_CASH:
            return [self._cash_record()]
        if category == QUERY_CATEGORY_STOCKS:
            return [self._stock_record(p) for p in self._positions.values()]
        if category == QUERY_CATEGORY_ORDER_OF_TODAY:
            return [self._order_record(o) for o in self._orders]
        if category == QUERY_CATEGORY_DEAL_OF_TODAY:
            return [self._deal_record(d) for d in self._deals]
        if category == QUERY_CATEGORY_CANCELABLE_ORDER:
            return [self._order_record(o) for o in self._orders if o.cancelable]
        if category == QUERY_CATEGORY_SHAREHOLDERS_CODE:
            return [{"code": self._account_no, "name": self._name, "market": 0}]
        #: 第 26 轮 F-83：这里此前对**任何**没写到的类别一律返回空列表，于是
        #: "融资余额查不了" 与 "今天没有委托" 在调用方看来是同一个答案。
        if category not in QUERY_CATEGORY_NAMES:
            raise ValidationError(
                f"未声明的查询类别 category={category}："
                f"本模块声明的取值见 QUERY_CATEGORY_NAMES（{sorted(QUERY_CATEGORY_NAMES)}）",
                context={"category": category, "allowed": sorted(QUERY_CATEGORY_NAMES)},
            )
        #: ``TradingUnavailable``（E4030）在本仓只有一个含义：红线，不连真实券商。
        #: 这里失败的只是"模拟器没建这一类的账"，两者混用会让调用方把范围缺口
        #: 读成红线（F-83）。
        raise TradeError(
            f"模拟券商未实现类别 {category}（{QUERY_CATEGORY_NAMES[category]}）："
            "显式失败，不用空列表冒充「这一类没有记录」",
            context={"category": category, "name": QUERY_CATEGORY_NAMES[category]},
        )

    def _cash_record(self) -> dict[str, int]:
        market_value = sum(p.qty * p.last_price for p in self._positions.values())
        return {
            "available_cash": self._cash_cents,
            "frozen_cash": 0,
            "market_value": market_value,
            "total_asset": self._cash_cents + market_value,
            "debt": 0,
        }

    def _stock_record(self, p: Position) -> dict[str, Any]:
        return {
            "code": p.code,
            "name": p.name,
            "qty": p.qty,
            "available_qty": p.available_qty,
            "cost_price": p.cost_price,
            "last_price": p.last_price,
            "profit": (p.last_price - p.cost_price) * p.qty,
        }

    def _order_record(self, o: Order) -> dict[str, Any]:
        return {
            "order_id": o.order_id,
            "code": o.code,
            "name": o.name,
            "side": o.side,
            "price": o.price,
            "qty": o.qty,
            "filled_qty": o.filled_qty,
            "status": o.status,
            "cancelable": int(o.cancelable),
        }

    def _deal_record(self, d: Deal) -> dict[str, Any]:
        return {
            "deal_id": d.deal_id,
            "order_id": d.order_id,
            "code": d.code,
            "name": d.name,
            "side": d.side,
            "price": d.price,
            "qty": d.qty,
            "amount": d.price * d.qty,
            "time": d.time,
        }

    # --- 委托 / 撤单 --------------------------------------------------------- #
    def send_order(
        self,
        *,
        code: str,
        side: int,
        price_type: int,
        price: int,
        quantity: int,
    ) -> dict[str, Any]:
        """委托下单：校验资金/持仓后冻结（限价）并生成委托，返回受理结果。"""
        self._require_login()
        if quantity <= 0:
            raise TradeRejected("委托数量必须为正")
        if price_type not in (PRICE_TYPE_LIMIT, PRICE_TYPE_MARKET):
            raise TradeRejected(f"不支持的价格类型 {price_type}")
        effective_price = price
        if price_type == PRICE_TYPE_LIMIT:
            if price <= 0:
                raise TradeRejected("限价单缺少有效价格")
        else:
            effective_price = self._last_prices.get(code, 0)
            if effective_price <= 0:
                raise TradeRejected("市价单缺少参考价")

        name = code
        if side == ORDER_SIDE_BUY:
            need = effective_price * quantity
            if need > self._cash_cents:
                raise TradeRejected("可用资金不足")
            self._cash_cents -= need
            name = code
        elif side == ORDER_SIDE_SELL:
            pos = self._positions.get(code)
            available = pos.available_qty if pos else 0
            if quantity > available:
                raise TradeRejected("可用持仓不足")
            assert pos is not None
            pos.available_qty -= quantity
            name = pos.name
        else:
            raise TradeRejected(f"不支持的委托类别 {side}")

        self._order_seq += 1
        order = Order(
            order_id=self._order_seq,
            code=code,
            name=name,
            side=side,
            price=effective_price,
            qty=quantity,
            filled_qty=0,
            status=ORDER_STATUS_SUBMITTED,
            cancelable=True,
        )
        self._orders.append(order)
        return {
            "order_id": order.order_id,
            "status": ORDER_STATUS_SUBMITTED,
            "error_code": 0,
            "error_msg": "",
        }

    def cancel_order(self, *, order_id: int) -> dict[str, Any]:
        """撤单：仅可撤单状态可撤；释放冻结的资金/持仓。"""
        self._require_login()
        for o in self._orders:
            if o.order_id == order_id:
                if not o.cancelable:
                    raise TradeRejected(f"该委托不可撤: {order_id}")
                o.status = ORDER_STATUS_CANCELLED
                o.cancelable = False
                released = o.qty - o.filled_qty
                if o.side == ORDER_SIDE_BUY:
                    self._cash_cents += o.price * released
                else:
                    pos = self._positions.get(o.code)
                    if pos is not None:
                        pos.available_qty += released
                return {"status": 1, "error_code": 0, "error_msg": ""}
        raise TradeRejected(f"委托不存在: {order_id}")

    def fill_order(
        self, *, order_id: int, qty: int, at: str, price: int | None = None
    ) -> dict[str, Any]:
        """注入一笔成交回报：委托随之推进，并生成一条当日成交记录。

        撮合发生在交易所，**不在 tstdx 的范围内**（红线：不猜真实券商行为），所以
        本方法是模拟器里成交的唯一来源 —— 由调用方给出数量、时间与成交价
        （``price`` 省略时按委托限价计）。:meth:`query` 的
        ``QUERY_CATEGORY_DEAL_OF_TODAY`` 只可能返回这里注入过的记录。

        账本口径与 :meth:`send_order` / :meth:`cancel_order` 的冻结规则对齐：

        * 买入下单时按限价冻结了 ``price*qty``，成交价更低则退回差额；
          成交股份计入持仓数量，但当日不可卖（T+1），故不动 ``available_qty``；
        * 卖出扣减持仓数量，成交金额计入可用资金；
        * 两侧都把参考价更新为成交价（这笔成交就是该代码最新的价格事实）。

        ``at`` 是自由字符串：帧里 ``time`` 字段是长度前缀 GBK 串，模拟器不持有时钟。
        """
        self._require_login()
        order = next((o for o in self._orders if o.order_id == order_id), None)
        if order is None:
            raise TradeRejected(f"委托不存在: {order_id}")
        remaining = order.qty - order.filled_qty
        if qty <= 0:
            raise TradeRejected(f"成交数量必须为正: {qty}")
        if qty > remaining:
            raise TradeRejected(f"成交数量 {qty} 超过委托未成交量 {remaining}")
        deal_price = order.price if price is None else price
        if deal_price <= 0:
            raise TradeRejected(f"成交价无效: {deal_price}")

        pos = self._positions.get(order.code)
        if order.side == ORDER_SIDE_BUY:
            self._cash_cents += (order.price - deal_price) * qty
            if pos is None:
                pos = Position(
                    code=order.code,
                    name=order.name,
                    qty=0,
                    available_qty=0,
                    cost_price=deal_price,
                    last_price=deal_price,
                )
                self._positions[order.code] = pos
            total = pos.qty + qty
            pos.cost_price = (pos.cost_price * pos.qty + deal_price * qty) // total
            pos.qty = total
        else:
            assert pos is not None
            pos.qty -= qty
            self._cash_cents += deal_price * qty
        pos.last_price = deal_price
        self._last_prices[order.code] = deal_price

        order.filled_qty += qty
        order.status = (
            ORDER_STATUS_FILLED if order.filled_qty == order.qty else ORDER_STATUS_PARTIAL
        )
        order.cancelable = order.filled_qty < order.qty

        self._deal_seq += 1
        deal = Deal(
            deal_id=self._deal_seq,
            order_id=order.order_id,
            code=order.code,
            name=order.name,
            side=order.side,
            price=deal_price,
            qty=qty,
            time=at,
        )
        self._deals.append(deal)
        return self._deal_record(deal)

    def heartbeat(self) -> bool:
        """心跳：模拟券商恒在线。"""
        return True


# --------------------------------------------------------------------------- #
# 协议回路传输
# --------------------------------------------------------------------------- #
class SimTransport:
    """把客户端语义经帧编解码接到 :class:`TradeSimulator`（纯内存，无网络）。"""

    def __init__(self, simulator: TradeSimulator | None = None) -> None:
        self.simulator = simulator or TradeSimulator()
        self.connected = False
        self.client_id: int | None = None
        self._seq = 0

    # --- 连接生命周期 ------------------------------------------------------- #
    def connect(self) -> None:
        self.connected = True

    def close(self) -> None:
        self.connected = False
        self.client_id = None

    def __enter__(self) -> SimTransport:
        self.connect()
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # --- 帧回路 ------------------------------------------------------------- #
    def _next_seq(self) -> int:
        self._seq += 1
        return self._seq

    def roundtrip(
        self, cmd: int, body: bytes, parse_body: Callable[[bytes], dict[str, Any]]
    ) -> dict[str, Any]:
        """全链路回路：组请求帧 → 服务端解帧/语义/组响应 → 客户端解帧解析。"""
        if not self.connected:
            raise TradeNotLoggedIn("传输未连接")
        #: 帧编解码对 cmd 不做任何检查（``build_request`` 接受任意 uint16），
        #: 于是"发了一个协议里没有的命令"曾经只在服务端那一侧报出来，
        #: 且要把请求写成帧再读回来才发现。发送侧先拦（F-83）。
        if cmd not in CMD_SET:
            raise TradeError(
                f"未声明的交易命令 0x{cmd:04x}：本模块声明的取值见 CMD_SET（{sorted(CMD_SET)}）",
                context={"cmd": cmd, "allowed": sorted(CMD_SET)},
            )
        seq = self._next_seq()
        request = build_request(cmd, seq, body)
        rcmd, rseq, body_in = parse_request(request)
        if rcmd != cmd:
            raise TradeError(f"请求命令不符: {rcmd:#x} != {cmd:#x}")
        response_body = self._serve(rcmd, body_in)
        response = build_response(rcmd, rseq, 0, response_body)
        rcmd2, rseq2, status, resp_body = parse_response(response)
        if rcmd2 != cmd or rseq2 != seq:
            raise TradeError("响应回显与请求不匹配")
        if status != 0:
            raise TradeRejected(f"服务端错误码 {status}")
        return parse_body(resp_body)

    def _serve(self, cmd: int, body: bytes) -> bytes:
        """模拟券商端：解帧请求体 → 语义方法 → 组响应体。"""
        if cmd == CMD_LOGIN:
            p = parse_login_body(body)
            result = self.simulator.login(
                yyb_id=p["yyb_id"],
                version=p["version"],
                account=p["account"],
                password=deobfuscate_password(p["password"]),
            )
            return build_login_response(**result)
        if cmd == CMD_HEARTBEAT:
            return build_heartbeat_response(self.simulator.heartbeat())
        if cmd == CMD_LOGOUT:
            self.simulator.logout()
            return b""
        if cmd == CMD_QUERY:
            q = parse_query_body(body)
            return build_query_response(q["category"], self.simulator.query(q["category"]))
        if cmd == CMD_SEND_ORDER:
            o = parse_order_body(body)
            result = self.simulator.send_order(
                code=o["code"],
                side=o["side"],
                price_type=o["price_type"],
                price=o["price"],
                quantity=o["quantity"],
            )
            return build_order_response(**result)
        if cmd == CMD_CANCEL_ORDER:
            c = parse_cancel_body(body)
            result = self.simulator.cancel_order(order_id=c["order_id"])
            return build_cancel_response(**result)
        raise TradeError(f"未知命令 0x{cmd:04x}")

    # --- 高层语义 ----------------------------------------------------------- #
    def login(
        self, account: str, password: str, *, yyb_id: int = 0, version: int = 0
    ) -> dict[str, Any]:
        body = build_login_body(
            yyb_id=yyb_id,
            version=version,
            account=account,
            password=obfuscate_password(password),
        )
        result = self.roundtrip(CMD_LOGIN, body, parse_login_response)
        self.client_id = result["client_id"]
        return result

    def query(self, category: int) -> list[dict[str, Any]]:
        result = self.roundtrip(
            CMD_QUERY, build_query_body(category=category), parse_query_response
        )
        return result["records"]

    def send_order(
        self,
        *,
        code: str,
        side: int,
        price_type: int,
        price: int,
        quantity: int,
    ) -> dict[str, Any]:
        body = build_order_body(
            code=code, side=side, price_type=price_type, price=price, quantity=quantity
        )
        return self.roundtrip(CMD_SEND_ORDER, body, parse_order_response)

    def cancel_order(self, *, order_id: int) -> dict[str, Any]:
        return self.roundtrip(
            CMD_CANCEL_ORDER, build_cancel_body(order_id=order_id), parse_cancel_response
        )

    def heartbeat(self) -> bool:
        result = self.roundtrip(CMD_HEARTBEAT, b"", parse_heartbeat_response)
        return bool(result["ok"])

    def logout(self) -> None:
        self.roundtrip(CMD_LOGOUT, b"", lambda _body: {})
