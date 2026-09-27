"""配置合并测试：strict execution-only defaults + 类型归一化。"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from atst.config.loader import (
    config_from_env,
    find_config_files,
    load_config,
    parse_env_value,
)
from atst.config.schema import Config, config_from_dict, merge_config
from atst.errors import ConfigError, ValidationError


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch):
    for k in list(os.environ):
        if k.startswith("ATST_"):
            monkeypatch.delenv(k, raising=False)


@pytest.mark.unit
class TestConfigMerge:
    def test_default_only(self):
        cfg = load_config(overrides=None, use_env=False, use_files=False)
        assert isinstance(cfg, Config)
        assert cfg.core.timeout == 5.0
        assert cfg.core.max_retries == 3
        assert cfg.core.default_provider == "tdx"
        assert cfg.hosts.servers == []
        assert cfg.security.use_tls is False

    def test_schema_has_only_wired_sections(self):
        """配置面即执行面清单：装饰段不得复活。"""

        assert Config._SUBCONFIGS == ("core", "hosts", "rate_limit", "web", "security")

    def test_env_override(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("ATST_CORE_TIMEOUT", "7")
        cfg = load_config(overrides=None, use_env=True, use_files=False)
        assert cfg.core.timeout == 7.0

    def test_env_default_provider_override(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("ATST_CORE_DEFAULT_PROVIDER", "tencent")
        cfg = load_config(overrides=None, use_env=True, use_files=False)
        assert cfg.core.default_provider == "tencent"

    def test_file_override(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        toml_content = b"""
[core]
timeout = 7.0
max_retries = 1
"""
        p = tmp_path / "atst.toml"
        p.write_bytes(toml_content)
        monkeypatch.setenv("ATST_CONFIG_FILE", str(p))
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
        p = tmp_path / "atst.toml"
        p.write_bytes(toml_content)
        monkeypatch.setenv("ATST_CONFIG_FILE", str(p))
        monkeypatch.setenv("ATST_CORE_TIMEOUT", "2.0")

        cfg = load_config(
            overrides={"core": {"timeout": 3.0}},
            use_env=True,
            use_files=True,
        )
        assert cfg.core.timeout == 3.0

        cfg2 = load_config(overrides=None, use_env=True, use_files=True)
        assert cfg2.core.timeout == 2.0

        monkeypatch.setenv("ATST_CONFIG_FILE", "")
        cfg3 = load_config(overrides=None, use_env=True, use_files=False)
        assert cfg3.core.timeout == 2.0

    def test_explicit_missing_config_file_fails_closed(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        missing = tmp_path / "missing.toml"
        monkeypatch.setenv("ATST_CONFIG_FILE", str(missing))

        with pytest.raises(ConfigError, match="不存在"):
            load_config(overrides=None, use_env=False, use_files=True)

    def test_explicit_config_directory_fails_closed(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        monkeypatch.setenv("ATST_CONFIG_FILE", str(tmp_path))

        with pytest.raises(ConfigError, match="不是普通文件"):
            find_config_files()

    def test_explicit_config_is_not_duplicated_by_project_discovery(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        project = tmp_path / "atst.toml"
        project.write_text("[core]\ntimeout = 4.0\n", encoding="utf-8")
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("ATST_CONFIG_FILE", str(project))

        files = find_config_files()

        assert sum(path.resolve() == project.resolve() for path in files) == 1

    def test_runtime_environment_keys_do_not_enter_schema_namespace(self):
        result = config_from_env(
            {
                "ATST_HOSTS": "1.2.3.4:7709",
                "ATST_CONFIG_FILE": "/tmp/example.toml",
                "ATST_FEEDBACK": "dry-run",
                "ATST_FEEDBACK_ENDPOINT": "https://feedback.example.com/api",
                "ATST_FEEDBACK_STORE_DIR": "/tmp/atst-feedback",
            }
        )

        assert result == {}

    def test_unknown_environment_section_fails_closed(self):
        with pytest.raises(ConfigError, match="无法识别环境变量"):
            config_from_env({"ATST_COER_TIMEOUT": "5"})

    def test_removed_section_environment_fails_closed(self):
        """``[cache]`` 等装饰段删除后，其环境变量不得再被当作有效覆盖。"""

        with pytest.raises(ConfigError, match="无法识别环境变量"):
            config_from_env({"ATST_CACHE_ENABLED": "false"})

    def test_unknown_environment_field_fails_closed(self):
        with pytest.raises(ConfigError, match="字段无法识别"):
            config_from_env({"ATST_CORE_TIMOUT": "5"})

    def test_unknown_key_tolerance(self):
        with pytest.raises(ValidationError):
            config_from_dict({"bogus_section": {"foo": 1}})

    def test_removed_file_sections_fail_closed(self):
        """TOML 里保留旧段必须报错，而不是"写了不生效"。"""

        for section in ("cache", "output", "profile", "sources", "observability"):
            with pytest.raises(ValidationError, match="未知配置段"):
                config_from_dict({section: {"enabled": True}})

    def test_invalid_type_rejection(self):
        with pytest.raises(ValidationError):
            merge_config({"core": {"timeout": "not_a_number"}})

    def test_empty_dict_merge(self):
        cfg = merge_config({}, None, {})
        assert isinstance(cfg, Config)
        assert cfg.core.timeout == 5.0

    def test_nested_section_merge(self):
        low = {"core": {"timeout": 1.0, "max_retries": 5}}
        high = {"core": {"timeout": 2.0}}
        cfg = merge_config(low, high)
        assert cfg.core.timeout == 2.0
        assert cfg.core.max_retries == 5

    def test_list_extension(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("ATST_WEB_ENABLED_SOURCES", "tencent,sina,eastmoney")
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

    def test_tls_flag_is_the_only_security_switch(self):
        assert config_from_env({"ATST_SECURITY_USE_TLS": "true"}) == {
            "security": {"use_tls": True}
        }

    def test_default_provider_is_registry_validated(self):
        cfg = config_from_dict({"core": {"default_provider": "tencent"}})
        assert cfg.core.default_provider == "tencent"
        with pytest.raises(ValidationError):
            config_from_dict({"core": {"default_provider": "not-a-provider"}})
