# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""V14 dynamic provider execution adapters.

Static provider/channel/capability truth lives in :mod:`tstdx.providers`.
Caching is not a Provider identity; semantic cache integration lives above this
execution-adapter layer.
"""

from __future__ import annotations

from typing import Any

from .base import Provider
from .local import LocalProvider
from .router import ProviderRouter
from .tdx import TdxProvider
from .web import WebProvider

__all__ = [
    "Provider",
    "ProviderRouter",
    "TdxProvider",
    "WebProvider",
    "LocalProvider",
    "adapter_for",
    "default_adapters",
]


def adapter_for(provider_id: str, backend: Any | None = None) -> Provider:
    """为 canonical Provider 构建动态执行适配器。

    按注册表角色选择适配器类型：

    * ``tdx`` / ``local_vipdoc`` -> 专用适配器（TdxProvider / LocalProvider）
    * 其余 web Provider -> 通用 WebProvider（绑定 source 后端）

    例子::

        tdx = adapter_for("tdx", client=tdx_client)
        east = adapter_for("eastmoney", source=eastmoney_source)
    """
    from ..providers import PROVIDERS

    canonical = provider_id.strip().lower()
    spec = PROVIDERS.get(canonical)
    if spec.id == "tdx":
        return TdxProvider(backend)
    if spec.id == "local_vipdoc":
        return LocalProvider(backend)
    return WebProvider(spec.id, backend)


def default_adapters() -> dict[str, Provider]:
    """构建全部 canonical Provider 的空适配器（未配置 source/client）。

    返回 {provider_id: Provider}，供 Runtime 预注册 / 观测审计使用。
    未配置 backends 的适配器 supports() 返回 False，不会被执行。
    """
    from ..providers import PROVIDERS

    return {pid: adapter_for(pid, None) for pid in PROVIDERS.ids()}
