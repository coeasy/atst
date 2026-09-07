# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""配置加载：6 源优先级合并（§21.3）。

优先级（高 → 低）::

    1. 函数入参        load_config(overrides={"core": {"timeout": 5}})
    2. 环境变量        TSTDX_CORE_TIMEOUT=5  TSTDX_WEB_ENABLED_SOURCES=tencent,sina
    3. 项目配置        ./tstdx.toml
    4. 用户配置        ~/.tstdx/config.toml
    5. 系统配置        /etc/tstdx/config.toml  （Windows: %PROGRAMDATA%\\tstdx\\config.toml）
    6. 内置默认        DEFAULT_CONFIG

环境变量命名规则：``TSTDX_<SECTION>_<KEY>``，全大写；
值按 JSON → bool → int/float → 逗号分隔列表 → 字符串 的顺序解析。
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ..errors import ConfigError
from .schema import DEFAULT_CONFIG, Config, merge_config

__all__ = [
    "ENV_PREFIX",
    "CONFIG_FILENAMES",
    "load_config",
    "get_config",
    "set_config",
    "reset_config",
    "find_config_files",
    "load_toml",
    "config_from_env",
    "parse_env_value",
]

ENV_PREFIX = "TSTDX_"
CONFIG_FILENAMES = ("tstdx.toml", ".tstdx.toml")
ENV_CONFIG_FILE = "TSTDX_CONFIG_FILE"

_global_config: Config = DEFAULT_CONFIG


# --------------------------------------------------------------------------- #
# TOML
# --------------------------------------------------------------------------- #
def _toml_loads(text: str) -> dict[str, Any]:
    """优先 tomllib(3.11+) / tomli，都没有则给出明确错误。"""
    try:
        import tomllib
    except ModuleNotFoundError:
        try:
            import tomli as tomllib
        except ModuleNotFoundError as exc:
            raise ConfigError(
                "解析 .toml 需要 Python 3.11+（内置 tomllib）或安装 tomli：pip install tomli"
            ) from exc
    return tomllib.loads(text)


def load_toml(path: str | Path) -> dict[str, Any]:
    p = Path(path).expanduser()
    if not p.exists():
        return {}
    try:
        return _toml_loads(p.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ConfigError(f"配置文件解析失败: {p}", context={"path": str(p)}, cause=exc) from exc


def find_config_files() -> list[Path]:
    """按优先级返回存在的配置文件路径（项目 → 用户 → 系统）。"""
    found: list[Path] = []

    # 3) 环境变量指定的显式路径（优先级最高，仅次于入参/env）
    explicit = os.environ.get(ENV_CONFIG_FILE)
    if explicit:
        p = Path(explicit).expanduser()
        if p.exists():
            found.append(p)

    # 3) 项目配置：从 CWD 向上查找，最多 5 层
    cwd = Path.cwd()
    for parent in [cwd, *cwd.parents[:5]]:
        for name in CONFIG_FILENAMES:
            cand = parent / name
            if cand.exists():
                found.append(cand)
                break
        if found and found[-1].parent == parent:
            break

    # 4) 用户配置
    user_dir = Path(os.path.expanduser("~")) / ".tstdx"
    for name in ("config.toml", "tstdx.toml"):
        cand = user_dir / name
        if cand.exists():
            found.append(cand)
            break

    # 5) 系统配置
    if os.name == "nt":
        base = Path(os.environ.get("PROGRAMDATA", r"C:\ProgramData"))
        sys_path = base / "tstdx" / "config.toml"
    else:
        sys_path = Path("/etc/tstdx/config.toml")
    if sys_path.exists():
        found.append(sys_path)

    return found


# --------------------------------------------------------------------------- #
# 环境变量
# --------------------------------------------------------------------------- #
def parse_env_value(raw: str) -> Any:
    """按 JSON → bool → 数值 → 逗号列表 → 字符串 的顺序解析。

    注意：``"1"/"0"`` 按**数值**解析而非 bool——否则
    ``TSTDX_CORE_MAX_RETRIES=1`` 这类数值字段会被伪装成 bool 触发校验崩溃。
    bool 仅识别 true/false/yes/no/on/off（不区分大小写）。
    """
    s = raw.strip()
    if s[:1] in "[{":
        try:
            return json.loads(s)
        except json.JSONDecodeError:
            pass
    low = s.lower()
    if low in ("true", "yes", "on"):
        return True
    if low in ("false", "no", "off"):
        return False
    try:
        return int(s)
    except ValueError:
        pass
    try:
        return float(s)
    except ValueError:
        pass
    if "," in s:
        return [x.strip() for x in s.split(",") if x.strip()]
    return s


def _split_env_section(body: str) -> tuple[str, str] | None:
    """把 ``<SECTION>_<FIELD>`` 按 _SUBCONFIGS **最长前缀**切分。

    固定 ``split("_", 1)`` 会把唯一带下划线的段名 ``rate_limit`` 切成
    ``"rate"``，导致 ``TSTDX_RATE_LIMIT_*`` 永远无法表达（实测崩溃）。
    """
    for section in sorted(Config._SUBCONFIGS, key=len, reverse=True):
        if body.startswith(section + "_") and len(body) > len(section) + 1:
            return section, body[len(section) + 1 :]
    return None


def config_from_env(environ: Mapping[str, str] | None = None) -> dict[str, Any]:
    """把 ``TSTDX_*`` 环境变量解析为嵌套配置字典。"""
    env = os.environ if environ is None else environ
    out: dict[str, Any] = {}
    for key, raw in env.items():
        if not key.startswith(ENV_PREFIX) or key == ENV_CONFIG_FILE:
            continue
        body = key[len(ENV_PREFIX) :].lower()
        split = _split_env_section(body)
        if split is None:
            import warnings

            warnings.warn(
                f"忽略无法识别的环境变量 {key}（段名须属于 {list(Config._SUBCONFIGS)}）",
                RuntimeWarning,
                stacklevel=2,
            )
            continue
        section, field_name = split
        out.setdefault(section, {})[field_name] = parse_env_value(raw)
    return out


# --------------------------------------------------------------------------- #
# 主入口
# --------------------------------------------------------------------------- #
def load_config(
    overrides: Mapping[str, Any] | None = None,
    *,
    use_env: bool = True,
    use_files: bool = True,
    set_global: bool = False,
    verbose: bool = False,
) -> Config:
    """加载并合并 6 源配置。

    Parameters
    ----------
    overrides:
        最高优先级覆盖（函数入参）。
    use_env / use_files:
        关闭对应源（测试用）。
    set_global:
        True 时同时写回全局单例（供 ``tstdx.get_config()`` 使用）。
    verbose:
        True 时返回前打印各层来源（调试用）。
    """
    layers: list[dict[str, Any]] = []
    sources: list[str] = []

    if overrides:
        layers.append(dict(overrides))
        sources.append("overrides")
    if use_env:
        env_cfg = config_from_env()
        if env_cfg:
            layers.append(env_cfg)
            sources.append("env")
    if use_files:
        for path in find_config_files():
            data = load_toml(path)
            if data:
                layers.append(data)
                sources.append(str(path))

    # merge_config 语义为「后出现覆盖先出现」（后面的层优先级更高），
    # 因此这里按 低→高 传入：默认 < 文件 < 环境变量 < 函数入参。
    cfg = merge_config(*reversed(layers))

    if verbose:
        print(f"[tstdx] 配置来源（高→低）: {sources or ['defaults']}")

    if set_global:
        set_config(cfg)
    return cfg


def get_config() -> Config:
    """获取全局配置单例。"""
    return _global_config


def set_config(cfg: Config) -> None:
    global _global_config
    _global_config = cfg.validate()


def reset_config() -> None:
    global _global_config
    _global_config = DEFAULT_CONFIG
