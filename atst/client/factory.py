# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""客户端工厂（REFACTOR_PLAN_v8 P5：自 ``atst/client.py`` 拆出）。

``get_client`` 与 ``_CLIENT_REGISTRY``。公开导入路径不变：仍从
:mod:`atst.client` 导入。
"""

from __future__ import annotations

from typing import Any, Literal, overload

from .sync import (
    ExMarketClient,
    F10Client,
    GoodsClient,
    MacClient,
    TdxClient,
)

_CLIENT_REGISTRY = {
    "stock": TdxClient,
    "goods": GoodsClient,
    "ex": ExMarketClient,
    "mac": MacClient,
    "f10": F10Client,
}


# overload：kind 字面量 → 具体客户端类型（L1b：修 cli.py 等下游 attr-defined）
@overload
def get_client(kind: Literal["stock"] = "stock", **kwargs: Any) -> TdxClient: ...


@overload
def get_client(kind: Literal["goods"], **kwargs: Any) -> GoodsClient: ...


@overload
def get_client(kind: Literal["ex"], **kwargs: Any) -> ExMarketClient: ...


@overload
def get_client(kind: Literal["mac"], **kwargs: Any) -> MacClient: ...


@overload
def get_client(kind: Literal["f10"], **kwargs: Any) -> F10Client: ...


def get_client(kind: str = "stock", **kwargs: Any) -> Any:
    """按 exact canonical kind 获取对应协议族客户端。

    ``kind`` ∈ {stock, goods, ex, mac, f10}。未知或非字符串 ``kind`` 显式
    抛 :class:`ValueError`。工厂不做大小写/空白归一化，也不静默回退到
    ``TdxClient``，避免拼写和配置错误被掩盖。

    .. note:: 静态返回类型由上方 ``@overload`` 按 ``kind`` 字面量收窄。"""
    if not isinstance(kind, str):
        raise ValueError(f"get_client: kind 必须是字符串，收到 {type(kind).__name__}: {kind!r}")
    if kind not in _CLIENT_REGISTRY:
        raise ValueError(
            f"get_client: 未知客户端 kind={kind!r}，可用值: {sorted(_CLIENT_REGISTRY)}"
        )
    return _CLIENT_REGISTRY[kind](**kwargs)
