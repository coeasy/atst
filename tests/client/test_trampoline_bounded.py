# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""trampoline 必须有步数上界：模板不收敛时失败，而不是把调用方挂死。

``_drive`` / ``_adrive`` 是 ``while True`` 的驱动器：每个 op 要么是一次 RPC，要么是
一次同客户端方法调用。模板只要持续 ``yield`` 而退出条件永不成立，这条循环里就
**没有 I/O、没有超时、也没有 socket 超时会来打断它**——协议侧的分页/重试上界都管不到。
第 47 轮审计把这条"结构性死循环"补成可判定的失败：步数越界即
``InternalError(phase=client_template)``。
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from atst.client._mixin import _ClientMixin, _op_call
from atst.errors import InternalError


class _SyncDriver(_ClientMixin):
    """最小驱动器：``_req`` 不碰网络，``noop`` 供模板调用。"""

    timeout = 1.0
    family = "quotation"

    def __init__(self) -> None:
        self._errors_lock = None
        self.last_errors = []

    def _req(self, cmd: int, body: bytes, **kwargs: Any) -> Any:  # noqa: ANN401
        return None

    def noop(self) -> None:
        return None


class _AsyncDriver(_SyncDriver):
    async def _req(self, cmd: int, body: bytes, **kwargs: Any) -> Any:  # noqa: ANN401
        return None

    async def noop(self) -> None:
        return None


def _forever():  # noqa: ANN202 - 测试用生成器
    while True:
        yield _op_call("noop")


def test_sync_driver_stops_a_non_converging_template() -> None:
    driver = _SyncDriver()
    with pytest.raises(InternalError) as caught:
        driver._drive(_forever())
    assert caught.value.context["phase"] == "client_template"
    assert caught.value.context["steps"] > 0


def test_async_driver_stops_a_non_converging_template() -> None:
    driver = _AsyncDriver()

    async def _run() -> None:
        await driver._adrive(_forever())

    with pytest.raises(InternalError) as caught:
        asyncio.run(_run())
    assert caught.value.context["phase"] == "client_template"


def test_converging_template_still_returns_its_value() -> None:
    driver = _SyncDriver()

    def _twice():
        yield _op_call("noop")
        yield _op_call("noop")
        return "done"

    assert driver._drive(_twice()) == "done"
