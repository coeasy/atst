from __future__ import annotations

import pytest

from tstdx.errors import ConfigError
from tstdx.protocol.commands import Family
from tstdx.transport import ConnectionPool, HostEntry


def test_from_config_explicit_empty_hosts_fails_closed() -> None:
    with pytest.raises(ConfigError, match="explicit hosts 不能为空"):
        ConnectionPool.from_config(None, hosts=[])


def test_from_config_invalid_explicit_cfg_fails_closed() -> None:
    with pytest.raises(ConfigError, match="cfg 必须是 Config 或 None"):
        ConnectionPool.from_config({"hosts": ["1.2.3.4:7709"]})


def test_from_config_none_uses_environment_selector_deterministically(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TSTDX_HOSTS", "9.9.9.9:7709")

    pool = ConnectionPool.from_config(None, hosts=None)
    try:
        assert [entry.key for entry in pool.hosts] == ["9.9.9.9:7709"]
        assert all(entry.family == Family.STANDARD for entry in pool.hosts)
    finally:
        pool.close()


def test_from_config_explicit_hosts_override_environment_selector(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TSTDX_HOSTS", "9.9.9.9:7709")
    explicit = HostEntry(host="1.2.3.4", port=7709, family=Family.STANDARD)

    pool = ConnectionPool.from_config(None, hosts=[explicit])
    try:
        assert [entry.key for entry in pool.hosts] == ["1.2.3.4:7709"]
    finally:
        pool.close()


def test_public_factory_is_hardened_before_export() -> None:
    assert ConnectionPool.from_config.__func__.__module__ == (
        "tstdx.transport._pool_factory_hardening"
    )
