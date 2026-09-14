from __future__ import annotations

import json

import pytest

from tstdx.integration.ws_app import JsonRpcHandler


def test_ws_notification_native_failure_is_contained_without_response() -> None:
    class FailingHandler(JsonRpcHandler):
        def _dispatch(self, method, params):  # noqa: ANN001,ANN201
            raise RuntimeError("notification-native-failure")

    handler = FailingHandler()
    raw = json.dumps({"jsonrpc": "2.0", "method": "quotes", "params": {}})
    assert handler.handle_message(raw) is None


def test_ws_notification_does_not_swallow_process_control_baseexception() -> None:
    class InterruptingHandler(JsonRpcHandler):
        def _dispatch(self, method, params):  # noqa: ANN001,ANN201
            raise KeyboardInterrupt("stop")

    handler = InterruptingHandler()
    raw = json.dumps({"jsonrpc": "2.0", "method": "quotes", "params": {}})
    with pytest.raises(KeyboardInterrupt):
        handler.handle_message(raw)
