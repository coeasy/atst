"""v17 Phase 6：配置面到传输层的唯一翻译点契约。

``pool_settings_from_config`` 之前，``ConnectionPool.from_config`` 是第二份
配置解释：它读的键名与 ``Config`` 实际字段不重合，用户写的限流值被静默丢弃。
本文件锁定"一个键只有一处含义、不匹配就 fail closed"。
"""

from __future__ import annotations

import pytest

from atst.config.schema import DEFAULT_CONFIG, Config
from atst.errors import ConfigError
from atst.transport.pool import pool_settings_from_config


def test_no_config_yields_pool_defaults():
    assert pool_settings_from_config(None) == {}


def test_dict_config_fails_closed_instead_of_silently_defaulting():
    with pytest.raises(ConfigError, match="Config 或 None"):
        pool_settings_from_config({"hosts": [["1.2.3.4", 7709]]})


def test_settings_mirror_every_wired_execution_key():
    cfg = Config().validate()
    settings = pool_settings_from_config(cfg)

    assert settings["timeout"] == cfg.core.timeout
    assert settings["heartbeat_interval"] == cfg.core.heartbeat_interval
    assert settings["max_retries"] == cfg.core.max_retries
    assert settings["slots_per_host"] == cfg.hosts.slots_per_host
    assert settings["use_tls"] is cfg.security.use_tls
    assert settings["rate_limiter"] is not None


def test_configured_rate_limits_reach_the_limiter():
    """回归：旧工厂读 ``rate_continuous`` 这类不存在的键，配置值全部蒸发。"""

    from atst.transport.ratelimit import SessionState

    cfg = DEFAULT_CONFIG.with_overrides(rate_limit={"continuous": 3})
    limiter = pool_settings_from_config(cfg)["rate_limiter"]

    assert limiter.snapshot()[SessionState.CONTINUOUS].rate == 3


def test_tls_flag_reaches_the_pool():
    cfg = DEFAULT_CONFIG.with_overrides(security={"use_tls": True})

    assert pool_settings_from_config(cfg)["use_tls"] is True
