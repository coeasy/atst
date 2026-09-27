from __future__ import annotations

from typing import Any

import pytest

from atst.client import AsyncTdxClient
from atst.errors import ValidationError


class _Pool:
    hosts: list[Any] = []


@pytest.mark.asyncio
async def test_async_quotes_concurrent_collects_dicts_then_converts_once() -> None:
    client = AsyncTdxClient(pool=_Pool())
    calls: list[tuple[str, str]] = []

    async def fake_quotes(
        symbols: list[str],
        *,
        as_format: str = "dict",
        _collect: list[tuple[str, BaseException]] | None = None,
    ) -> list[dict[str, Any]]:
        symbol = symbols[0]
        calls.append((symbol, as_format))
        return [{"symbol": symbol, "price": 1.0}]

    client.quotes = fake_quotes  # type: ignore[method-assign]

    result = await client.quotes_concurrent(
        ["000001", "600000"],
        workers=2,
        as_format="tuple",
    )

    assert calls == [("000001", "dict"), ("600000", "dict")]
    assert result == [("000001", 1.0), ("600000", 1.0)]
    assert client.last_errors == []


@pytest.mark.asyncio
async def test_async_quotes_concurrent_validates_workers_like_sync() -> None:
    client = AsyncTdxClient(pool=_Pool())

    with pytest.raises(ValidationError, match="workers"):
        await client.quotes_concurrent(["000001"], workers=False)
    with pytest.raises(ValidationError, match="workers"):
        await client.quotes_concurrent(["000001"], workers=65)


@pytest.mark.asyncio
async def test_async_quotes_concurrent_isolates_symbol_errors_and_preserves_order() -> None:
    client = AsyncTdxClient(pool=_Pool())

    async def fake_quotes(
        symbols: list[str],
        *,
        as_format: str = "dict",
        _collect: list[tuple[str, BaseException]] | None = None,
    ) -> list[dict[str, Any]]:
        symbol = symbols[0]
        assert as_format == "dict"
        if symbol == "bad":
            raise RuntimeError("boom")
        return [{"symbol": symbol, "price": 1.0}]

    client.quotes = fake_quotes  # type: ignore[method-assign]

    result = await client.quotes_concurrent(["first", "bad", "last"], workers=3)

    assert [row["symbol"] for row in result] == ["first", "last"]
    assert len(client.last_errors) == 1
    assert client.last_errors[0][0] == "bad"
    assert isinstance(client.last_errors[0][1], RuntimeError)
