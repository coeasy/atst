# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""G51 — HTTP 服务面自己造的 ``Client`` 必须被释放，而且异常路径上也要释放。

第 26 轮 F-100 给 ``create_runtime_app`` 补了 lifespan 收尾（当时的说法是"每起停一次服务就
留下一批 socket 与心跳线程"——那句后果本轮实测是过头主张，见
``tests/runtime/test_close_chain_ownership.py``；入口本身留得住，它管的是所有权），但**收尾
自身没有尺子**：全仓扫不到一条断言"应用退出时那份 Client 被关过"。第 31 轮第 3 遍按 G41 的
口径复查时，它和执行器那条 ``with`` 是同一族——收尾写在 ``yield`` 之后而不是 ``finally``
里，于是"启动成功之后、收尾之前"任何一次抛错都会把这条链断在半空。

判据只看三件事，不看注释也不看分支写法：

1. 工厂自己造的那份 ``Client`` 在应用退出时被关**恰好一次**；
2. 调用方交进来的那份**不归它关**（与 MCP 面 ``_owns_client`` 同一条口径）；
3. 生命周期体里抛错时仍然关——这一格量的就是 ``finally`` 与"写在 yield 后面"的差别，
   缺了 ``finally`` 它必红（修复前实测见本轮台账）。
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from fastapi.testclient import TestClient

from tstdx.integration.runtime_http import create_runtime_app


class _SpyClient:
    """只数 ``close()``；其余方法一概不给，工厂也不该碰它们。"""

    instances: list[_SpyClient] = []

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.closes = 0
        self.kwargs = kwargs
        _SpyClient.instances.append(self)

    def close(self) -> None:
        self.closes += 1


def _factory_lifespan(app: Any) -> Any:
    """拿到工厂挂在路由上的那个生命周期上下文管理器。

    ``TestClient`` 的 ``__exit__`` 总会走完 shutdown，用它量不到"生命周期体中途抛错"这一格；
    直接驱动挂件的生成器才量得到 ``yield`` 与 ``finally`` 的分别。
    """
    return app.router.lifespan_context


@pytest.mark.unit
def test_the_app_closes_the_client_it_built(monkeypatch: pytest.MonkeyPatch) -> None:
    """① 工厂自己造的东西归它收尾：退出一次，关一次。"""

    _SpyClient.instances = []
    monkeypatch.setattr("tstdx.integration.runtime_http.Client", _SpyClient)
    app = create_runtime_app()
    built = _SpyClient.instances[0]
    with TestClient(app):
        assert built.closes == 0, "还没退出就关了，后面的请求拿什么服务"
    assert built.closes == 1, f"应用退出后工厂造的 Client 被关了 {built.closes} 次"


@pytest.mark.unit
def test_a_caller_supplied_client_is_left_alone(monkeypatch: pytest.MonkeyPatch) -> None:
    """② 传入的那份归调用方所有：服务面不许替它关。"""

    _SpyClient.instances = []
    monkeypatch.setattr("tstdx.integration.runtime_http.Client", _SpyClient)
    mine = _SpyClient()
    with TestClient(create_runtime_app(mine)):  # type: ignore[arg-type]
        pass
    assert mine.closes == 0, "服务面关掉了别人的客户端"


@pytest.mark.unit
def test_the_release_happens_even_when_the_lifespan_body_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """③ 启动之后、收尾之前抛错，那份池也必须被收掉——这就是 ``finally`` 的存在理由。

    修复前写法是 ``yield`` 后两行：异常从 ``yield`` 处穿出时，那两行根本不会被执行，
    socket 与心跳线程留在进程里。
    """

    _SpyClient.instances = []
    monkeypatch.setattr("tstdx.integration.runtime_http.Client", _SpyClient)
    app = create_runtime_app()
    built = _SpyClient.instances[0]

    async def main() -> None:
        lifespan = _factory_lifespan(app)
        with pytest.raises(RuntimeError):
            async with lifespan(app):
                raise RuntimeError("宿主在生命周期体里抛错")

    asyncio.run(main())
    assert built.closes == 1, f"异常路径没收尾（关了 {built.closes} 次）"
