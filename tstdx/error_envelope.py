# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""Canonical safe error envelope shared by all external surfaces.

The envelope is the *only* error shape allowed to cross a trust boundary.  It

* carries the stable ``code`` plus the concrete exception ``type`` (``error`` is
  kept as a serialization alias for older consumers);
* keeps auditable non-sensitive diagnostics (``provider`` / ``channel`` /
  ``capability`` / ``phase`` / ``query_id`` + free-form context) visible;
* redacts credentials and raw payloads by key **keyword**, not by an exhaustive
  allow-list, so a newly added diagnostic field is visible by default while
  ``token`` / ``cookie`` / ``secret``-like keys are removed;
* is fail-closed: the top level always reports ``fallback_allowed`` and
  ``provider_switch_allowed`` as ``False``, and legacy permission keys inside the
  context are normalized to ``False`` rather than injected;
* lets an explicit ``retry_same_provider`` decision in the exception context
  outrank the transport's static retry advice, so a query whose total deadline
  is already gone is never advertised as retryable.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from .errors import TdxError, http_status_for

__all__ = ["ErrorEnvelope", "to_error_envelope", "is_sensitive_key"]


#: 精确匹配的敏感上下文键。
_SENSITIVE_EXACT_KEYS = frozenset(
    {
        "token",
        "cookie",
        "cookies",
        "set_cookie",
        "authorization",
        "auth",
        "credential",
        "credentials",
        "password",
        "passwd",
        "secret",
        "session",
        "session_id",
        "headers",
        "payload",
        "raw",
        "raw_body",
        "body",
    }
)

#: 子串匹配的敏感关键字（捕获 ``access_token`` / ``secret_token`` / ``api_key`` 等变体）。
_SENSITIVE_KEY_MARKERS = (
    "token",
    "cookie",
    "secret",
    "password",
    "passwd",
    "credential",
    "api_key",
    "apikey",
    "private_key",
    "session",
    "payload",
    "header",
)

#: 在信封顶层平铺暴露的上下文字段（其余仅保留在 ``context`` 内）。
_FLAT_CONTEXT_KEYS = ("query_id", "request_id")

#: 已提升为信封顶层独立字段的上下文键：序列化时不再在 ``context`` 里重复出现，
#: 以免 ``context`` 变成「顶层字段的镜像」，也让「无附加上下文」可被断言为 ``{}``。
_PROMOTED_CONTEXT_KEYS = ("phase", "query_id", "request_id")


def is_sensitive_key(key: str) -> bool:
    """Whether a key names a credential-bearing field.

    Shared by error-context redaction and query-fingerprint redaction so both
    surfaces agree on what counts as a secret: exact key matches plus substring
    markers (which catch ``access_token`` / ``secret_token`` / ``api_key`` …).
    """

    lowered = str(key).lower()
    if lowered in _SENSITIVE_EXACT_KEYS:
        return True
    return any(marker in lowered for marker in _SENSITIVE_KEY_MARKERS)


def _opt_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


@dataclass(frozen=True, slots=True)
class ErrorEnvelope:
    """Transport-neutral, fail-closed description of one failure."""

    code: str
    type: str
    message: str = ""
    http_status: int = 500
    retryable: bool = False
    phase: str | None = None
    provider: str | None = None
    channel: str | None = None
    capability: str | None = None
    partial: bool = False
    context: Mapping[str, Any] = field(default_factory=dict)

    @property
    def error(self) -> str:
        """Legacy alias of :attr:`type` (kept for serialization compatibility)."""

        return self.type

    def to_dict(self) -> dict[str, Any]:
        # ``context`` 只暴露「残差诊断」：phase / query_id / request_id 已作为顶层
        # 独立字段出现，这里剔除以避免镜像重复（无附加诊断时即为 ``{}``）。
        residual_context = {
            key: value for key, value in self.context.items() if key not in _PROMOTED_CONTEXT_KEYS
        }
        payload: dict[str, Any] = {
            "error": self.type,
            "type": self.type,
            "code": self.code,
            "message": self.message,
            "http_status": self.http_status,
            "retryable": self.retryable,
            "partial": self.partial,
            # fail-closed：对外层永远声明不允许 fallback / provider 切换。
            "fallback_allowed": False,
            "provider_switch_allowed": False,
            "context": residual_context,
        }
        if self.phase is not None:
            payload["phase"] = self.phase
        # 单字段优先取显式属性，其次回落到 context（调用方两种写法都支持）。
        for key in ("provider", "channel", "capability"):
            explicit = getattr(self, key)
            if explicit is not None:
                payload[key] = explicit
            elif key in self.context:
                payload[key] = self.context[key]
        for key in _FLAT_CONTEXT_KEYS:
            if key in self.context:
                payload[key] = self.context[key]
        return payload


#: 一组 legacy 许可字段：只要调用方表达过其中任意一个，就将整组归一化为 ``False``。
_LEGACY_PERMISSION_FLAGS = ("fallback", "fallback_allowed", "provider_switch_allowed")


def _safe_context(context: Mapping[str, Any]) -> dict[str, Any]:
    """Redact sensitive keys, then normalize the legacy permission triad.

    采用**敏感键关键字黑名单**而非白名单：契约要求非敏感诊断字段（``attempted`` /
    ``batch_status`` / ``chunk_index`` / ``safe_detail`` 等）必须保留可见，而
    token/cookie/payload 及其变体必须剔除。

    对 ``fallback`` / ``fallback_allowed`` / ``provider_switch_allowed`` 采取**归一化**
    而非**注入**：三者是同一个 legacy「允许降级/切换 Provider」许可的不同写法，因此
    只要调用方表达过其中任意一个，就整组收敛为 ``False``（避免出现互相矛盾的半套许可），
    而**绝不**凭空新增字段——信封顶层本就无条件声明 ``fallback_allowed=False`` /
    ``provider_switch_allowed=False``，在最小化的公开 context 里重复常量只会造成噪声。
    """

    sanitized = {key: value for key, value in context.items() if not is_sensitive_key(str(key))}
    if any(flag in sanitized for flag in _LEGACY_PERMISSION_FLAGS):
        for flag in _LEGACY_PERMISSION_FLAGS:
            sanitized[flag] = False
    return sanitized


def _prefers_disposition_retryability(
    context: Mapping[str, Any],
    advice_retryable: bool,
) -> bool:
    """Retryability: an explicit decision in the context outranks static advice.

    ``retry_same_provider`` 由写入方放进异常 context——今天是
    :meth:`~tstdx.query.ExecutionBudget.ensure_remaining`（查询总 deadline 已耗尽）。
    它压过传输建议：建议是"这类错误一般可以重试"，决策是"这一次不该再来"，
    后者不赢就会对调用方宣告一个明知无用的重试。
    """

    decided = context.get("retry_same_provider")
    if isinstance(decided, bool):
        return decided
    return advice_retryable


def to_error_envelope(exc: Exception, **context: Any) -> ErrorEnvelope:
    """Normalize a normal Exception without swallowing process-control signals.

    Callers must deliberately catch ``Exception`` rather than ``BaseException``;
    KeyboardInterrupt/SystemExit therefore remain observable process-control
    signals and never become API errors.

    ``**context`` 是**信封级富化**（例如 ``request_id`` / ``query_id`` /
    ``provider``），优先级高于异常自带的 context；最终仍统一经过脱敏与
    fail-closed 收敛。
    """

    if isinstance(exc, TdxError):
        merged: dict[str, Any] = dict(exc.context)
        merged.update({key: value for key, value in context.items() if value is not None})
        return ErrorEnvelope(
            code=exc.code,
            type=type(exc).__name__,
            message=exc.message or type(exc).__name__,
            http_status=http_status_for(exc),
            retryable=_prefers_disposition_retryability(merged, bool(exc.advice.retryable)),
            phase=_opt_str(merged.get("phase")),
            provider=_opt_str(merged.get("provider")),
            channel=_opt_str(merged.get("channel")),
            capability=_opt_str(merged.get("capability")),
            partial=bool(merged.get("partial", False)),
            context=_safe_context(merged),
        )
    # Native exception: no exception-carried context, so only the envelope-level
    # enrichment kwargs are honored. The raw native message is never exposed, but
    # the caller's auditable identity (phase / request_id / provider…) still is,
    # so native failures carry the same envelope contract as domain failures.
    merged = {key: value for key, value in context.items() if value is not None}
    return ErrorEnvelope(
        code="E9000",
        type="InternalError",
        message="internal error",
        http_status=500,
        retryable=False,
        phase=_opt_str(merged.get("phase")),
        provider=_opt_str(merged.get("provider")),
        channel=_opt_str(merged.get("channel")),
        capability=_opt_str(merged.get("capability")),
        partial=bool(merged.get("partial", False)),
        context=_safe_context(merged),
    )
