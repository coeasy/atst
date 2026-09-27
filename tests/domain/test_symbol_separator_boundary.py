from __future__ import annotations

import pytest

from atst.domain.symbol import normalize_symbol, parse_symbol, to_tdx_market
from atst.errors import SymbolError


@pytest.mark.parametrize(
    "raw",
    [
        "sh#600000",
        "sh/600000",
        "sh@600000",
        "600000?sh",
        "600000_sh",
        "hk/00700",
    ],
)
def test_unknown_symbol_separators_fail_closed(raw: str) -> None:
    with pytest.raises(SymbolError, match="无法解析证券代码"):
        parse_symbol(raw)


@pytest.mark.parametrize(
    "raw",
    [
        "sh.600000",
        "sh-600000",
        "sh:600000",
        "sh 600000",
        "600000.sh",
    ],
)
def test_documented_symbol_separators_remain_supported(raw: str) -> None:
    assert normalize_symbol(raw) == "sh600000"


def test_malformed_symbol_cannot_become_tdx_identity_through_helper() -> None:
    with pytest.raises(SymbolError):
        to_tdx_market("sh/600000")
