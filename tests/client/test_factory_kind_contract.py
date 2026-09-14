from __future__ import annotations

import pytest

from tstdx.client import TdxClient, get_client


class _OpaquePool:
    pass


def test_factory_accepts_exact_canonical_kind() -> None:
    client = get_client("stock", pool=_OpaquePool())

    assert isinstance(client, TdxClient)


@pytest.mark.parametrize("kind", ["Stock", " stock", "stock ", "", "unknown"])
def test_factory_does_not_normalize_or_fallback_unknown_string_kind(kind: str) -> None:
    with pytest.raises(ValueError, match="未知客户端"):
        get_client(kind, pool=_OpaquePool())


@pytest.mark.parametrize("kind", [None, 1, True, [], {}, object()])
def test_factory_non_string_kind_is_deterministic_value_error(kind: object) -> None:
    with pytest.raises(ValueError, match="kind 必须是字符串"):
        get_client(kind, pool=_OpaquePool())  # type: ignore[arg-type]
