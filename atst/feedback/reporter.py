# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""反馈系统：脱敏错误 / 用量 / 配置上报（默认关闭）。"""

from __future__ import annotations

import json
import math
import os
import platform
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Any

from .. import __version__
from ..errors import ConfigError, TdxError

__all__ = ["FeedbackReporter"]

_ENV_ENABLED = "ATST_FEEDBACK"
_ENV_DRY_RUN = "dry-run"
_ENV_ENDPOINT = "ATST_FEEDBACK_ENDPOINT"
_ENV_STORE_DIR = "ATST_FEEDBACK_STORE_DIR"

_REDACTED_IP = "[REDACTED_IP]"
_REDACTED_HOST = "[REDACTED_HOST]"
_REDACTED_PATH = "[REDACTED_PATH]"
_REDACTED_HEX = "[REDACTED_HEX]"
_TRUNCATION_SUFFIX = "…[TRUNCATED]"
_RE_IPV4 = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
_RE_IPV6 = re.compile(r"\b(?:[0-9a-fA-F]{1,4}:){2,7}[0-9a-fA-F]{1,4}\b")
_RE_HOSTNAME = re.compile(r"\b(?:[a-zA-Z0-9](?:[a-zA-Z0-9\-]*[a-zA-Z0-9])?\.)+[a-zA-Z]{2,}\b")
_RE_WINDOWS_PATH = re.compile(r"[A-Za-z]:[\\/][^\s\"'<>|*\t]+")
_RE_UNIX_PATH = re.compile(r"(?:/[\w.\-]+){2,}")
_RE_HEX_DUMP = re.compile(r"0x[0-9a-fA-F]{2,}(?:\s+0x[0-9a-fA-F]{2,})*")
_MAX_STRING_LENGTH = 200


def _sanitize_string(value: str) -> str:
    value = _RE_IPV4.sub(_REDACTED_IP, value)
    value = _RE_IPV6.sub(_REDACTED_IP, value)
    value = _RE_HOSTNAME.sub(_REDACTED_HOST, value)
    if len(value) > _MAX_STRING_LENGTH:
        value = value[:_MAX_STRING_LENGTH] + _TRUNCATION_SUFFIX
    value = _RE_WINDOWS_PATH.sub(_REDACTED_PATH, value)
    value = _RE_UNIX_PATH.sub(_REDACTED_PATH, value)
    return _RE_HEX_DUMP.sub(_REDACTED_HEX, value)


def _set_sort_key(value: Any) -> tuple[int, Any]:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return (0, value)
    return (1, str(value))


def _sanitize_value(value: Any) -> Any:
    """Recursively sanitize without assuming transformed set members stay hashable."""

    if isinstance(value, str):
        return _sanitize_string(value)
    if isinstance(value, dict):
        return {_sanitize_string(str(key)): _sanitize_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_sanitize_value(item) for item in value]
    if isinstance(value, set):
        return sorted((_sanitize_value(item) for item in value), key=_set_sort_key)
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, (int, float)):
        return value
    return _sanitize_string(str(value))


def _validated_endpoint(value: str | None) -> str | None:
    if value is None or not value.strip():
        return None
    endpoint = value.strip()
    parsed = urllib.parse.urlsplit(endpoint)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ConfigError(
            f"feedback endpoint 仅支持 http/https 且必须包含 host: {endpoint!r}",
            context={"endpoint": endpoint},
        )
    if parsed.username is not None or parsed.password is not None:
        raise ConfigError(
            "feedback endpoint 不允许 URL userinfo；凭据不得嵌入 URL",
            context={"endpoint_host": parsed.hostname},
        )
    return endpoint


def _safe_endpoint_display(value: str | None) -> str | None:
    """Return only endpoint origin; never expose path/query/fragment in repr/logs."""

    if value is None:
        return None
    parsed = urllib.parse.urlsplit(value)
    host = parsed.hostname or ""
    if ":" in host:
        host = f"[{host}]"
    port = f":{parsed.port}" if parsed.port is not None else ""
    return f"{parsed.scheme}://{host}{port}"


def _validated_store_dir(value: str | Path | None) -> Path:
    raw = value if value is not None else os.environ.get(_ENV_STORE_DIR, "~/.atst/feedback")
    if isinstance(raw, Path):
        path = raw
    elif isinstance(raw, str) and raw.strip():
        path = Path(raw.strip())
    else:
        raise ConfigError("feedback store_dir 必须是非空路径")
    return path.expanduser()


def _validated_timeout(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigError(f"feedback timeout 必须是正有限数值，收到 {value!r}")
    timeout = float(value)
    if not math.isfinite(timeout) or timeout <= 0:
        raise ConfigError(f"feedback timeout 必须是正有限数值，收到 {value!r}")
    return timeout


def _validated_usage(feature: Any, duration_ms: Any, result: Any) -> tuple[str, float, str]:
    if not isinstance(feature, str) or not feature.strip():
        raise ConfigError(f"feedback feature 必须是非空字符串，收到 {feature!r}")
    if isinstance(duration_ms, bool) or not isinstance(duration_ms, (int, float)):
        raise ConfigError(f"feedback duration_ms 必须是非负有限数值，收到 {duration_ms!r}")
    duration = float(duration_ms)
    if not math.isfinite(duration) or duration < 0:
        raise ConfigError(f"feedback duration_ms 必须是非负有限数值，收到 {duration_ms!r}")
    if not isinstance(result, str) or not result.strip():
        raise ConfigError(f"feedback result 必须是非空字符串，收到 {result!r}")
    return feature, duration, result


class FeedbackReporter:
    """反馈上报器；只有 ``ATST_FEEDBACK=1`` 或 ``dry-run`` 才执行。"""

    def __init__(
        self,
        *,
        endpoint: str | None = None,
        store_dir: str | Path | None = None,
        timeout: float = 10.0,
    ) -> None:
        raw_endpoint = endpoint if endpoint is not None else os.environ.get(_ENV_ENDPOINT)
        self._endpoint = _validated_endpoint(raw_endpoint)
        self._store_dir = _validated_store_dir(store_dir)
        self._timeout = _validated_timeout(timeout)

    @property
    def enabled(self) -> bool:
        return os.environ.get(_ENV_ENABLED) == "1"

    @property
    def dry_run(self) -> bool:
        return os.environ.get(_ENV_ENABLED) == _ENV_DRY_RUN

    def report_error(
        self,
        exc: BaseException,
        context: dict[str, Any] | None = None,
    ) -> bool:
        if not self.enabled and not self.dry_run:
            return False
        return self._send(self._build_error_payload(exc, context))

    def report_usage(self, feature: str, duration_ms: float, result: str) -> bool:
        if not self.enabled and not self.dry_run:
            return False
        validated_feature, validated_duration, validated_result = _validated_usage(
            feature,
            duration_ms,
            result,
        )
        return self._send(
            self._build_usage_payload(
                validated_feature,
                validated_duration,
                validated_result,
            )
        )

    def report_profile(self, profile: dict[str, Any]) -> bool:
        if not self.enabled and not self.dry_run:
            return False
        return self._send(self._build_profile_payload(profile))

    def _build_error_payload(
        self,
        exc: BaseException,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        error_type = type(exc).__name__
        code = getattr(exc, "code", None) or error_type
        advice: dict[str, Any] = {}
        if isinstance(exc, TdxError):
            current = exc.advice
            advice = {
                "retryable": current.retryable,
                "backoff": current.backoff,
            }
        return self._sanitize(
            {
                "event": "error",
                "error_type": error_type,
                "error_code": code,
                "message": str(exc),
                "advice": advice,
                "context": context or {},
            }
        )

    def _build_usage_payload(
        self,
        feature: str,
        duration_ms: float,
        result: str,
    ) -> dict[str, Any]:
        return self._sanitize(
            {
                "event": "usage",
                "feature": feature,
                "duration_ms": round(duration_ms, 3),
                "result": result,
            }
        )

    def _build_profile_payload(self, profile: dict[str, Any]) -> dict[str, Any]:
        return self._sanitize({"event": "profile", "profile": profile})

    def _sanitize(self, data: dict[str, Any]) -> dict[str, Any]:
        sanitized = _sanitize_value(data)
        if not isinstance(sanitized, dict):
            raise TypeError("feedback root payload must sanitize to dict")
        sanitized["timestamp"] = time.time()
        sanitized["version"] = __version__
        sanitized["platform"] = {
            "python": sys.version.split()[0],
            "platform": platform.platform(),
            "machine": platform.machine(),
        }
        return sanitized

    def _send(self, payload: dict[str, Any]) -> bool:
        try:
            json_str = json.dumps(
                payload,
                ensure_ascii=False,
                default=str,
                allow_nan=False,
            )
        except (TypeError, ValueError) as exc:
            print(f"[atst-feedback] payload JSON 序列化失败: {exc}", file=sys.stderr)
            return False
        if self.dry_run:
            print(f"[atst-feedback-dry-run] {json_str}", file=sys.stderr)
            return True
        if self._endpoint:
            return self._send_http(json_str)
        return self._send_file(json_str)

    def _send_http(self, json_str: str) -> bool:
        assert self._endpoint is not None
        try:
            request = urllib.request.Request(
                self._endpoint,
                data=json_str.encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(request, timeout=self._timeout) as response:
                return 200 <= response.status < 300
        except (urllib.error.URLError, urllib.error.HTTPError, OSError) as exc:
            endpoint = _safe_endpoint_display(self._endpoint)
            if isinstance(exc, urllib.error.HTTPError):
                detail = f"HTTPError status={exc.code}"
            else:
                detail = type(exc).__name__
            print(
                f"[atst-feedback] HTTP 发送失败: endpoint={endpoint!r} error={detail}",
                file=sys.stderr,
            )
            return False

    def _send_file(self, json_str: str) -> bool:
        """Write one uniquely named event; concurrent reports never overwrite each other."""

        try:
            self._store_dir.mkdir(parents=True, exist_ok=True)
            filename = f"feedback_{time.time_ns()}_{uuid.uuid4().hex}.json"
            filepath = self._store_dir / filename
            with filepath.open("x", encoding="utf-8") as stream:
                stream.write(json_str)
            return True
        except OSError as exc:
            print(f"[atst-feedback] 文件写入失败: {exc}", file=sys.stderr)
            return False

    def __repr__(self) -> str:
        state = "dry-run" if self.dry_run else ("enabled" if self.enabled else "disabled")
        display_endpoint = _safe_endpoint_display(self._endpoint)
        endpoint_info = f" endpoint={display_endpoint!r}" if display_endpoint else ""
        return f"FeedbackReporter({state}{endpoint_info})"
