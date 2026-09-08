# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""配置 Schema（§21.1）。

核心库零硬依赖：本模块用标准库 dataclass 实现 strict 校验（拼写错误立即报错）。
若环境装有 **pydantic v2**，:func:`build_config` 会自动改用 pydantic 做更强的类型校验。

v12 配置原则：Provider 选择与 host/endpoint 恢复分层。历史 fallback 字段
只为迁移兼容保留；有效配置不得开启跨 Provider 自动降级。
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import asdict, dataclass, field, fields, replace
from typing import Any

from ..errors import ValidationError

__all__ = [
    "CoreConfig",
    "HostsConfig",
    "RateLimitConfig",
    "CacheConfig",
    "OutputConfig",
    "ProfileConfig",
    "WebConfig",
    "SourcesConfig",
    "ObservabilityConfig",
    "SecurityConfig",
    "CompatibilityConfig",
    "FeedbackConfig",
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
    if lo is not None and value < lo:
        raise ValidationError(f"{name} = {value} 小于下限 {lo}")
    if hi is not None and value > hi:
        raise ValidationError(f"{name} = {value} 超过上限 {hi}")


# --------------------------------------------------------------------------- #
# 子配置
# --------------------------------------------------------------------------- #
@dataclass
class CoreConfig:
    timeout: float = 3.0
    heartbeat_interval: int = 30
    #: 当前 Provider 内 host/endpoint 的重试预算；不是 Provider 切换次数。
    max_retries: int = 3
    #: v12 迁移兼容字段。有效配置必须为 False。
    auto_fallback: bool = False
    #: 批量行情单次上限（服务端限制 60）
    batch_quotes_limit: int = 60
    #: K 线单次请求上限
    bars_page_size: int = 800

    def validate(self) -> None:
        _check_range("core.timeout", self.timeout, 0.1, 300)
        _check_range("core.heartbeat_interval", self.heartbeat_interval, 1, 3600)
        _check_range("core.max_retries", self.max_retries, 0, 20)
        _check_range("core.batch_quotes_limit", self.batch_quotes_limit, 1, 60)
        _check_range("core.bars_page_size", self.bars_page_size, 1, 800)
        if not isinstance(self.auto_fallback, bool):
            raise ValidationError("core.auto_fallback 必须是 bool")
        if self.auto_fallback:
            raise ValidationError(
                "core.auto_fallback 已停用：v12 禁止跨 Provider 自动 fallback；"
                "请显式选择 provider，TDX 内部 host failover 仍由传输层处理",
                context={"field": "core.auto_fallback", "provider_switch_allowed": False},
            )


@dataclass
class HostsConfig:
    #: 手动指定主站列表；为空则使用内置候选池
    servers: list[list[Any]] = field(default_factory=list)
    auto_speedtest: bool = True
    ranking_file: str = "~/.tstdx/server_ranking.json"
    #: 每台主站的 TCP 连接数（Slot = hosts × slots_per_host）
    slots_per_host: int = 4
    #: 最多同时作为候选的主站数（默认 8：候选池未逐条实测，多带几个保证可达）
    max_hosts: int = 8
    speedtest_timeout: float = 1.0

    def validate(self) -> None:
        _check_range("hosts.slots_per_host", self.slots_per_host, 1, 64)
        _check_range("hosts.max_hosts", self.max_hosts, 1, 64)
        _check_range("hosts.speedtest_timeout", self.speedtest_timeout, 0.1, 30)
        for item in self.servers:
            if not isinstance(item, (list, tuple)) or len(item) != 2:
                raise ValidationError(f"hosts.servers 每项必须是 [host, port]，收到 {item!r}")
            host, port = item
            if not isinstance(host, str) or not isinstance(port, int):
                raise ValidationError(
                    f"hosts.servers 项类型错误: host 应为 str、port 应为 int，收到 {item!r}"
                )


@dataclass
class RateLimitConfig:
    """本地请求限流（req/s），按交易状态分档。"""

    in_session: int = 15
    pre_post: int = 30
    closed: int = 60

    def validate(self) -> None:
        for k in ("in_session", "pre_post", "closed"):
            _check_range(f"rate_limit.{k}", getattr(self, k), 1, 1000)


@dataclass
class CacheConfig:
    enabled: bool = True
    #: legacy memory | disk backend；planned query cache 另由完整 QueryFingerprint 管理。
    backend: str = "memory"
    ttl: int = 3
    max_entries: int = 4096
    directory: str = "~/.tstdx/cache"

    def validate(self) -> None:
        if self.backend not in ("memory", "disk"):
            raise ValidationError(f"cache.backend 必须是 memory|disk，收到 {self.backend!r}")
        _check_range("cache.ttl", self.ttl, 0, 86400)
        _check_range("cache.max_entries", self.max_entries, 1, 1_000_000)


@dataclass
class OutputConfig:
    #: dict | tuple | dataframe
    default_format: str = "dict"
    timezone: str = "Asia/Shanghai"
    df_datetime_index: bool = True

    def validate(self) -> None:
        if self.default_format not in ("dict", "tuple", "dataframe"):
            raise ValidationError(
                f"output.default_format 非法: {self.default_format!r}（可选 dict/tuple/dataframe）"
            )


@dataclass
class ProfileConfig:
    default: str = "a_share_day"
    auto_detect: bool = True
    min_confidence: float = 0.5

    def validate(self) -> None:
        _check_range("profile.min_confidence", self.min_confidence, 0.0, 1.0)
        if self.default not in _BUILTIN_PROFILE_NAMES():
            raise ValidationError(
                f"profile.default = {self.default!r} 不是内置档案；"
                f"可选: {sorted(_BUILTIN_PROFILE_NAMES())}"
            )


def _BUILTIN_PROFILE_NAMES() -> set[str]:
    from ..reader.profile import BUILTIN_PROFILES

    return set(BUILTIN_PROFILES)


@dataclass
class WebConfig:
    """Legacy WebQuoteClient 兼容配置。

    ``enabled_sources`` 是显式 legacy Web 客户端允许使用的 Provider 列表，
    **不是** TDX 失败后的 fallback 顺序。planned runtime 通过 ProviderRegistry
    直接选择 ``tencent/sina/eastmoney/...``。
    """

    enabled: bool = False
    enabled_sources: list[str] = field(default_factory=lambda: ["tencent", "sina", "eastmoney"])
    timeout: float = 5.0
    max_retries: int = 2
    headers: dict[str, str] = field(
        default_factory=lambda: {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
            ),
            "Referer": "https://finance.sina.com.cn",
        }
    )
    rate_limit: dict[str, int] = field(default_factory=lambda: {"_default": 5})
    normalize: dict[str, Any] = field(
        default_factory=lambda: {"volume": "share", "amount": "yuan", "strict": True}
    )

    def validate(self) -> None:
        from ..web.sources import KNOWN_SOURCES

        for s in self.enabled_sources:
            if s not in KNOWN_SOURCES:
                raise ValidationError(
                    f"web.enabled_sources 含未知源 {s!r}；已知: {sorted(KNOWN_SOURCES)}"
                )
        _check_range("web.timeout", self.timeout, 0.5, 120)
        _check_range("web.max_retries", self.max_retries, 0, 10)
        if self.normalize.get("volume") not in ("share", "lot", "contract"):
            raise ValidationError(
                f"web.normalize.volume 必须是 share|lot|contract，"
                f"收到 {self.normalize.get('volume')!r}"
            )
        if self.normalize.get("amount") not in ("yuan", "wan", "yi"):
            raise ValidationError(
                f"web.normalize.amount 必须是 yuan|wan|yi，收到 {self.normalize.get('amount')!r}"
            )


@dataclass
class SourcesConfig:
    """Provider 选择 + legacy Router 兼容字段。

    正式运行语义由 ``default_provider`` 决定。``order`` 仅供 legacy
    ``DataSourceRouter`` 单入口兼容，最多允许一项；``continue_on_error``
    必须为 False。多级 ``tdx -> web -> reader -> cache`` 已被 v12 禁止。
    """

    default_provider: str = "tdx"
    #: legacy Router 单选择器；不能包含多项 fallback chain。
    order: list[str] = field(default_factory=lambda: ["tdx"])
    enabled: dict[str, bool] = field(
        default_factory=lambda: {
            "tdx": True,
            "web": True,
            "reader": True,
            "cache": True,
            "synthetic": False,
        }
    )
    vipdoc_root: str | None = None
    #: legacy 字段；有效 v12 配置必须 False。
    continue_on_error: bool = False
    kline_cache_db: str | None = None

    def validate(self) -> None:
        from ..providers import PROVIDERS

        PROVIDERS.get(self.default_provider)
        known = {"tdx", "web", "reader", "cache", "synthetic"}
        for s in self.order:
            if s not in known:
                raise ValidationError(f"sources.order 含未知源 {s!r}；可选: {sorted(known)}")
        if len(self.order) > 1:
            raise ValidationError(
                "sources.order 多级 fallback 已停用；最多保留一个 legacy selector",
                context={"order": list(self.order), "provider_switch_allowed": False},
            )
        if not isinstance(self.continue_on_error, bool):
            raise ValidationError("sources.continue_on_error 必须是 bool")
        if self.continue_on_error:
            raise ValidationError(
                "sources.continue_on_error 已停用：Provider 失败必须向调用方暴露",
                context={"provider_switch_allowed": False},
            )
        if self.vipdoc_root is not None and not isinstance(self.vipdoc_root, str):
            raise ValidationError("sources.vipdoc_root 必须是 str 或 None")
        if self.kline_cache_db is not None and not isinstance(self.kline_cache_db, str):
            raise ValidationError("sources.kline_cache_db 必须是 str 或 None")


@dataclass
class ObservabilityConfig:
    metrics: dict[str, Any] = field(default_factory=lambda: {"enabled": False, "exporter": "prom"})
    logging: dict[str, Any] = field(default_factory=lambda: {"level": "INFO", "json": False})
    tracing: dict[str, Any] = field(default_factory=lambda: {"enabled": False, "exporter": "otel"})

    def validate(self) -> None:
        if self.metrics.get("exporter") not in ("prom", "statsd", "otel"):
            raise ValidationError(
                f"observability.metrics.exporter 必须是 prom|statsd|otel，"
                f"收到 {self.metrics.get('exporter')!r}"
            )
        level = self.logging.get("level")
        if level not in ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"):
            raise ValidationError(f"observability.logging.level 非法: {level!r}")


@dataclass
class SecurityConfig:
    use_tls: bool = False
    credential_backend: str = "keyring"
    user_agent: str = "tstdx/0.x"

    def validate(self) -> None:
        if self.credential_backend not in ("keyring", "env") and not (
            self.credential_backend.startswith("file:")
        ):
            raise ValidationError(
                f"security.credential_backend 必须是 keyring|env|file:路径，"
                f"收到 {self.credential_backend!r}"
            )


@dataclass
class CompatibilityConfig:
    web_facade: bool = False
    market_facade: bool = False

    def validate(self) -> None:
        pass


@dataclass
class FeedbackConfig:
    """反馈回路（§27）——默认全部关闭。"""

    telemetry: bool = False
    report_protocol_diff: bool = True
    report_source_failure: bool = False
    sanitize: str = "strict"

    def validate(self) -> None:
        if self.sanitize not in ("strict", "normal", "off"):
            raise ValidationError(
                f"feedback.sanitize 必须是 strict|normal|off，收到 {self.sanitize!r}"
            )


# --------------------------------------------------------------------------- #
# 根配置
# --------------------------------------------------------------------------- #
def _deep_merge(base: Any, override: Any) -> Any:
    """dict 深合并（一递归）：override 键胜出，base 未覆盖键保留。"""
    if isinstance(base, Mapping) and isinstance(override, Mapping):
        merged = dict(base)
        for k, v in override.items():
            merged[k] = _deep_merge(merged[k], v) if k in merged else v
        return merged
    return override


@dataclass
class Config:
    core: CoreConfig = field(default_factory=CoreConfig)
    hosts: HostsConfig = field(default_factory=HostsConfig)
    rate_limit: RateLimitConfig = field(default_factory=RateLimitConfig)
    cache: CacheConfig = field(default_factory=CacheConfig)
    output: OutputConfig = field(default_factory=OutputConfig)
    profile: ProfileConfig = field(default_factory=ProfileConfig)
    web: WebConfig = field(default_factory=WebConfig)
    sources: SourcesConfig = field(default_factory=SourcesConfig)
    observability: ObservabilityConfig = field(default_factory=ObservabilityConfig)
    security: SecurityConfig = field(default_factory=SecurityConfig)
    compatibility: CompatibilityConfig = field(default_factory=CompatibilityConfig)
    feedback: FeedbackConfig = field(default_factory=FeedbackConfig)

    _SUBCONFIGS = (
        "core",
        "hosts",
        "rate_limit",
        "cache",
        "output",
        "profile",
        "web",
        "sources",
        "observability",
        "security",
        "compatibility",
        "feedback",
    )

    def validate(self) -> Config:
        for name in self._SUBCONFIGS:
            getattr(self, name).validate()
        return self

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def with_overrides(self, **kw: Any) -> Config:
        """按子配置名覆盖（嵌套 dict 字段深合并，标量字段替换）。"""
        updates: dict[str, Any] = {}
        for k, v in kw.items():
            if k not in self._SUBCONFIGS:
                raise ValidationError(f"未知配置段 {k!r}；可选: {list(self._SUBCONFIGS)}")
            sub = getattr(self, k)
            if not isinstance(v, Mapping):
                raise ValidationError(f"配置段 {k!r} 必须是 dict，收到 {type(v).__name__}")
            unknown = set(v) - {f.name for f in fields(sub)}
            if unknown:
                raise ValidationError(
                    f"配置段 {k!r} 含未知字段: {sorted(unknown)}；"
                    f"可选: {sorted(f.name for f in fields(sub))}"
                )
            merged_values = {
                f: _deep_merge(getattr(sub, f), v[f])
                if isinstance(getattr(sub, f), Mapping) and isinstance(v[f], Mapping)
                else v[f]
                for f in v
            }
            updates[k] = replace(sub, **merged_values)
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
        for k in set(sa) | set(sb):
            if sa.get(k) != sb.get(k):
                out.setdefault(section, {})[k] = (sa.get(k), sb.get(k))
    return out
