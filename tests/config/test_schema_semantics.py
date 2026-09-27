"""config schema 语义测试（审计 §3-3）。

覆盖：
* ``config_from_dict`` 返回前 validate（非法值立即报错）；
* ``with_overrides`` 对嵌套 dict 字段深合并（不再整字段替换）；
* Host endpoint 与整数配置保持 fail-closed strict 语义；
* 配置面只包含单一内核真实读取的段（v17 Phase 6：装饰段已物理删除）。
"""

from __future__ import annotations

import pytest

from atst.config.schema import (
    DEFAULT_CONFIG,
    RateLimitConfig,
    config_from_dict,
    merge_config,
)

pytestmark = pytest.mark.unit


class TestConfigFromDictValidates:
    """构建即校验。"""

    def test_invalid_core_timeout_raises(self) -> None:
        from atst.errors import ValidationError

        with pytest.raises(ValidationError, match="timeout"):
            config_from_dict({"core": {"timeout": 99999}})

    def test_unknown_default_provider_raises(self) -> None:
        from atst.errors import ValidationError

        with pytest.raises(ValidationError):
            config_from_dict({"core": {"default_provider": "not-a-provider"}})

    def test_blank_vipdoc_root_raises(self) -> None:
        from atst.errors import ValidationError

        with pytest.raises(ValidationError, match="vipdoc_root"):
            config_from_dict({"core": {"vipdoc_root": "   "}})

    def test_valid_partial_dict_ok(self) -> None:
        cfg = config_from_dict({"core": {"timeout": 8.0}})
        assert cfg.core.timeout == 8.0
        assert cfg.core.default_provider == "tdx"  # 未覆盖字段保持默认

    def test_merge_config_still_validates(self) -> None:
        from atst.errors import ValidationError

        with pytest.raises(ValidationError):
            merge_config({"core": {"max_retries": -1}})

    @pytest.mark.parametrize(
        "servers",
        [
            [["", 7709]],
            [["1.2.3.4", 0]],
            [["1.2.3.4", 65536]],
            [["host example", 7709]],
        ],
    )
    def test_invalid_host_endpoints_fail_during_config_build(self, servers) -> None:
        from atst.errors import ValidationError

        with pytest.raises(ValidationError, match="hosts.servers"):
            config_from_dict({"hosts": {"servers": servers}})

    def test_host_config_rejects_non_list_servers(self) -> None:
        from atst.errors import ValidationError

        with pytest.raises(ValidationError, match="hosts.servers"):
            config_from_dict({"hosts": {"servers": "1.2.3.4:7709"}})

    @pytest.mark.parametrize(
        ("section", "field"),
        [
            ("core", "heartbeat_interval"),
            ("core", "max_retries"),
            ("hosts", "slots_per_host"),
            ("rate_limit", "continuous"),
            ("web", "max_retries"),
        ],
    )
    def test_integer_fields_reject_fractional_values(self, section: str, field: str) -> None:
        from atst.errors import ValidationError

        with pytest.raises(ValidationError, match="必须是整数"):
            config_from_dict({section: {field: 1.5}})

    def test_rate_limit_strict_must_be_bool(self) -> None:
        from atst.errors import ValidationError

        with pytest.raises(ValidationError, match="rate_limit.strict"):
            config_from_dict({"rate_limit": {"strict": 1}})


class TestWithOverridesDeepMerge:
    """嵌套 dict 字段深合并：override 键胜出，base 其余键保留。"""

    def test_rate_limit_partial_merge(self) -> None:
        cfg = DEFAULT_CONFIG.with_overrides(rate_limit={"continuous": 3})
        assert cfg.rate_limit.continuous == 3
        assert cfg.rate_limit.closed == 15  # 未覆盖键保留

    def test_web_rate_limit_partial_merge(self) -> None:
        cfg = DEFAULT_CONFIG.with_overrides(web={"rate_limit": {"eastmoney": 1}})
        assert cfg.web.rate_limit["eastmoney"] == 1

    def test_scalar_fields_still_replace(self) -> None:
        cfg = DEFAULT_CONFIG.with_overrides(core={"timeout": 2.5})
        assert cfg.core.timeout == 2.5
        assert cfg.core.max_retries == 3

    def test_unknown_section_still_rejected(self) -> None:
        from atst.errors import ValidationError

        with pytest.raises(ValidationError, match="未知配置段"):
            DEFAULT_CONFIG.with_overrides(nonexist={"a": 1})


class TestRateLimitDefaults:
    """限流段字段名必须与 :class:`SessionState` 对齐，否则配置被静默丢弃。"""

    def test_field_names_match_limiter_states(self) -> None:
        from atst.transport.ratelimit import DEFAULT_RATES, SessionState

        limiter_rates = RateLimitConfig()
        for state, default in DEFAULT_RATES.items():
            field = {
                SessionState.CALL_AUCTION: limiter_rates.call_auction,
                SessionState.CONTINUOUS: limiter_rates.continuous,
                SessionState.NOON_BREAK: limiter_rates.noon_break,
                SessionState.CLOSED: limiter_rates.closed,
            }[state]
            assert float(field) == default

    def test_limiter_reads_real_values(self) -> None:
        from atst.transport.ratelimit import SessionRateLimiter, SessionState

        limiter = SessionRateLimiter.from_config(
            RateLimitConfig(continuous=7, strict=True),
        )
        assert limiter.snapshot()[SessionState.CONTINUOUS].rate == 7
        assert limiter.strict is True
