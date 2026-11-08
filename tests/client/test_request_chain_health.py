# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""核心请求面端到端体检（离线闭环）。

单元测试大量停在「适配器被直接调用」这一层；本文件把真实链路接通：
``Client`` / ``AsyncClient`` → ``QueryPlanner`` → ``DirectProviderExecutor`` →
Provider 实现 → 传输层，并让四张对外面（HTTP / WS / MCP / CLI）跑在同一个
真实 ``Client`` 上。

离线手段两条：

* TDX 链路：``FakeTdxServer`` 回放仓库内真机 golden payload（真实 socket、
  帧编解码、分页、归一化全在链上）；
* Web / 会话链路：在 HTTP 客户端工厂与 ``WebQuoteSession`` 这两个**传输缝**上
  注入本地回放，适配器解析与内核编排保持原样。

断言只钉对外可观察的契约（返回类型 / 字段 / 错误归类），不复制内部实现。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from atst.cli import main as cli_main
from atst.client.api import AsyncClient, Client
from atst.config.schema import Config, CoreConfig
from atst.domain.models import Bar, Quote
from atst.errors import (
    AllHostsUnreachable,
    AllSourcesExhausted,
    CommandOffline,
    SymbolError,
    ValidationError,
)
from atst.runtime.orchestration import FallbackPolicy
from atst.server.mcp._server import MCPServer
from atst.server.runtime_http import create_runtime_app
from atst.server.runtime_ws import RuntimeJsonRpcHandler
from atst.transport.hosts import HostEntry
from tests.sources.test_web_sources import TENCENT_RESPONSE as _TENCENT_QUOTE
from tests.transport.fake_server import FakeTdxServer

_GOLDEN = Path(__file__).resolve().parents[1] / "golden" / "quotation"


def _payload(directory: str) -> bytes:
    """取该 golden 样本最新一次抓包的 payload。"""
    samples = sorted((_GOLDEN / directory).glob("20*"))
    assert samples, f"golden 样本缺失: {directory}"
    return (samples[-1] / "payload.bin").read_bytes()


_QUOTE = _payload("0x0530_realtime_quote_600519")
_BARS = _payload("0x052d_security_bars_600000_cat4")
_MINUTE = _payload("0x0537_minute_today_600000")
_TRADES = _payload("0x0fc5_trade_today_600000")
_COUNT = _payload("0x044e_security_count_market1")
_FINANCE = _payload("0x0010_finance_info_600000")
_CAPITAL = _payload("0x000f_capital_changes_600000")

#: Web 侧回放体：日 K 与 5 分钟 K 各一根，列序与腾讯接口一致
_DAY_KLINE = json.dumps(
    {
        "code": 0,
        "data": {"sh600519": {"day": [["2026-08-31", "9.00", "9.10", "9.20", "8.95", "100"]]}},
    }
)
_M5_KLINE = json.dumps(
    {
        "code": 0,
        "data": {
            "sh600519": {
                "m5": [["2026-08-31 09:35", "9.00", "9.10", "9.20", "8.95", "100", "910.0"]]
            }
        },
    }
)


#: 腾讯当天分时：一行 ``HHMM 价格 累计手数 累计金额``
_TENCENT_MINUTE = json.dumps(
    {
        "code": 0,
        "data": {"sh600519": {"data": {"data": ["0930 9.50 100 950.0"], "date": "2026-08-31"}}},
    }
)


class _Resp:
    def __init__(self, text: str) -> None:
        self._text = text
        self.ok = True
        self.status = 200
        self.status_code = 200

    def text(self, encoding: str = "utf-8") -> str:
        return self._text

    def json(self) -> Any:
        return json.loads(self._text)


class _LoopbackHttp:
    """只认 URL 形状的本地 HTTP 回放；不触网。"""

    def __init__(self) -> None:
        self.urls: list[str] = []

    def get(self, url: str, **_kwargs: Any) -> _Resp:
        self.urls.append(url)
        if "q=" in url:
            return _Resp(_TENCENT_QUOTE)
        if "minute/query" in url:
            return _Resp(_TENCENT_MINUTE)
        if "mkline" in url:
            return _Resp(_M5_KLINE)
        if "kline" in url:
            return _Resp(_DAY_KLINE)
        raise AssertionError(f"未登记的 loopback URL: {url}")

    def close(self) -> None:
        pass


class _LoopbackSession:
    """会话型能力的回放：形状与 ``WebQuoteSession`` 的方法一致。"""

    def __init__(self, *_args: Any, **_kwargs: Any) -> None:
        pass

    def close(self) -> None:
        pass

    def klines(self, symbol, period="day", *, count=320, adjust="qfq"):
        return [
            Bar(
                datetime="2026-08-31 15:00",
                open=10.0,
                high=10.5,
                low=9.8,
                close=10.2,
                volume=100.0,
                amount=1020.0,
            )
        ]

    def index(self):
        return [Quote(code="000001", price=3200.0)]

    def trading_calendar(self, *, year=0, refresh=False):
        return [{"date": "2026-08-31", "is_trading_day": True}]


@pytest.fixture
def tdx_server():
    with FakeTdxServer() as server:
        server.payloads.update(
            {
                0x0530: _QUOTE,
                0x052D: _BARS,
                0x0537: _MINUTE,
                0x0FC5: _TRADES,
                0x044E: _COUNT,
                0x0010: _FINANCE,
                0x000F: _CAPITAL,
            }
        )
        yield server


@pytest.fixture
def tdx_client(tdx_server) -> Any:
    client = Client(
        hosts=[HostEntry("127.0.0.1", tdx_server.port)],
        default_provider="tdx",
        timeout=1.0,
    )
    with client:
        yield client


def test_core_tdx_chain_returns_documented_shapes(tdx_client: Client) -> None:
    quotes = tdx_client.quotes("sh600519", provider="tdx")
    #: ``quotes`` 的对外数据形态是 JSON-ready dict（不是 domain 对象）：四面同一份序列化。
    assert quotes.data and isinstance(quotes.data[0], dict)
    assert quotes.data[0]["code"] == "600519"
    assert quotes.meta is not None

    bars = tdx_client.bars("sh600000", provider="tdx", period="day", count=10)
    assert len(bars.data) == 10
    assert isinstance(bars.data[0], dict)
    assert bars.data[0]["datetime"] and bars.data[0]["close"]

    snapshot = tdx_client.snapshot("sh600519", provider="tdx")
    assert snapshot.data is not None

    minute = tdx_client.minute("sh600000", provider="tdx")
    assert minute.data

    trades = tdx_client.trades("sh600000", provider="tdx", count=10)
    assert trades.data


def test_core_tdx_scalar_and_migrated_capabilities(tdx_client: Client) -> None:
    count = tdx_client.security_count(market=1, provider="tdx")
    assert isinstance(count.data, int) and count.data > 0

    finance = tdx_client.call("finance", "sh600000", provider="tdx")
    assert isinstance(finance.data, dict) and finance.data

    capital = tdx_client.call("capital_changes", "sh600000", provider="tdx")
    assert isinstance(capital.data, list)


def test_async_client_parity_on_the_same_chain(tdx_client: Client) -> None:
    async_client = AsyncClient(client=tdx_client)

    sync_quotes = tdx_client.quotes("sh600519", provider="tdx")
    async_quotes = asyncio.run(async_client.quotes("sh600519", provider="tdx"))
    assert async_quotes.data == sync_quotes.data

    sync_finance = tdx_client.call("finance", "sh600000", provider="tdx")
    async_finance = asyncio.run(async_client.call("finance", "sh600000", provider="tdx"))
    assert async_finance.data == sync_finance.data


def test_explicit_provider_failure_is_loud_not_empty() -> None:
    """全部主机连不上 → 抛错；"没有行情"不能与"取不到行情"长得一样。"""
    server = FakeTdxServer()
    port = server.port
    server.close()
    client = Client(
        hosts=[HostEntry("127.0.0.1", port)],
        config=Config(core=CoreConfig(max_retries=0)),
        timeout=0.3,
    )
    with client, pytest.raises(AllHostsUnreachable) as raised:
        client.quotes("sh600519", provider="tdx")
    assert raised.value.code == "E2040"


def test_provider_and_fallback_conflict_is_client_side_validation(tdx_client: Client) -> None:
    with pytest.raises(ValidationError) as raised:
        tdx_client.quotes(
            "sh600519",
            provider="tdx",
            policy=FallbackPolicy(providers=("tencent",)),
        )
    assert raised.value.code == "E1010"


def test_http_surface_over_real_client(tdx_client: Client) -> None:
    with TestClient(create_runtime_app(tdx_client), raise_server_exceptions=False) as http:
        response = http.get("/v13/quotes", params={"symbols": "sh600519", "provider": "tdx"})
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["data"]

        bars = http.get(
            "/v13/bars/sh600000",
            params={
                "provider": "tdx",
                "period": "day",
                "count": 10,
                "currentness": "historical",
            },
        )
        assert bars.status_code == 200, bars.text
        assert len(bars.json()["data"]) == 10

        assert http.get("/v13/snapshot/sh600519", params={"provider": "tdx"}).json()["data"]
        assert http.get("/v13/minute/sh600000", params={"provider": "tdx"}).json()["data"]
        assert http.get("/v13/trades/sh600000", params={"provider": "tdx", "count": 10}).json()[
            "data"
        ]
        assert http.get("/v13/security/count", params={"market": "1", "provider": "tdx"}).json()[
            "data"
        ]

        offline = http.get("/v13/security/list", params={"market": "1", "provider": "tdx"})
        assert offline.status_code == 501, offline.text
        assert offline.json()["error"]["code"].startswith("E")


def test_ws_surface_over_real_client(tdx_client: Client) -> None:
    handler = RuntimeJsonRpcHandler(tdx_client)
    raw = handler.handle_message(
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "quotes",
                "params": {"symbols": ["sh600519"], "provider": "tdx"},
            }
        )
    )
    assert raw is not None
    body = json.loads(raw)
    assert "error" not in body, body
    assert body["result"]["data"]


def _mcp_result(server: MCPServer, name: str, arguments: dict[str, Any], request_id: int) -> Any:
    response = server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        }
    )
    assert "error" not in response, response
    assert not response["result"].get("isError"), response
    return json.loads(response["result"]["content"][0]["text"])


def test_mcp_surface_over_real_client(tdx_client: Client) -> None:
    server = MCPServer(tdx_client)
    payload = _mcp_result(server, "get_quote", {"symbol": "sh600519", "provider": "tdx"}, 2)
    assert payload["data"]

    assert _mcp_result(
        server,
        "get_bars",
        {"symbol": "sh600000", "provider": "tdx", "count": 10},
        3,
    )["data"]
    assert _mcp_result(server, "get_snapshot", {"symbol": "sh600519", "provider": "tdx"}, 4)["data"]
    assert _mcp_result(server, "get_minute_today", {"symbol": "sh600000", "provider": "tdx"}, 5)[
        "data"
    ]
    assert _mcp_result(
        server,
        "get_trades",
        {"symbol": "sh600000", "provider": "tdx", "count": 10},
        6,
    )["data"]
    assert _mcp_result(server, "get_security_count", {"market": "1", "provider": "tdx"}, 7)["data"]


def test_cli_surface_over_real_host(tdx_server, capsys) -> None:
    code = cli_main(
        [
            "quotes",
            "sh600519",
            "--provider",
            "tdx",
            "--host",
            f"127.0.0.1:{tdx_server.port}",
        ]
    )
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["data"]


def test_cli_core_subcommands_over_real_host(tdx_server, capsys) -> None:
    host = f"127.0.0.1:{tdx_server.port}"
    cases = [
        (["bars", "sh600000", "--provider", "tdx", "--host", host, "--count", "10"], "data"),
        (["snapshot", "sh600519", "--provider", "tdx", "--host", host], "data"),
        (["minute", "sh600000", "--provider", "tdx", "--host", host], "data"),
        (["trades", "sh600000", "--provider", "tdx", "--host", host, "--count", "10"], "data"),
        (["security-count", "--market", "1", "--provider", "tdx", "--host", host], "data"),
    ]
    for argv, key in cases:
        assert cli_main(argv) == 0, argv
        assert json.loads(capsys.readouterr().out)[key], argv

    assert cli_main(["security-list", "--market", "1", "--host", host]) != 0
    capsys.readouterr()


def test_tencent_web_chain_with_loopback_http(monkeypatch) -> None:
    """Web 适配器真实解析：只替换 HTTP 客户端工厂，链上其余环节不动。"""
    http = _LoopbackHttp()
    monkeypatch.setattr("atst.sources._base_core.build_client", lambda **_: http)

    client = Client(default_provider="tencent")
    with client:
        quotes = client.quotes("sh600519", provider="tencent")
        #: ``code`` 保留源侧写法（tencent 带前缀、tdx 主站回显裸码）；
        #: canonical dict 契约锁键集，不锁跨源前缀归一。
        assert quotes.data and quotes.data[0]["code"].endswith("600519")
        assert quotes.data[0]["price"] > 0

        bars = client.bars("sh600519", provider="tencent", period="day", count=1)
        assert bars.data and bars.data[0]["close"] == 9.1

        minutes = client.call(
            "minute_klines",
            "sh600519",
            provider="tencent",
            period="5min",
            count=1,
        )
        #: 目录型 K 线能力（minute_klines / adjusted_bars）沿用 domain ``Bar``；
        #: 与核心 ``bars`` 的 canonical dict 行不是同一条出口（既有契约如此）。
        assert minutes.data and minutes.data[0].datetime == "2026-08-31 09:35"

    async_quotes = asyncio.run(
        AsyncClient(client=Client(default_provider="tencent")).quotes(
            "sh600519", provider="tencent"
        )
    )
    assert async_quotes.data


def test_tencent_direct_adapter_capability_is_wired(monkeypatch) -> None:
    """``direct_adapter`` 后端（tencent minute）在真实执行体里可解析到适配器类。

    该分支按 ``CHANNEL_ADAPTERS`` 现解析类；重构后曾残留指向已删除模块的
    导入，静态门禁（mypy）能看见、但只有真正调用才会在运行期炸。这里钉住
    「能力名 → 绑定 → 适配器 → 解析结果」这条链。
    """
    http = _LoopbackHttp()
    monkeypatch.setattr("atst.sources._base_core.build_client", lambda **_: http)

    client = Client(default_provider="tencent")
    with client:
        minute = client.minute("sh600519", provider="tencent")

    assert minute.data
    assert minute.data[0].price == 9.5


def test_adjusted_bars_composes_real_raw_bars(monkeypatch) -> None:
    """组合能力：真实取原始 K 线 + 空事件集（显式不调整），不再偷偷回源。"""
    http = _LoopbackHttp()
    monkeypatch.setattr("atst.sources._base_core.build_client", lambda **_: http)

    client = Client(default_provider="tencent")
    with client:
        adjusted = client.call(
            "adjusted_bars",
            "sh600519",
            provider="tencent",
            period="day",
            count=1,
            events=[],
        )
    #: 组合能力的对外形态是 domain ``Bar``（与 ``bars`` 的 canonical dict 行不同，
    #: 该差异由既有契约测试锁定，不在体检轮改动）。
    assert adjusted.data and adjusted.data[0].close == 9.1


def test_derived_sessions_are_reachable_through_client(monkeypatch) -> None:
    """``klines`` / ``index`` / ``trading_calendar`` 是会话型能力，走同一编排链。"""
    monkeypatch.setattr("atst.sources.session.WebQuoteSession", _LoopbackSession)

    client = Client(default_provider="tdx")
    with client:
        klines = client.call("klines", "sh600519", count=5)
        assert klines.data and isinstance(klines.data[0], Bar)

        index = client.call("index")
        assert index.data and isinstance(index.data[0], Quote)

        calendar = client.call("trading_calendar", year=2026)
        assert calendar.data and calendar.data[0]["date"] == "2026-08-31"


def test_http_migrated_capability_route(monkeypatch) -> None:
    """``POST /v13/query/{capability}`` 走的是同一个 ``Client.call``，不是第二条实现。"""
    monkeypatch.setattr("atst.sources.session.WebQuoteSession", _LoopbackSession)
    client = Client(default_provider="tdx")
    with client, TestClient(create_runtime_app(client), raise_server_exceptions=False) as http:
        ok = http.post(
            "/v13/query/klines",
            json={"args": ["sh600519"], "kwargs": {"count": 5}},
        )
        assert ok.status_code == 200, ok.text
        assert ok.json()["data"]

        reserved = http.post(
            "/v13/query/klines",
            json={"args": ["sh600519"], "kwargs": {"provider": "tdx"}},
        )
        assert reserved.status_code == 422, reserved.text
        assert reserved.json()["error"]["code"] == "E1010"


def test_ws_query_surface_over_real_client(monkeypatch) -> None:
    monkeypatch.setattr("atst.sources.session.WebQuoteSession", _LoopbackSession)
    client = Client(default_provider="tdx")
    with client:
        handler = RuntimeJsonRpcHandler(client)
        body = json.loads(
            handler.handle_message(
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": 10,
                        "method": "query",
                        "params": {
                            "capability": "klines",
                            "args": ["sh600519"],
                            "kwargs": {"count": 5},
                        },
                    }
                )
            )
        )
    assert "error" not in body, body
    assert body["result"]["data"]


def test_cli_query_surface_and_reserved_kwargs(monkeypatch, capsys) -> None:
    monkeypatch.setattr("atst.sources.session.WebQuoteSession", _LoopbackSession)

    code = cli_main(["query", "klines", "--args", '["sh600519"]', "--kwargs", '{"count": 5}'])
    assert code == 0
    assert json.loads(capsys.readouterr().out)["data"]

    assert (
        cli_main(["query", "klines", "--args", '["sh600519"]', "--kwargs", '{"provider": "tdx"}'])
        == 2
    )


def test_security_list_fails_fast_as_documented(tdx_client: Client) -> None:
    """``0x044D`` 已登记 offline：客户端在发包前抛错，而不是回一页空。"""
    with pytest.raises(CommandOffline):
        tdx_client.security_list(market=1)


def test_public_metadata_surfaces_are_callable(tdx_client: Client) -> None:
    capabilities = tdx_client.capabilities()
    assert "quotes" in capabilities and "bars" in capabilities

    statuses = tdx_client.core_capability_statuses()
    assert {"quotes", "bars"} <= set(statuses)
    assert all({"available", "declared_providers"} <= set(item) for item in statuses.values())

    assert isinstance(tdx_client.provider_health_status(), dict)
    assert isinstance(tdx_client.capability_statuses(), dict)


def test_mcp_reports_parameter_conflict_as_invalid_params(tdx_client: Client) -> None:
    response = MCPServer(tdx_client).handle_request(
        {
            "jsonrpc": "2.0",
            "id": 8,
            "method": "tools/call",
            "params": {
                "name": "get_quote",
                "arguments": {
                    "symbol": "sh600519",
                    "provider": "tdx",
                    "fallback": "tencent",
                },
            },
        }
    )
    assert response["error"]["code"] == -32602
    assert response["error"]["data"]["code"] == "E1010"


def test_async_preserves_sync_exception_identity() -> None:
    client = Client(default_provider="tdx")
    async_client = AsyncClient(client=client)
    with client:
        with pytest.raises(ValidationError) as sync_exc:
            client.call("klines", "sh600519", provider="tdx")
        with pytest.raises(ValidationError) as async_exc:
            asyncio.run(async_client.call("klines", "sh600519", provider="tdx"))
    assert sync_exc.value.code == async_exc.value.code == "E1010"


def test_stream_polls_and_stops_without_hanging(tdx_client: Client) -> None:
    """真实轮询 + 显式 stop：不遗留后台线程，也不吞掉首个行情。"""
    import threading

    received = threading.Event()
    stream = tdx_client.stream(
        "sh600519",
        provider="tdx",
        interval=0.05,
        on_quote=lambda code, quote: received.set(),
    )
    try:
        stream.start()
        assert received.wait(5.0), "轮询线程在 5s 内没有产生任何行情"
    finally:
        stream.stop(timeout=2.0)
    assert stream.state.value == "closed"


def test_fallback_policy_switches_and_marks_provenance(monkeypatch) -> None:
    """主源真失败 → 按名单换到 Web 源；来源标记必须显式。"""
    http = _LoopbackHttp()
    monkeypatch.setattr("atst.sources._base_core.build_client", lambda **_: http)

    dead = FakeTdxServer()
    dead_port = dead.port
    dead.close()

    client = Client(
        hosts=[HostEntry("127.0.0.1", dead_port)],
        config=Config(core=CoreConfig(max_retries=0)),
        timeout=0.3,
    )
    with client:
        orchestrated = client.quotes(
            "sh600519",
            policy=FallbackPolicy(providers=("tdx", "tencent")),
        )
    assert orchestrated.result.data
    assert orchestrated.result.meta.provenance.provider == "tencent"
    assert orchestrated.result.meta.provenance.fallback is True
    assert [attempt.status for attempt in orchestrated.attempts] == ["failed", "ok"]


def test_all_sources_exhausted_is_explicit(monkeypatch) -> None:
    """名单里每一跳都失败 → E7040，不允许把失败折成空结果。"""

    def _boom(**_kwargs: Any) -> Any:
        raise TimeoutError("loopback 全断")

    monkeypatch.setattr("atst.sources._base_core.build_client", _boom)

    dead = FakeTdxServer()
    dead_port = dead.port
    dead.close()

    client = Client(
        hosts=[HostEntry("127.0.0.1", dead_port)],
        config=Config(core=CoreConfig(max_retries=0)),
        timeout=0.3,
    )
    with client, pytest.raises(AllSourcesExhausted) as raised:
        client.quotes(
            "sh600519",
            policy=FallbackPolicy(providers=("tdx", "tencent")),
        )
    assert raised.value.code == "E7040"


def test_whole_batch_failure_is_visible_in_errors() -> None:
    """整批都取不到时，``errors`` 必须逐项可见，而不是三态里一片 ``missing``。"""
    server = FakeTdxServer()
    port = server.port
    server.close()

    client = Client(hosts=[HostEntry("127.0.0.1", port)], default_provider="tdx", timeout=0.3)
    with client:
        batch = client.quotes_batch(("sh600519", "sh600000"), provider="tdx")
    assert {item.status for item in batch.items.values()} == {"failed"}
    assert set(batch.errors) == {"sh600519", "sh600000"}
    #: ``partial`` 的契约就是"``errors`` 非空"（BatchResult 自校验），不是"成功失败混合"。
    assert batch.partial is True


def test_empty_symbols_rejected_before_provider_io() -> None:
    client = Client(default_provider="tdx")
    with client, pytest.raises(SymbolError) as raised:
        client.quotes(("",), provider="tdx")
    assert raised.value.code == "E4040"


def test_capability_provider_mismatch_is_validation_error() -> None:
    client = Client(default_provider="tdx")
    with client, pytest.raises(ValidationError) as raised:
        client.call("klines", "sh600519", provider="tdx")
    assert raised.value.code == "E1010"


def test_provider_fallback_conflict_across_wire_faces(tdx_client: Client) -> None:
    """同一个入参冲突在 HTTP / WS / CLI 三面给出同一归类，不是三份抄写。"""
    with TestClient(create_runtime_app(tdx_client), raise_server_exceptions=False) as http:
        response = http.get(
            "/v13/quotes",
            params={"symbols": "sh600519", "provider": "tdx", "fallback": "tencent"},
        )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "E1010"

    handler = RuntimeJsonRpcHandler(tdx_client)
    body = json.loads(
        handler.handle_message(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 7,
                    "method": "quotes",
                    "params": {
                        "symbols": ["sh600519"],
                        "provider": "tdx",
                        "fallback": "tencent",
                    },
                }
            )
        )
    )
    assert body["error"]["code"] == -32602

    assert cli_main(["quotes", "sh600519", "--provider", "tdx", "--fallback", "tencent"]) == 2
