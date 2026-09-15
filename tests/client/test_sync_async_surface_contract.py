from __future__ import annotations

import asyncio
import inspect
from collections.abc import Callable
from typing import Any

import pytest

from tstdx.client import (
    AsyncExMarketClient,
    AsyncF10Client,
    AsyncGoodsClient,
    AsyncMacClient,
    AsyncTdxClient,
    ExMarketClient,
    F10Client,
    GoodsClient,
    MacClient,
    TdxClient,
)

_PAIRS = (
    (TdxClient, AsyncTdxClient),
    (GoodsClient, AsyncGoodsClient),
    (ExMarketClient, AsyncExMarketClient),
    (MacClient, AsyncMacClient),
    (F10Client, AsyncF10Client),
)
_SYNC_HELPERS = {"parse_text"}


def _public_methods(cls: type[Any]) -> dict[str, Callable[..., Any]]:
    methods: dict[str, Callable[..., Any]] = {}
    for name, value in inspect.getmembers(cls):
        if name.startswith("_"):
            continue
        if inspect.isfunction(value) or inspect.ismethod(value):
            methods[name] = value
    return methods


def _parameter_contract(callable_obj: Callable[..., Any]) -> tuple[tuple[str, Any, Any], ...]:
    signature = inspect.signature(callable_obj)
    return tuple(
        (parameter.name, parameter.kind, parameter.default)
        for parameter in signature.parameters.values()
    )


@pytest.mark.parametrize(("sync_cls", "async_cls"), _PAIRS)
def test_sync_async_public_method_sets_are_exact_mirrors(
    sync_cls: type[Any],
    async_cls: type[Any],
) -> None:
    sync_methods = _public_methods(sync_cls)
    async_methods = _public_methods(async_cls)

    assert set(sync_methods) == set(async_methods)


@pytest.mark.parametrize(("sync_cls", "async_cls"), _PAIRS)
def test_sync_async_public_parameter_contracts_are_exact_mirrors(
    sync_cls: type[Any],
    async_cls: type[Any],
) -> None:
    sync_methods = _public_methods(sync_cls)
    async_methods = _public_methods(async_cls)

    for name in sorted(sync_methods):
        assert _parameter_contract(sync_methods[name]) == _parameter_contract(async_methods[name]), name


@pytest.mark.parametrize(("sync_cls", "async_cls"), _PAIRS)
def test_async_mirror_methods_are_coroutines_except_pure_helpers(
    sync_cls: type[Any],
    async_cls: type[Any],
) -> None:
    sync_methods = _public_methods(sync_cls)
    async_methods = _public_methods(async_cls)

    for name in sorted(sync_methods):
        assert not asyncio.iscoroutinefunction(sync_methods[name]), name
        if name in _SYNC_HELPERS:
            assert not asyncio.iscoroutinefunction(async_methods[name]), name
        else:
            assert asyncio.iscoroutinefunction(async_methods[name]), name


def test_base_constructor_parameter_contract_is_mirrored() -> None:
    assert _parameter_contract(TdxClient.__init__) == _parameter_contract(AsyncTdxClient.__init__)
