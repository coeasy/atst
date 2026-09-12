from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from tstdx.cli import main as cli_main
from tstdx.direct_provider import DirectProviderExecutor
from tstdx.errors import ValidationError
from tstdx.query import QueryPlanner, QuerySpec


def _bars_plan(provider: str, adjustment: str = "qfq"):
    return QueryPlanner().compile(
        QuerySpec.build(
            "bars",
            symbols="sh600519",
            provider=provider,
            period="day",
            count=10,
            adjustment=adjustment,
            currentness="historical",
        )
    )


def test_tdx_adjustment_fails_before_provider_io(monkeypatch) -> None:
    executor = DirectProviderExecutor()
    imported = False

    def explode(*args, **kwargs):
        nonlocal imported
        imported = True
        raise AssertionError("provider I/O should not happen")

    monkeypatch.setattr(executor, "_tdx_bars", executor._tdx_bars)
    with pytest.raises(ValidationError):
        executor._tdx_bars(_bars_plan("tdx"))
    assert imported is False


def test_local_adjustment_fails_before_facade_io() -> None:
    executor = DirectProviderExecutor(vipdoc_root="/tmp/vipdoc")
    with pytest.raises(ValidationError):
        executor._local_bars(_bars_plan("local_vipdoc"))


def test_cli_normal_exception_is_safe_envelope(monkeypatch, capsys) -> None:
    class Parser:
        def parse_args(self, argv):
            def fail(args):
                raise RuntimeError("secret native detail")

            return SimpleNamespace(func=fail)

    monkeypatch.setattr("tstdx.cli.build_parser", lambda: Parser())
    assert cli_main([]) == 1
    payload = json.loads(capsys.readouterr().err)
    assert payload["error"]["code"] == "E9000"
    assert payload["error"]["message"] == "internal error"
    assert "secret native detail" not in json.dumps(payload)


def test_cli_keyboard_interrupt_keeps_process_control_semantics(monkeypatch) -> None:
    class Parser:
        def parse_args(self, argv):
            def stop(args):
                raise KeyboardInterrupt

            return SimpleNamespace(func=stop)

    monkeypatch.setattr("tstdx.cli.build_parser", lambda: Parser())
    assert cli_main([]) == 130


def test_runtime_http_validation_and_native_failures_use_envelopes() -> None:
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient

    from tstdx.integration.runtime_http import create_runtime_app

    class FailingRuntime:
        class Planner:
            default_provider = "tdx"

        class Executor:
            _bindings = {("tdx", "quotation", "quotes"): object()}

        planner = Planner()
        executor = Executor()

        def quotes(self, *args, **kwargs):
            raise RuntimeError("secret filesystem detail")

        def bars(self, *args, **kwargs):
            raise ValidationError("bad bars", context={"provider": "tdx", "secret": "x"})

    client = TestClient(create_runtime_app(FailingRuntime()))

    invalid = client.get("/v2/bars/sh600519?count=0")
    assert invalid.status_code == 422
    assert invalid.json()["error"]["code"] == "E1010"

    native = client.get("/v2/quotes?symbols=sh600519")
    assert native.status_code == 500
    assert native.json()["error"]["code"] == "E9000"
    assert "secret filesystem detail" not in native.text

    domain = client.get("/v2/bars/sh600519")
    assert domain.status_code == 422
    assert domain.json()["error"]["code"] == "E1010"
    assert domain.json()["error"]["context"] == {"provider": "tdx"}
