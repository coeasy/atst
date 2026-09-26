# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""统一错误体系（§24）。

错误码分 9 段，与异常类一一对应::

    E1xxx  配置与入参      config / validation
    E2xxx  传输层          connection / timeout / host
    E3xxx  协议层          framing / codec / parser
    E4xxx  数据与领域      profile / adjust / calendar / freshness
    E5xxx  本地文件        vipdoc / block / finance
    E6xxx  流式订阅        subscription / gap / backpressure
    E7xxx  HTTP Web Provider anti-spider / rate-limit / deprecated
    E8xxx  门面层          facade / bridge shim
    E9xxx  内部与依赖      internal / missing-dependency

v12 保留已有错误码、继承关系与公共诊断信息。唯一收敛的执行语义是：
RetryAdvice 中的 host/endpoint 恢复永远限制在**当前 Provider**；历史
``fallback_to_web`` 字段仅为序列化兼容保留，新 Provider-aware 内核不消费它。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

__all__ = [
    "TdxError",
    "RetryAdvice",
    "RETRY_ADVICE",
    "advice_for",
    "http_status_for",
    "ConfigError",
    "ValidationError",
    "DependencyMissingError",
    "TransportError",
    "ConnectionFailed",
    "ConnectionClosed",
    "ReadTimeout",
    "WriteTimeout",
    "AllHostsUnreachable",
    "RateLimitedLocal",
    "ProtocolError",
    "FramingError",
    "DecompressError",
    "CommandOffline",
    "ParseError",
    "IntegrityViolation",
    "LowConfidenceParse",
    "DataError",
    "ProfileError",
    "ProfileUndetectable",
    "AdjustError",
    "CalendarError",
    "SymbolError",
    "TruncatedDataError",
    "FreshnessViolation",
    "FileFormatError",
    "DataFileNotFound",
    "TruncatedRecordError",
    "StreamError",
    "SubscriptionError",
    "GapUnfilledError",
    "BackpressureOverflow",
    "WebSourceError",
    "AntiSpiderBlocked",
    "WebRateLimited",
    "SourceDeprecated",
    "AllSourcesExhausted",
    "CompatibilityError",
    "InternalError",
    "NotImplementedFeature",
]


@dataclass(frozen=True)
class RetryAdvice:
    """异常的可重试性建议（§24.2）。

    ``switch_host`` 仅表示当前 Provider 内的等价 host/endpoint 恢复。
    ``fallback_to_offline`` / ``fallback_to_web`` 为历史兼容字段；v12 正式
    Planner 不使用它们决定跨 Provider 执行。
    """

    retryable: bool = False
    backoff: float = 0.0
    switch_host: bool = False
    fallback_to_offline: bool = False
    fallback_to_web: bool = False
    note: str = ""


_DEFAULT = RetryAdvice()
RETRY_ADVICE: dict[type, RetryAdvice] = {}


class TdxError(Exception):
    """所有 tstdx 异常的基类。"""

    code: str = "E9000"
    http_status: int = 500
    default_advice: RetryAdvice = _DEFAULT

    def __init__(
        self,
        message: str = "",
        *,
        code: str | None = None,
        advice: RetryAdvice | None = None,
        context: dict[str, Any] | None = None,
        cause: BaseException | None = None,
    ) -> None:
        self.message = message
        if code is not None:
            self.code = code
        self.context: dict[str, Any] = dict(context or {})
        self.cause = cause
        self._advice = advice
        super().__init__(message)

    @property
    def advice(self) -> RetryAdvice:
        if self._advice is not None:
            return self._advice
        if type(self) in RETRY_ADVICE:
            return RETRY_ADVICE[type(self)]
        return type(self).default_advice

    def __str__(self) -> str:
        base = f"[{self.code}] {self.message}" if self.message else f"[{self.code}]"
        if self.context:
            kv = " ".join(f"{k}={v!r}" for k, v in list(self.context.items())[:6])
            base = f"{base} ({kv})"
        return base

    def to_dict(self) -> dict[str, Any]:
        a = self.advice
        return {
            "error": type(self).__name__,
            "code": self.code,
            "message": self.message,
            "context": self.context,
            "advice": {
                "retryable": a.retryable,
                "backoff": a.backoff,
                "switch_host": a.switch_host,
                "fallback_to_offline": a.fallback_to_offline,
                "fallback_to_web": a.fallback_to_web,
                "note": a.note,
            },
        }


def advice_for(exc: BaseException) -> RetryAdvice:
    if isinstance(exc, TdxError):
        return exc.advice
    return _DEFAULT


def http_status_for(exc: BaseException) -> int:
    if isinstance(exc, TdxError):
        return exc.http_status
    return 500


# --- E1xxx 配置与入参 ----------------------------------------------------- #
class ConfigError(TdxError):
    code = "E1000"
    http_status = 400


class ValidationError(ConfigError):
    code = "E1010"
    http_status = 422


class DependencyMissingError(ConfigError):
    code = "E1020"
    http_status = 503
    default_advice = RetryAdvice(note="缺少可选依赖，请安装对应 extra")


# --- E2xxx 传输层 --------------------------------------------------------- #
class TransportError(TdxError):
    code = "E2000"
    http_status = 502
    default_advice = RetryAdvice(
        retryable=True,
        backoff=0.5,
        switch_host=True,
        note="传输失败：只允许当前 Provider 内部重试/切换等价 host 或 endpoint",
    )


class ConnectionFailed(TransportError):
    code = "E2010"
    default_advice = RetryAdvice(
        retryable=True,
        backoff=0.5,
        switch_host=True,
        note="当前 Provider 内连接失败：可重试或切换同 Provider 等价 host/endpoint",
    )


class ConnectionClosed(TransportError):
    code = "E2020"
    default_advice = RetryAdvice(
        retryable=True,
        backoff=0.5,
        switch_host=True,
        note="连接已关闭：在当前 Provider 内重连",
    )


class ReadTimeout(TransportError):
    code = "E2030"
    http_status = 504
    default_advice = RetryAdvice(
        retryable=True,
        backoff=1.0,
        switch_host=True,
        note="读取超时：只允许当前 Provider 内部恢复",
    )


class WriteTimeout(ReadTimeout):
    code = "E2031"


class AllHostsUnreachable(TransportError):
    """当前 Provider/Channel 的 host pool 全部不可达。"""

    code = "E2040"
    http_status = 503
    default_advice = RetryAdvice(
        retryable=True,
        backoff=5.0,
        switch_host=False,
        note="当前 Provider/Channel 主站池已耗尽；显式报错，不跨 Provider 兜底",
    )


# 保留 v1.4.0 的公共诊断常量；仅删除其中“跨 Provider 自动降级”的语义。
ALL_HOSTS_UNREACHABLE_NEXT_STEPS = (
    "下一步：\n"
    "  1. 运行 `tstdx hosts scan` 对内置候选池并发测速，自动把可达主站写入"
    " ~/.tstdx/server_ranking.json（后续启动按真实 RTT 排序）；\n"
    "  2. 或在配置文件中显式指定主站（hosts.servers），例如：\n"
    "       [hosts]\n"
    '       servers = [["180.153.18.170", 7709], ["60.191.117.167", 7709]]\n'
    "  3. 或查看当前主站池：`tstdx hosts list`。\n"
    "  4. 如需查询其它 Provider，请由调用方显式指定 provider=。"
)


class RateLimitedLocal(TransportError):
    code = "E2050"
    http_status = 429
    default_advice = RetryAdvice(retryable=True, backoff=0.2)


# --- E3xxx 协议层 --------------------------------------------------------- #
class ProtocolError(TdxError):
    code = "E3000"
    http_status = 502
    default_advice = RetryAdvice(
        retryable=True,
        backoff=0.5,
        switch_host=True,
        note="协议错误：只允许当前 Provider 内部恢复",
    )


class FramingError(ProtocolError):
    code = "E3010"


class DecompressError(ProtocolError):
    code = "E3020"
    default_advice = RetryAdvice(
        retryable=True,
        backoff=0.5,
        switch_host=True,
    )


class CommandOffline(ProtocolError):
    """命令已被协议事实账本确认 offline。

    这是当前 Provider 内的命令事实，不代表可以自动调用另一个 Provider 的
    同名数据。调用方如需其它 Provider 必须显式发起另一条 Query。
    """

    code = "E3035"
    http_status = 501
    default_advice = RetryAdvice(
        retryable=False,
        note="命令实测离线：直接返回不可用；如需其它 Provider 请显式选择",
    )


class ParseError(ProtocolError):
    code = "E3040"
    fatal: bool = False


class IntegrityViolation(ParseError):
    """数据完整性违规（已明确识别出脏数据，禁止解析降级）。"""

    code = "E3042"
    fatal = True
    default_advice = RetryAdvice(
        retryable=False,
        note="响应完整性校验未通过；拒绝降级以避免把噪声当行情",
    )


class LowConfidenceParse(ParseError):
    code = "E3041"
    default_advice = RetryAdvice(note="L2 置信度过低，要求调用方显式指定 DataProfile")


# --- E4xxx 数据与领域 ----------------------------------------------------- #
class DataError(TdxError):
    code = "E4000"


class ProfileError(DataError):
    code = "E4010"


class ProfileUndetectable(ProfileError):
    code = "E4011"
    default_advice = RetryAdvice(note="无法自动探测数据规格，需显式指定 profile")


class AdjustError(DataError):
    code = "E4020"


class CalendarError(DataError):
    code = "E4030"


class SymbolError(DataError):
    code = "E4040"
    http_status = 404


class TruncatedDataError(DataError):
    """请求 N 条但实际返回不足 N，且并非历史数据耗尽。"""

    code = "E4050"
    default_advice = RetryAdvice(
        retryable=True,
        backoff=1.0,
        switch_host=True,
        note="数据被截断：可在当前 Provider 内重试/换主站，或降低单次请求量",
    )


class FreshnessViolation(DataError):
    """结果无法满足“最新、真实、可追溯”的数据契约。"""

    code = "E4060"
    http_status = 503
    default_advice = RetryAdvice(
        retryable=True,
        backoff=0.2,
        switch_host=False,
        note="新鲜度证据不足：只可重试当前 Provider，禁止跨 Provider 替代",
    )


# --- E5xxx 本地文件 ------------------------------------------------------- #
class FileFormatError(TdxError):
    code = "E5000"


class DataFileNotFound(FileFormatError):
    code = "E5010"
    http_status = 404
    default_advice = RetryAdvice(note="本地数据文件不存在")


class TruncatedRecordError(FileFormatError):
    code = "E5020"


# --- E6xxx 流式订阅 ------------------------------------------------------- #
class StreamError(TdxError):
    code = "E6000"


class SubscriptionError(StreamError):
    code = "E6010"
    default_advice = RetryAdvice(
        retryable=True,
        backoff=1.0,
        switch_host=True,
        note="当前 Provider 的订阅失败；只在该 Provider 内重连/换 host",
    )


class GapUnfilledError(StreamError):
    code = "E6020"
    default_advice = RetryAdvice(
        retryable=True,
        backoff=1.0,
        switch_host=True,
        note="当前 Provider 流出现缺口；只在该 Provider 内恢复",
    )


class BackpressureOverflow(StreamError):
    code = "E6030"
    http_status = 429
    default_advice = RetryAdvice(retryable=True, backoff=0.1)


# --- E7xxx HTTP Web Provider --------------------------------------------- #
class WebSourceError(TdxError):
    code = "E7000"
    http_status = 502
    default_advice = RetryAdvice(
        retryable=True,
        backoff=0.5,
        switch_host=True,
        note="当前 Web Provider 请求失败：仅允许同 Provider endpoint 重试/切换",
    )


class AntiSpiderBlocked(WebSourceError):
    code = "E7010"
    http_status = 403
    default_advice = RetryAdvice(
        retryable=True,
        backoff=5.0,
        switch_host=True,
        note="检查当前 Provider Referer/UA/限速；不得切换 Provider 伪装成功",
    )


class WebRateLimited(WebSourceError):
    code = "E7020"
    http_status = 429
    default_advice = RetryAdvice(
        retryable=True,
        backoff=3.0,
        switch_host=False,
        note="当前 Provider 被限流：遵循退避/Retry-After，不跨 Provider",
    )


class SourceDeprecated(WebSourceError):
    code = "E7030"
    http_status = 410
    default_advice = RetryAdvice(
        retryable=False,
        note="当前 Provider 的该 Adapter 已下线；显式报告，不自动切换其它 Provider",
    )


class AllSourcesExhausted(TdxError):
    """旧聚合 WebClient 的兼容异常；Provider-aware 内核不主动产生。"""

    code = "E7040"
    http_status = 503
    default_advice = RetryAdvice(
        retryable=False,
        note="legacy aggregate source client exhausted；新 Provider API 不使用该聚合路径",
    )


# --- E8xxx 兼容层 --------------------------------------------------------- #
class CompatibilityError(TdxError):
    code = "E8000"


# --- E9xxx 内部 ----------------------------------------------------------- #
class InternalError(TdxError):
    code = "E9000"


class NotImplementedFeature(TdxError):
    code = "E9010"
    http_status = 501


def _register_all() -> None:
    for cls in list(globals().values()):
        if (
            isinstance(cls, type)
            and issubclass(cls, TdxError)
            and cls is not TdxError
            and cls not in RETRY_ADVICE
        ):
            RETRY_ADVICE[cls] = cls.default_advice


_register_all()
