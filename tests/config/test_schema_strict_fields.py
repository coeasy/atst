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
        ("web", "timeout", math.nan),
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
        ("rate_limit", "strict"),
        ("security", "use_tls"),
    ],
)
def test_public_boolean_fields_reject_integer_coercion(section: str, field: str) -> None:
    with pytest.raises(ValidationError, match="必须是 bool"):
        config_from_dict({section: {field: 1}})


def test_web_rate_limit_rejects_unknown_source() -> None:
    with pytest.raises(ValidationError, match="web.rate_limit 含未知源"):
        config_from_dict({"web": {"rate_limit": {"mystery": 1}}})


def test_web_rate_limit_rejects_non_mapping_container() -> None:
    with pytest.raises(ValidationError, match="web.rate_limit 必须是 mapping"):
        config_from_dict({"web": {"rate_limit": []}})


def test_web_rate_limit_rejects_fractional_value() -> None:
    with pytest.raises(ValidationError, match="必须是整数"):
        config_from_dict({"web": {"rate_limit": {"eastmoney": 1.5}}})


def test_web_enabled_sources_reject_unknown_source() -> None:
    with pytest.raises(ValidationError, match="web.enabled_sources 含未知源"):
        config_from_dict({"web": {"enabled_sources": ["mystery"]}})


def test_blank_runtime_paths_are_rejected() -> None:
    with pytest.raises(ValidationError, match="vipdoc_root"):
        config_from_dict({"core": {"vipdoc_root": ""}})
    with pytest.raises(ValidationError, match="default_provider"):
        config_from_dict({"core": {"default_provider": "   "}})


def test_root_config_rejects_non_config_section_instances() -> None:
    cfg = Config(core={})  # type: ignore[arg-type]

    with pytest.raises(ValidationError, match="CoreConfig"):
        cfg.validate()


def test_merge_config_rejects_non_mapping_layer() -> None:
    with pytest.raises(ValidationError, match="配置层必须是 mapping"):
        merge_config([("core", {"timeout": 5})])  # type: ignore[arg-type]
