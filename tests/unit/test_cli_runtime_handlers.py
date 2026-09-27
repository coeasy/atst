"""CLI 处理器接线测试（v17 Phase 5 收口）。

覆盖 :mod:`atst.cli.runtime_commands` 中此前无测试的分支：

* ``cmd_*``：唯一业务入口 ``Client`` 的选择器透传（provider/channel/policy/currentness）
  与结果信封序列化，以及连接释放；
* ``_cmd_*``：迁移自 v12 的传输层 / Web 源薄壳——表格式输出、空数据、异常出口
  三条路径（旧测试只走 ``--json``）；
* hosts / probe / feedback 薄壳的参数装配与退出码。

全部离线：Client、TdxClient、prober、Web 源、speedtest 均以录制替身注入。
"""

from __future__ import annotations

import argparse
import json
import time
from typing import Any

import pytest

from atst.cli import runtime_commands as rc
from atst.errors import TdxError, ValidationError
from atst.result import Provenance, ProvenanceKind, QueryResult, ResultMeta

pytestmark = pytest.mark.unit


def _ns(**kw: Any) -> argparse.Namespace:
    base: dict[str, Any] = {"host": None, "timeout": 3.0, "json": False}
    base.update(kw)
    return argparse.Namespace(**base)


def _result(
    data: Any,
    *,
    capability: str = "quote",
    provider: str = "tencent",
    channel: str = "web_quote",
) -> QueryResult[Any]:
    provenance = Provenance(
        provider=provider,
        channel=channel,
        capability=capability,
        kind=ProvenanceKind.DIRECT,
        observed_at_ns=time.time_ns(),
    )
    return QueryResult(
        data=data,
        meta=ResultMeta(
            provider=provider,
            channel=channel,
            capability=capability,
            fingerprint="f" * 32,
            provenance=provenance,
        ),
    )


class FakeClient:
    """录制 ``Client`` 的每次调用，返回真实 :class:`QueryResult` 信封。"""

    calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []
    instances: list[FakeClient] = []
    data: Any = []
    error: BaseException | None = None

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        type(self).calls.append(("__init__", args, kwargs))
        type(self).instances.append(self)
        self.init_args = args
        self.init_kwargs = kwargs
        self.closed = False

    def __enter__(self) -> FakeClient:
        return self

    def __exit__(self, *exc: Any) -> bool:
        self.closed = True
        return False

    def close(self) -> None:
        self.closed = True

    @classmethod
    def capabilities(cls) -> frozenset[str]:
        return frozenset({"quote", "bars"})

    def __getattr__(self, name: str) -> Any:
        def call(*args: Any, **kwargs: Any) -> Any:
            cls = type(self)
            cls.calls.append((name, args, kwargs))
            if cls.error is not None:
                raise cls.error
            return _result(cls.data, capability=name)

        call.__name__ = name
        return call


@pytest.fixture()
def fake_client(monkeypatch: pytest.MonkeyPatch) -> type[FakeClient]:
    import atst.client.api as client_api_mod

    FakeClient.calls = []
    FakeClient.instances = []
    FakeClient.data = []
    FakeClient.error = None
    # 顶层命令在模块导入期绑定 Client；_ClientRows 在函数体内 import
    monkeypatch.setattr(rc, "Client", FakeClient)
    monkeypatch.setattr(client_api_mod, "Client", FakeClient)
    return FakeClient


def _last(fake: type[FakeClient], name: str) -> tuple[tuple[Any, ...], dict[str, Any]]:
    for method, args, kwargs in reversed(fake.calls):
        if method == name:
            return args, kwargs
    raise AssertionError(f"{name} was never called: {fake.calls}")


class TestV13QueryHandlers:
    """``cmd_*`` 只经唯一入口 Client，选择器逐项透传。"""

    def test_version(self, capsys: pytest.CaptureFixture[str]) -> None:
        from atst import __version__

        assert rc.cmd_version(_ns()) == 0
        assert capsys.readouterr().out.strip() == __version__

    def test_capabilities(self, fake_client: type[FakeClient], capsys) -> None:  # type: ignore[no-untyped-def]
        assert rc.cmd_capabilities(_ns()) == 0
        payload = json.loads(capsys.readouterr().out)
        assert sorted(payload["capabilities"]) == ["bars", "quote"]

    def test_query_forwards_selectors(
        self, fake_client: type[FakeClient], capsys: pytest.CaptureFixture[str]
    ) -> None:
        fake_client.data = [{"code": "600000"}]
        args = _ns(
            capability="quote",
            args_json='["sh600519"]',
            kwargs_json='{"count": 5}',
            provider="tencent",
            channel="web_quote",
            currentness="live",
        )
        assert rc.cmd_query(args) == 0
        call_args, call_kwargs = _last(fake_client, "call")
        assert call_args == ("quote", "sh600519")
        assert call_kwargs == {
            "provider": "tencent",
            "channel": "web_quote",
            "currentness": "live",
            "count": 5,
        }
        payload = json.loads(capsys.readouterr().out)
        assert payload["data"] == [{"code": "600000"}]
        assert payload["meta"]["provenance"]["kind"] == "direct"
        assert fake_client.instances[-1].closed is True

    @pytest.mark.parametrize(
        ("args_json", "kwargs_json", "message"),
        [
            ("not json", "{}", r"valid JSON"),
            ('{"a": 1}', "{}", "JSON array"),
            ("[]", "[1]", "JSON object"),
        ],
    )
    def test_query_rejects_bad_input(
        self, fake_client: type[FakeClient], args_json: str, kwargs_json: str, message: str
    ) -> None:  # type: ignore[no-untyped-def]
        args = _ns(
            capability="quote",
            args_json=args_json,
            kwargs_json=kwargs_json,
            provider=None,
            channel=None,
            currentness=None,
        )
        with pytest.raises(ValidationError, match=message):
            rc.cmd_query(args)

    def test_quotes_forwards_provider_and_policy(
        self, fake_client: type[FakeClient], capsys: pytest.CaptureFixture[str]
    ) -> None:
        fake_client.data = [{"code": "600000", "price": 10.0}]
        args = _ns(symbols=["600000"], provider="tencent", fallback="tdx,eastmoney")
        assert rc.cmd_quotes(args) == 0
        call_args, call_kwargs = _last(fake_client, "quotes")
        assert call_args == (["600000"],)
        assert call_kwargs["provider"] == "tencent"
        assert call_kwargs["policy"].providers == ("tdx", "eastmoney")
        assert json.loads(capsys.readouterr().out)["meta"]["capability"] == "quotes"

    def test_bars_forwards_range(self, fake_client: type[FakeClient]) -> None:
        args = _ns(
            symbol="sh600519",
            provider=None,
            fallback=None,
            period="day",
            count=320,
            start="2024-01-01",
            adjustment="qfq",
        )
        assert rc.cmd_bars(args) == 0
        call_args, call_kwargs = _last(fake_client, "bars")
        assert call_args == ("sh600519",)
        assert call_kwargs["policy"] is None
        assert call_kwargs["adjustment"] == "qfq"
        assert call_kwargs["start"] == "2024-01-01"
        assert call_kwargs["period"] == "day"
        assert call_kwargs["count"] == 320

    def test_snapshot_and_minute(self, fake_client: type[FakeClient]) -> None:
        fake_client.data = {"code": "600000", "price": 10.0}
        assert rc.cmd_snapshot(_ns(symbol="600000", provider="tencent")) == 0
        assert _last(fake_client, "snapshot") == (("600000",), {"provider": "tencent"})
        assert rc.cmd_minute(_ns(symbol="600000", provider=None)) == 0
        assert _last(fake_client, "minute") == (("600000",), {"provider": None})

    def test_trades_and_security_commands(self, fake_client: type[FakeClient]) -> None:
        assert rc.cmd_trades(_ns(symbol="600000", provider=None, start=800, count=100)) == 0
        assert _last(fake_client, "trades") == (
            ("600000",),
            {"provider": None, "start": 800, "count": 100},
        )
        assert rc.cmd_security_count(_ns(market=1, provider=None)) == 0
        assert _last(fake_client, "security_count") == ((), {"market": 1, "provider": None})
        assert rc.cmd_security_list(_ns(market=0, start=2500, provider=None)) == 0
        assert _last(fake_client, "security_list") == (
            (),
            {"market": 0, "start": 2500, "provider": None},
        )

    def test_policy_builder(self) -> None:
        assert rc._policy(None) is None
        assert rc._policy("") is None
        assert rc._policy(" tdx , eastmoney ").providers == ("tdx", "eastmoney")


class TestHostsHandlers:
    """hosts / server-test 薄壳：参数装配与表格输出。"""

    @staticmethod
    def _entry(key: str, rtt_ms: float | None = 1.5) -> argparse.Namespace:
        return argparse.Namespace(key=key, family="quotation", rtt_ms=rtt_ms, verified=True)

    @staticmethod
    def _probe_row(host: str, ok: bool) -> argparse.Namespace:
        return argparse.Namespace(
            host=host,
            port=7709,
            rtt_ms=1.5 if ok else None,
            ok=ok,
            error=None if ok else "refused",
        )

    @staticmethod
    def _speedtest_mod() -> Any:
        """``atst.transport.speedtest`` 属性被同名函数遮蔽，只能走 sys.modules。"""
        import importlib

        return importlib.import_module("atst.transport.speedtest")

    def test_hosts_audit_assembles_argv(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import atst.tools.host_audit as host_audit_mod

        captured: dict[str, Any] = {}

        def fake_main(argv: list[str]) -> int:
            captured["argv"] = argv
            return 3

        monkeypatch.setattr(host_audit_mod, "main", fake_main)
        args = _ns(
            family=["quotation", "exp"],
            timeout=2.5,
            samples=4,
            workers=8,
            report="r.json",
            markdown="r.md",
            ranking_file="rank.json",
            hosts_file="hosts.json",
            quiet=True,
            strict=True,
            no_save_ranking=True,
        )
        assert rc._cmd_hosts_audit(args) == 3
        assert captured["argv"] == [
            "--family",
            "quotation",
            "--family",
            "exp",
            "--timeout=2.5",
            "--samples=4",
            "--workers=8",
            "--report=r.json",
            "--markdown=r.md",
            "--ranking-file=rank.json",
            "--hosts-file=hosts.json",
            "--quiet",
            "--strict",
            "--no-save-ranking",
        ]

    def test_hosts_audit_skips_absent_flags(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import atst.tools.host_audit as host_audit_mod

        captured: dict[str, Any] = {}

        def fake_main(argv: list[str]) -> int:
            captured["argv"] = argv
            return 0

        monkeypatch.setattr(host_audit_mod, "main", fake_main)
        args = _ns(family=None, timeout=3.0, samples=None, workers=None, report=None)
        assert rc._cmd_hosts_audit(args) == 0
        # getattr 缺省即视为「未开启」：markdown/ranking_file/hosts_file 等不存在
        assert captured["argv"] == ["--timeout=3.0"]

    def test_hosts_scan_prints_table(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        import atst.transport.hosts as hosts_mod

        speedtest_mod = self._speedtest_mod()
        monkeypatch.setattr(hosts_mod, "resolve_hosts", lambda *a, **kw: [self._entry("a")])
        saved: dict[str, Any] = {}

        def fake_save(entries, **kwargs):  # type: ignore[no-untyped-def]
            saved["entries"] = list(entries)
            saved["kwargs"] = kwargs
            return [self._probe_row("a", True), self._probe_row("b", False)]

        monkeypatch.setattr(speedtest_mod, "speedtest_and_save", fake_save)
        assert rc._cmd_hosts_scan(_ns(timeout=2.0)) == 0
        assert saved["kwargs"]["timeout"] == 2.0
        assert saved["entries"][0].key == "a"
        out = capsys.readouterr().out
        assert "a:7709" in out and "b:7709" in out
        assert "1/2 主站可达" in out and "refused" in out

    def test_hosts_list_prints_table(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        import atst.transport.hosts as hosts_mod

        monkeypatch.setattr(
            hosts_mod,
            "resolve_hosts",
            lambda *a, **kw: [self._entry("a"), self._entry("b", rtt_ms=None)],
        )
        assert rc._cmd_hosts_list(_ns()) == 0
        out = capsys.readouterr().out
        assert "共 2 台主站" in out and "verified" in out and "-" in out

    def test_server_test_prints_table(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        import atst.transport.hosts as hosts_mod

        speedtest_mod = self._speedtest_mod()
        monkeypatch.setattr(hosts_mod, "resolve_hosts", lambda *a, **kw: [self._entry("a")])

        def fake_speedtest(entries, **kwargs):  # type: ignore[no-untyped-def]
            assert kwargs["progress"] is True
            assert kwargs["timeout"] == 1.0
            return [self._probe_row("a", True)]

        monkeypatch.setattr(speedtest_mod, "speedtest", fake_speedtest)
        assert rc._cmd_server_test(_ns(timeout=1.0)) == 0
        assert "1/1 主站可达" in capsys.readouterr().out


def _probe_result(cmd_id: int, market: int, code: str, *, ok: bool = True) -> Any:
    class Result:
        def __init__(self) -> None:
            self.ok = ok
            self.frame_size = 64
            self.plausible_record_size = 32
            self.notes = ["plausible"]
            self.draft_path = "PROTOCOL_SPEC/UNKNOWN/053e_DRAFT.yaml"
            self.archived = False

        def to_dict(self) -> dict[str, Any]:
            return {
                "ok": self.ok,
                "cmd_id": self.cmd_id_hex(),
                "frame_size": self.frame_size,
                "plausible_record_size": self.plausible_record_size,
                "notes": list(self.notes),
                "draft_path": self.draft_path,
            }

        def cmd_id_hex(self) -> str:  # pragma: no cover - 仅序列化辅助
            return f"0x{cmd_id:04x}"

    return Result()


class TestProbeHandler:
    """主动探测薄壳的退出码、限流钳制与归档失败可见性。"""

    @staticmethod
    def _install(
        monkeypatch: pytest.MonkeyPatch,
        *,
        prober: Any = None,
        client: Any = None,
    ) -> None:
        import atst.client.factory as factory_mod
        import atst.protocol.prober as prober_mod

        #: 直连命令的构造口是工厂注册表，不是 ``atst.client.TdxClient`` 这个名字——
        #: 改名字拦不住构造，改注册表才拦得住（判据见 test_cli_connection_contract.py）。
        monkeypatch.setitem(factory_mod._CLIENT_REGISTRY, "stock", client or FakeTdxSession)
        monkeypatch.setattr(prober_mod, "Prober", prober)

    def test_illegal_cmd_number_exit_2(self, capsys: pytest.CaptureFixture[str]) -> None:
        args = _ns(cmd="not-a-command", market=1, code="600519", rate_limit=1.0)
        assert rc._cmd_probe(args) == 2
        err = capsys.readouterr().err
        assert "非法命令号" in err and "用法" in err

    def test_client_error_exit_2(self, monkeypatch: pytest.MonkeyPatch, capsys) -> None:  # type: ignore[no-untyped-def]
        import atst.client.factory as factory_mod

        def boom(*a: Any, **kw: Any) -> None:
            raise TdxError("no host reachable")

        monkeypatch.setitem(factory_mod._CLIENT_REGISTRY, "stock", boom)
        args = _ns(
            cmd="0x053e",
            market=1,
            code="600519",
            rate_limit=1.0,
            archive_dir=None,
            allow_trading_hours=False,
            json=True,
        )
        assert rc._cmd_probe(args) == 2
        assert "no host reachable" in capsys.readouterr().err

    def test_ok_prints_text_then_json(self, monkeypatch: pytest.MonkeyPatch, capsys) -> None:  # type: ignore[no-untyped-def]
        inits: list[dict[str, Any]] = []
        results: list[Any] = []

        class Probe:
            def __init__(self, **kwargs: Any) -> None:
                inits.append(kwargs)

            def probe_command(self, cmd_id: int, *, market: int, code: str) -> Any:
                res = _probe_result(cmd_id, market, code)
                results.append(res)
                return res

            def archive(self, result: Any) -> None:
                result.archived = True

        self._install(monkeypatch, prober=Probe)
        args = _ns(
            cmd="1342",
            market=1,
            code="600519",
            rate_limit=99.0,
            archive_dir="arch",
            allow_trading_hours=False,
            json=False,
        )
        assert rc._cmd_probe(args) == 0
        out = capsys.readouterr().out
        assert "命令 0x053E 探测完成" in out
        assert "plausible_record    : 32" in out
        assert "draft               : PROTOCOL_SPEC" in out
        assert inits[0]["rate_limit"] == 5.0  # 99 → 上限 5.0
        assert inits[0]["block_offline_only"] is True
        assert inits[0]["archive_dir"] == "arch"
        assert results[0].archived is True

        assert rc._cmd_probe(_ns(**{**args.__dict__, "json": True})) == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["ok"] is True
        assert payload["draft_path"].endswith("053e_DRAFT.yaml")

    def test_failed_probe_exits_1(self, monkeypatch: pytest.MonkeyPatch, capsys) -> None:  # type: ignore[no-untyped-def]
        class Probe:
            def __init__(self, **kwargs: Any) -> None:
                assert kwargs["block_offline_only"] is False  # allow_trading_hours=True

            def probe_command(self, cmd_id: int, *, market: int, code: str) -> Any:
                res = _probe_result(cmd_id, market, code, ok=False)
                res.draft_path = None
                res.notes = []
                return res

            def archive(self, result: Any) -> None:  # pragma: no cover - 失败不归档
                raise AssertionError("failed probe must not be archived")

        self._install(monkeypatch, prober=Probe)
        args = _ns(
            cmd="0x053e",
            market=1,
            code="600519",
            rate_limit=0.01,
            archive_dir=None,
            allow_trading_hours=True,
            json=True,
        )
        assert rc._cmd_probe(args) == 1
        assert json.loads(capsys.readouterr().out)["ok"] is False

    def test_archive_oserror_surfaces_in_notes(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        class Probe:
            def __init__(self, **kwargs: Any) -> None:
                assert kwargs["rate_limit"] == 0.1  # 0.0 → 下限 0.1

            def probe_command(self, cmd_id: int, *, market: int, code: str) -> Any:
                return _probe_result(cmd_id, market, code)

            def archive(self, result: Any) -> None:
                raise OSError("disk full")

        self._install(monkeypatch, prober=Probe)
        args = _ns(
            cmd="0x053e",
            market=1,
            code="600519",
            rate_limit=0.0,
            archive_dir="arch",
            allow_trading_hours=True,
            json=True,
        )
        assert rc._cmd_probe(args) == 0
        payload = json.loads(capsys.readouterr().out)
        assert any("disk full" in note for note in payload["notes"])


class FakeTdxSession:
    """``TdxClient`` 替身：只提供 CLI 用到的方法。

    直连命令现在只经 ``atst.cli._common.family_client`` 交出，收尾动作只有 ``close()``
    —— ``__enter__``/``__exit__`` 不再是这条链上的一件事，所以替身也不装它们。
    """

    behavior: dict[str, Any] = {}
    closes: list[int] = []

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.init_args = args
        self.init_kwargs = kwargs
        type(self).closes.append(0)

    def close(self) -> None:
        type(self).closes[-1] += 1

    def _page(self) -> list[dict[str, Any]]:
        pages = type(self).behavior.get("pages")
        if pages:
            return list(pages.pop(0))
        return list(type(self).behavior.get("rows", []))

    def security_list(self, market: int, start: int = 0) -> list[dict[str, Any]]:
        type(self).behavior.setdefault("markets", []).append((market, start))
        return self._page()

    def block_quotes(self, block_type: int, start: int = 0) -> list[dict[str, Any]]:
        type(self).behavior.setdefault("block_calls", []).append((block_type, start))
        return self._page()

    def quotes_snapshot(self, symbols: list[str]) -> list[dict[str, Any]]:
        type(self).behavior.setdefault("snapshot_symbols", []).append(list(symbols))
        return self._page()


@pytest.fixture()
def fake_tdx(monkeypatch: pytest.MonkeyPatch) -> type[FakeTdxSession]:
    import atst.client.factory as factory_mod

    FakeTdxSession.behavior = {}
    FakeTdxSession.closes = []
    monkeypatch.setitem(factory_mod._CLIENT_REGISTRY, "stock", FakeTdxSession)
    return FakeTdxSession


class TestTransportHandlers:
    """TDX 传输层薄壳的非 JSON 分支（表格 / 空数据 / 异常）。"""

    def test_blocks_table_and_empty(
        self, fake_tdx: type[FakeTdxSession], capsys: pytest.CaptureFixture[str]
    ) -> None:
        fake_tdx.behavior = {"rows": [{"code": "880305", "name": "券商"}]}
        assert rc._cmd_blocks(_ns(block_type=0, count=10, json=False)) == 0
        assert "880305" in capsys.readouterr().out
        assert fake_tdx.behavior["block_calls"] == [(0, 0)]

        fake_tdx.behavior = {"rows": []}
        assert rc._cmd_blocks(_ns(block_type=0, count=10, json=False)) == 0
        assert "无板块行情返回" in capsys.readouterr().out
        #: 两次调用、两份客户端，各自被保护区收尾恰好一次。
        assert fake_tdx.closes == [1, 1], fake_tdx.closes

    def test_blocks_paging_stops_on_short_page(self, fake_tdx: type[FakeTdxSession]) -> None:
        full = [{"code": str(i)} for i in range(1000)]
        fake_tdx.behavior = {"pages": [full, [{"code": "last"}]]}
        assert rc._cmd_blocks(_ns(block_type=1, count=2000, json=True)) == 0
        assert fake_tdx.behavior["block_calls"] == [(1, 0), (1, 1000)]

    def test_blocks_error_exit_2(self, monkeypatch: pytest.MonkeyPatch, capsys) -> None:  # type: ignore[no-untyped-def]
        import atst.client.factory as factory_mod

        def boom(*a: Any, **kw: Any) -> None:
            raise RuntimeError("socket down")

        monkeypatch.setitem(factory_mod._CLIENT_REGISTRY, "stock", boom)
        assert rc._cmd_blocks(_ns(block_type=0, count=10, json=False)) == 2
        assert "socket down" in capsys.readouterr().err

    def test_list_market_alias_and_table(
        self, fake_tdx: type[FakeTdxSession], capsys: pytest.CaptureFixture[str]
    ) -> None:
        from atst.client import _PREFIX_MARKET

        fake_tdx.behavior = {"rows": [{"code": "600000"}]}
        assert rc._cmd_list(_ns(market="sh", start=0, count=100, json=False)) == 0
        assert fake_tdx.behavior["markets"] == [(_PREFIX_MARKET["sh"], 0)]
        out = capsys.readouterr().out
        assert "# 市场 sh 代码表共 1 条" in out and "600000" in out

    def test_list_empty_and_error(
        self,
        fake_tdx: type[FakeTdxSession],
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        fake_tdx.behavior = {"rows": []}
        assert rc._cmd_list(_ns(market="1", start=0, count=10, json=False)) == 0
        assert "无代码表返回" in capsys.readouterr().out

        import atst.client.factory as factory_mod

        def boom(*a: Any, **kw: Any) -> None:
            raise RuntimeError("registry down")

        monkeypatch.setitem(factory_mod._CLIENT_REGISTRY, "stock", boom)
        assert rc._cmd_list(_ns(market="1", start=0, count=10, json=False)) == 2
        assert "registry down" in capsys.readouterr().err

    def test_quotes_snapshot_table_output(
        self, fake_tdx: type[FakeTdxSession], capsys: pytest.CaptureFixture[str]
    ) -> None:
        fake_tdx.behavior = {
            "rows": [
                {
                    "code": "600000",
                    "price": 10.5,
                    "last_close": 10.0,
                    "open": 10.1,
                    "high": 10.9,
                    "low": 10.0,
                    "volume": 123,
                    "amount": 4567.0,
                }
            ]
        }
        assert rc._cmd_quotes_snapshot(_ns(symbols=["600000"], json=False)) == 0
        out = capsys.readouterr().out
        assert "600000" in out and "5.00%" in out
        assert fake_tdx.behavior["snapshot_symbols"] == [["600000"]]

    def test_goods_quote_bars_and_empty(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        import atst.client.factory as factory_mod

        built: list[dict[str, Any]] = []

        class FakeGoods:
            calls: list[tuple[str, dict[str, Any]]] = []
            rows: Any = []
            closes: list[int] = []

            def __init__(self, **kwargs: Any) -> None:
                built.append(kwargs)

            def close(self) -> None:
                type(self).closes.append(1)

            def goods_quote(self, symbol: str, *, as_format: str) -> dict[str, Any]:
                type(self).calls.append(("quote", {"symbol": symbol, "as_format": as_format}))
                return type(self).rows  # type: ignore[no-any-return]

            def goods_bars(
                self, symbol: str, *, period: str, count: int, as_format: str
            ) -> list[dict[str, Any]]:
                type(self).calls.append(("bars", {"symbol": symbol, "period": period}))
                return list(type(self).rows)

        #: 注册表那一格就是构造出口：CLI 问 ``get_client`` 要 "goods"，建出来的必是这只假
        #: 客户端（``__init__`` 记下它拿到的连接参数）；问别的 kind 建不出它，下面那些
        #: ``calls`` 断言当场落空。
        monkeypatch.setitem(factory_mod._CLIENT_REGISTRY, "goods", FakeGoods)
        FakeGoods.calls = []
        FakeGoods.rows = {"symbol": "AU2412", "price": 500.5}
        args = _ns(symbol="AU2412", kind="quote", period="day", count=10, json=False)
        assert rc._cmd_goods(args) == 0
        assert len(built) == 1, "goods 命令没有向工厂要客户端"
        assert "hosts" in built[0] and "timeout" in built[0]
        assert FakeGoods.calls[0] == ("quote", {"symbol": "AU2412", "as_format": "dict"})
        assert "AU2412" in capsys.readouterr().out

        FakeGoods.rows = [{"datetime": "2024-01-02", "open": 1.0, "volume": 2, "amount": 3.0}]
        bars = _ns(symbol="AU2412", kind="bars", period="day", count=10, json=False)
        assert rc._cmd_goods(bars) == 0
        out = capsys.readouterr().out
        assert "2024-01-02" in out and "datetime" in out
        assert FakeGoods.calls[-1] == ("bars", {"symbol": "AU2412", "period": "day"})

        FakeGoods.rows = []
        assert rc._cmd_goods(bars) == 0
        assert "无商品数据返回" in capsys.readouterr().out

    def test_goods_error_exit_2(self, monkeypatch: pytest.MonkeyPatch, capsys) -> None:  # type: ignore[no-untyped-def]
        import atst.client.factory as factory_mod

        def boom(**kw: Any) -> None:
            raise RuntimeError("goods offline")

        monkeypatch.setitem(factory_mod._CLIENT_REGISTRY, "goods", boom)
        args = _ns(symbol="AU2412", kind="quote", period="day", count=10, json=False)
        assert rc._cmd_goods(args) == 2
        assert "goods offline" in capsys.readouterr().err

    def test_f10_catalog_sections_and_empty(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        import atst.client.factory as factory_mod

        class Section:
            def __init__(self, title: str, text: str) -> None:
                self.title = title
                self.text = text

        class FakeF10:
            rows: Any = []
            built: list[dict[str, Any]] = []
            closes: list[int] = []

            def __init__(self, **kwargs: Any) -> None:
                type(self).built.append(kwargs)

            def close(self) -> None:
                type(self).closes.append(1)

            def catalog(self, symbol: str) -> list[dict[str, Any]]:
                return list(type(self).rows)

            def download(self, symbol: str, filename: str) -> bytes:
                return b"raw"

            def parse_text(self, raw: bytes) -> list[Section]:
                return [Section("财务分析", "净利润增长")]

        monkeypatch.setitem(factory_mod._CLIENT_REGISTRY, "f10", FakeF10)
        FakeF10.rows = [{"title": "公司概况", "filename": "gsgk.dat"}]
        assert rc._cmd_f10(_ns(symbol="sh600519", file=None, json=False)) == 0
        out = capsys.readouterr().out
        assert "公司概况" in out and "gsgk.dat" in out
        assert len(FakeF10.built) == 1, "f10 命令没有向工厂要客户端"

        # 无目录 → 「无 F10 数据返回」，但 --file 走解析分支
        FakeF10.rows = []
        assert rc._cmd_f10(_ns(symbol="sh600519", file=None, json=False)) == 0
        assert "无 F10 数据返回" in capsys.readouterr().out
        assert rc._cmd_f10(_ns(symbol="sh600519", file="cwbj.dat", json=False)) == 0
        assert "【财务分析】" in capsys.readouterr().out

    def test_f10_error_exit_2(self, monkeypatch: pytest.MonkeyPatch, capsys) -> None:  # type: ignore[no-untyped-def]
        import atst.client.factory as factory_mod

        def boom(**kw: Any) -> None:
            raise RuntimeError("f10 unavailable")

        monkeypatch.setitem(factory_mod._CLIENT_REGISTRY, "f10", boom)
        assert rc._cmd_f10(_ns(symbol="sh600519", file=None, json=False)) == 2
        assert "f10 unavailable" in capsys.readouterr().err


class TestWebCapabilityHandlers:
    """迁移自 v12 的 Web 能力薄壳：表格 / 空数据 / 异常三分支。"""

    def test_changes_table_empty_and_error(
        self, fake_client: type[FakeClient], capsys: pytest.CaptureFixture[str]
    ) -> None:
        fake_client.data = [
            {
                "time": "09:31:02",
                "code": "600000",
                "name": "浦发银行",
                "change_type": 8201,
                "change_name": "火箭发射",
                "metrics": [1.0, 2.0],
            }
        ]
        args = _ns(types="8201", page=1, size=30, json=False)
        assert rc._cmd_changes(args) == 0
        out = capsys.readouterr().out
        assert "火箭发射" in out and "1.0,2.0" in out
        assert _last(fake_client, "stock_changes")[0] == ([8201],)

        fake_client.data = []
        assert rc._cmd_changes(args) == 0
        assert "当前无异动数据" in capsys.readouterr().out

        fake_client.error = RuntimeError("eastmoney blocked")
        assert rc._cmd_changes(args) == 2
        assert "eastmoney blocked" in capsys.readouterr().err

    def test_hot_json_table_and_error(
        self, fake_client: type[FakeClient], capsys: pytest.CaptureFixture[str]
    ) -> None:
        fake_client.data = [
            {
                "rank": 1,
                "symbol": "SH600000",
                "code": "600000",
                "rank_change": 2,
                "his_rank_change": -1,
            }
        ]
        args = _ns(page=1, size=1, json=True)
        assert rc._cmd_hot(args) == 0
        assert json.loads(capsys.readouterr().out)[0]["symbol"] == "SH600000"
        assert _last(fake_client, "hot_rank")[1] == {"page": 1, "size": 1}

        assert rc._cmd_hot(_ns(page=1, size=1, json=False)) == 0
        assert "SH600000" in capsys.readouterr().out

        fake_client.error = RuntimeError("rank unavailable")
        assert rc._cmd_hot(_ns(page=1, size=1, json=False)) == 2
        assert "rank unavailable" in capsys.readouterr().err

    def test_margin_json_table_empty_and_error(
        self, fake_client: type[FakeClient], capsys: pytest.CaptureFixture[str]
    ) -> None:
        fake_client.data = [
            {
                "date": "2024-01-02",
                "rzye": 1.0e8,
                "rzmre": 2.0e7,
                "rzjme": -1.0e6,
                "rqye": 3.0e5,
                "rzrqye": 1.03e8,
                "rzyezb": 1.5,
            }
        ]
        args = _ns(symbol="600000", days=5, timeout=3.0, json=True)
        assert rc._cmd_margin(args) == 0
        assert json.loads(capsys.readouterr().out)[0]["date"] == "2024-01-02"
        assert _last(fake_client, "margin") == (("600000",), {"days": 5})

        assert rc._cmd_margin(_ns(symbol="600000", days=5, timeout=3.0, json=False)) == 0
        out = capsys.readouterr().out
        assert "# 600000 两融明细最近 1 个交易日" in out and "2024-01-02" in out

        fake_client.data = []
        assert rc._cmd_margin(_ns(symbol="600000", days=5, timeout=3.0, json=False)) == 0
        assert "无融资融券数据" in capsys.readouterr().out

        fake_client.error = RuntimeError("margin down")
        assert rc._cmd_margin(_ns(symbol="600000", days=5, timeout=3.0, json=True)) == 2
        assert "margin down" in capsys.readouterr().err

    def test_sector_flow_table_and_empty(
        self, fake_client: type[FakeClient], capsys: pytest.CaptureFixture[str]
    ) -> None:
        fake_client.data = [
            {
                "code": "BK0475",
                "name": "券商",
                "change_pct": 1.234,
                "main_net": 2.0e8,
                "main_net_ratio": 3.5,
                "super_large_net": 1.0e8,
                "large_net": None,
            }
        ]
        args = _ns(board="industry", sort="main_net", limit=10, timeout=3.0, json=False)
        assert rc._cmd_sector_flow(args) == 0
        out = capsys.readouterr().out
        assert "# industry 板块资金流（按 main_net 降序）" in out
        assert "BK0475" in out and "2.00" in out
        assert _last(fake_client, "sector_flow") == (
            ("industry",),
            {"sort": "main_net", "limit": 10},
        )

        fake_client.data = []
        assert rc._cmd_sector_flow(args) == 0
        assert "无板块资金流数据" in capsys.readouterr().out

        fake_client.error = RuntimeError("flow down")
        assert rc._cmd_sector_flow(args) == 2
        assert "flow down" in capsys.readouterr().err

    def test_bars_and_market_table_outputs(self, fake_client: type[FakeClient], capsys) -> None:  # type: ignore[no-untyped-def]
        fake_client.data = [{"datetime": "2024-01-02", "open": 1.5, "close": 1.6, "volume": 10}]
        adjusted = _ns(symbol="sh600519", method="qfq", period="day", count=2, timeout=3.0)
        assert rc._cmd_adjusted_bars(_ns(**{**adjusted.__dict__, "json": False})) == 0
        assert "# sh600519 day [qfq] 共 1 根" in capsys.readouterr().out

        minute = _ns(symbol="sh600519", period="5min", count=2, timeout=3.0, json=False)
        assert rc._cmd_minute_klines(minute) == 0
        assert "# sh600519 5min 共 1 根" in capsys.readouterr().out

        fake_client.data = [{"code": "600000", "name": "浦发银行", "price": 10.0}]
        all_market = _ns(
            node="hs_a", page_size=80, max_pages=None, source="sina", timeout=3.0, json=False
        )
        assert rc._cmd_all_market(all_market) == 0
        assert "# hs_a（sina）共 1 条" in capsys.readouterr().out

    @pytest.mark.parametrize(
        ("kind", "method", "expected_kwargs", "expected"),
        [
            ("minute", "baidu_minute", {}, "600000"),
            ("ticks", "baidu_ticks", {"limit": 5}, "600000"),
            ("quote", "baidu_quote", {}, "600000"),
            (
                "kline",
                "baidu_kline",
                {"period": "day", "count": 10, "end_time": "20240102"},
                "2024-01-02",
            ),
        ],
    )
    def test_baidu_kinds(
        self,
        fake_client: type[FakeClient],
        kind: str,
        method: str,
        expected_kwargs: dict[str, Any],
        expected: str,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        row = {"code": "600000", "price": 10.0, "datetime": "2024-01-02", "open": 1.5}
        fake_client.data = row if kind == "quote" else [row]
        args = _ns(
            symbol="sh600519",
            kind=kind,
            period="day",
            count=10,
            end_time="20240102",
            limit=5,
            timeout=3.0,
            json=False,
        )
        assert rc._cmd_baidu(args) == 0
        assert _last(fake_client, method) == (("sh600519",), expected_kwargs)
        assert expected in capsys.readouterr().out
        assert fake_client.instances[-1].closed is True

    def test_baidu_error_exit_2(self, fake_client: type[FakeClient], capsys) -> None:  # type: ignore[no-untyped-def]
        fake_client.error = RuntimeError("baidu 403")
        args = _ns(
            symbol="sh600519",
            kind="kline",
            period="day",
            count=10,
            end_time=None,
            limit=5,
            timeout=3.0,
            json=False,
        )
        assert rc._cmd_baidu(args) == 2
        assert "百度财经 kline 获取失败" in capsys.readouterr().err

    def test_fund_actions_and_guard(
        self, fake_client: type[FakeClient], capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert rc._cmd_fund(_ns(action="nav", code=None, timeout=3.0, json=False)) == 2
        assert "需要 <code>" in capsys.readouterr().err
        assert rc._cmd_fund(_ns(action="estimate", code="", timeout=3.0, json=False)) == 2

        fake_client.data = [{"nav_date": "2024-01-02", "nav": 1.23}]
        nav = _ns(action="nav", code="000001", page_size=20, page_index=1, timeout=3.0, json=False)
        assert rc._cmd_fund(nav) == 0
        assert "# 基金 nav 共 1 条" in capsys.readouterr().out
        assert _last(fake_client, "fund_nav_history") == (
            ("000001",),
            {"page_size": 20, "page_index": 1},
        )

        fake_client.data = {"code": "000001", "gsz": 1.24}
        estimate = _ns(
            action="estimate", code="000001", page_size=20, page_index=1, timeout=3.0, json=True
        )
        assert rc._cmd_fund(estimate) == 0
        assert json.loads(capsys.readouterr().out) == [{"code": "000001", "gsz": 1.24}]

        fake_client.data = [{"code": "000001"}]
        listing = _ns(action="list", code=None, page_size=20, page_index=1, timeout=3.0, json=False)
        assert rc._cmd_fund(listing) == 0
        assert _last(fake_client, "fund_list") == ((), {})

    def test_fund_error_exit_2(self, fake_client: type[FakeClient], capsys) -> None:  # type: ignore[no-untyped-def]
        fake_client.error = RuntimeError("fund down")
        listing = _ns(action="list", code=None, page_size=20, page_index=1, timeout=3.0, json=False)
        assert rc._cmd_fund(listing) == 2
        assert "基金 list 获取失败" in capsys.readouterr().err

    def test_index_constituents_paths(
        self, fake_client: type[FakeClient], capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert rc._cmd_index(_ns(action="constituents", code="", timeout=3.0, json=False)) == 2
        assert "index constituents 需要 <code>" in capsys.readouterr().err

        fake_client.data = [{"code": "600000", "name": "浦发银行"}]
        args = _ns(action="constituents", code="000300", timeout=3.0, json=False)
        assert rc._cmd_index(args) == 0
        assert "# 指数 000300 成分股共 1 条" in capsys.readouterr().out
        assert _last(fake_client, "index_constituents") == (("000300",), {})

        assert rc._cmd_index(_ns(action="constituents", code="000300", timeout=3.0, json=True)) == 0
        assert json.loads(capsys.readouterr().out)[0]["code"] == "600000"

    def test_index_error_exit_2(self, fake_client: type[FakeClient], capsys) -> None:  # type: ignore[no-untyped-def]
        fake_client.error = RuntimeError("index down")
        args = _ns(action="constituents", code="000300", timeout=3.0, json=True)
        assert rc._cmd_index(args) == 2
        assert "指数成分获取失败" in capsys.readouterr().err


class TestFeedbackHandler:
    """feedback 薄壳：提交成功分支与统计表格（不依赖真实环境变量）。"""

    def test_submit_success(self, monkeypatch: pytest.MonkeyPatch, capsys) -> None:  # type: ignore[no-untyped-def]
        import atst.feedback as feedback_mod

        recorded: dict[str, Any] = {}

        class FakeReporter:
            def __init__(self, **kwargs: Any) -> None:
                recorded["init"] = kwargs

            def report_error(self, error: Any, *, context: Any = None) -> bool:
                recorded["error"] = str(error)
                recorded["context"] = context
                return True

        monkeypatch.setattr(feedback_mod, "FeedbackReporter", FakeReporter)
        args = _ns(feedback_command="submit", endpoint="http://x", store_dir="store", message="m")
        assert rc._cmd_feedback(args) == 0
        assert "反馈已提交" in capsys.readouterr().out
        assert recorded["init"] == {"endpoint": "http://x", "store_dir": "store"}
        assert recorded["context"] == {"source": "cli"}

    def test_stats_table(self, monkeypatch: pytest.MonkeyPatch, capsys) -> None:  # type: ignore[no-untyped-def]
        import atst.feedback as feedback_mod

        class FakeStats:
            def snapshot(self) -> dict[str, Any]:
                return {"total_commands": 3, "version": "1.1.0"}

        monkeypatch.setattr(feedback_mod, "UserStats", FakeStats)
        assert rc._cmd_feedback(_ns(feedback_command="stats", json=False)) == 0
        out = capsys.readouterr().out
        assert "total_commands" in out and "3" in out


class TestCmdNumberParsing:
    """命令号解析：十六进制前缀 / 十进制 / 非法。"""

    @pytest.mark.parametrize(
        ("text", "expected"),
        [("0x053e", 1342), ("0X053E", 1342), ("1342", 1342), (" 0x053e ", 1342)],
    )
    def test_valid(self, text: str, expected: int) -> None:
        assert rc._parse_cmd_number(text) == expected

    def test_invalid(self) -> None:
        with pytest.raises(ValueError, match="非法命令号"):
            rc._parse_cmd_number("0xzz")
