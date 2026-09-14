from __future__ import annotations

import math

import pytest

from tstdx.config.schema import Config, config_from_dict, merge_config
from tstdx.errors import ValidationError

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    ("section", "field", "value"),
    [
        ("core", "timeout", math.nan),
        ("core", "timeout", math.inf),
        ("profile", "min_confidence", math.nan),
        ("web", "timeout", math.inf),
    ],
)
def test_non_finite_numeric_config_is_rejected(
    section: str,
    field: str,
    value: float,
) -> None:
    with pytest.raises(ValidationError, match="有限数值"):
        config_from_dict({section: {field: value}})


@pytest.mark.parametrize(
    ("section", "field"),
    [
        ("cache", "enabled"),
        ("output", "df_datetime_index"),
        ("profile", "auto_detect"),
        ("web", "enabled"),
        ("security", "use_tls"),
        ("compatibility", "web_facade"),
        ("compatibility", "market_facade"),
        ("feedback", "telemetry"),
        ("feedback", "report_protocol_diff"),
        ("feedback", "report_source_failure"),
    ],
)
def test_public_boolean_fields_reject_integer_coercion(section: str, field: str) -> None:
    with pytest.raises(ValidationError, match="必须是 bool"):
        config_from_dict({section: {field: 1}})


def test_web_normalize_rejects_unknown_nested_key() -> None:
    with pytest.raises(ValidationError, match="web.normalize 含未知字段"):
        config_from_dict({"web": {"normalize": {"volum": "share"}}})


def test_web_normalize_rejects_non_boolean_strict() -> None:
    with pytest.raises(ValidationError, match="web.normalize.strict"):
        config_from_dict({"web": {"normalize": {"strict": 1}}})


def test_web_headers_reject_non_string_value() -> None:
    with pytest.raises(ValidationError, match="web.headers"):
        config_from_dict({"web": {"headers": {"X-Test": 1}}})


def test_web_rate_limit_rejects_fractional_value() -> None:
    with pytest.raises(ValidationError, match="必须是整数"):
        config_from_dict({"web": {"rate_limit": {"_default": 1.5}}})


def test_sources_enabled_rejects_unknown_key_and_non_bool_value() -> None:
    with pytest.raises(ValidationError, match="sources.enabled 含未知源"):
        config_from_dict({"sources": {"enabled": {"mystery": True}}})
    with pytest.raises(ValidationError, match="必须是 bool"):
        config_from_dict({"sources": {"enabled": {"tdx": 1}}})


def test_observability_wrong_container_types_raise_validation_error() -> None:
    with pytest.raises(ValidationError, match="observability.metrics"):
        config_from_dict({"observability": {"metrics": []}})
    with pytest.raises(ValidationError, match="observability.logging"):
        config_from_dict({"observability": {"logging": "INFO"}})


def test_observability_nested_booleans_are_strict() -> None:
    with pytest.raises(ValidationError, match="metrics.enabled"):
        config_from_dict({"observability": {"metrics": {"enabled": 1}}})
    with pytest.raises(ValidationError, match="logging.json"):
        config_from_dict({"observability": {"logging": {"json": 1}}})
    with pytest.raises(ValidationError, match="tracing.enabled"):
        config_from_dict({"observability": {"tracing": {"enabled": 1}}})


def test_security_invalid_backend_never_leaks_attribute_error() -> None:
    with pytest.raises(ValidationError, match="credential_backend"):
        config_from_dict({"security": {"credential_backend": 123}})
    with pytest.raises(ValidationError, match="路径不能为空"):
        config_from_dict({"security": {"credential_backend": "file:"}})


def test_blank_runtime_paths_and_identity_strings_are_rejected() -> None:
    with pytest.raises(ValidationError, match="cache.directory"):
        config_from_dict({"cache": {"directory": "   "}})
    with pytest.raises(ValidationError, match="output.timezone"):
        config_from_dict({"output": {"timezone": ""}})
    with pytest.raises(ValidationError, match="security.user_agent"):
        config_from_dict({"security": {"user_agent": ""}})
    with pytest.raises(ValidationError, match="vipdoc_root"):
        config_from_dict({"sources": {"vipdoc_root": ""}})


def test_root_config_rejects_non_config_section_instances() -> None:
    cfg = Config(core={})  # type: ignore[arg-type]

    with pytest.raises(ValidationError, match="CoreConfig"):
        cfg.validate()


def test_merge_config_rejects_non_mapping_layer() -> None:
    with pytest.raises(ValidationError, match="配置层必须是 mapping"):
        merge_config([("core", {"timeout": 5})])  # type: ignore[arg-type]
