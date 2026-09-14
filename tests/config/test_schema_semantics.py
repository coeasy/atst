"""config schema 语义测试（审计 §3-3）。

覆盖：
* ``config_from_dict`` 返回前 validate（非法值立即报错）；
* ``with_overrides`` 对嵌套 dict 字段深合并（不再整字段替换）；
* ``output.default_format`` 枚举收窄（"model" 无消费者，已移除）；
* Host endpoint 与整数配置保持 fail-closed strict 语义。
"""

from __future__ import annotations

import pytest

from tstdx.config.schema import (
    DEFAULT_CONFIG,
    OutputConfig,
    config_from_dict,
    merge_config,
)

pytestmark = pytest.mark.unit


class TestConfigFromDictValidates:
    """构建即校验。"""

    def test_invalid_core_timeout_raises(self) -> None:
        from tstdx.errors import ValidationError

        with pytest.raises(ValidationError, match="timeout"):
            config_from_dict({"core": {"timeout": 99999}})

    def test_invalid_web_normalize_raises(self) -> None:
        from tstdx.errors import ValidationError

        with pytest.raises(ValidationError, match="normalize"):
            config_from_dict({"web": {"normalize": {"volume": "bushel"}}})

    def test_valid_partial_dict_ok(self) -> None:
        cfg = config_from_dict({"core": {"timeout": 8.0}})
        assert cfg.core.timeout == 8.0
        assert cfg.output.default_format == "dict"  # 未覆盖段保持默认

    def test_merge_config_still_validates(self) -> None:
        from tstdx.errors import ValidationError

        with pytest.raises(ValidationError):
            merge_config({"output": {"default_format": "model"}})

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
        from tstdx.errors import ValidationError

        with pytest.raises(ValidationError, match="hosts.servers"):
            config_from_dict({"hosts": {"servers": servers}})

    def test_host_config_rejects_non_boolean_auto_speedtest(self) -> None:
        from tstdx.errors import ValidationError

        with pytest.raises(ValidationError, match="auto_speedtest"):
            config_from_dict({"hosts": {"auto_speedtest": 1}})

    def test_host_config_rejects_blank_ranking_file(self) -> None:
        from tstdx.errors import ValidationError

        with pytest.raises(ValidationError, match="ranking_file"):
            config_from_dict({"hosts": {"ranking_file": "   "}})

    @pytest.mark.parametrize(
        ("section", "field"),
        [
            ("core", "heartbeat_interval"),
            ("core", "max_retries"),
            ("hosts", "slots_per_host"),
            ("hosts", "max_hosts"),
            ("cache", "ttl"),
            ("web", "max_retries"),
        ],
    )
    def test_integer_fields_reject_fractional_values(self, section: str, field: str) -> None:
        from tstdx.errors import ValidationError

        with pytest.raises(ValidationError, match="必须是整数"):
            config_from_dict({section: {field: 1.5}})


class TestWithOverridesDeepMerge:
    """嵌套 dict 字段深合并：override 键胜出，base 其余键保留。"""

    def test_rate_limit_partial_merge(self) -> None:
        cfg = DEFAULT_CONFIG.with_overrides(rate_limit={"in_session": 3})
        assert cfg.rate_limit.in_session == 3
        assert cfg.rate_limit.closed == 60  # 未覆盖键保留

    def test_sources_enabled_partial_merge(self) -> None:
        cfg = DEFAULT_CONFIG.with_overrides(sources={"enabled": {"tdx": False}})
        assert cfg.sources.enabled["tdx"] is False
        assert cfg.sources.enabled["web"] is True  # 未覆盖键保留
        assert cfg.sources.enabled["reader"] is True

    def test_normalize_partial_merge(self) -> None:
        cfg = DEFAULT_CONFIG.with_overrides(web={"normalize": {"volume": "lot"}})
        assert cfg.web.normalize["volume"] == "lot"
        assert cfg.web.normalize["amount"] == "yuan"  # 默认键保留
        assert cfg.web.normalize["strict"] is True

    def test_headers_partial_merge(self) -> None:
        cfg = DEFAULT_CONFIG.with_overrides(web={"headers": {"Referer": "https://example.com"}})
        assert cfg.web.headers["Referer"] == "https://example.com"
        assert "User-Agent" in cfg.web.headers  # 默认键保留

    def test_scalar_fields_still_replace(self) -> None:
        cfg = DEFAULT_CONFIG.with_overrides(output={"default_format": "dataframe"})
        assert cfg.output.default_format == "dataframe"
        assert cfg.output.df_datetime_index is True

    def test_unknown_section_still_rejected(self) -> None:
        from tstdx.errors import ValidationError

        with pytest.raises(ValidationError, match="未知配置段"):
            DEFAULT_CONFIG.with_overrides(nonexist={"a": 1})


class TestOutputFormatEnum:
    """default_format 枚举收窄："model" 已移除（全库无消费者）。"""

    def test_model_rejected(self) -> None:
        from tstdx.errors import ValidationError

        with pytest.raises(ValidationError, match="dict/tuple/dataframe"):
            OutputConfig(default_format="model").validate()

    def test_valid_values_accepted(self) -> None:
        for fmt in ("dict", "tuple", "dataframe"):
            OutputConfig(default_format=fmt).validate()
