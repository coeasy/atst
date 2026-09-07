"""C2 WebSocket JSON-RPC 2.0 服务离线测试（handler 直测，无 socket）。"""

from __future__ import annotations

import json

import pytest

pytestmark = pytest.mark.unit

from tstdx.integration.ws_server import (  # noqa: E402
    ERR_INVALID_PARAMS,
    ERR_METHOD_NOT_FOUND,
    ERR_PARSE,
    NOTIFY_QUOTE_SNAPSHOT,
    NOTIFY_QUOTE_UPDATE,
    JsonRpcHandler,
)


class FakeClient:
    def quotes(self, codes):
        return [{"code": c, "price": 1.0} for c in codes]

    def bars(self, symbol, period="day", count=100):
        return [{"symbol": symbol, "period": period}]

    def minute_today(self, symbol):
        return [{"price": 1.0}]

    def trade_today(self, symbol):
        return [{"price": 1.0}]

    def finance_info(self, symbol):
        return {"roe": 15.0}

    def security_count(self, market):
        return 42


@pytest.fixture()
def handler():
    return JsonRpcHandler(client=FakeClient())


def _req(method: str, params: dict | None = None, req_id: int = 1) -> str:
    msg = {"jsonrpc": "2.0", "id": req_id, "method": method}
    if params is not None:
        msg["params"] = params
    return json.dumps(msg)


def test_bars_roundtrip(handler):
    out = json.loads(handler.handle_message(_req("bars", {"symbol": "600519"})))
    assert out["result"][0]["symbol"] == "600519"
    assert out["id"] == 1


def test_quotes(handler):
    out = json.loads(handler.handle_message(_req("quotes", {"symbols": ["600519", "000001"]})))
    assert len(out["result"]) == 2


def test_minute_trades_finance_security_count(handler):
    assert json.loads(handler.handle_message(_req("minute", {"symbol": "x"})))["result"]
    assert json.loads(handler.handle_message(_req("trades", {"symbol": "x"})))["result"]
    assert (
        json.loads(handler.handle_message(_req("finance", {"symbol": "x"})))["result"]["roe"]
        == 15.0
    )
    assert json.loads(handler.handle_message(_req("security_count", {"market": 1})))["result"] == 42


def test_method_not_found(handler):
    out = json.loads(handler.handle_message(_req("nope")))
    assert out["error"]["code"] == ERR_METHOD_NOT_FOUND


def test_missing_params(handler):
    out = json.loads(handler.handle_message(_req("bars", {})))
    assert out["error"]["code"] == ERR_INVALID_PARAMS


def test_parse_error(handler):
    out = json.loads(handler.handle_message(b"{oops"))
    assert out["error"]["code"] == ERR_PARSE


def test_invalid_request(handler):
    out = json.loads(handler.handle_message(json.dumps({"id": 1, "method": "bars"})))
    assert "error" in out  # 缺 jsonrpc 字段


def test_notification_no_reply(handler):
    assert (
        handler.handle_message(
            json.dumps({"jsonrpc": "2.0", "method": "subscribe", "params": {"symbols": ["x"]}})
        )
        is None
    )


def test_subscribe_unsubscribe(handler):
    out = json.loads(
        handler.handle_message(_req("subscribe", {"symbols": ["600519", "000001"]}, req_id=4))
    )
    assert "600519" in out["result"]["subscribed"]
    out = json.loads(handler.handle_message(_req("unsubscribe", {"symbols": ["600519"]}, req_id=5)))
    assert "600519" not in out["result"]["subscribed"]
    assert "000001" in out["result"]["subscribed"]


def test_bad_subscribe_params(handler):
    out = json.loads(handler.handle_message(_req("subscribe", {"symbols": "not-a-list"})))
    assert out["error"]["code"] == ERR_INVALID_PARAMS


def test_batch_requests(handler):
    batch = json.dumps(
        [
            {"jsonrpc": "2.0", "id": 1, "method": "quotes", "params": {"symbols": ["600519"]}},
            {"jsonrpc": "2.0", "method": "subscribe", "params": {"symbols": ["x"]}},  # 通知：无回包
        ]
    )
    out = json.loads(handler.handle_message(batch))
    assert isinstance(out, list) and len(out) == 1


def test_bytes_input(handler):
    out = json.loads(handler.handle_message(_req("security_count", {"market": 0}).encode()))
    assert out["result"] == 42


def test_push_notifications(handler):
    handler.handle_message(_req("subscribe", {"symbols": ["600519"]}, req_id=9))
    notes = handler.push_quote_updates()
    assert len(notes) == 1
    parsed = json.loads(notes[0])
    assert parsed["method"] == NOTIFY_QUOTE_UPDATE
    assert parsed["params"]["quote"]["code"] == "600519"


def test_empty_push(handler):
    assert handler.push_quote_updates() == []


def test_push_snapshot_method(handler):
    """F3：push_snapshot 生成 quote_snapshot 补洞通知（断线补洞消息类型）。"""
    notes = handler.push_snapshot(["600519", "000001"])
    assert len(notes) == 2
    for note in notes:
        parsed = json.loads(note)
        assert parsed["method"] == NOTIFY_QUOTE_SNAPSHOT
        assert "quote" in parsed["params"]
        assert "id" not in parsed  # notification 无 id


def test_push_snapshot_empty(handler):
    assert handler.push_snapshot([]) == []


def test_on_message_reports_added_symbols(handler):
    """F3：on_message 报告本次新增订阅符号（传输层据此触发补洞快照）。"""
    reply, added = handler.on_message(
        _req("subscribe", {"symbols": ["600519", "000001"]}, req_id=1)
    )
    assert json.loads(reply)["result"]["subscribed"] == ["000001", "600519"]
    assert added == ["000001", "600519"]

    # 再次订阅相同符号 → 无新增
    reply, added = handler.on_message(_req("subscribe", {"symbols": ["600519"]}, req_id=2))
    assert added == []

    # 非订阅方法 → 无新增
    _, added = handler.on_message(_req("quotes", {"symbols": ["600519"]}, req_id=3))
    assert added == []

    # 退订 → 无新增
    _, added = handler.on_message(_req("unsubscribe", {"symbols": ["000001"]}, req_id=4))
    assert added == []
