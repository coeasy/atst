"""错误分类树测试（§24）：验证所有异常类的 code、http_status、advice、hierarchy。

覆盖：唯一 code、E-range 段、http_status 合理性、RetryAdvice 完整性、
advice_for()/http_status_for()、to_dict() 序列化、继承层级。
"""

from __future__ import annotations

import pytest

from tstdx.errors import (
    RETRY_ADVICE,
    AdjustError,
    AllHostsUnreachable,
    AllSourcesExhausted,
    AntiSpiderBlocked,
    BackpressureOverflow,
    CalendarError,
    CommandOffline,
    CompatibilityError,
    ConfigError,
    ConnectionClosed,
    ConnectionFailed,
    DataError,
    DataFileNotFound,
    DecompressError,
    DependencyMissingError,
    FileFormatError,
    FramingError,
    FreshnessViolation,
    GapUnfilledError,
    IntegrityViolation,
    InternalError,
    LowConfidenceParse,
    NotImplementedFeature,
    ParseError,
    ProfileError,
    ProfileUndetectable,
    ProtocolError,
    RateLimitedLocal,
    ReadTimeout,
    RetryAdvice,
    SourceDeprecated,
    StreamError,
    SubscriptionError,
    SymbolError,
    TdxError,
    TransportError,
    TruncatedDataError,
    TruncatedRecordError,
    ValidationError,
    WebRateLimited,
    WebSourceError,
    WriteTimeout,
    advice_for,
    http_status_for,
)

# 所有异常类（排除基类和 Warning）
ALL_ERROR_CLASSES = [
    ConfigError,
    ValidationError,
    DependencyMissingError,
    TransportError,
    ConnectionFailed,
    ConnectionClosed,
    ReadTimeout,
    WriteTimeout,
    AllHostsUnreachable,
    RateLimitedLocal,
    ProtocolError,
    FramingError,
    DecompressError,
    CommandOffline,
    ParseError,
    IntegrityViolation,
    LowConfidenceParse,
    DataError,
    ProfileError,
    ProfileUndetectable,
    AdjustError,
    CalendarError,
    SymbolError,
    TruncatedDataError,
    FreshnessViolation,
    FileFormatError,
    DataFileNotFound,
    TruncatedRecordError,
    StreamError,
    SubscriptionError,
    GapUnfilledError,
    BackpressureOverflow,
    WebSourceError,
    AntiSpiderBlocked,
    WebRateLimited,
    SourceDeprecated,
    AllSourcesExhausted,
    CompatibilityError,
    InternalError,
    NotImplementedFeature,
]

#: E-range 段映射：类 → 期望的 E 前缀
E_RANGE: dict[type, str] = {
    ConfigError: "E1",
    ValidationError: "E1",
    DependencyMissingError: "E1",
    TransportError: "E2",
    ConnectionFailed: "E2",
    ConnectionClosed: "E2",
    ReadTimeout: "E2",
    WriteTimeout: "E2",
    AllHostsUnreachable: "E2",
    RateLimitedLocal: "E2",
    ProtocolError: "E3",
    FramingError: "E3",
    DecompressError: "E3",
    CommandOffline: "E3",
    ParseError: "E3",
    IntegrityViolation: "E3",
    LowConfidenceParse: "E3",
    DataError: "E4",
    ProfileError: "E4",
    ProfileUndetectable: "E4",
    AdjustError: "E4",
    CalendarError: "E4",
    SymbolError: "E4",
    TruncatedDataError: "E4",
    FreshnessViolation: "E4",
    FileFormatError: "E5",
    DataFileNotFound: "E5",
    TruncatedRecordError: "E5",
    StreamError: "E6",
    SubscriptionError: "E6",
    GapUnfilledError: "E6",
    BackpressureOverflow: "E6",
    WebSourceError: "E7",
    AntiSpiderBlocked: "E7",
    WebRateLimited: "E7",
    SourceDeprecated: "E7",
    AllSourcesExhausted: "E7",
    CompatibilityError: "E8",
    InternalError: "E9",
    NotImplementedFeature: "E9",
}

#: 偏离 ``TdxError`` 默认 500 的 http_status（由 ``test_http_status_table_matches_deviations``
#: 双向核对：继承默认值的类不得进表，改过状态码的类必须进表）
EXPECTED_HTTP_STATUS = {
    ConfigError: 400,
    ValidationError: 422,
    DependencyMissingError: 503,
    TransportError: 502,
    ConnectionFailed: 502,
    ConnectionClosed: 502,
    ReadTimeout: 504,
    WriteTimeout: 504,
    AllHostsUnreachable: 503,
    RateLimitedLocal: 429,
    ProtocolError: 502,
    FramingError: 502,
    DecompressError: 502,
    CommandOffline: 501,
    ParseError: 502,
    IntegrityViolation: 502,
    LowConfidenceParse: 502,
    SymbolError: 404,
    FreshnessViolation: 503,
    DataFileNotFound: 404,
    BackpressureOverflow: 429,
    AntiSpiderBlocked: 403,
    WebSourceError: 502,
    WebRateLimited: 429,
    SourceDeprecated: 410,
    AllSourcesExhausted: 503,
    NotImplementedFeature: 501,
}

#: 期望的继承层级（直接基类）：必须覆盖 ``ALL_ERROR_CLASSES`` 每一个类，
#: 由 ``test_hierarchy_table_covers_every_class`` 双向核对
EXPECTED_PARENTS: dict[type, type] = {
    ConfigError: TdxError,
    ValidationError: ConfigError,
    DependencyMissingError: ConfigError,
    TransportError: TdxError,
    ConnectionFailed: TransportError,
    ConnectionClosed: TransportError,
    ReadTimeout: TransportError,
    WriteTimeout: ReadTimeout,
    AllHostsUnreachable: TransportError,
    RateLimitedLocal: TransportError,
    ProtocolError: TdxError,
    FramingError: ProtocolError,
    DecompressError: ProtocolError,
    CommandOffline: ProtocolError,
    ParseError: ProtocolError,
    IntegrityViolation: ParseError,
    LowConfidenceParse: ParseError,
    DataError: TdxError,
    ProfileError: DataError,
    ProfileUndetectable: ProfileError,
    AdjustError: DataError,
    CalendarError: DataError,
    SymbolError: DataError,
    TruncatedDataError: DataError,
    FreshnessViolation: DataError,
    FileFormatError: TdxError,
    DataFileNotFound: FileFormatError,
    TruncatedRecordError: FileFormatError,
    StreamError: TdxError,
    SubscriptionError: StreamError,
    GapUnfilledError: StreamError,
    BackpressureOverflow: StreamError,
    WebSourceError: TdxError,
    AntiSpiderBlocked: WebSourceError,
    WebRateLimited: WebSourceError,
    SourceDeprecated: WebSourceError,
    AllSourcesExhausted: TdxError,
    CompatibilityError: TdxError,
    InternalError: TdxError,
    NotImplementedFeature: TdxError,
}


@pytest.mark.unit
class TestErrorTaxonomy:
    """错误分类树完整性测试。"""

    def test_all_codes_unique(self):
        """所有异常类的 code 必须唯一。"""
        codes: dict[str, list[type]] = {}
        for cls in ALL_ERROR_CLASSES:
            code = cls.code
            codes.setdefault(code, []).append(cls)
        dupes = {k: v for k, v in codes.items() if len(v) > 1}
        assert not dupes, f"重复 code: {dupes}"

    @pytest.mark.parametrize("cls", ALL_ERROR_CLASSES, ids=lambda c: c.__name__)
    def test_code_format(self, cls: type):
        """每个 code 必须符合 E\\d{4} 格式。"""
        import re

        assert re.match(r"^E\d{4}$", cls.code), f"{cls.__name__}: {cls.code}"

    @pytest.mark.parametrize(
        "cls,expected_prefix",
        list(E_RANGE.items()),
        ids=lambda c: c.__name__ if isinstance(c, type) else str(c),
    )
    def test_e_range(self, cls: type, expected_prefix: str):
        """每个异常的 code 必须落在正确的 E-range 段。"""
        assert cls.code.startswith(expected_prefix), (
            f"{cls.__name__}: code={cls.code} 期望前缀 {expected_prefix}"
        )

    @pytest.mark.parametrize("cls", ALL_ERROR_CLASSES, ids=lambda c: c.__name__)
    def test_http_status_sane(self, cls: type):
        """http_status 必须是 4xx 或 5xx。"""
        status = cls.http_status
        assert 400 <= status <= 599, f"{cls.__name__}: http_status={status}"

    def test_http_status_table_matches_deviations(self):
        """``EXPECTED_HTTP_STATUS`` 必须正好等于「偏离默认 500」的那批类。

        这张表此前没有任何测试读取它——一张无人核对的期望表比没有表更糟，它会让人
        误以为 http_status 已经被双向核对过。
        """
        deviations = {cls for cls in ALL_ERROR_CLASSES if cls.http_status != 500}
        listed = set(EXPECTED_HTTP_STATUS)
        assert deviations == listed, (
            f"表里缺：{sorted(c.__name__ for c in deviations - listed)}；"
            f"表里多（其实继承 500）：{sorted(c.__name__ for c in listed - deviations)}"
        )
        for cls, status in EXPECTED_HTTP_STATUS.items():
            assert cls.http_status == status, (
                f"{cls.__name__}: 表期望 {status}，实际 {cls.http_status}"
            )

    @pytest.mark.parametrize("cls", ALL_ERROR_CLASSES, ids=lambda c: c.__name__)
    def test_has_retry_advice(self, cls: type):
        """每个异常必须有 RetryAdvice（类级 default_advice）。"""
        assert hasattr(cls, "default_advice"), f"{cls.__name__} 缺少 default_advice"
        assert isinstance(cls.default_advice, RetryAdvice), (
            f"{cls.__name__}.default_advice 不是 RetryAdvice"
        )

    @pytest.mark.parametrize("cls", ALL_ERROR_CLASSES, ids=lambda c: c.__name__)
    def test_retry_advice_registered(self, cls: type):
        """每个异常类必须在 RETRY_ADVICE 字典中注册。"""
        assert cls in RETRY_ADVICE, f"{cls.__name__} 未在 RETRY_ADVICE 注册"

    def test_advice_for_tdx_error(self):
        """advice_for 对 TdxError 返回正确 advice。"""
        exc = ConnectionFailed("test")
        adv = advice_for(exc)
        assert adv.retryable is True
        assert adv.max_retries >= 1

    def test_advice_for_non_tdx(self):
        """advice_for 对非 TdxError 返回默认 advice。"""
        adv = advice_for(ValueError("not tdx"))
        assert adv.retryable is False

    def test_http_status_for_tdx_error(self):
        """http_status_for 对 TdxError 返回正确状态码。"""
        exc = ReadTimeout("test")
        assert http_status_for(exc) == 504

    def test_http_status_for_non_tdx(self):
        """http_status_for 对非 TdxError 返回 500。"""
        assert http_status_for(ValueError("test")) == 500

    @pytest.mark.parametrize("cls", ALL_ERROR_CLASSES, ids=lambda c: c.__name__)
    def test_to_dict_serializable(self, cls: type):
        """to_dict() 必须返回可 JSON 序列化的 dict。"""
        import json

        exc = cls("test message")
        d = exc.to_dict()
        assert isinstance(d, dict)
        assert "error" in d and d["error"] == cls.__name__
        assert "code" in d
        assert "message" in d
        assert "advice" in d
        assert isinstance(d["advice"], dict)
        # 确保可 JSON 序列化
        json.dumps(d)

    @pytest.mark.parametrize(
        "cls,parent",
        list(EXPECTED_PARENTS.items()),
        ids=lambda c: c.__name__ if isinstance(c, type) else str(c),
    )
    def test_hierarchy(self, cls: type, parent: type):
        """继承层级必须符合设计。"""
        assert issubclass(cls, parent), f"{cls.__name__} 应继承 {parent.__name__}"

    def test_hierarchy_table_covers_every_class(self):
        """``EXPECTED_PARENTS`` 必须写到每一个类，且写的就是直接基类。

        逐条 ``test_hierarchy`` 只核对表内已有条目：漏一行不会红，那张表就会退化成
        一份没人核对的历史清单（本步实测漏掉 4 行）。
        """
        listed = set(EXPECTED_PARENTS)
        missing = {cls.__name__ for cls in ALL_ERROR_CLASSES} - {c.__name__ for c in listed}
        extra = {c.__name__ for c in listed} - {cls.__name__ for cls in ALL_ERROR_CLASSES}
        assert not missing and not extra, (
            f"表未覆盖：{sorted(missing)}；表内是表外类：{sorted(extra)}"
        )
        for cls, parent in EXPECTED_PARENTS.items():
            assert cls.__mro__[1] is parent, (
                f"{cls.__name__}: 直接基类是 {cls.__mro__[1].__name__}，表里写 {parent.__name__}"
            )

    def test_integrity_violation_is_fatal(self):
        """IntegrityViolation 必须标记为 fatal。"""
        assert IntegrityViolation.fatal is True
        assert not ParseError.fatal

    def test_all_classes_subclass_tdx_error(self):
        """所有异常类必须继承 TdxError。"""
        for cls in ALL_ERROR_CLASSES:
            assert issubclass(cls, TdxError), f"{cls.__name__} 不是 TdxError 子类"

    def test_the_table_is_the_module_surface(self):
        """本表必须正好覆盖 ``tstdx.errors`` 对外的异常类，双向都不许多。

        删类与删表是两次独立动作：多删一行会红（名字不存在），少删一行在既有判据里
        完全静默——表就会变成一份没人核对的历史清单。
        """
        import tstdx.errors as errors

        public = {
            name
            for name in errors.__all__
            if isinstance(getattr(errors, name), type)
            and issubclass(getattr(errors, name), TdxError)
            and getattr(errors, name) is not TdxError
        }
        listed = {cls.__name__ for cls in ALL_ERROR_CLASSES}
        assert public == listed, (
            f"模块有而表里没有：{sorted(public - listed)}；表里有而模块没有：{sorted(listed - public)}"
        )
