"""cli.py 退出码与参数接线语义测试（计划 §3-3 / F4-V5）。

覆盖（全部离线，mock client / prober）：
* ``quotes-snapshot`` 全失败 → exit 1 + stderr last_errors 摘要；
* ``stream`` 零数据成功 → exit 1（与 snapshot 同步）；
* ``changes --types`` 非法值 → exit 2 + usage（不再裸 traceback）；
* ``serve --port 0`` 透传 0（不被 ``or 8000`` 短路）；
* ``list --start`` 分页起始接线；
* ``feedback submit/stats`` 与 ``probe`` 薄壳接线。
"""

from __future__ import annotations

import argparse
import json
from typing import Any

import pytest

import tstdx.cli as cli
from tstdx.cli import _cmd_changes, _cmd_list, _cmd_quotes_snapshot, _cmd_serve, main

pytestmark = pytest.mark.unit


class FakeTdxClient:
    """可编程假 client（模拟 TdxClient 上下文管理器语义）。"""

    behavior: dict[str, Any] = {}

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.init_kwargs = kwargs
        self.last_errors: list[tuple[str, BaseException]] = list(
            FakeTdxClient.behavior.get("last_errors", [])
        )

    def __enter__(self) -> FakeTdxClient:
        return self

    def __exit__(self, *exc: Any) -> bool:
        return False

    def quotes_snapshot(self, symbols: list[str]) -> list[dict[str, Any]]:
        return list(FakeTdxClient.behavior.get("quotes_snapshot", []))

    def security_list(self, market: int, start: int = 0) -> list[dict[str, Any]]:
        FakeTdxClient.behavior.setdefault("security_list_calls", []).append(start)
        pages = FakeTdxClient.behavior.setdefault("security_list_pages", [])
        if pages:
            return pages.pop(0)
        return list(FakeTdxClient.behavior.get("security_list", []))


@pytest.fixture()
def fake_client(monkeypatch: pytest.MonkeyPatch) -> type[FakeTdxClient]:
    import tstdx.client as client_mod

    monkeypatch.setattr(client_mod, "TdxClient", FakeTdxClient)
    FakeTdxClient.behavior = {}
    return FakeTdxClient


def _ns(**kw: Any) -> argparse.Namespace:
    base = {"host": None, "timeout": 3.0, "json": False, "provider": "tdx"}
    base.update(kw)
    return argparse.Namespace(**base)


class TestQuotesSnapshotExitCode:
    """全失败 → exit 1 + stderr 错误摘要（V5 契约）。"""

    def test_total_failure_exits_1_with_summary(
        self, fake_client: type[FakeTdxClient], capsys: pytest.CaptureFixture[str]
    ) -> None:
        fake_client.behavior = {
            "quotes_snapshot": [],
            "last_errors": [("h1:7709", RuntimeError("conn refused"))],
        }
        rc = _cmd_quotes_snapshot(_ns(symbols=["600000", "000001"]))
        assert rc == 1
        err = capsys.readouterr().err
        assert "h1:7709" in err and "conn refused" in err  # stderr 摘要含错误明细

    def test_partial_results_still_ok(self, fake_client: type[FakeTdxClient]) -> None:
        fake_client.behavior = {
            "quotes_snapshot": [{"code": "600000", "price": 10.0}],
        }
        rc = _cmd_quotes_snapshot(_ns(symbols=["600000", "000001"]))
        assert rc == 0

    def test_no_last_errors_falls_back_to_unknown_reason(
        self, fake_client: type[FakeTdxClient], capsys: pytest.CaptureFixture[str]
    ) -> None:
        fake_client.behavior = {"quotes_snapshot": [], "last_errors": []}
        rc = _cmd_quotes_snapshot(_ns(symbols=["600000"]))
        assert rc == 1
        assert "未知原因" in capsys.readouterr().err


class TestChangesTypesParsing:
    """--types 解析进 try：ValueError → exit 2 + usage。"""

    def test_invalid_types_exit_2(self, monkeypatch: pytest.MonkeyPatch, capsys) -> None:  # type: ignore[no-untyped-def]
        # 不触网：解析先于网络调用失败
        rc = main(["changes", "--types", "abc,def"])
        assert rc == 2
        err = capsys.readouterr().err
        assert "--types" in err

    def test_valid_types_reaches_the_kernel(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls: dict[str, Any] = {}

        class FakeClient:
            def __init__(self, *a: Any, **kw: Any) -> None:
                pass

            def close(self) -> None:
                pass

            def stock_changes(self, types, *, page=1, size=30):  # type: ignore[no-untyped-def]
                calls["types"] = tuple(types)
                calls["size"] = size
                return argparse.Namespace(data=[{"code": "600000"}])

        monkeypatch.setattr("tstdx.client.api.Client", FakeClient)
        rc = _cmd_changes(_ns(types="8201,8193", page=1, size=30, json=True))
        assert rc == 0
        assert calls["types"] == (8201, 8193)
        assert calls["size"] == 30


class TestStreamExitCode:
    """stream 零数据 → exit 1（与 quotes-snapshot 同步）。

    替身换在 ``Client`` 上而非 ``tstdx.streaming`` 上：F-56 之后 CLI 不再自建流，
    ``cmd_stream`` 唯一的执行入口就是 ``Client.stream``。
    """

    @staticmethod
    def _fake_client(seen: dict[str, Any], *, emit: bool) -> Any:
        class FakeStream:
            def start(self) -> FakeStream:
                return self

            def stop(self) -> None:
                return None

        class FakeClient:
            def __init__(self, *a: Any, **kw: Any) -> None:
                seen["client"] = kw

            def __enter__(self) -> FakeClient:
                return self

            def __exit__(self, *exc: Any) -> None:
                return None

            def stream(self, symbols, **kwargs):  # type: ignore[no-untyped-def]
                seen["stream"] = kwargs
                if emit:
                    kwargs["on_quote"](symbols[0], {"price": 10.0, "volume": 1})
                return FakeStream()

        return FakeClient

    def test_zero_quotes_exits_1(self, monkeypatch: pytest.MonkeyPatch, capsys) -> None:  # type: ignore[no-untyped-def]
        seen: dict[str, Any] = {}
        monkeypatch.setattr(
            "tstdx.cli.runtime_commands.Client", self._fake_client(seen, emit=False)
        )
        rc = cli._cmd_stream(
            _ns(symbols=["600000"], interval=1.0, seconds=0.01, diff=False, max_queue=1024)
        )
        assert rc == 1
        assert "未收到任何行情" in capsys.readouterr().err
        #: 连接参数走内核助手，不再由 CLI 自带字面默认值（F-27/F-56）。
        assert seen["client"] == {"hosts": None, "timeout": 3.0}

    def test_data_received_exits_0(self, monkeypatch: pytest.MonkeyPatch, capsys) -> None:  # type: ignore[no-untyped-def]
        seen: dict[str, Any] = {}
        monkeypatch.setattr("tstdx.cli.runtime_commands.Client", self._fake_client(seen, emit=True))
        rc = cli._cmd_stream(
            _ns(symbols=["600000"], interval=1.0, seconds=0.01, diff=False, max_queue=32)
        )
        assert rc == 0
        assert seen["stream"]["max_queue"] == 32
        assert seen["stream"]["provider"] == "tdx"


class TestServePortZero:
    """--port 0 透传（OS 随机端口是合法值）。"""

    @pytest.fixture()
    def captured_run(self, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
        import uvicorn

        captured: dict[str, Any] = {}

        def fake_run(app: Any, host: str, port: int) -> None:
            captured["port"] = port

        monkeypatch.setattr(uvicorn, "run", fake_run)
        return captured

    def test_port_zero_not_swapped(self, captured_run: dict[str, Any]) -> None:
        rc = _cmd_serve(_ns(port=0, bind=None))
        assert rc == 0 or rc is None
        assert captured_run["port"] == 0

    def test_default_port_is_8000(self, captured_run: dict[str, Any]) -> None:
        _cmd_serve(_ns(port=None, bind=None))
        assert captured_run["port"] == 8000


class TestListStartWired:
    """list --start 作为分页起始偏移。"""

    def test_start_offset_used(self, fake_client: type[FakeTdxClient]) -> None:
        # 首页满 1000 条 → 继续下一页；次页不足 → 停
        full = [{"code": f"{600000 + i}"} for i in range(1000)]
        fake_client.behavior = {"security_list_pages": [full, [{"code": "600000"}]]}
        rc = _cmd_list(_ns(market="1", start=2500, count=3000, json=True))
        assert rc == 0
        calls = fake_client.behavior["security_list_calls"]
        assert calls == [2500, 3500]  # start..start+count 步长 1000，末页不足即停

    def test_short_first_page_stops(self, fake_client: type[FakeTdxClient]) -> None:
        fake_client.behavior = {"security_list": [{"code": "600000"}]}
        _cmd_list(_ns(market="1", start=2500, count=3000, json=True))
        assert fake_client.behavior["security_list_calls"] == [2500]

    def test_default_start_zero(self, fake_client: type[FakeTdxClient]) -> None:
        fake_client.behavior = {"security_list": [{"code": "600000"}]}
        _cmd_list(_ns(market="sz", start=0, count=1000, json=True))
        assert fake_client.behavior["security_list_calls"] == [0]


class TestFeedbackSubcommand:
    """feedback submit / stats 接线。"""

    def test_submit_disabled_exits_1(self, monkeypatch: pytest.MonkeyPatch, capsys) -> None:  # type: ignore[no-untyped-def]
        monkeypatch.delenv("TSTDX_FEEDBACK", raising=False)
        rc = main(["feedback", "submit", "--message", "x"])
        assert rc == 1
        assert "TSTDX_FEEDBACK" in capsys.readouterr().err

    def test_submit_dryrun_exits_0(self, monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:  # type: ignore[no-untyped-def]
        monkeypatch.setenv("TSTDX_FEEDBACK", "dry-run")
        rc = main(["feedback", "submit", "--message", "x", "--store-dir", str(tmp_path)])
        assert rc == 0

    def test_stats_json(self, capsys: pytest.CaptureFixture[str]) -> None:
        rc = main(["feedback", "stats", "--json"])
        assert rc == 0
        payload = json.loads(capsys.readouterr().out)
        assert "total_commands" in payload


class TestF10Subcommand:
    """f10 子命令接线：默认列栏目目录；--file 下载并解析正文（mock get_client）。"""

    def _patch_get_client(self, monkeypatch: pytest.MonkeyPatch) -> type:
        class FakeF10:
            calls: list[tuple[str, str | None]] = []

            def __init__(self, *a: Any, **kw: Any) -> None:
                pass

            def __enter__(self) -> FakeF10:
                return self

            def __exit__(self, *exc: Any) -> bool:
                return False

            def catalog(self, symbol: str) -> list[dict[str, Any]]:
                self.calls.append((symbol, None))
                return [{"title": "公司概况", "filename": "gsgk.dat"}]

            def download(self, symbol: str, filename: str) -> bytes:
                self.calls.append((symbol, filename))
                return "【财务分析】净利润增长\n".encode("gbk")

            def parse_text(self, raw: bytes) -> list[Any]:
                from tstdx.protocol.parsers.f10 import parse_f10_text

                return parse_f10_text(raw)

        import tstdx.client as client_mod

        monkeypatch.setattr(client_mod, "get_client", lambda kind, **kw: FakeF10())
        FakeF10.calls = []
        return FakeF10

    def test_catalog_default(self, monkeypatch, capsys) -> None:  # type: ignore[no-untyped-def]
        FakeF10 = self._patch_get_client(monkeypatch)
        rc = cli.main(["f10", "sh600519", "--json"])
        assert rc == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload[0]["title"] == "公司概况"
        assert payload[0]["filename"] == "gsgk.dat"
        assert FakeF10.calls == [("sh600519", None)]

    def test_file_download_and_parse(self, monkeypatch, capsys) -> None:  # type: ignore[no-untyped-def]
        FakeF10 = self._patch_get_client(monkeypatch)
        rc = cli.main(["f10", "sh600519", "--file", "cwbj.dat", "--json"])
        assert rc == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload[0]["title"] == "财务分析"
        assert "净利润增长" in payload[0]["text"]
        assert FakeF10.calls == [("sh600519", "cwbj.dat")]


class TestN5ClientSubcommands:
    """N5：adjusted-bars / all-market / minute-klines 子命令接线（mock 门面，离线）。"""

    def _patch_client(self, monkeypatch: pytest.MonkeyPatch) -> type:
        class FakeApi:
            calls: list[tuple[str, tuple, dict]] = []

            def __init__(self, *a: Any, **kw: Any) -> None:
                self.init_kwargs = kw

            def __enter__(self) -> FakeApi:
                return self

            def __exit__(self, *exc: Any) -> bool:
                return False

            def close(self) -> None:
                return None

            def adjusted_bars(self, symbol, *, method="qfq", period="day", count=320):
                self.calls.append(
                    ("adjusted_bars", (symbol,), dict(method=method, period=period, count=count))
                )
                return [{"symbol": symbol, "method": method, "period": period, "count": count}]

            def all_market(self, *, node="hs_a", page_size=80, max_pages=None, source="sina"):
                self.calls.append(
                    (
                        "all_market",
                        (),
                        dict(node=node, page_size=page_size, max_pages=max_pages, source=source),
                    )
                )
                return [{"node": node, "source": source}]

            def minute_klines(self, symbol, *, period="5min", count=240):
                self.calls.append(("minute_klines", (symbol,), dict(period=period, count=count)))
                return [{"symbol": symbol, "period": period, "count": count}]

        import tstdx.client.api as client_api_mod

        monkeypatch.setattr(client_api_mod, "Client", FakeApi)
        FakeApi.calls = []
        return FakeApi

    def test_adjusted_bars_default_qfq(self, monkeypatch, capsys) -> None:  # type: ignore[no-untyped-def]
        FakeApi = self._patch_client(monkeypatch)
        rc = cli.main(["adjusted-bars", "sh600519", "--json"])
        assert rc == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload[0]["method"] == "qfq"
        assert payload[0]["count"] == 320
        assert FakeApi.calls[0][0] == "adjusted_bars"

    def test_adjusted_bars_method_flag(self, monkeypatch, capsys) -> None:  # type: ignore[no-untyped-def]
        FakeApi = self._patch_client(monkeypatch)
        rc = cli.main(["adjusted-bars", "sh600519", "--method", "hfq", "--count", "100", "--json"])
        assert rc == 0
        assert FakeApi.calls[0][1] == ("sh600519",)
        assert FakeApi.calls[0][2]["method"] == "hfq"
        assert FakeApi.calls[0][2]["count"] == 100

    def test_all_market_defaults(self, monkeypatch, capsys) -> None:  # type: ignore[no-untyped-def]
        FakeApi = self._patch_client(monkeypatch)
        rc = cli.main(["all-market", "--json"])
        assert rc == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload[0]["node"] == "hs_a"
        assert payload[0]["source"] == "sina"
        assert FakeApi.calls[0][0] == "all_market"

    def test_all_market_flags(self, monkeypatch, capsys) -> None:  # type: ignore[no-untyped-def]
        FakeApi = self._patch_client(monkeypatch)
        rc = cli.main(
            ["all-market", "--node", "hk", "--source", "tencent", "--max-pages", "2", "--json"]
        )
        assert rc == 0
        assert FakeApi.calls[0][2]["node"] == "hk"
        assert FakeApi.calls[0][2]["source"] == "tencent"
        assert FakeApi.calls[0][2]["max_pages"] == 2

    def test_minute_klines_defaults(self, monkeypatch, capsys) -> None:  # type: ignore[no-untyped-def]
        FakeApi = self._patch_client(monkeypatch)
        rc = cli.main(["minute-klines", "sh600519", "--json"])
        assert rc == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload[0]["period"] == "5min"
        assert FakeApi.calls[0][0] == "minute_klines"

    def test_minute_klines_flags(self, monkeypatch, capsys) -> None:  # type: ignore[no-untyped-def]
        FakeApi = self._patch_client(monkeypatch)
        rc = cli.main(["minute-klines", "sh600519", "--period", "15min", "--count", "60", "--json"])
        assert rc == 0
        assert FakeApi.calls[0][2]["period"] == "15min"
        assert FakeApi.calls[0][2]["count"] == 60

    def test_unknown_subcommand_rejected(self, capsys) -> None:  # type: ignore[no-untyped-def]
        # 缺必填 symbol → argparse SystemExit(2)（与其它子命令一致）
        with pytest.raises(SystemExit) as exc:
            cli.main(["adjusted-bars"])
        assert exc.value.code == 2
        assert "symbol" in capsys.readouterr().err  # usage 提示


class TestB0BaiduSubcommand:
    """B0：``baidu`` 子命令接线（mock 门面，离线）。"""

    def _patch_client(self, monkeypatch: pytest.MonkeyPatch) -> type:
        class FakeApi:
            calls: list[tuple[str, tuple, dict]] = []

            def __init__(self, *a: Any, **kw: Any) -> None:
                self.init_kwargs = kw

            def __enter__(self) -> FakeApi:
                return self

            def __exit__(self, *exc: Any) -> bool:
                return False

            def close(self) -> None:
                return None

            def baidu_kline(self, symbol, *, period="day", count=320, end_time=None):
                self.calls.append(
                    ("baidu_kline", (symbol,), dict(period=period, count=count, end_time=end_time))
                )
                return [{"symbol": symbol, "period": period, "count": count}]

            def baidu_minute(self, symbol):
                self.calls.append(("baidu_minute", (symbol,), {}))
                return [{"symbol": symbol}]

            def baidu_ticks(self, symbol, *, limit=200):
                self.calls.append(("baidu_ticks", (symbol,), dict(limit=limit)))
                return [{"symbol": symbol, "limit": limit}]

            def baidu_quote(self, symbol):
                self.calls.append(("baidu_quote", (symbol,), {}))
                return {"symbol": symbol}

        import tstdx.client.api as client_api_mod

        monkeypatch.setattr(client_api_mod, "Client", FakeApi)
        FakeApi.calls = []
        return FakeApi

    def test_kline_defaults(self, monkeypatch, capsys) -> None:  # type: ignore[no-untyped-def]
        FakeApi = self._patch_client(monkeypatch)
        rc = cli.main(["baidu", "sh600519", "--json"])
        assert rc == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload[0]["period"] == "day"
        assert payload[0]["count"] == 320
        assert FakeApi.calls[0][0] == "baidu_kline"

    def test_kline_flags(self, monkeypatch, capsys) -> None:  # type: ignore[no-untyped-def]
        FakeApi = self._patch_client(monkeypatch)
        rc = cli.main(
            [
                "baidu",
                "600519",
                "--period",
                "month",
                "--count",
                "60",
                "--end-time",
                "1234",
                "--json",
            ]
        )
        assert rc == 0
        assert FakeApi.calls[0][2]["period"] == "month"
        assert FakeApi.calls[0][2]["count"] == 60
        assert FakeApi.calls[0][2]["end_time"] == 1234

    def test_minute(self, monkeypatch, capsys) -> None:  # type: ignore[no-untyped-def]
        FakeApi = self._patch_client(monkeypatch)
        rc = cli.main(["baidu", "600519", "--kind", "minute", "--json"])
        assert rc == 0
        assert json.loads(capsys.readouterr().out)[0]["symbol"] == "600519"
        assert FakeApi.calls[0][0] == "baidu_minute"

    def test_ticks(self, monkeypatch, capsys) -> None:  # type: ignore[no-untyped-def]
        FakeApi = self._patch_client(monkeypatch)
        rc = cli.main(["baidu", "600519", "--kind", "ticks", "--limit", "50", "--json"])
        assert rc == 0
        assert FakeApi.calls[0][0] == "baidu_ticks"
        assert FakeApi.calls[0][2]["limit"] == 50

    def test_quote(self, monkeypatch, capsys) -> None:  # type: ignore[no-untyped-def]
        FakeApi = self._patch_client(monkeypatch)
        rc = cli.main(["baidu", "600519", "--kind", "quote", "--json"])
        assert rc == 0
        assert json.loads(capsys.readouterr().out)[0]["symbol"] == "600519"
        assert FakeApi.calls[0][0] == "baidu_quote"

    def test_missing_symbol_rejected(self, capsys) -> None:  # type: ignore[no-untyped-def]
        with pytest.raises(SystemExit) as exc:
            cli.main(["baidu"])
        assert exc.value.code == 2
        assert "symbol" in capsys.readouterr().err


class TestP01FundSubcommand:
    """P0-1：``fund`` 子命令接线（mock 门面，离线）。"""

    def _patch_client(self, monkeypatch: pytest.MonkeyPatch) -> type:
        class FakeApi:
            calls: list[tuple[str, tuple, dict]] = []

            def __init__(self, *a: Any, **kw: Any) -> None:
                self.init_kwargs = kw

            def __enter__(self) -> FakeApi:
                return self

            def __exit__(self, *exc: Any) -> bool:
                return False

            def close(self) -> None:
                return None

            def fund_nav_history(self, code, *, page_size=100, page_index=1):
                self.calls.append(
                    ("fund_nav_history", (code,), dict(page_size=page_size, page_index=page_index))
                )
                return [{"code": code, "date": "2026-09-03"}]

            def fund_estimate(self, code):
                self.calls.append(("fund_estimate", (code,), {}))
                return {"code": code, "gsz": 0.8950}

            def fund_list(self):
                self.calls.append(("fund_list", (), {}))
                return [{"code": "161725", "name": "招商中证白酒指数(LOF)A"}]

        import tstdx.client.api as client_api_mod

        monkeypatch.setattr(client_api_mod, "Client", FakeApi)
        FakeApi.calls = []
        return FakeApi

    def test_nav(self, monkeypatch, capsys) -> None:  # type: ignore[no-untyped-def]
        FakeApi = self._patch_client(monkeypatch)
        rc = cli.main(["fund", "nav", "161725", "--json"])
        assert rc == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload[0]["code"] == "161725"
        assert FakeApi.calls[0][2] == {"page_size": 100, "page_index": 1}

    def test_nav_paging(self, monkeypatch, capsys) -> None:  # type: ignore[no-untyped-def]
        FakeApi = self._patch_client(monkeypatch)
        rc = cli.main(["fund", "nav", "161725", "--page-size", "50", "--page-index", "3", "--json"])
        assert rc == 0
        assert FakeApi.calls[0][2] == {"page_size": 50, "page_index": 3}

    def test_estimate(self, monkeypatch, capsys) -> None:  # type: ignore[no-untyped-def]
        FakeApi = self._patch_client(monkeypatch)
        rc = cli.main(["fund", "estimate", "161725", "--json"])
        assert rc == 0
        assert json.loads(capsys.readouterr().out)[0]["code"] == "161725"
        assert FakeApi.calls[0][0] == "fund_estimate"

    def test_list(self, monkeypatch, capsys) -> None:  # type: ignore[no-untyped-def]
        FakeApi = self._patch_client(monkeypatch)
        rc = cli.main(["fund", "list", "--json"])
        assert rc == 0
        assert json.loads(capsys.readouterr().out)[0]["name"] == "招商中证白酒指数(LOF)A"
        assert FakeApi.calls[0][0] == "fund_list"

    def test_nav_missing_code_rejected(self, capsys) -> None:  # type: ignore[no-untyped-def]
        rc = cli.main(["fund", "nav"])
        assert rc == 2
        assert "需要" in capsys.readouterr().err

    def test_invalid_action_rejected(self, capsys) -> None:  # type: ignore[no-untyped-def]
        with pytest.raises(SystemExit) as exc:
            cli.main(["fund", "nope", "161725"])
        assert exc.value.code == 2


class TestP02IndexSubcommand:
    """P0-2：``index`` 子命令接线（mock 门面，离线）。"""

    def _patch_client(self, monkeypatch: pytest.MonkeyPatch) -> type:
        class FakeApi:
            calls: list[tuple[str, tuple, dict]] = []

            def __init__(self, *a: Any, **kw: Any) -> None:
                self.init_kwargs = kw

            def __enter__(self) -> FakeApi:
                return self

            def __exit__(self, *exc: Any) -> bool:
                return False

            def close(self) -> None:
                return None

            def index_constituents(self, index):
                self.calls.append(("index_constituents", (index,), {}))
                return [{"code": "000001", "name": "平安银行", "weight": 0.45}]

        import tstdx.client.api as client_api_mod

        monkeypatch.setattr(client_api_mod, "Client", FakeApi)
        FakeApi.calls = []
        return FakeApi

    def test_constituents(self, monkeypatch, capsys) -> None:  # type: ignore[no-untyped-def]
        FakeApi = self._patch_client(monkeypatch)
        rc = cli.main(["index", "constituents", "000300", "--json"])
        assert rc == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload[0]["code"] == "000001"
        assert FakeApi.calls[0][0] == "index_constituents"
        assert FakeApi.calls[0][1] == ("000300",)

    def test_missing_code_rejected(self, capsys) -> None:  # type: ignore[no-untyped-def]
        rc = cli.main(["index", "constituents"])
        assert rc == 2
        assert "需要" in capsys.readouterr().err

    def test_invalid_action_rejected(self, capsys) -> None:  # type: ignore[no-untyped-def]
        with pytest.raises(SystemExit) as exc:
            cli.main(["index", "nope", "000300"])
        assert exc.value.code == 2


class TestProbeSubcommand:
    """probe 薄壳接线（mock Prober / client）。"""

    def test_invalid_cmd_exits_2(self, capsys: pytest.CaptureFixture[str]) -> None:
        rc = main(["probe", "zzz"])
        assert rc == 2
        assert "0x053e" in capsys.readouterr().err  # usage 提示

    def test_probe_wiring_and_archive(self, monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:  # type: ignore[no-untyped-def]
        import tstdx.client as client_mod
        import tstdx.protocol.prober as prober_mod

        FakeTdxClient.behavior = {}
        monkeypatch.setattr(client_mod, "TdxClient", FakeTdxClient)

        created: dict[str, Any] = {}

        class FakeProbeResult:
            ok = True
            frame_size = 128
            plausible_record_size = 32
            notes: list[str] = []
            draft_path = ""

            def to_dict(self) -> dict[str, Any]:
                return {"ok": True}

        class FakeProber:
            def __init__(self, *a: Any, **kw: Any) -> None:
                created["rate_limit"] = kw.get("rate_limit")
                created["archive_dir"] = kw.get("archive_dir")
                created["block_offline_only"] = kw.get("block_offline_only")

            def probe_command(
                self, cmd_id: int, market: int = 0, code: str = ""
            ) -> FakeProbeResult:
                created["cmd_id"] = cmd_id
                return FakeProbeResult()

            def archive(self, result: FakeProbeResult) -> object:
                created["archived"] = True
                return tmp_path / "draft.yaml"

        monkeypatch.setattr(prober_mod, "Prober", FakeProber)
        rc = main(
            ["probe", "0x053e", "--rate-limit", "99", "--archive-dir", str(tmp_path), "--json"]
        )
        assert rc == 0
        assert created["cmd_id"] == 0x053E
        assert created["rate_limit"] == 5.0  # 钳制到上限
        assert created["block_offline_only"] is True  # 默认拒绝盘中
        assert created["archived"] is True  # ok → 归档 DRAFT

    def test_probe_not_ok_exits_1(self, monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:  # type: ignore[no-untyped-def]
        import tstdx.client as client_mod
        import tstdx.protocol.prober as prober_mod

        monkeypatch.setattr(client_mod, "TdxClient", FakeTdxClient)

        class FakeResult:
            ok = False
            frame_size = 0
            plausible_record_size = 0
            notes = ["no response"]

            def to_dict(self) -> dict[str, Any]:
                return {"ok": False}

        class FakeProber:
            def __init__(self, *a: Any, **kw: Any) -> None:
                pass

            def probe_command(self, cmd_id: int, market: int = 0, code: str = "") -> FakeResult:
                return FakeResult()

            def archive(self, result: FakeResult) -> object:
                raise AssertionError("失败结果不应归档")

        monkeypatch.setattr(prober_mod, "Prober", FakeProber)
        rc = main(["probe", "0x053e", "--json"])
        assert rc == 1
