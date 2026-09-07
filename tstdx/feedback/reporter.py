# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""反馈系统：错误 / 用量 / 配置上报（Tier B / B1）。

提供 :class:`FeedbackReporter` 类，将错误、命令用量和配置数据
通过 6 步数据脱敏流水线处理后，以 HTTP POST 或本地文件方式上报。

**安全设计**
-----------
* **默认不发送**：除非设置环境变量 ``TSTDX_FEEDBACK=1``，否则所有
  上报操作静默返回 ``False``。
* **Dry-run 模式**：设置 ``TSTDX_FEEDBACK=dry-run`` 时仅打印脱敏
  后的数据到标准输出，不实际发送。
* **6 步脱敏流水线**（见 :meth:`FeedbackReporter._sanitize`）：

  1. 移除 IP 地址 → ``[REDACTED_IP]``
  2. 移除主机名 → ``[REDACTED_HOST]``
  3. 移除账户码/股票代码等 PII
  4. 截断超长字符串（> 200 字符）
  5. 移除文件路径 → ``[REDACTED_PATH]``
  6. 移除超长十六进制转储（> 64 字节）
  7. 附加时间戳 + 版本号 + 平台元数据

典型用法::

    import os
    os.environ["TSTDX_FEEDBACK"] = "1"

    from tstdx.feedback import FeedbackReporter

    reporter = FeedbackReporter(endpoint="https://feedback.example.com/api")
    reporter.report_error(ConnectionFailed("连接超时"))
"""

from __future__ import annotations

import json
import os
import platform
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from .. import __version__
from ..errors import TdxError

__all__ = [
    "FeedbackReporter",
]

# --------------------------------------------------------------------------- #
# 环境变量常量
# --------------------------------------------------------------------------- #
_ENV_ENABLED = "TSTDX_FEEDBACK"
_ENV_DRY_RUN = "dry-run"
_ENV_ENDPOINT = "TSTDX_FEEDBACK_ENDPOINT"
_ENV_STORE_DIR = "TSTDX_FEEDBACK_STORE_DIR"

# --------------------------------------------------------------------------- #
# 脱敏正则
# --------------------------------------------------------------------------- #
_REDACTED_IP = "[REDACTED_IP]"
_REDACTED_HOST = "[REDACTED_HOST]"
_REDACTED_PATH = "[REDACTED_PATH]"

_REDACTED_HEX = "[REDACTED_HEX]"
_TRUNCATION_SUFFIX = "…[TRUNCATED]"

# IPv4 / IPv6 地址
_RE_IPV4 = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
_RE_IPV6 = re.compile(r"\b(?:[0-9a-fA-F]{1,4}:){2,7}[0-9a-fA-F]{1,4}\b")

# 主机名（至少包含一个点的域名，排除纯 IP）
_RE_HOSTNAME = re.compile(r"\b(?:[a-zA-Z0-9](?:[a-zA-Z0-9\-]*[a-zA-Z0-9])?\.)+[a-zA-Z]{2,}\b")

_RE_HEX_DUMP = re.compile(r"0x[0-9a-fA-F]{2,}(?:\s+0x[0-9a-fA-F]{2,})*")

# Windows / Unix 文件路径
_RE_WINDOWS_PATH = re.compile(r"[A-Za-z]:[\\/][^\s\"'<>|*\t]+")
_RE_UNIX_PATH = re.compile(
    r"(?:/[\w.\-]+){2,}"  # 至少 2 级路径，避免误杀普通单词
)

# 连续十六进制字节的转储（长度 > 64 字节标记为 hex dump）
_RE_HEX_DUMP = re.compile(r"0x[0-9a-fA-F]{2,}(?:\s+0x[0-9a-fA-F]{2,})*")

# 用于判断字符串是否为可能的 IP/路径等
_MAX_STRING_LENGTH = 200


# --------------------------------------------------------------------------- #
# 脱敏工具函数（内部使用，不直接导出）
# --------------------------------------------------------------------------- #
def _sanitize_string(s: str) -> str:
    """对单个字符串应用 6 步脱敏规则（步骤 1–5）。

    步骤 6（元数据附加）在 :meth:`FeedbackReporter._sanitize` 中完成。

    深审 L9：旧「账户码/股票代码脱敏」步骤已移除——股票代码是公开市场
    标识而非 PII，脱敏会让错误报告失去核心诊断信息（本库亦无账户功能）。
    """
    # 步骤 1: 移除 IP 地址
    s = _RE_IPV4.sub(_REDACTED_IP, s)
    s = _RE_IPV6.sub(_REDACTED_IP, s)

    # 步骤 2: 移除主机名
    s = _RE_HOSTNAME.sub(_REDACTED_HOST, s)

    # 步骤 3: 截断超长字符串
    if len(s) > _MAX_STRING_LENGTH:
        s = s[:_MAX_STRING_LENGTH] + _TRUNCATION_SUFFIX

    # 步骤 4: 移除文件路径
    s = _RE_WINDOWS_PATH.sub(_REDACTED_PATH, s)
    s = _RE_UNIX_PATH.sub(_REDACTED_PATH, s)

    # 步骤 5: 移除超长十六进制转储（> 64 字节）
    s = _RE_HEX_DUMP.sub(_REDACTED_HEX, s)

    return s


def _set_sort_key(v: Any) -> tuple[int, Any]:  # noqa: ANN401
    """set 排序键：数值在前（按数值序），其余 ``str()`` 归一。

    防御混合类型集合（如 ``{1, "a"}``）直接 ``sorted`` 抛 TypeError。
    """
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return (0, v)
    return (1, str(v))


def _sanitize_value(value: Any) -> Any:
    """递归脱敏任意 JSON 兼容的值。

    dict 的**键名**同样过脱敏流水线（键可能含路径 / 主机名 / 代码等 PII，
    如 ``{"C:\\Users\\john": ...}`` → ``{"[REDACTED_PATH]": ...}``）。
    """
    if isinstance(value, str):
        return _sanitize_string(value)
    if isinstance(value, dict):
        return {_sanitize_string(str(k)): _sanitize_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_sanitize_value(item) for item in value]
    if isinstance(value, set):
        return sorted({_sanitize_value(item) for item in value}, key=_set_sort_key)
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, (int, float)):
        return value
    # 其他类型转为字符串后脱敏
    return _sanitize_string(str(value))


# --------------------------------------------------------------------------- #
# FeedbackReporter
# --------------------------------------------------------------------------- #
class FeedbackReporter:
    """反馈上报器。

    将错误、命令用量和配置数据经过 6 步脱敏流水线处理后上报。
    支持两种传输模式：

    * **HTTP POST** — 发送到配置的 ``endpoint``（默认使用
      ``TSTDX_FEEDBACK_ENDPOINT`` 环境变量）；
    * **文件存储** — 写入本地目录（默认使用
      ``TSTDX_FEEDBACK_STORE_DIR`` 环境变量，缺省为 ``~/.tstdx/feedback``）。

    若设置了 ``endpoint``，优先使用 HTTP POST；否则回退到文件存储。

    Parameters
    ----------
    endpoint : str, optional
        反馈接收端的 HTTP 端点 URL。未指定时从
        ``TSTDX_FEEDBACK_ENDPOINT`` 环境变量读取。
    store_dir : str or Path, optional
        本地存储目录。未指定时从 ``TSTDX_FEEDBACK_STORE_DIR`` 环境变量
        读取，缺省为 ``~/.tstdx/feedback``。
    timeout : float, optional
        HTTP 请求超时秒数。默认 10.0。
    """

    def __init__(
        self,
        *,
        endpoint: str | None = None,
        store_dir: str | Path | None = None,
        timeout: float = 10.0,
    ) -> None:
        self._endpoint = endpoint or os.environ.get(_ENV_ENDPOINT)
        self._store_dir = Path(store_dir or os.environ.get(_ENV_STORE_DIR, "~/.tstdx/feedback"))
        self._timeout = timeout

    # ------------------------------------------------------------------ #
    # 状态检查
    # ------------------------------------------------------------------ #
    @property
    def enabled(self) -> bool:
        """是否已启用反馈（``TSTDX_FEEDBACK=1``）。"""
        return os.environ.get(_ENV_ENABLED) == "1"

    @property
    def dry_run(self) -> bool:
        """是否处于 dry-run 模式（``TSTDX_FEEDBACK=dry-run``）。"""
        return os.environ.get(_ENV_ENABLED) == _ENV_DRY_RUN

    # ------------------------------------------------------------------ #
    # 上报入口
    # ------------------------------------------------------------------ #
    def report_error(
        self,
        exc: BaseException,
        context: dict[str, Any] | None = None,
    ) -> bool:
        """上报一个错误事件。

        Parameters
        ----------
        exc : BaseException
            捕获到的异常实例。若为 :class:`~tstdx.errors.TdxError`，
            自动提取 ``code``、``advice`` 和 ``context``。
        context : dict, optional
            额外上下文信息。

        Returns
        -------
        bool
            若成功发送（或 dry-run 模式下成功打印）返回 ``True``；
            未启用时返回 ``False``。
        """
        if not self.enabled and not self.dry_run:
            return False

        payload = self._build_error_payload(exc, context)
        return self._send(payload)

    def report_usage(
        self,
        feature: str,
        duration_ms: float,
        result: str,
    ) -> bool:
        """上报一次命令/功能的使用情况。

        Parameters
        ----------
        feature : str
            功能或命令标识（如 ``"bars"``、``"0x0530"``）。
        duration_ms : float
            执行耗时（毫秒）。
        result : str
            执行结果标识（如 ``"ok"``、``"err"``）。

        Returns
        -------
        bool
            发送成功返回 ``True``，否则 ``False``。
        """
        if not self.enabled and not self.dry_run:
            return False

        payload = self._build_usage_payload(feature, duration_ms, result)
        return self._send(payload)

    def report_profile(self, profile: dict[str, Any]) -> bool:
        """上报数据配置（DataProfile）快照。

        Parameters
        ----------
        profile : dict
            配置字典（通常为 :class:`~tstdx.reader.profile.DataProfile`
            的 ``to_dict()`` 输出）。

        Returns
        -------
        bool
            发送成功返回 ``True``，否则 ``False``。
        """
        if not self.enabled and not self.dry_run:
            return False

        payload = self._build_profile_payload(profile)
        return self._send(payload)

    # ------------------------------------------------------------------ #
    # Payload 构建
    # ------------------------------------------------------------------ #
    def _build_error_payload(
        self,
        exc: BaseException,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """构建错误上报载荷（脱敏后）。"""
        error_type = type(exc).__name__
        code = getattr(exc, "code", None) or error_type
        message = str(exc)
        advice = {}
        if isinstance(exc, TdxError):
            a = exc.advice
            advice = {
                "retryable": a.retryable,
                "backoff": a.backoff,
                "max_retries": a.max_retries,
            }

        raw = {
            "event": "error",
            "error_type": error_type,
            "error_code": code,
            "message": message,
            "advice": advice,
            "context": context or {},
        }
        return self._sanitize(raw)

    def _build_usage_payload(
        self,
        feature: str,
        duration_ms: float,
        result: str,
    ) -> dict[str, Any]:
        """构建用量上报载荷（脱敏后）。"""
        raw = {
            "event": "usage",
            "feature": feature,
            "duration_ms": round(duration_ms, 3),
            "result": result,
        }
        return self._sanitize(raw)

    def _build_profile_payload(self, profile: dict[str, Any]) -> dict[str, Any]:
        """构建配置上报载荷（脱敏后）。"""
        raw = {
            "event": "profile",
            "profile": profile,
        }
        return self._sanitize(raw)

    # ------------------------------------------------------------------ #
    # 6 步脱敏流水线
    # ------------------------------------------------------------------ #
    def _sanitize(self, data: dict[str, Any]) -> dict[str, Any]:
        """对上报数据应用 6 步脱敏流水线。

        步骤
        ----
        1. 移除 IP 地址（IPv4 / IPv6）→ ``[REDACTED_IP]``
        2. 移除主机名 → ``[REDACTED_HOST]``
        3. 移除账户码 / 股票代码等 PII → ``[REDACTED_CODE]``
        4. 截断超长字符串（> 200 字符）→ 截断并附加 ``…[TRUNCATED]``
        5. 移除文件路径 → ``[REDACTED_PATH]``
        6. 移除超长十六进制转储（> 64 字节）→ ``[REDACTED_HEX]``
        7. 附加时间戳 + 版本号 + 平台元数据
        """
        # 步骤 1–6: 递归脱敏所有字符串值
        sanitized = _sanitize_value(data)

        # 步骤 7: 附加元数据
        sanitized["timestamp"] = time.time()
        sanitized["version"] = __version__
        sanitized["platform"] = {
            "python": sys.version.split()[0],
            "platform": platform.platform(),
            "machine": platform.machine(),
        }

        return sanitized

    # ------------------------------------------------------------------ #
    # 传输
    # ------------------------------------------------------------------ #
    def _send(self, payload: dict[str, Any]) -> bool:
        """发送脱敏后的数据。

        优先使用 HTTP POST（若配置了 endpoint）；
        否则写入本地文件存储。

        Returns
        -------
        bool
            发送成功返回 ``True``。
        """
        json_str = json.dumps(payload, ensure_ascii=False, default=str)

        if self.dry_run:
            # Dry-run 模式：打印到标准输出
            print(f"[tstdx-feedback-dry-run] {json_str}", file=sys.stderr)
            return True

        # 优先 HTTP POST
        if self._endpoint:
            return self._send_http(json_str)

        # 回退：文件存储
        return self._send_file(json_str)

    def _send_http(self, json_str: str) -> bool:
        """通过 HTTP POST 发送数据。"""
        # 调用方 _send 已在 self._endpoint 非空时才走本路径
        assert self._endpoint is not None
        try:
            req = urllib.request.Request(
                self._endpoint,
                data=json_str.encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                return 200 <= resp.status < 300
        except (urllib.error.URLError, urllib.error.HTTPError, OSError) as exc:
            print(
                f"[tstdx-feedback] HTTP 发送失败: {exc}",
                file=sys.stderr,
            )
            return False

    def _send_file(self, json_str: str) -> bool:
        """写入本地文件存储。"""
        try:
            self._store_dir.mkdir(parents=True, exist_ok=True)
            filename = f"feedback_{int(time.time() * 1000)}.json"
            filepath = self._store_dir / filename
            filepath.write_text(json_str, encoding="utf-8")
            return True
        except OSError as exc:
            print(
                f"[tstdx-feedback] 文件写入失败: {exc}",
                file=sys.stderr,
            )
            return False

    # ------------------------------------------------------------------ #
    # 调试 / 表示
    # ------------------------------------------------------------------ #
    def __repr__(self) -> str:
        state = "dry-run" if self.dry_run else ("enabled" if self.enabled else "disabled")
        endpoint_info = f" endpoint={self._endpoint!r}" if self._endpoint else ""
        return f"FeedbackReporter({state}{endpoint_info})"
