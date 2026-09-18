from __future__ import annotations

import pytest

from tstdx.client import TdxClient
from tstdx.client.core import (
    _encode_gbk_field,
    _require_int,
    _require_yyyymmdd,
    _standard_market_id,
    period_to_category,
)
from tstdx.errors import ParseError


def _client_without_io() -> TdxClient:
    return TdxClient(pool=object())


@pytest.mark.parametrize("market", ["xx", "", -1, 3, True, 1.0, "1"])
def test_standard_market_parser_rejects_unknown_or_coercible_identity(market) -> None:
    with pytest.raises(ParseError):
        _standard_market_id(market)


def test_standard_market_parser_accepts_only_canonical_names_and_ids() -> None:
    assert _standard_market_id("sz") == 0
    assert _standard_market_id("SH") == 1
    assert _standard_market_id(" bj ") == 2
    assert [_standard_market_id(value) for value in (0, 1, 2)] == [0, 1, 2]


@pytest.mark.parametrize("period", ["99", "-1", "", None, 4])
def test_period_parser_rejects_unknown_or_non_string_category(period) -> None:
    with pytest.raises(ParseError):
        period_to_category(period)  # type: ignore[arg-type]


def test_period_parser_keeps_declared_numeric_categories_only() -> None:
    assert period_to_category("0") == 0
    assert period_to_category("11") == 11
    assert period_to_category("day") == 4


@pytest.mark.parametrize("value", [True, 1.0, "1", -1])
def test_protocol_integer_parser_never_coerces_values(value) -> None:
    with pytest.raises(ParseError):
        _require_int("field", value, minimum=0)


def test_yyyymmdd_parser_rejects_calendar_invalid_date() -> None:
    with pytest.raises(ParseError, match="合法 YYYYMMDD"):
        _require_yyyymmdd("date", 20240230)


def test_yyyymmdd_parser_accepts_leap_day() -> None:
    assert _require_yyyymmdd("date", 20240229) == 20240229


def test_gbk_field_rejects_lossy_or_truncated_encoding() -> None:
    with pytest.raises(ParseError, match="无法无损编码"):
        _encode_gbk_field("filename", "😀.txt", max_bytes=80)
    with pytest.raises(ParseError, match="超过协议上限"):
        _encode_gbk_field("filename", "中" * 41, max_bytes=80)
    with pytest.raises(ParseError, match="NUL"):
        _encode_gbk_field("filename", "abc\x00.txt", max_bytes=80)


def test_security_count_rejects_unknown_market_before_pool_io() -> None:
    client = _client_without_io()

    with pytest.raises(ParseError, match="未知标准市场"):
        client.security_count("unknown")


def test_security_list_rejects_fractional_start_before_pool_io() -> None:
    client = _client_without_io()

    with pytest.raises(ParseError, match="start 必须是整数"):
        client.security_list("sh", start=1.5)  # type: ignore[arg-type]


def test_export_security_list_rejects_zero_page_budget_before_pool_io() -> None:
    client = _client_without_io()

    with pytest.raises(ParseError, match="max_pages"):
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

    with pytest.raises(ParseError):
        client.bars("600519", **kwargs)


def test_bars_rejects_page_range_that_would_overflow_uint16_offset() -> None:
    client = _client_without_io()

    with pytest.raises(ParseError, match="16-bit"):
        client.bars("600519", start=65530, count=10)


def test_minute_history_rejects_invalid_calendar_date_before_pool_io() -> None:
    client = _client_without_io()

    with pytest.raises(ParseError, match="合法 YYYYMMDD"):
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

    with pytest.raises(ParseError):
        client.file_download("600519", **kwargs)  # type: ignore[arg-type]
