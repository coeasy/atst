from __future__ import annotations

import pytest

from atst.client import TdxClient
from atst.client.core import _emit
from atst.errors import ValidationError

#: 输出格式是**调用方写错的那一格**，不是上游故障。E1010/422/不可重试这条映射由
#: ``tests/errors/test_error_envelope.py`` 与 ``test_client_parameter_fail_closed.py``
#: 在别处钉住（第 25 轮 G34）。


def test_emit_accepts_declared_non_optional_formats_without_io() -> None:
    assert _emit([], "dict") == []
    assert _emit([], "tuple") == []


@pytest.mark.parametrize("as_format", ["json", "model", "yaml", "", None, 1])
def test_emit_rejects_unknown_or_non_string_output_format(as_format) -> None:
    with pytest.raises(ValidationError, match="未知输出格式"):
        _emit([], as_format)  # type: ignore[arg-type]


def test_bars_rejects_unknown_output_format_before_pool_io() -> None:
    client = TdxClient(pool=object())

    with pytest.raises(ValidationError, match="未知输出格式"):
        client.bars("600519", count=0, as_format="json")


def test_quotes_rejects_unknown_output_format_before_pool_io_for_empty_batch() -> None:
    client = TdxClient(pool=object())

    with pytest.raises(ValidationError, match="未知输出格式"):
        client.quotes([], as_format="yaml")
