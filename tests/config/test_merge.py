"""配置合并测试（§21）：12 个用例覆盖 6 源优先级合并、校验与类型归一化。

覆盖场景：default-only / env override / file override / cli override /
precedence order / unknown-key / invalid type / empty dict / nested section /
list extension / boolean coercion / float coercion。
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from tstdx.config.loader import (
    config_from_env,
    load_config,
    parse_env_value,
)
from tstdx.config.schema import (
    Config,
    config_from_dict,
    merge_config,
)
from tstdx.errors import ValidationError


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch):
    """每个测试自动隔离环境变量。

    用 ``monkeypatch.delenv`` 而非「删后再恢复」：后者在测试内新增的
    ``TSTDX_*`` 键不会被 ``os.environ.update`` 清掉（update 只覆盖已存在
    键），会把最后一个测试的残留（如 ``TSTDX_CONFIG_FILE``）泄漏给后续
    测试文件——security 的 ``list_keys()`` 通配扫描会把它当成凭据键
    （实测全量回归 ``config_file`` 串扰）。
    """
    for k in list(os.environ):
        if k.startswith("TSTDX_"):
            monkeypatch.delenv(k, raising=False)


# --------------------------------------------------------------------------- #
# 12 个用例
# --------------------------------------------------------------------------- #
@pytest.mark.unit
class TestConfigMerge:
    """12 个配置合并用例。"""

    def test_default_only(self):
        """#1 纯默认配置：use_env=False, use_files=False, overrides=None。"""
        cfg = load_config(overrides=None, use_env=False, use_files=False)
        assert isinstance(cfg, Config)
        assert cfg.core.timeout == 3.0
        assert cfg.core.max_retries == 3
        assert cfg.web.enabled is False
        assert cfg.sources.order == ["tdx", "web", "reader", "cache"]

    def test_env_override(self, monkeypatch: pytest.MonkeyPatch):
        """#2 环境变量覆盖：TSTDX_CORE_TIMEOUT=5。"""
        monkeypatch.setenv("TSTDX_CORE_TIMEOUT", "5")
        cfg = load_config(overrides=None, use_env=True, use_files=False)
        assert cfg.core.timeout == 5.0

    def test_file_override(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        """#3 文件覆盖：TSTDX_CONFIG_FILE 指向临时 TOML。"""
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
        """#4 函数入参覆盖（最高优先级）。"""
        cfg = load_config(
            overrides={"core": {"timeout": 99.0}},
            use_env=False,
            use_files=False,
        )
        assert cfg.core.timeout == 99.0

    def test_precedence_order(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        """#5 优先级 file < env < cli（cli 入参最高）。"""
        toml_content = b"[core]\ntimeout = 1.0\n"
        p = tmp_path / "tstdx.toml"
        p.write_bytes(toml_content)
        monkeypatch.setenv("TSTDX_CONFIG_FILE", str(p))
        monkeypatch.setenv("TSTDX_CORE_TIMEOUT", "2.0")

        # cli (overrides) 最高 → 应为 3.0
        cfg = load_config(
            overrides={"core": {"timeout": 3.0}},
            use_env=True,
            use_files=True,
        )
        assert cfg.core.timeout == 3.0

        # 无 cli → env 应胜出（环境变量优先级高于文件，见 loader 优先级清单）
        cfg2 = load_config(overrides=None, use_env=True, use_files=True)
        # 优先级：env(2.0) > file(1.0)
        assert cfg2.core.timeout == 2.0  # env 覆盖 file

        # 仅 env
        monkeypatch.setenv("TSTDX_CONFIG_FILE", "")
        cfg3 = load_config(overrides=None, use_env=True, use_files=False)
        assert cfg3.core.timeout == 2.0

    def test_unknown_key_tolerance(self):
        """#6 未知配置段 → ValidationError。"""
        with pytest.raises(ValidationError):
            config_from_dict({"bogus_section": {"foo": 1}})

    def test_invalid_type_rejection(self):
        """#7 非法类型 → ValidationError（如 timeout 传入字符串）。"""
        with pytest.raises(ValidationError):
            merge_config({"core": {"timeout": "not_a_number"}})

    def test_empty_dict_merge(self):
        """#8 空字典合并 → 等价于默认配置。"""
        cfg = merge_config({}, None, {})
        assert isinstance(cfg, Config)
        assert cfg.core.timeout == 3.0

    def test_nested_section_merge(self):
        """#9 嵌套 section 合并（多源字段覆盖）。"""
        low = {"core": {"timeout": 1.0, "max_retries": 5}}
        high = {"core": {"timeout": 2.0}}
        cfg = merge_config(low, high)
        assert cfg.core.timeout == 2.0  # high 覆盖
        assert cfg.core.max_retries == 5  # low 保留（high 未提及）

    def test_list_extension(self, monkeypatch: pytest.MonkeyPatch):
        """#10 逗号分隔列表解析（环境变量）。"""
        monkeypatch.setenv("TSTDX_WEB_ENABLED_SOURCES", "tencent,sina,eastmoney")
        cfg = load_config(overrides=None, use_env=True, use_files=False)
        assert isinstance(cfg.web.enabled_sources, list)
        assert "tencent" in cfg.web.enabled_sources

    def test_boolean_coercion(self):
        """#11 布尔类型归一化（F0-3 契约：纯数字按数值解析，不伪装 bool）。

        旧契约把 "1"/"0" 解析为 True/False，导致 ``TSTDX_CORE_MAX_RETRIES=1``
        这类数值字段触发 "必须是数值，收到 bool" 校验崩溃（实测 P0）。
        """
        assert parse_env_value("true") is True
        assert parse_env_value("True") is True
        assert parse_env_value("1") == 1 and parse_env_value("1") is not True
        assert parse_env_value("yes") is True
        assert parse_env_value("false") is False
        assert parse_env_value("0") == 0 and parse_env_value("0") is not False
        assert parse_env_value("off") is False

        # 通过 config_from_env 验证
        env = {"TSTDX_CORE_AUTO_FALLBACK": "true", "TSTDX_CACHE_ENABLED": "false"}
        result = config_from_env(env)
        assert result["core"]["auto_fallback"] is True
        assert result["cache"]["enabled"] is False

    def test_float_coercion(self):
        """#12 浮点类型归一化。"""
        assert parse_env_value("3.14") == 3.14
        assert parse_env_value("0.001") == 0.001
        assert parse_env_value("100.5") == 100.5

        # 整数
        assert parse_env_value("42") == 42
        assert isinstance(parse_env_value("42"), int)

        # JSON 列表
        assert parse_env_value("[1,2,3]") == [1, 2, 3]
