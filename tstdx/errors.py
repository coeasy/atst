# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""统一错误体系（§24）。

错误码分 9 段，与异常类一一对应::

    E1xxx  配置与入参      config / validation
    E2xxx  传输层          connection / timeout / host
    E3xxx  协议层          framing / codec / parser
    E4xxx  数据与领域      profile / adjust / calendar
    E5xxx  本地文件        vipdoc / block / finance
    E6xxx  流式订阅        subscription / gap / backpressure
    E7xxx  HTTP Web 源     anti-spider / rate-limit / deprecated
    E8xxx  门面层          facade / bridge shim
    E9xxx  内部与依赖      internal / missing-dependency

每个异常都携带一个 :class:`RetryAdvice`，供上层自动决策
（重试 / 退避 / 换主站 / 降级离线 / 降级 HTTP Web 源）。

使用侧文档（错误树速查表、易混对照、扩展规则、上层边界约定）见
``docs/errors.md``。
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
    "UnknownCommand",
    "CommandOffline",
    "ParseError",
    "LowConfidenceParse",
    "ChecksumMismatch",
    "DataError",
    "ProfileError",
    "ProfileUndetectable",
    "AdjustError",
    "CalendarError",
    "SymbolError",
    "TruncatedDataError",
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
    "SourceUnavailable",
    "AllSourcesExhausted",
    "CompatibilityError",
    "CompatibilityWarning",
    "InternalError",
    "NotImplementedFeature",
]


@dataclass(frozen=True)
class RetryAdvice:
    """异常的可重试性建议（§24.2）。"""

    retryable: bool = False
    backoff: float = 0.0
    max_retries: int = 0
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
                "max_retries": a.max_retries,
                "switch_host": a.switch_host,
                "fallback_to_offline": a.fallback_to_offline,
                "fallback_to_web": a.fallback_to_web,
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
        max_retries=3,
        switch_host=True,
        fallback_to_offline=True,
        fallback_to_web=True,
    )


class ConnectionFailed(TransportError):
    code = "E2010"
    default_advice = RetryAdvice(
        retryable=True,
        backoff=0.5,
        max_retries=2,
        switch_host=True,
        fallback_to_offline=True,
        fallback_to_web=True,
    )


class ConnectionClosed(TransportError):
    code = "E2020"
    default_advice = RetryAdvice(
        retryable=True,
        backoff=0.5,
        max_retries=3,
        switch_host=True,
        fallback_to_offline=True,
        fallback_to_web=True,
    )


class ReadTimeout(TransportError):
    code = "E2030"
    http_status = 504
    default_advice = RetryAdvice(
        retryable=True,
        backoff=1.0,
        max_retries=2,
        switch_host=True,
        fallback_to_offline=True,
        fallback_to_web=True,
    )


class WriteTimeout(ReadTimeout):
    code = "E2031"


class AllHostsUnreachable(TransportError):
    """所有 TDX 主站均不可达——触发 §33 的 HTTP Web 源降级。"""

    code = "E2040"
    http_status = 503
    default_advice = RetryAdvice(
        retryable=True,
        backoff=5.0,
        max_retries=1,
        fallback_to_offline=True,
        fallback_to_web=True,
        note="全部主站不可达，应降级到本地 vipdoc 或 HTTP Web 源",
    )


#: U2：``AllHostsUnreachable`` 的可操作「下一步」建议（附在错误消息尾部）。
#: 提示用 ``tstdx hosts scan`` 自测生成排名文件，以及可复制的配置片段。
ALL_HOSTS_UNREACHABLE_NEXT_STEPS = (
    "下一步：\n"
    "  1. 运行 `tstdx hosts scan` 对内置候选池并发测速，自动把可达主站写入"
    " ~/.tstdx/server_ranking.json（后续启动按真实 RTT 排序）；\n"
    "  2. 或在配置文件中显式指定主站（hosts.servers），例如：\n"
    "       [hosts]\n"
    '       servers = [["180.153.18.170", 7709], ["60.191.117.167", 7709]]\n'
    "  3. 或查看当前主站池：`tstdx hosts list`。"
)


class RateLimitedLocal(TransportError):
    code = "E2050"
    http_status = 429
    default_advice = RetryAdvice(retryable=True, backoff=0.2, max_retries=5)


# --- E3xxx 协议层 --------------------------------------------------------- #
class ProtocolError(TdxError):
    code = "E3000"
    http_status = 502
    default_advice = RetryAdvice(retryable=True, backoff=0.5, max_retries=1, switch_host=True)


class FramingError(ProtocolError):
    code = "E3010"


class DecompressError(ProtocolError):
    code = "E3020"
    default_advice = RetryAdvice(retryable=True, backoff=0.5, max_retries=1, switch_host=True)


class UnknownCommand(ProtocolError):
    code = "E3030"
    http_status = 501
    default_advice = RetryAdvice(note="未知命令：应由 L2/L3 兜底，并上报 ProtocolSniffer")


class CommandOffline(ProtocolError):
    """B5：命令在账本中标记为 ``STATUS_OFFLINE``（多主站实测无响应）。

    client 层在发请求前 fail-fast，避免调用方吃满「N 主站 × 3s 超时」链。
    ``message`` 应包含替代方案指引（如 0x054C → 逐只 0x0530 回退说明）。
    重试无意义（``retryable=False``）：除非账本 status 更新，结果不会改变。
    """

    code = "E3035"
    http_status = 501
    default_advice = RetryAdvice(
        retryable=False, note="命令实测离线：请改用替代命令或更新账本 status"
    )


class ParseError(ProtocolError):
    code = "E3040"
    #: 是否为「致命」错误。致命错误**禁止**被三级分派降级到 L2/L3 兜底——
    #: 因为它意味着已经识别出了明确的数据问题（而非「布局未知」），
    #: 降级只会把「已知错误」替换成「看起来像数据的噪声」。
    fatal: bool = False


class IntegrityViolation(ParseError):
    """数据完整性违规（✅ 已知错误，禁止降级）。

    与 :class:`ParseError` 的区别：

    ============  ==============================  ==========================
    错误类型      含义                            分派器行为
    ============  ==============================  ==========================
    ParseError    布局未知 / 结构漂移             降级 L2 启发式 → L3 原始
    本异常        **已明确识别出数据不对**         直接抛出，不做任何兜底
    ============  ==============================  ==========================

    典型场景：请求参数写反导致服务端返回「**帧结构合法但内容错误**」的响应
    （实测 0x0530 请求 market 字节写反时，服务端返回一个回声 ``600839``、
    载荷全零的 56 字节响应）。若此时降级到 L2，调用方会拿到一堆看起来
    正常的零值记录——这比直接报错危险得多。
    """

    code = "E3042"
    fatal = True
    default_advice = RetryAdvice(
        retryable=False,
        note="响应完整性校验未通过（回声/哨兵/区间校验），已拒绝降级以避免脏数据",
    )


class LowConfidenceParse(ParseError):
    code = "E3041"
    default_advice = RetryAdvice(note="L2 置信度过低，要求调用方显式指定 DataProfile")


class ChecksumMismatch(ProtocolError):
    code = "E3050"


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
    """请求 N 条但实际返回不足 N，且**并非**历史数据耗尽（v5 PG1/PG2）。

    典型场景：服务端单请求上限静默截断（``bars`` count>800 不分页直接
    透传）、多包下载循环提前终止而 ``total_len`` 未满足。仅在调用方
    ``strict=True`` 时抛出；非 strict 模式以 :class:`UserWarning` 告警
    （与 ``export_security_list`` 截断告警风格一致）。
    """

    code = "E4050"
    default_advice = RetryAdvice(
        retryable=True,
        backoff=1.0,
        max_retries=2,
        switch_host=True,
        note="数据被截断：可重试、换主站，或降低单次请求量",
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
# 处置决议（工业审计 §3-3 → v1.4.0 M1 接线落地，用户拍板）：本四类自此具备
# 库内真实抛点——QuoteStream / AsyncQuoteStream 内核由 streaming.engine 组件
# 构成（DeltaMerger / BackpressureQueue / ReconnectPolicy），其中：
#   SubscriptionError  → 不可解析订阅符号（streaming/__init__.py::_resolve_payload）
#   GapUnfilledError   → 标的连续 3 轮未见于响应（同上；不自动补数，0x0530 仅回快照）
#   BackpressureOverflow → 背压队列溢出丢弃最旧事件（_poll_once 溢出检测）
# StreamError（E6000）仍为公共基类。禁止再造第五种流式异常。
class StreamError(TdxError):
    code = "E6000"


class SubscriptionError(StreamError):
    code = "E6010"
    default_advice = RetryAdvice(retryable=True, backoff=1.0, max_retries=3, switch_host=True)


class GapUnfilledError(StreamError):
    code = "E6020"
    default_advice = RetryAdvice(retryable=True, backoff=1.0, max_retries=3, switch_host=True)


class BackpressureOverflow(StreamError):
    code = "E6030"
    http_status = 429
    default_advice = RetryAdvice(retryable=True, backoff=0.1, max_retries=10)


# --- E7xxx HTTP Web 源 ---------------------------------------------------- #
class WebSourceError(TdxError):
    code = "E7000"
    http_status = 502
    default_advice = RetryAdvice(
        retryable=True,
        backoff=0.5,
        max_retries=2,
        switch_host=True,
        note="切换下一个 HTTP Web 源",
    )


class AntiSpiderBlocked(WebSourceError):
    code = "E7010"
    http_status = 403
    default_advice = RetryAdvice(
        retryable=True,
        backoff=5.0,
        max_retries=1,
        switch_host=True,
        note="疑似被反爬拦截：检查 Referer/UA，降速后切换下一源",
    )


class WebRateLimited(WebSourceError):
    code = "E7020"
    http_status = 429
    default_advice = RetryAdvice(retryable=True, backoff=3.0, max_retries=3, switch_host=True)


class SourceDeprecated(WebSourceError):
    code = "E7030"
    http_status = 410
    default_advice = RetryAdvice(switch_host=True, note="上游接口下线，禁用该 Adapter 并上报")


class SourceUnavailable(TdxError):
    """数据源当前不可用（所有通路均无对应能力或环境失效）。

    与 :class:`AllHostsUnreachable`（传输层：某协议族主站池全部超时）不同，
    本类专指**能力层面**的不可用：命令已 offline、协议族整体下线、或
    该方法既无 tdx 通路也无 web 兜底路径——即"当前版本无可用数据源"。

    与 :class:`CommandOffline`（协议层：某命令实测下线）也不同：后者针对
    具体命令号，本类针对具体**方法**（如 ``block_quotes`` 命令 offline 且
    无 web 兜底路径 → 抛本类并给出替代方案提示）。

    P13-A：为 facade 层「仅 tdx 路由」但对应命令已停答且**无 web 近似能力**
    的方法提供明确异常路径，避免用户拿到误导性的 ``AllHostsUnreachable``
    或长时间读超时。``context["alternatives"]`` 给出可用的替代方案。
    """

    code = "E7050"
    http_status = 503
    default_advice = RetryAdvice(
        retryable=False,
        note="数据源环境级失效或无对应能力，见 context['alternatives']",
    )


class AllSourcesExhausted(TdxError):
    code = "E7040"
    http_status = 503
    default_advice = RetryAdvice(retryable=True, backoff=30.0, max_retries=1)


# --- E8xxx 兼容层 --------------------------------------------------------- #
class CompatibilityError(TdxError):
    code = "E8000"


class CompatibilityWarning(UserWarning):
    """兼容垫片行为与原始库不一致时发出（非异常，不中断）。"""


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
