from __future__ import annotations

import pytest

from tstdx.client import TdxClient
from tstdx.client.core import _emit
from tstdx.errors import ParseError


def test_emit_accepts_declared_non_optional_formats_without_io() -> None:
    assert _emit([], "dict") == []
    assert _emit([], "tuple") == []


@pytest.mark.parametrize("as_format", ["json", "model", "yaml", "", None, 1])
def test_emit_rejects_unknown_or_non_string_output_format(as_format) -> None:
    with pytest.raises(ParseError, match="未知输出格式"):
        _emit([], as_format)  # type: ignore[arg-type]


def test_bars_rejects_unknown_output_format_before_pool_io() -> None:
    client = TdxClient(pool=object())

    with pytest.raises(ParseError, match="未知输出格式"):
        client.bars("600519", count=0, as_format="json")


def test_quotes_rejects_unknown_output_format_before_pool_io_for_empty_batch() -> None:
    client = TdxClient(pool=object())

    with pytest.raises(ParseError, match="未知输出格式"):
        client.quotes([], as_format="yaml")
