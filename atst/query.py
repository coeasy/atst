# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""Canonical query contracts and deterministic single-Provider planning.

This module is intentionally side-effect free: planning validates capability,
Provider, Channel, period and currentness *before* any service I/O. One
:class:`QueryPlan` always binds to exactly one Provider and one canonical
Channel. Cross-Provider fallback is an orchestration policy and must never occur
inside the planner.
"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from dataclasses import fields as dataclass_fields
from enum import Enum
from typing import Any, Final

from .domain.period import MINUTE_PERIODS, normalize_bar_period
from .domain.symbol import normalize_symbol
from .error_envelope import is_sensitive_key
from .errors import ReadTimeout, ValidationError
from .providers import PROVIDERS, ChannelSpec, resolve_capability_provider, resolve_provider

__all__ = [
    "CurrentnessMode",
    "ExecutionBudget",
    "QuerySpec",
    "QueryFingerprint",
    "QueryPlan",
    "QueryPlanner",
    "EXECUTED_OPTIONS",
    "REJECTED_OPTIONS",
]

#: 直连执行面上没有对象可作用的策略键。设置它们必须当场失败，而不是被静默收下——
#: 一个"看起来生效"的开关比没有开关更糟（``max_age`` 就是静默收下然后无人消费）。
REJECTED_OPTIONS: dict[str, str] = {
    "allow_stale": (
        "数据始终来自绑定的 Provider，过期容忍没有可作用的对象，新鲜度口径请用 currentness"
    ),
    "allow_partial": (
        "quotes BatchResult 始终逐 symbol 记录三态，partial 是结果事实而非可放行的策略"
    ),
}
#: ``options`` 袋里唯一被直连执行面读取的键（``runtime/executor.py`` 的
#: ``options.get("args")`` / ``options.get("kwargs")`` / ``options.get("market")`` /
#: ``options.get("strict")``）。
#: 袋里的键从此只有两种下场：在这个名单里被执行面读取，或者在 ``normalized()`` 当场被拒。
#: 名单与真实读取点的一致性由架构门禁 ``test_option_bag_keys_are_executed_or_rejected``
#: 对 AST 扫描结果求差把守——在这里加一个执行面不读的键，门禁即红。
EXECUTED_OPTIONS: frozenset[str] = frozenset({"args", "kwargs", "market", "strict"})
_CANONICAL_UNIFIED_CHANNELS: dict[tuple[str, str], str] = {
    ("tdx", "quotes"): "quotation",
    ("tdx", "bars"): "quotation",
    ("tencent", "quotes"): "quote",
    ("sina", "quotes"): "quote",
    ("eastmoney", "quotes"): "quote",
    ("baidu", "quotes"): "quote",
    ("sina", "bars"): "history_kline",
    ("eastmoney", "bars"): "kline",
    ("baidu", "bars"): "kline",
    ("local_vipdoc", "bars"): "vipdoc",
}


class CurrentnessMode(str, Enum):
    """Caller intent for how current a result must be."""

    AUTO = "auto"
    LIVE = "live"
    HISTORICAL = "historical"
    BUSINESS = "business"


@dataclass(slots=True)
class ExecutionBudget:
    """The wall-clock ceiling of **one** ``execute()``.

    A ``policy`` fan-out re-compiles per symbol and therefore re-starts the clock;
    attempt counting is not here on purpose - retries belong to the transport pool
    (``[core] max_retries``) and to the fallback policy, not to this object.
    """

    deadline_ns: int

    @classmethod
    def from_deadline_ms(cls, deadline_ms: int) -> ExecutionBudget:
        if deadline_ms <= 0:
            raise ValidationError(
                "deadline_ms 必须大于 0",
                context={"deadline_ms": deadline_ms},
            )
        return cls(deadline_ns=time.monotonic_ns() + int(deadline_ms * 1_000_000))

    def remaining_s(self) -> float:
        return max(0.0, (self.deadline_ns - time.monotonic_ns()) / 1_000_000_000)

    def ensure_remaining(self, phase: str) -> None:
        if time.monotonic_ns() >= self.deadline_ns:
            raise ReadTimeout(
                "查询总 deadline 已耗尽",
                context={
                    "phase": phase,
                    "deadline_scope": "query",
                    #: 这是一条**决策**而不是建议：预算已经用光的失败不许再被对外
                    #: 宣告成"可以重试同一个 Provider"（`error_envelope` 读的就是它）。
                    "retry_same_provider": False,
                },
            )


def _norm_text(value: str | None) -> str:
    return "" if value is None else str(value).strip().lower()


def _require_int(value: Any, name: str) -> int:
    """绑定处的整数后闸（第 25 轮 G34）。

    四个入口在这之前的规整口径各不相同，坏值最后都落到 :meth:`QuerySpec.normalized`
    的 ``self.count < 0`` 一类比较上：库面因此抛出裸 :class:`TypeError`，而它被每一张
    服务面一致地翻译成 **E9000 内部错误**（HTTP 500）——调用方少写了一个引号，
    服务器却把故障登记在自己名下。这里只补一条口径：绑定处收到的必须是
    :class:`int`，否则当场 :class:`ValidationError`（E1010 / 422 / -32602）。

    这里**不**做字符串转换：三张服务面已在各自的入口用
    :func:`atst.integration.wire_fields.as_request_int` 完成传输形状的规整，库面调用方
    则应当直接传整数。绑定处替人猜一次，就多一处"看起来生效"的口径分歧。
    """
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValidationError(
            f"{name} 必须是整数：这里收到 {value!r}（{type(value).__name__}）。"
            "绑定处不把它猜成另一个数，也不让裸 TypeError 冒充服务器故障",
            context={
                "phase": "wire_validation",
                "face": "binder",
                "field": name,
                "received": repr(value),
            },
        )
    return value


def _canonical_unified_channel(provider: str, capability: str, period: str) -> str | None:
    """Return the single channel the unified ``quotes``/``bars`` executor uses.

    ``None`` means the (provider, capability) pair has no unified canonical
    channel, so an explicit provider-internal channel must be honored verbatim.
    """

    pid = str(provider)
    cap = str(capability)
    if pid == "tencent" and cap == "bars":
        return "minute_kline" if period in MINUTE_PERIODS else "kline"
    return _CANONICAL_UNIFIED_CHANNELS.get((pid, cap))


def _reject_core_channel_mismatch(
    provider: str,
    capability: str,
    channel: str | None,
    canonical: str | None,
) -> None:
    """Fail fast when a unified core query targets a provider-specific channel.

    ``quotes`` / ``bars`` are unified capabilities: exactly one canonical channel
    per Provider executes them. Any other declared channel (``extended``,
    ``goods``, ``vipdoc`` …) must never be silently re-routed through the
    canonical executor, so the mismatch is rejected *before* registry lookup.
    """

    if channel is None or canonical is None or channel == canonical:
        return
    if capability not in {"quotes", "bars"}:
        return
    raise ValidationError(
        f"统一 {capability} QueryPlan 不能执行 provider {provider!r} "
        f"channel {channel!r}；canonical channel 为 {canonical!r}",
        context={
            "provider": provider,
            "channel": channel,
            "canonical_channel": canonical,
            "capability": capability,
            "provider_switch_allowed": False,
            "channel_switch_allowed": False,
        },
    )


def _canonical_options(options: Mapping[str, Any] | None) -> str:
    try:
        return json.dumps(
            dict(options or {}),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ValidationError(
            "query options 必须是可 JSON 序列化的确定性值",
            context={"options_type": type(options).__name__},
        ) from exc


def _parse_currentness(value: str | CurrentnessMode) -> CurrentnessMode:
    if isinstance(value, CurrentnessMode):
        return value
    normalized = str(value or CurrentnessMode.AUTO.value).strip().lower()
    try:
        return CurrentnessMode(normalized)
    except ValueError as exc:
        raise ValidationError(
            f"未知 currentness {value!r}",
            context={"currentness": value, "allowed": [item.value for item in CurrentnessMode]},
        ) from exc


@dataclass(frozen=True, slots=True)
class QuerySpec:
    """Immutable semantic request before Provider execution."""

    capability: str
    symbols: tuple[str, ...] = ()
    provider: str | None = None
    channel: str | None = None
    period: str = ""
    count: int = 0
    start: int = 0
    adjustment: str = ""
    currentness: str = CurrentnessMode.AUTO.value
    deadline_ms: int = 5000
    schema_version: int = 1
    options_json: str = "{}"

    @classmethod
    def build(
        cls,
        capability: str,
        *,
        symbols: str | Sequence[str] = (),
        provider: str | None = None,
        channel: str | None = None,
        period: str = "",
        count: int = 0,
        start: int = 0,
        adjustment: str = "",
        allow_stale: bool = False,
        currentness: str | CurrentnessMode = CurrentnessMode.AUTO,
        deadline_ms: int = 5000,
        schema_version: int = 1,
        options: Mapping[str, Any] | None = None,
    ) -> QuerySpec:
        if isinstance(symbols, str):
            symbol_tuple: tuple[Any, ...] = (symbols,)
        else:
            try:
                symbol_tuple = tuple(symbols)
            except TypeError as exc:
                #: 裸 ``TypeError`` 会被四张面一致地报成 E9000/HTTP 500——调用方写错的键被说成
                #: 服务器故障。这里也不替调用方把整数翻成字符串：``000001`` 写成整数就是 ``1``，
                #: 猜一次就是一条安静错码的查询。
                raise ValidationError(
                    "symbols 必须是代码字符串或代码字符串的序列：整数不是合法写法，"
                    "前导零会在到达这里之前就已经丢了",
                    context={
                        "phase": "wire_validation",
                        "capability": capability,
                        "symbols_type": type(symbols).__name__,
                    },
                ) from exc
        current = _parse_currentness(currentness)
        # 构造期只保留"折叠进 options 袋"这一条通路：袋里的键要么被执行面消费，
        # 要么在 ``REJECTED_OPTIONS`` 里当场拒绝，不存在第三种"收下但没人读"。
        merged_options: dict[str, Any] = dict(options or {})
        if allow_stale:
            merged_options["allow_stale"] = True
        return cls(
            capability=capability,
            symbols=symbol_tuple,
            provider=provider,
            channel=channel,
            period=period,
            count=count,
            start=start,
            adjustment=adjustment,
            currentness=current.value,
            deadline_ms=deadline_ms,
            schema_version=schema_version,
            options_json=_canonical_options(merged_options),
        )

    @property
    def options(self) -> dict[str, Any]:
        value = json.loads(self.options_json or "{}")
        return dict(value) if isinstance(value, dict) else {}

    def normalized(self, *, default_provider: str | None = None) -> QuerySpec:
        cap = _norm_text(self.capability)
        if not cap:
            raise ValidationError("capability 不能为空")

        # A-share/quote/bar capabilities use the canonical security-symbol
        # engine.  Derivative identifiers are provider-native contracts (for
        # example ``IF2509`` and Eastmoney option quote ids) and must remain
        # opaque; forcing them through the A-share parser rejects valid
        # canonical capabilities before Provider execution.
        opaque_symbol_capabilities = frozenset(
            {
                "bond_kline",
                "futures_kline",
                "options_snapshot",
                "options_kline",
            }
        )
        if cap in opaque_symbol_capabilities:
            symbols = tuple(str(item).strip() for item in self.symbols)
            if any(not item for item in symbols):
                raise ValidationError(
                    f"{cap} 的 symbol 不能为空",
                    context={"capability": cap},
                )
        else:
            symbols = tuple(normalize_symbol(item) for item in self.symbols)
        if cap in {"quotes", "bars"} and not symbols:
            raise ValidationError(f"{cap} 至少需要一个 symbol", context={"capability": cap})
        if cap == "bars" and len(symbols) != 1:
            raise ValidationError("bars 统一 QuerySpec 一次只接受一个 symbol")
        for rejected, reason in REJECTED_OPTIONS.items():
            if self.options.get(rejected):
                raise ValidationError(
                    f"直连执行面不接受 {rejected}：{reason}",
                    context={"capability": cap, rejected: True},
                )
        if not self.options.keys() <= EXECUTED_OPTIONS:
            raise ValidationError(
                "options 里有直连执行面不读取的键（设置它们不会改变任何行为，"
                "因此当场拒绝而不是静默收下）",
                context={
                    "capability": cap,
                    "unknown_options": sorted(set(self.options) - set(EXECUTED_OPTIONS)),
                    "executed_options": sorted(EXECUTED_OPTIONS),
                },
            )
        for field_name in SPEC_INT_FIELDS:
            _require_int(getattr(self, field_name), field_name)
        if self.count < 0:
            raise ValidationError("count 不能为负数", context={"count": self.count})
        if cap == "bars" and self.count <= 0:
            raise ValidationError("bars.count 必须大于 0", context={"count": self.count})
        if self.start < 0:
            raise ValidationError("start 不能为负数", context={"start": self.start})
        if self.deadline_ms <= 0:
            raise ValidationError(
                "deadline_ms 必须大于 0", context={"deadline_ms": self.deadline_ms}
            )
        if self.schema_version <= 0:
            raise ValidationError("schema_version 必须大于 0")

        currentness = _parse_currentness(self.currentness)
        selected = resolve_capability_provider(
            cap,
            self.provider,
            default=default_provider or PROVIDERS.default_provider,
        )
        channel = _norm_text(self.channel) or None
        period = normalize_bar_period(self.period) if cap == "bars" else _norm_text(self.period)
        # v13 SSOT：统一 quotes/bars 只能走 Provider 的 canonical channel。该检查必须先于
        # 注册表存在性校验，否则 ``provider=tdx, channel=vipdoc`` 会先报“未知 channel”，
        # 掩盖真正的“canonical channel 不匹配”语义。
        _reject_core_channel_mismatch(
            selected,
            cap,
            channel,
            _canonical_unified_channel(selected, cap, period),
        )
        PROVIDERS.require(selected, cap, channel=channel)

        return replace(
            self,
            capability=cap,
            symbols=symbols,
            provider=selected,
            channel=channel,
            period=period,
            adjustment=_norm_text(self.adjustment),
            currentness=currentness.value,
            options_json=_canonical_options(self.options),
        )


#: :meth:`QuerySpec.normalized` 之前必须已经是真整数的字段：**从 dataclass 自己的 ``int``
#: 注解现扫**，不另抄名单——抄来的名单会在下一个 int 形参上漏掉自己（第 25 轮 G34）。
SPEC_INT_FIELDS: Final[tuple[str, ...]] = tuple(
    spec_field.name for spec_field in dataclass_fields(QuerySpec) if spec_field.type == "int"
)


def _secret_digest(value: Any) -> str:
    """Stable non-reversible placeholder for a credential value.

    The digest keeps the fingerprint deterministic — the same credential still
    yields the same identity across processes — while the plaintext never
    reaches a fingerprint, a log line or a wire payload.
    """

    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return "__secret_sha256__" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _redact_secrets(value: Any) -> Any:
    """Recursively replace credential-bearing values with digests."""

    if isinstance(value, Mapping):
        return {
            str(key): (
                _secret_digest(item) if is_sensitive_key(str(key)) else _redact_secrets(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_redact_secrets(item) for item in value]
    return value


@dataclass(frozen=True, slots=True)
class QueryFingerprint:
    """Stable full semantic identity of one request.

    The kernel binds every result back to the plan that produced it through
    this value and emits it verbatim on the canonical serialization; nothing on
    the request path reads it to skip a Provider call.
    """

    value: str
    canonical: str

    @staticmethod
    def _payload(spec: QuerySpec, *, channel: str) -> dict[str, Any]:
        # v13 SSOT：fingerprint 只描述**数据身份**（问的是什么），不描述**执行预算**。
        # ``deadline_ms`` 是调用方给的超时上界，同一份数据的不同 deadline 必须共享
        # 一个身份；新鲜度口径由 ``currentness`` 表达，所以它在身份之内。
        #
        # ``options`` 会携带调用方凭证（例如 wencai 的 ``cookie``）：它们参与
        # 身份判定（换凭证即换身份），但**绝不能**以明文进入 fingerprint 或日志，
        # 故按敏感键递归替换为 sha256 占位符。
        return {
            "schema_version": spec.schema_version,
            "capability": spec.capability,
            "provider": spec.provider,
            "channel": str(channel).strip().lower(),
            "symbols": list(spec.symbols),
            "period": spec.period,
            "count": spec.count,
            "start": spec.start,
            "adjustment": spec.adjustment,
            "currentness": spec.currentness,
            "options": _redact_secrets(spec.options),
        }

    @classmethod
    def from_normalized_spec(cls, spec: QuerySpec, *, channel: str) -> QueryFingerprint:
        canonical = json.dumps(
            cls._payload(spec, channel=channel),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        return cls(value=f"q{spec.schema_version}:{digest}", canonical=canonical)


@dataclass(frozen=True, slots=True)
class QueryPlan:
    """Compiled executable identity. It can never contain fallback Providers."""

    spec: QuerySpec
    provider: str
    channel: str
    fingerprint: QueryFingerprint
    #: 单次逻辑查询共享的总 deadline 预算。属于**运行时状态**而非编译身份，
    #: 因此刻意排除在相等性与哈希之外（两个独立编译、语义相同的 plan 仍相等）。
    #: 它是 deadline 的唯一载体——`deadline_ms` 曾经另存一份，无人读取（F-50）。
    budget: ExecutionBudget = field(compare=False, repr=False)


class QueryPlanner:
    """Compile semantic requests into deterministic one-Provider plans."""

    def __init__(self, *, default_provider: str | None = None) -> None:
        if default_provider is None:
            selected: str = PROVIDERS.default_provider
        else:
            # v13 SSOT：显式传入的 default_provider 是调用方的意图声明，空串必须
            # fail-fast，绝不能静默回落成内置默认 Provider（否则配置拼写错误会被吞掉）。
            cleaned = str(default_provider).strip()
            if not cleaned:
                raise ValidationError(
                    "default_provider 不能为空；如需内置默认 Provider 请传 None",
                    context={"default_provider": default_provider},
                )
            selected = cleaned
        PROVIDERS.get(selected)
        self.default_provider = resolve_provider(provider=selected)

    @staticmethod
    def _canonical_unified_channel(spec: QuerySpec) -> str | None:
        return _canonical_unified_channel(str(spec.provider), spec.capability, spec.period)

    @classmethod
    def _reject_core_channel_mismatch(cls, spec: QuerySpec, canonical: str | None) -> None:
        _reject_core_channel_mismatch(
            str(spec.provider),
            spec.capability,
            spec.channel,
            canonical,
        )

    @classmethod
    def _select_channel(cls, spec: QuerySpec) -> ChannelSpec:
        pid = str(spec.provider)
        cap = spec.capability
        canonical = cls._canonical_unified_channel(spec)

        if spec.channel:
            # 先判 canonical 不匹配，再确认 channel 是否注册：这样才能对
            # ``tdx + vipdoc`` 这类“channel 属于别的 Provider”的情况给出
            # canonical channel 诊断，而不是笼统的“未知 channel”。
            cls._reject_core_channel_mismatch(spec, canonical)
            PROVIDERS.require(pid, cap, channel=spec.channel)
            selected = PROVIDERS.get(pid).channel(spec.channel)
        elif canonical is not None:
            PROVIDERS.require(pid, cap, channel=canonical)
            selected = PROVIDERS.get(pid).channel(canonical)
        else:
            candidates = PROVIDERS.get(pid).channels_for(cap)
            if not candidates:
                raise ValidationError(
                    f"provider {pid!r} 不支持 capability {cap!r}",
                    context={"provider": pid, "capability": cap},
                )
            if len(candidates) != 1:
                raise ValidationError(
                    f"provider {pid!r} capability {cap!r} 有多个 channel，必须显式指定",
                    context={
                        "provider": pid,
                        "capability": cap,
                        "channels": [item.id for item in candidates],
                    },
                )
            selected = candidates[0]

        if cap == "bars":
            PROVIDERS.require_period(pid, selected.id, spec.period)
        return selected

    @classmethod
    def _validate_migrated_call(cls, spec: QuerySpec, channel: ChannelSpec) -> None:
        """Bind migrated-call arguments when the caller uses the payload convention.

        A capability served by the table-driven ``_migrated_capability`` backend
        may reach its implementation through ``options["args"] / options["kwargs"]``.
        When that payload is present it is bound against the real implementation
        signature *here*, so a caller who opted into the raw payload convention
        gets a planning-time :class:`~atst.errors.ValidationError`.

        The convention is deliberately optional: the typed path
        (:meth:`atst.Client.typed` via
        :func:`atst.typed_query.call_payload_from_typed`) compiles the same
        capability names from semantic fields with no raw positional payload,
        and validates at dispatch instead. Requiring the payload at planning
        would reject that first-class path, so the hard guarantee is enforced
        where it is universal — before Provider I/O in
        :meth:`~atst.runtime.executor.DirectProviderExecutor._migrated_capability`.
        """

        from .catalog.capability import binding_for, validate_call

        options = spec.options
        if "args" not in options and "kwargs" not in options:
            return
        key = (str(spec.provider), channel.id, spec.capability)
        try:
            binding_for(*key)
        except KeyError:
            return
        args = options.get("args", [])
        kwargs = options.get("kwargs", {})
        if not isinstance(args, list) or not isinstance(kwargs, dict):
            raise ValidationError(
                "migrated capability options 必须包含 args:list / kwargs:object",
                context={
                    "provider": key[0],
                    "channel": key[1],
                    "capability": key[2],
                    "phase": "query_validation",
                },
            )
        validate_call(key[0], key[1], key[2], tuple(args), dict(kwargs))

    def compile(self, spec: QuerySpec) -> QueryPlan:
        normalized = spec.normalized(default_provider=self.default_provider)
        selected = self._select_channel(normalized)
        self._validate_migrated_call(normalized, selected)
        currentness = _parse_currentness(normalized.currentness)
        if currentness is CurrentnessMode.LIVE and not selected.live:
            raise ValidationError(
                "所选 Channel 不是 live channel，不能满足 currentness='live'",
                context={
                    "provider": normalized.provider,
                    "channel": selected.id,
                    "capability": normalized.capability,
                    "currentness": currentness.value,
                    "local": selected.local,
                },
            )
        fingerprint = QueryFingerprint.from_normalized_spec(
            normalized,
            channel=selected.id,
        )
        return QueryPlan(
            spec=normalized,
            provider=str(normalized.provider),
            channel=selected.id,
            fingerprint=fingerprint,
            budget=ExecutionBudget.from_deadline_ms(normalized.deadline_ms),
        )
