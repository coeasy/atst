"""配置合并测试：strict Provider-bound defaults + 类型归一化。"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from tstdx.config.loader import config_from_env, load_config, parse_env_value
from tstdx.config.schema import Config, config_from_dict, merge_config
from tstdx.errors import ValidationError


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch):
    for k in list(os.environ):
        if k.startswith("TSTDX_"):
            monkeypatch.delenv(k, raising=False)


@pytest.mark.unit
class TestConfigMerge:
    def test_default_only(self):
        cfg = load_config(overrides=None, use_env=False, use_files=False)
        assert isinstance(cfg, Config)
        assert cfg.core.timeout == 3.0
        assert cfg.core.max_retries == 3
        assert cfg.core.auto_fallback is False
        assert cfg.web.enabled is False
        assert cfg.sources.default_provider == "tdx"
        assert cfg.sources.order == ["tdx"]
        assert cfg.sources.continue_on_error is False

    def test_env_override(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("TSTDX_CORE_TIMEOUT", "5")
        cfg = load_config(overrides=None, use_env=True, use_files=False)
        assert cfg.core.timeout == 5.0

    def test_file_override(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        toml_content = b"""
[core]
timeout = 7.0
max_retries = 1
"""
        p = tmp_path / "tstdx.toml"
        p.write_bytes(toml_content)
        monkeypatch.setenv("TSTDX_CONFIG_FILE", str(p))
        cfg = load_config(overrides=None, use_env=False, use_files=True)
        assert cfg.core.timeout == 7.0
        assert cfg.core.max_retries == 1

    def test_cli_override(self):
        cfg = load_config(
            overrides={"core": {"timeout": 99.0}},
            use_env=False,
            use_files=False,
        )
        assert cfg.core.timeout == 99.0

    def test_precedence_order(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        toml_content = b"[core]\ntimeout = 1.0\n"
        p = tmp_path / "tstdx.toml"
        p.write_bytes(toml_content)
        monkeypatch.setenv("TSTDX_CONFIG_FILE", str(p))
        monkeypatch.setenv("TSTDX_CORE_TIMEOUT", "2.0")

        cfg = load_config(
            overrides={"core": {"timeout": 3.0}},
            use_env=True,
            use_files=True,
        )
        assert cfg.core.timeout == 3.0

        cfg2 = load_config(overrides=None, use_env=True, use_files=True)
        assert cfg2.core.timeout == 2.0

        monkeypatch.setenv("TSTDX_CONFIG_FILE", "")
        cfg3 = load_config(overrides=None, use_env=True, use_files=False)
        assert cfg3.core.timeout == 2.0

    def test_unknown_key_tolerance(self):
        with pytest.raises(ValidationError):
            config_from_dict({"bogus_section": {"foo": 1}})

    def test_invalid_type_rejection(self):
        with pytest.raises(ValidationError):
            merge_config({"core": {"timeout": "not_a_number"}})

    def test_empty_dict_merge(self):
        cfg = merge_config({}, None, {})
        assert isinstance(cfg, Config)
        assert cfg.core.timeout == 3.0

    def test_nested_section_merge(self):
        low = {"core": {"timeout": 1.0, "max_retries": 5}}
        high = {"core": {"timeout": 2.0}}
        cfg = merge_config(low, high)
        assert cfg.core.timeout == 2.0
        assert cfg.core.max_retries == 5

    def test_list_extension(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("TSTDX_WEB_ENABLED_SOURCES", "tencent,sina,eastmoney")
        cfg = load_config(overrides=None, use_env=True, use_files=False)
        assert isinstance(cfg.web.enabled_sources, list)
        assert "tencent" in cfg.web.enabled_sources

    def test_boolean_coercion(self):
        assert parse_env_value("true") is True
        assert parse_env_value("True") is True
        assert parse_env_value("1") == 1 and parse_env_value("1") is not True
        assert parse_env_value("yes") is True
        assert parse_env_value("false") is False
        assert parse_env_value("0") == 0 and parse_env_value("0") is not False
        assert parse_env_value("off") is False

        # Parsing remains backward compatible. Validation is the boundary that
        # rejects the old runtime semantic.
        env = {"TSTDX_CORE_AUTO_FALLBACK": "true", "TSTDX_CACHE_ENABLED": "false"}
        result = config_from_env(env)
        assert result["core"]["auto_fallback"] is True
        assert result["cache"]["enabled"] is False

    def test_float_coercion(self):
        assert parse_env_value("3.14") == 3.14
        assert parse_env_value("0.001") == 0.001
        assert parse_env_value("100.5") == 100.5
        assert parse_env_value("42") == 42
        assert isinstance(parse_env_value("42"), int)
        assert parse_env_value("[1,2,3]") == [1, 2, 3]

    def test_runtime_rejects_cross_provider_fallback_flags(self):
        with pytest.raises(ValidationError, match="auto_fallback"):
            config_from_dict({"core": {"auto_fallback": True}})
        with pytest.raises(ValidationError, match="continue_on_error"):
            config_from_dict({"sources": {"continue_on_error": True}})
        with pytest.raises(ValidationError, match="多级 fallback"):
            config_from_dict({"sources": {"order": ["tdx", "web"]}})

    def test_default_provider_is_registry_validated(self):
        cfg = config_from_dict({"sources": {"default_provider": "tencent"}})
        assert cfg.sources.default_provider == "tencent"
        with pytest.raises(ValidationError, match="未知 provider"):
            config_from_dict({"sources": {"default_provider": "not-a-provider"}})
