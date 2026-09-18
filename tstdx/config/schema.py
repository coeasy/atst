# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""配置 Schema（§21.1）。

核心库零硬依赖：本模块用标准库 dataclass 实现 strict 校验（拼写错误立即报错）。

**配置面即执行面契约**：这里存在的每一个键都必须被单一内核读取并改变行为
（``UnifiedRuntime`` / ``DirectProviderExecutor`` / ``WebQuoteClient``）。
因此本 Schema 只覆盖执行参数：Provider 选择、超时与重试、主站与槽位、
本地限流、TLS、Web 源清单。缓存、降级链、输出格式、档案、可观测性开关
都不在此处——内核数据请求零缓存、零跨 Provider 静默降级，
写这些键不会改变任何行为，所以它们不存在（v17 Phase 6，F-13/F-16）。
"""

from __future__ import annotations

import math
from collections.abc import Iterator, Mapping
from dataclasses import asdict, dataclass, field, fields, replace
from typing import Any, Protocol

from ..errors import ConfigError, ValidationError

__all__ = [
    "CoreConfig",
    "HostsConfig",
    "RateLimitConfig",
    "WebConfig",
    "SecurityConfig",
    "Config",
    "DEFAULT_CONFIG",
    "config_from_dict",
    "merge_config",
    "validate_keys",
    "config_diff",
]


def _check_range(name: str, value: Any, lo: float | None, hi: float | None) -> None:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValidationError(f"{name} 必须是数值，收到 {type(value).__name__}: {value!r}")
    normalized = float(value)
    if not math.isfinite(normalized):
        raise ValidationError(f"{name} 必须是有限数值，收到 {value!r}")
    if lo is not None and normalized < lo:
        raise ValidationError(f"{name} = {value} 小于下限 {lo}")
    if hi is not None and normalized > hi:
        raise ValidationError(f"{name} = {value} 超过上限 {hi}")


def _check_int_range(name: str, value: Any, lo: int | None, hi: int | None) -> None:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValidationError(f"{name} 必须是整数，收到 {type(value).__name__}: {value!r}")
    if lo is not None and value < lo:
        raise ValidationError(f"{name} = {value} 小于下限 {lo}")
    if hi is not None and value > hi:
        raise ValidationError(f"{name} = {value} 超过上限 {hi}")


def _check_bool(name: str, value: Any) -> None:
    if not isinstance(value, bool):
        raise ValidationError(f"{name} 必须是 bool，收到 {type(value).__name__}: {value!r}")


def _check_non_empty_str(name: str, value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"{name} 必须是非空字符串，收到 {value!r}")
    return value


def _check_mapping(name: str, value: Any) -> Mapping[Any, Any]:
    if not isinstance(value, Mapping):
        raise ValidationError(f"{name} 必须是 mapping，收到 {type(value).__name__}")
    return value


# --------------------------------------------------------------------------- #
# 子配置
# --------------------------------------------------------------------------- #
class _Validatable(Protocol):
    """Structural contract every config section satisfies for :meth:`Config.validate`."""

    def validate(self) -> None: ...


@dataclass
class CoreConfig:
    #: 未显式传 provider 时 ``QueryPlanner`` 使用的 Provider。
    default_provider: str = "tdx"
    timeout: float = 5.0
    heartbeat_interval: int = 30
    #: 当前 Provider 内 host/endpoint 的重试预算；不是 Provider 切换次数。
    max_retries: int = 3
    #: ``local_vipdoc`` Provider 与复权/同步类 capability 的本地数据根目录。
    vipdoc_root: str | None = None

    def validate(self) -> None:
        from ..providers import PROVIDERS

        default_provider = _check_non_empty_str("core.default_provider", self.default_provider)
        PROVIDERS.get(default_provider)
        _check_range("core.timeout", self.timeout, 0.1, 300)
        _check_int_range("core.heartbeat_interval", self.heartbeat_interval, 0, 3600)
        _check_int_range("core.max_retries", self.max_retries, 0, 20)
        if self.vipdoc_root is not None:
            _check_non_empty_str("core.vipdoc_root", self.vipdoc_root)


@dataclass
class HostsConfig:
    #: 手动指定主站列表；为空则使用内置候选池
    servers: list[list[Any]] = field(default_factory=list)
    #: 每台主站的 TCP 连接数（Slot = hosts × slots_per_host）
    slots_per_host: int = 4

    def validate(self) -> None:
        from ..transport.hosts import parse_server

        if not isinstance(self.servers, list):
            raise ValidationError(f"hosts.servers 必须是 list，收到 {type(self.servers).__name__}")
        _check_int_range("hosts.slots_per_host", self.slots_per_host, 1, 64)
        for item in self.servers:
            if not isinstance(item, (list, tuple)) or len(item) != 2:
                raise ValidationError(f"hosts.servers 每项必须是 [host, port]，收到 {item!r}")
            try:
                parse_server(item)
            except ConfigError as exc:
                raise ValidationError(
                    f"hosts.servers 主站无效: {item!r} —— {exc.message}",
                    context={"field": "hosts.servers"},
                    cause=exc,
                ) from exc


@dataclass
class RateLimitConfig:
    """本地请求限流（req/s），按交易状态分档。

    字段名与 :class:`~tstdx.transport.ratelimit.SessionState` 一一对应——
    旧字段名（``in_session``/``pre_post``）与限流器读取的键从不重合，
    因此本段的所有取值都曾被静默丢弃（v17 Phase 6 修正）。
    """

    call_auction: int = 80
    continuous: int = 120
    noon_break: int = 25
    closed: int = 15
    #: True：令牌不足立即抛 :class:`~tstdx.errors.RateLimitedLocal`；
    #: False：阻塞等待令牌。
    strict: bool = False

    def validate(self) -> None:
        for key in ("call_auction", "continuous", "noon_break", "closed"):
            _check_int_range(f"rate_limit.{key}", getattr(self, key), 1, 1000)
        _check_bool("rate_limit.strict", self.strict)


@dataclass
class WebConfig:
    """Legacy WebQuoteClient 兼容配置。

    ``enabled_sources`` 是显式 legacy Web 客户端（:class:`tstdx.web.WebQuoteClient`）
    缺省时按序尝试的源列表，**不是** TDX 失败后的 fallback 顺序。单一内核经
    ``QueryPlanner`` 直接选择 ``tencent/sina/eastmoney/...`` 的 Provider channel。
    """

    enabled_sources: list[str] = field(default_factory=lambda: ["tencent", "sina", "eastmoney"])
    timeout: float = 5.0
    max_retries: int = 2
    #: 按源名区分的 req/s；未列出的源沿用各适配器默认。
    rate_limit: dict[str, int] = field(default_factory=dict)

    def validate(self) -> None:
        from ..web.sources import KNOWN_SOURCES

        if not isinstance(self.enabled_sources, list):
            raise ValidationError(
                f"web.enabled_sources 必须是 list，收到 {type(self.enabled_sources).__name__}"
            )
        for source in self.enabled_sources:
            if not isinstance(source, str) or source not in KNOWN_SOURCES:
                raise ValidationError(
                    f"web.enabled_sources 含未知源 {source!r}；已知: {sorted(KNOWN_SOURCES)}"
                )
        _check_range("web.timeout", self.timeout, 0.5, 120)
        _check_int_range("web.max_retries", self.max_retries, 0, 10)

        rate_limit = _check_mapping("web.rate_limit", self.rate_limit)
        for key, value in rate_limit.items():
            if not isinstance(key, str) or key not in KNOWN_SOURCES:
                raise ValidationError(
                    f"web.rate_limit 含未知源 {key!r}；已知: {sorted(KNOWN_SOURCES)}"
                )
            _check_int_range(f"web.rate_limit[{key!r}]", value, 1, 1000)


@dataclass
class SecurityConfig:
    #: 标准族 TCP 连接是否包裹 TLS。默认关闭：主流 TDX 站点明文服务。
    use_tls: bool = False

    def validate(self) -> None:
        _check_bool("security.use_tls", self.use_tls)


# --------------------------------------------------------------------------- #
# 根配置
# --------------------------------------------------------------------------- #
def _deep_merge(base: Any, override: Any) -> Any:
    """dict 深合并（一递归）：override 键胜出，base 未覆盖键保留。"""
    if isinstance(base, Mapping) and isinstance(override, Mapping):
        merged = dict(base)
        for key, value in override.items():
            merged[key] = _deep_merge(merged[key], value) if key in merged else value
        return merged
    return override


@dataclass
class Config:
    core: CoreConfig = field(default_factory=CoreConfig)
    hosts: HostsConfig = field(default_factory=HostsConfig)
    rate_limit: RateLimitConfig = field(default_factory=RateLimitConfig)
    web: WebConfig = field(default_factory=WebConfig)
    security: SecurityConfig = field(default_factory=SecurityConfig)

    _SUBCONFIGS = (
        "core",
        "hosts",
        "rate_limit",
        "web",
        "security",
    )

    def validate(self) -> Config:
        expected_types: dict[str, type[_Validatable]] = {
            "core": CoreConfig,
            "hosts": HostsConfig,
            "rate_limit": RateLimitConfig,
            "web": WebConfig,
            "security": SecurityConfig,
        }
        for name in self._SUBCONFIGS:
            value = getattr(self, name)
            expected = expected_types[name]
            if not isinstance(value, expected):
                raise ValidationError(
                    f"配置段 {name!r} 必须是 {expected.__name__}，收到 {type(value).__name__}"
                )
            value.validate()
        return self

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def with_overrides(self, **kw: Any) -> Config:
        """按子配置名覆盖（嵌套 dict 字段深合并，标量字段替换）。"""
        updates: dict[str, Any] = {}
        for key, value in kw.items():
            if key not in self._SUBCONFIGS:
                raise ValidationError(f"未知配置段 {key!r}；可选: {list(self._SUBCONFIGS)}")
            subconfig = getattr(self, key)
            if not isinstance(value, Mapping):
                raise ValidationError(f"配置段 {key!r} 必须是 dict，收到 {type(value).__name__}")
            unknown = set(value) - {item.name for item in fields(subconfig)}
            if unknown:
                raise ValidationError(
                    f"配置段 {key!r} 含未知字段: {sorted(unknown)}；"
                    f"可选: {sorted(item.name for item in fields(subconfig))}"
                )
            merged_values = {
                field_name: _deep_merge(getattr(subconfig, field_name), value[field_name])
                if isinstance(getattr(subconfig, field_name), Mapping)
                and isinstance(value[field_name], Mapping)
                else value[field_name]
                for field_name in value
            }
            updates[key] = replace(subconfig, **merged_values)
        return replace(self, **updates)

    def __iter__(self) -> Iterator[tuple[str, Any]]:
        for name in self._SUBCONFIGS:
            yield name, getattr(self, name)


DEFAULT_CONFIG = Config()


# --------------------------------------------------------------------------- #
# 构建与合并
# --------------------------------------------------------------------------- #
def validate_keys(data: Mapping[str, Any], *, where: str = "config") -> None:
    """strict 校验：未知配置段立即报错（防拼写错误被静默忽略）。"""
    if not isinstance(data, Mapping):
        raise ValidationError(f"{where} 必须是 mapping，收到 {type(data).__name__}")
    unknown = set(data) - set(Config._SUBCONFIGS)
    if unknown:
        raise ValidationError(
            f"{where} 含未知配置段: {sorted(unknown)}；可选: {list(Config._SUBCONFIGS)}",
            context={"where": where, "unknown": sorted(unknown)},
        )


def config_from_dict(data: Mapping[str, Any], *, where: str = "config") -> Config:
    """从字典构建子配置（增量覆盖，未给出的段保持默认），并立即校验。"""
    validate_keys(data, where=where)
    return DEFAULT_CONFIG.with_overrides(**dict(data)).validate()


def merge_config(*layers: Mapping[str, Any] | None) -> Config:
    """按优先级从低到高合并多层配置；后层覆盖前层。"""
    merged: dict[str, dict[str, Any]] = {}
    for layer in layers:
        if layer is None:
            continue
        if not isinstance(layer, Mapping):
            raise ValidationError(f"配置层必须是 mapping，收到 {type(layer).__name__}")
        if not layer:
            continue
        validate_keys(layer)
        for section, values in layer.items():
            if not isinstance(values, Mapping):
                raise ValidationError(
                    f"配置段 {section!r} 必须是 dict，收到 {type(values).__name__}"
                )
            merged[section] = dict(_deep_merge(merged.setdefault(section, {}), values))
    return config_from_dict(merged).validate()


def config_diff(a: Config, b: Config) -> dict[str, dict[str, tuple[Any, Any]]]:
    """对比两份配置，返回 ``{段: {字段: (a值, b值)}}``。"""
    da, db = a.to_dict(), b.to_dict()
    out: dict[str, dict[str, tuple[Any, Any]]] = {}
    for section in Config._SUBCONFIGS:
        sa, sb = da.get(section, {}), db.get(section, {})
        for key in set(sa) | set(sb):
            if sa.get(key) != sb.get(key):
                out.setdefault(section, {})[key] = (sa.get(key), sb.get(key))
    return out
