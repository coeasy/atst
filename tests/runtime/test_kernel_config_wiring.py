"""v17 Phase 6：配置面必须真实贯通到单一内核执行链（F-16）。

回归的是"写了 TOML 不生效"这一 P0 谎话：``tstdx.toml`` / 环境变量里的执行
参数必须原样出现在 ``Client()`` 的规划器与执行器上；显式构造参数优先于配置；
配置里的键必须一路到达传输层构造参数。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from tstdx import Client
from tstdx.config.loader import reset_config
from tstdx.config.schema import DEFAULT_CONFIG
from tstdx.runtime.executor import DirectProviderExecutor
from tstdx.transport.pool import pool_settings_from_config

_TOML = """
[core]
default_provider = "tencent"
timeout = 7.5
max_retries = 1
vipdoc_root = "D:/tdx/vipdoc"

[hosts]
servers = [["119.147.212.81", 443]]
slots_per_host = 6

[rate_limit]
continuous = 11

[security]
use_tls = true
"""


@pytest.fixture(autouse=True)
def _isolated_process_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """每个用例前后清空进程级配置，并隔离项目/用户/系统配置发现。"""

    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("TSTDX_CONFIG_FILE", raising=False)
    reset_config()
    yield
    reset_config()


def _write_project_config(tmp_path: Path) -> None:
    (tmp_path / "tstdx.toml").write_text(_TOML, encoding="utf-8")


def test_project_toml_drives_the_kernel() -> None:
    _write_project_config(Path.cwd())

    client = Client()
    try:
        assert client.runtime.planner.default_provider == "tencent"
        assert client.runtime.executor.timeout == 7.5
        assert client.runtime.executor.vipdoc_root == "D:/tdx/vipdoc"
        assert client.runtime.executor.hosts == [["119.147.212.81", 443]]
    finally:
        client.close()


def test_wired_config_reaches_the_transport_layer() -> None:
    _write_project_config(Path.cwd())

    client = Client()
    try:
        settings = pool_settings_from_config(client.runtime.config)
    finally:
        client.close()

    assert settings["max_retries"] == 1
    assert settings["slots_per_host"] == 6
    assert settings["use_tls"] is True
    assert settings["rate_limiter"].snapshot()["continuous"].rate == 11


def test_environment_variable_overrides_the_file(monkeypatch: pytest.MonkeyPatch) -> None:
    _write_project_config(Path.cwd())
    monkeypatch.setenv("TSTDX_CORE_TIMEOUT", "2.5")

    client = Client()
    try:
        assert client.runtime.executor.timeout == 2.5
    finally:
        client.close()


def test_explicit_constructor_arguments_win_over_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_project_config(Path.cwd())
    monkeypatch.setenv("TSTDX_CONFIG_FILE", str(Path.cwd() / "tstdx.toml"))

    client = Client(timeout=1.25, default_provider="sina", vipdoc_root="E:/other")
    try:
        assert client.runtime.executor.timeout == 1.25
        assert client.runtime.planner.default_provider == "sina"
        assert client.runtime.executor.vipdoc_root == "E:/other"
    finally:
        client.close()


def test_executor_forwards_configured_pool_settings_to_the_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    def fake_client(hosts: Any = None, **kwargs: Any) -> object:
        captured["hosts"] = hosts
        captured.update(kwargs)
        return object()

    monkeypatch.setattr("tstdx.client.TdxClient", fake_client)
    cfg = DEFAULT_CONFIG.with_overrides(
        hosts={"slots_per_host": 5},
        security={"use_tls": True},
    )

    DirectProviderExecutor(timeout=4.0, hosts=[["1.2.3.4", 7709]], config=cfg)._tdx_client()

    assert captured == {
        "hosts": [["1.2.3.4", 7709]],
        "timeout": 4.0,
        "heartbeat_interval": cfg.core.heartbeat_interval,
        "max_retries": cfg.core.max_retries,
        "slots_per_host": 5,
        "use_tls": True,
        "rate_limiter": captured["rate_limiter"],
    }


def test_injected_executor_keeps_the_kernel_config_for_provenance() -> None:
    """注入假执行面时不得触碰网络配置解析，但配置仍是内核的一部分。"""

    class _Fake:
        def execute(self, plan: Any) -> Any:  # pragma: no cover - 断言用不到
            raise AssertionError("not called")

    client = Client(executor=_Fake())
    try:
        assert client.runtime.config.core.timeout == DEFAULT_CONFIG.core.timeout
        assert client.runtime.executor is not None
    finally:
        client.close()
