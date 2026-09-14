from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from tstdx.integration.ws_app import JsonRpcHandler


def test_ws_stock_changes_uses_bound_eastmoney_provider_runtime() -> None:
    calls: list[tuple[tuple[int, ...], int, int]] = []

    class Eastmoney:
        def stock_changes(
            self,
            types: tuple[int, ...],
            *,
            page: int,
            size: int,
        ) -> list[dict[str, Any]]:
            calls.append((types, page, size))
            return [{"type": 8201, "name": "sample"}]

    client = SimpleNamespace(
        service=SimpleNamespace(eastmoney=Eastmoney()),
    )
    handler = JsonRpcHandler(client=client)

    result = handler._m_stock_changes(
        {"types": [8201, 8193], "page": 2, "size": 30}
    )

    assert result == [{"type": 8201, "name": "sample"}]
    assert calls == [((8201, 8193), 2, 30)]


def test_ws_stock_changes_rejects_non_integer_types_before_provider_call() -> None:
    calls = 0

    class Eastmoney:
        def stock_changes(self, *args: Any, **kwargs: Any) -> list[Any]:
            nonlocal calls
            calls += 1
            return []

    client = SimpleNamespace(
        service=SimpleNamespace(eastmoney=Eastmoney()),
    )
    handler = JsonRpcHandler(client=client)

    raw = handler.handle_message(
        '{"jsonrpc":"2.0","id":"w-invalid","method":"stock_changes",'
        '"params":{"types":[8201,"bad"]}}'
    )

    assert raw is not None
    assert calls == 0
    assert '"code": -32602' in raw
    assert '"fallback_allowed": false' in raw
    assert '"provider_switch_allowed": false' in raw
