# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""v16 gateway contract: RuntimeGateway is a thin delegation shell over Client.

These tests lock the single-kernel constitution:

- the gateway owns no provider selection, no fallback, no cache and no
  provenance logic — it delegates to :class:`tstdx.client_api.Client`;
- every business capability (core *and* migrated) is reachable;
- ``QueryResponse`` is only an envelope around the canonical ``QueryResult``.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from tstdx.client_api import Client
from tstdx.orchestration import FallbackPolicy
from tstdx.runtime import QueryRequest, RuntimeGateway, create_runtime


def _query_result(
    data: Any,
    *,
    provider: str = "tdx",
    capability: str = "bars",
    channel: str = "quotation",
) -> Any:
    provenance = SimpleNamespace(
        provider=provider,
        channel=channel,
        capability=capability,
        kind=SimpleNamespace(value="direct"),
        observed_at_ns=1,
        provider_timestamp=None,
        cache_tier=None,
        requested_provider=provider,
        fallback=False,
    )
    meta = SimpleNamespace(
        provenance=provenance,
        channel=channel,
        fingerprint=f"q1:{capability}:{provider}",
    )
    return SimpleNamespace(data=data, meta=meta)


class FakeClient:
    """Recording stand-in for the canonical Client."""

    def __init__(self) -> None:
        self.calls: list[tuple[Any, ...]] = []
        self.closed = False

    def bars(self, symbol: str, **kwargs: Any) -> Any:
        self.calls.append(("bars", symbol, kwargs))
        return _query_result([{"close": 100}])

    def quotes(self, symbols: Any, **kwargs: Any) -> Any:
        self.calls.append(("quotes", symbols, kwargs))
        return _query_result([{"symbol": "test"}], capability="quotes")

    def snapshot(self, symbol: str, **kwargs: Any) -> Any:
        self.calls.append(("snapshot", symbol, kwargs))
        return _query_result({"last": 1}, capability="snapshot")

    def minute(self, symbol: str, **kwargs: Any) -> Any:
        self.calls.append(("minute", symbol, kwargs))
        return _query_result([], capability="minute")

    def trades(self, symbol: str, **kwargs: Any) -> Any:
        self.calls.append(("trades", symbol, kwargs))
        return _query_result([], capability="trades")

    def security_count(self, *, market: Any = 0, **kwargs: Any) -> Any:
        self.calls.append(("security_count", market, kwargs))
        return _query_result(100, capability="security_count")

    def security_list(
        self, *, market: Any = 0, start: int = 0, **kwargs: Any
    ) -> Any:
        self.calls.append(("security_list", market, start, kwargs))
        return _query_result([], capability="security_list")

    def call(self, capability: str, *args: Any, **kwargs: Any) -> Any:
        self.calls.append(("call", capability, args, kwargs))
        return _query_result({"roe": 0.1}, capability=capability)

    def execute_with_policy(self, spec: Any, *, policy: Any) -> Any:
        self.calls.append(("policy", spec, policy))
        return SimpleNamespace(
            result=_query_result(
                [{"close": 1}], provider="tencent", capability="bars", channel="kline"
            ),
            attempts=[SimpleNamespace(provider="tencent", status="ok", code=None)],
        )

    def board_list(self, *args: Any, **kwargs: Any) -> Any:
        self.calls.append(("board_list", args, kwargs))
        return _query_result([], capability="board_list")

    def close(self) -> None:
        self.closed = True


def _make_gateway() -> tuple[RuntimeGateway, FakeClient]:
    client = FakeClient()
    return RuntimeGateway(create_runtime(), client), client


def test_gateway_bars_delegates_to_client() -> None:
    gw, client = _make_gateway()

    result = gw.bars("sh600519", count=30)

    assert result.success is True
    assert result.data == [{"close": 100}]
    name, symbol, kwargs = client.calls[0]
    assert (name, symbol) == ("bars", "sh600519")
    assert kwargs["count"] == 30
    assert kwargs["provider"] is None
    assert kwargs["policy"] is None


def test_gateway_quotes_delegates_to_client() -> None:
    gw, client = _make_gateway()

    result = gw.quotes(("sh600000", "sh600519"))

    assert result.success is True
    assert client.calls[0][0] == "quotes"
    assert client.calls[0][1] == ("sh600000", "sh600519")


def test_gateway_route_maps_to_canonical_provider() -> None:
    gw, client = _make_gateway()

    gw.bars("sh600519", route="tdx")
    gw.bars("sh600519", route="local")
    gw.bars("sh600519", route="auto")

    assert client.calls[0][2]["provider"] == "tdx"
    assert client.calls[1][2]["provider"] == "local_vipdoc"
    assert client.calls[2][2]["provider"] is None


def test_gateway_single_provider_list_selects_provider() -> None:
    gw, client = _make_gateway()

    gw.bars("sh600519", providers=("tencent",))

    assert client.calls[0][2]["provider"] == "tencent"
    assert client.calls[0][2]["policy"] is None


def test_gateway_multi_provider_list_becomes_explicit_policy() -> None:
    gw, client = _make_gateway()

    gw.bars("sh600519", providers=("eastmoney", "tencent"))

    policy = client.calls[0][2]["policy"]
    assert isinstance(policy, FallbackPolicy)
    assert policy.providers == ("eastmoney", "tencent")
    assert client.calls[0][2]["provider"] is None


def test_gateway_security_count_and_list_delegate() -> None:
    gw, client = _make_gateway()

    count = gw.security_count(0)
    listing = gw.security_list(1, start=50)

    assert count.success is True and count.data == 100
    assert listing.success is True and listing.data == []
    assert client.calls[0][:2] == ("security_count", 0)
    assert client.calls[1][:3] == ("security_list", 1, 50)


def test_gateway_finance_info_alias_routes_finance_capability() -> None:
    gw, client = _make_gateway()

    result = gw.finance_info("sh600519")

    assert result.success is True
    assert client.calls == [("call", "finance", ("sh600519",), {})]


def test_gateway_minute_today_alias_routes_minute_capability() -> None:
    gw, client = _make_gateway()

    result = gw.minute_today("sh600519")

    assert result.success is True
    assert client.calls == [("call", "minute", ("sh600519",), {})]


def test_gateway_call_dispatches_any_capability() -> None:
    gw, client = _make_gateway()

    result = gw.call("quotes", "sh600519", provider="tencent")

    assert client.calls == [("call", "quotes", ("sh600519",), {"provider": "tencent"})]
    assert result.meta.provenance.capability == "quotes"


def test_gateway_getattr_reaches_migrated_capabilities() -> None:
    """Regression lock: migrated capabilities must not dead-end in the gateway."""
    gw, client = _make_gateway()

    result = gw.board_list("sh600519")

    assert client.calls == [("board_list", ("sh600519",), {})]
    assert result.meta.provenance.capability == "board_list"


def test_gateway_execute_with_policy_delegates_to_client() -> None:
    gw, client = _make_gateway()
    policy = FallbackPolicy.build("tencent", "eastmoney")

    orchestrated = gw.execute_with_policy(object(), policy=policy)

    assert client.calls[0][0] == "policy"
    assert client.calls[0][2] is policy
    assert orchestrated.attempts[0].provider == "tencent"


def test_gateway_response_carries_canonical_provenance() -> None:
    gw, _ = _make_gateway()

    response = gw.bars("sh600519")

    provenance = response.metadata["provenance"]
    assert provenance["provider"] == "tdx"
    assert provenance["capability"] == "bars"
    assert provenance["kind"] == "direct"
    assert provenance["cache_tier"] is None
    assert provenance["fallback"] is False
    assert response.metadata["query_fingerprint"].startswith("q1:")
    assert response.metadata["execution"] == "client-kernel"


def test_gateway_wraps_client_errors_as_failed_responses() -> None:
    class Boom(FakeClient):
        def bars(self, symbol: str, **kwargs: Any) -> Any:
            raise RuntimeError("boom")

    client = Boom()
    gw = RuntimeGateway(create_runtime(), client)

    result = gw.bars("sh600519")

    assert result.success is False
    assert "boom" in (result.error or "")
    assert result.metadata["error_type"] == "RuntimeError"


def test_gateway_owns_no_second_kernel() -> None:
    gw, _ = _make_gateway()

    assert not hasattr(gw, "_bridge")
    assert not hasattr(gw, "_adapter")
    assert not hasattr(gw, "semantic_cache_stats")


def test_gateway_close_propagates_to_client() -> None:
    gw, client = _make_gateway()

    gw.close()

    assert client.closed is True


def test_gateway_execute_and_batch_remain_envelope_path() -> None:
    runtime = create_runtime()
    calls: list[str] = []
    runtime.register("bars", lambda p: calls.append("bars") or [{"close": 100}])
    gw = RuntimeGateway(runtime, FakeClient())

    response = gw.execute(QueryRequest(operation="bars", args=("sh600519",)))
    batch = gw.execute_batch(
        [
            QueryRequest(operation="bars", args=("sh600000",)),
            QueryRequest(operation="bars", args=("sh600519",)),
        ],
        max_concurrent=1,
    )

    assert response.success is True
    assert [r.success for r in batch] == [True, True]
    assert calls == ["bars", "bars", "bars"]


def test_gateway_default_construction_owns_real_client() -> None:
    gw = RuntimeGateway()

    assert isinstance(gw.client, Client)
    gw.close()
