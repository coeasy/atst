# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""Query data identity: the redacted fingerprint of one normalized request.

P2-1：从原 ``atst/query.py`` 拆出的数据身份面——凭证脱敏与稳定指纹。
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from ..error_envelope import is_sensitive_key
from .spec import QuerySpec

__all__ = ["QueryFingerprint"]


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
