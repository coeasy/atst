# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""Client 一等公民便捷方法：存在性、签名镜像、只翻译为 call() 不新增内核路径。"""

from __future__ import annotations

import inspect

import pytest

from atst import AsyncClient, Client

pytestmark = pytest.mark.unit

_CONVENIENCE = (
    "adjusted_bars",
    "capital_changes",
    "finance",
    "f10",
    "ex_bars",
    "goods_bars",
    "mac_quotes",
)


def test_convenience_methods_exist_on_both_clients() -> None:
    for name in _CONVENIENCE:
        assert callable(getattr(Client, name, None)), name
        assert callable(getattr(AsyncClient, name, None)), name


def test_async_methods_are_coroutines() -> None:
    for name in _CONVENIENCE:
        assert inspect.iscoroutinefunction(getattr(AsyncClient, name)), name


def test_methods_delegate_to_call_only() -> None:
    """便捷方法体只调用 self.call，不得出现 execute/运行时直连。"""
    source = inspect.getsource(Client)
    for name in _CONVENIENCE:
        method = inspect.getsource(getattr(Client, name))
        assert "self.call(" in method, name
        assert "self.execute(" not in method, name
        assert "runtime" not in method, name
    assert source


def test_adjusted_bars_maps_adjust_to_method() -> None:
    captured: dict[str, object] = {}
    client = Client()

    def fake_call(self: Client, capability: str, *args: object, **kwargs: object) -> object:
        captured["capability"] = capability
        captured["kwargs"] = kwargs
        return None

    original = Client.call
    Client.call = fake_call  # type: ignore[method-assign]
    try:
        client.adjusted_bars("sh600519", adjust="hfq", count=5)
    finally:
        Client.call = original  # type: ignore[method-assign]
    assert captured["capability"] == "adjusted_bars"
    assert captured["kwargs"]["method"] == "hfq"
