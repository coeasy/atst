"""直连传输层入参守卫的域闸判据（第 25 轮 G34 把归类一并钉住）。

两件事分别有判据：

1. **合**：坏形状永不静默转换（``"1"`` 不是 1、``True`` 不是 1、``1.0`` 不是 1），越界与
   非法编码当场拒——这份一直是本文件的射程。
2. **归类**：拒的时候必须是 :class:`~atst.errors.ValidationError`（E1010 / HTTP 422 /
   ``retryable=False``）。第 25 轮之前这些守卫报 :class:`~atst.errors.ParseError`，
   对外因此是 **502** 且带 ``RetryAdvice(retryable=True, switch_host=True)``——调用方把
   ``start`` 写成 70000，服务器回答"上游故障，建议换主机重试"，而换到哪台都会同样失败。
   :func:`_assert_client_side_rejection` 就是为此存在：谁把守卫的异常类换回协议侧，
   这里立刻红。
"""

from __future__ import annotations

from contextlib import contextmanager

import pytest

from atst.client import TdxClient
from atst.client.core import (
    _encode_gbk_field,
    _require_bool,
    _require_int,
    _require_yyyymmdd,
    _standard_market_id,
    period_to_category,
)
from atst.error_envelope import to_error_envelope
from atst.errors import ValidationError


def _client_without_io() -> TdxClient:
    return TdxClient(pool=object())


def _assert_client_side_rejection(exc: ValidationError) -> None:
    """拒绝必须归到"调用方的错"那一类，且不许建议重试（量对外发布的那份信封）。"""
    envelope = to_error_envelope(exc)
    assert envelope.code == "E1010", f"入参守卫的拒绝码漂移：{envelope.code}"
    assert envelope.http_status == 422, f"入参守卫对外不再是客户端错误：{envelope.http_status}"
    assert envelope.retryable is False, "写错的参数被建议重试：换主机也会同样失败"


@contextmanager
def _raises_client_error(match: str | None = None):
    """拒得对还不够，还要拒成对的那一类。"""
    with pytest.raises(ValidationError, match=match) as excinfo:
        yield
    _assert_client_side_rejection(excinfo.value)


@pytest.mark.parametrize("market", ["xx", "", -1, 3, True, 1.0, "01", "9", "sh1"])
def test_standard_market_parser_rejects_unknown_or_coercible_identity(market) -> None:
    with _raises_client_error():
        _standard_market_id(market)


def test_standard_market_parser_accepts_only_canonical_names_and_ids() -> None:
    assert _standard_market_id("sz") == 0
    assert _standard_market_id("SH") == 1
    assert _standard_market_id(" bj ") == 2
    assert [_standard_market_id(value) for value in (0, 1, 2)] == [0, 1, 2]
    #: CLI/HTTP/MCP 把 market 声明成字符串（`--market` 的缺省就是 `"0"`），所以数字写法
    #: 是同一份契约的另一半，不是对脏输入的宽容。2026-09-22 盘中只认前缀时
    #: `atst security-count` 与 `GET /v13/security/count` 都当场被拒。
    assert [_standard_market_id(value) for value in ("0", "1", "2")] == [0, 1, 2]


@pytest.mark.parametrize("period", ["99", "-1", "", None, 4])
def test_period_parser_rejects_unknown_or_non_string_category(period) -> None:
    with _raises_client_error():
        period_to_category(period)  # type: ignore[arg-type]


def test_period_parser_keeps_declared_numeric_categories_only() -> None:
    assert period_to_category("0") == 0
    assert period_to_category("11") == 11
    assert period_to_category("day") == 4


@pytest.mark.parametrize("value", [True, 1.0, "1", -1])
def test_protocol_integer_parser_never_coerces_values(value) -> None:
    with _raises_client_error():
        _require_int("field", value, minimum=0)


@pytest.mark.parametrize("value", ["1", 0, None, 1.0])
def test_protocol_bool_parser_never_coerces_values(value) -> None:
    with _raises_client_error():
        _require_bool("flag", value)  # type: ignore[arg-type]


def test_yyyymmdd_parser_rejects_calendar_invalid_date() -> None:
    with _raises_client_error("合法 YYYYMMDD"):
        _require_yyyymmdd("date", 20240230)


def test_yyyymmdd_parser_accepts_leap_day() -> None:
    assert _require_yyyymmdd("date", 20240229) == 20240229


def test_gbk_field_rejects_lossy_or_truncated_encoding() -> None:
    with _raises_client_error("无法无损编码"):
        _encode_gbk_field("filename", "😀.txt", max_bytes=80)
    with _raises_client_error("超过协议上限"):
        _encode_gbk_field("filename", "中" * 41, max_bytes=80)
    with _raises_client_error("NUL"):
        _encode_gbk_field("filename", "abc\x00.txt", max_bytes=80)


def test_security_count_rejects_unknown_market_before_pool_io() -> None:
    client = _client_without_io()

    with _raises_client_error("未知标准市场"):
        client.security_count("unknown")


def test_security_list_rejects_fractional_start_before_pool_io() -> None:
    client = _client_without_io()

    with _raises_client_error("start 必须是整数"):
        client.security_list("sh", start=1.5)  # type: ignore[arg-type]


def test_export_security_list_rejects_zero_page_budget_before_pool_io() -> None:
    client = _client_without_io()

    with _raises_client_error("max_pages"):
        client.export_security_list("sz", max_pages=0)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"count": -1},
        {"count": 1.5},
        {"start": -1},
        {"start": 1.5},
        {"market": 3},
    ],
)
def test_bars_rejects_invalid_page_or_market_before_pool_io(kwargs) -> None:
    client = _client_without_io()

    with _raises_client_error():
        client.bars("600519", **kwargs)


def test_bars_rejects_page_range_that_would_overflow_uint16_offset() -> None:
    client = _client_without_io()

    with _raises_client_error("16-bit"):
        client.bars("600519", start=65530, count=10)


@pytest.mark.parametrize("kwargs", [{"count": 70000}, {"start": 70000}])
def test_bars_rejects_values_beyond_the_16bit_page_space(kwargs) -> None:
    """``0xFFFF`` 那一格一直有人守，第 25 轮之前只是守错了对象：报 502 而不是 422。"""
    client = _client_without_io()

    with _raises_client_error("超过上限 65535"):
        client.bars("600519", **kwargs)


def test_minute_history_rejects_invalid_calendar_date_before_pool_io() -> None:
    client = _client_without_io()

    with _raises_client_error("合法 YYYYMMDD"):
        client.minute_history("600519", 20240230)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"filename": "😀.txt"},
        {"filename": "中" * 41},
        {"filename": "abc\x00.txt"},
        {"filename": "test.txt", "offset": -1},
        {"filename": "test.txt", "length": -1},
        {"filename": "test.txt", "max_packets": 0},
        {"filename": "test.txt", "max_packets": 1.5},
        {"filename": "test.txt", "strict": 1},
    ],
)
def test_file_download_rejects_lossy_or_coercible_parameters_before_pool_io(kwargs) -> None:
    client = _client_without_io()

    with _raises_client_error():
        client.file_download("600519", **kwargs)  # type: ignore[arg-type]
