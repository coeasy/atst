# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""配置中心：strict schema + 单一内核执行参数。

每一个可导出段都由 ``UnifiedRuntime`` / ``DirectProviderExecutor`` /
``WebQuoteClient`` 真实读取；不存在缓存、降级或输出格式开关。
"""

from .loader import (  # noqa: F401
    CONFIG_FILENAMES,
    ENV_CONFIG_FILE,
    ENV_PREFIX,
    config_from_env,
    find_config_files,
    get_config,
    load_config,
    load_toml,
    parse_env_value,
    reset_config,
    set_config,
)
from .schema import (  # noqa: F401
    DEFAULT_CONFIG,
    Config,
    CoreConfig,
    HostsConfig,
    RateLimitConfig,
    SecurityConfig,
    WebConfig,
    config_diff,
    config_from_dict,
    merge_config,
    validate_keys,
)

__all__ = [
    "Config",
    "DEFAULT_CONFIG",
    "CoreConfig",
    "HostsConfig",
    "RateLimitConfig",
    "WebConfig",
    "SecurityConfig",
    "load_config",
    "get_config",
    "set_config",
    "reset_config",
    "merge_config",
    "config_from_dict",
    "config_from_env",
    "config_diff",
    "validate_keys",
    "find_config_files",
    "load_toml",
    "parse_env_value",
    "ENV_PREFIX",
]
