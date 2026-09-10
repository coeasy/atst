from __future__ import annotations

from types import SimpleNamespace

import pytest

from tstdx.errors import ConfigError
from tstdx.protocol.commands import Family
from tstdx.transport import ConnectionPool, HostEntry


def test_from_config_explicit_empty_hosts_fails_closed() -> None:
    with pytest.raises(ConfigError, match="explicit hosts 不能为空"):
        ConnectionPool.from_config(None, hosts=[])


def test_from_config_none_still_means_use_configured_or_default_hosts() -> None:
    pool = ConnectionPool.from_config(None, hosts=None)
    try:
        assert pool.hosts
        assert all(entry.family == Family.STANDARD for entry in pool.hosts)
    finally:
        pool.close()


def test_from_config_explicit_hosts_remain_authoritative() -> None:
    explicit = HostEntry(host="1.2.3.4", port=7709, family=Family.STANDARD)
    cfg = SimpleNamespace(
        core=None,
        hosts=SimpleNamespace(
            servers=["9.9.9.9:7709"],
            ranking_file=None,
            auto_speedtest=False,
            max_hosts=8,
            slots_per_host=1,
        ),
        rate_limit=SimpleNamespace(),
        security=SimpleNamespace(use_tls=False),
    )

    # An explicit override must not be replaced by cfg.hosts.servers.
    pool = ConnectionPool.from_config(cfg, hosts=[explicit])
    try:
        assert [entry.key for entry in pool.hosts] == ["1.2.3.4:7709"]
    finally:
        pool.close()


def test_public_factory_is_hardened_before_export() -> None:
    assert ConnectionPool.from_config.__func__.__module__ == (
        "tstdx.transport._pool_factory_hardening"
    )
