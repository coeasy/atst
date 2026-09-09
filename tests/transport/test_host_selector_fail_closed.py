from __future__ import annotations

import pytest

from tstdx.errors import ConfigError
from tstdx.protocol.commands import Family
from tstdx.transport.hosts import resolve_hosts


def test_explicit_empty_server_selector_does_not_restore_default_pool() -> None:
    with pytest.raises(ConfigError, match="explicit servers 不能为空"):
        resolve_hosts([], family=Family.STANDARD)


def test_invalid_environment_selector_does_not_fall_back_to_builtin_pool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TSTDX_HOSTS", "1.2.3.4:not-a-port")

    with pytest.raises(ConfigError, match="TSTDX_HOSTS 条目无效"):
        resolve_hosts(None, family=Family.STANDARD)


def test_environment_selector_rejects_one_bad_token_in_mixed_input(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TSTDX_HOSTS", "1.2.3.4:7709 bad:not-a-port")

    with pytest.raises(ConfigError, match="bad:not-a-port"):
        resolve_hosts(None, family=Family.STANDARD)


def test_environment_selector_rejects_empty_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TSTDX_HOSTS", ":7709")

    with pytest.raises(ConfigError, match="host 为空"):
        resolve_hosts(None, family=Family.STANDARD)


def test_blank_environment_value_remains_equivalent_to_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TSTDX_HOSTS", "   ")

    resolved = resolve_hosts(None, family=Family.F10, use_ranking=False)

    assert resolved
    assert all(entry.family == Family.F10 for entry in resolved)
