# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""v14 Provider 统一契约（Phase 3 Provider Adapter）。

Refactor 计划 Phase 3 的目标是每个 Provider 拥有统一接口::

    capabilities() -> frozenset[str]   声明能力
    execute(request)                   执行一次运行时请求
    health() -> bool                   健康探针
    metadata() -> dict                 适配器元信息

与 canonical :mod:`tstdx.providers` 注册表的关系:

* ``tstdx.providers`` 是静态单一事实源（谁提供、支持什么、走哪个 channel）;
* ``tstdx.provider`` 是动态执行适配器（如何把一个请求实际发出去）;
* 一个 Provider 适配器必须声明自己的 canonical id，并优先从注册表
  推导 ``capabilities()``，避免第二套能力命名空间。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from ..providers import PROVIDERS, normalize_provider_id


class Provider(ABC):
    """Unified backend contract for runtime execution.

    Implementations may wrap TDX, HTTP, local reader or cache sources.
    """

    name: str = "provider"

    #: 绑定的 canonical Provider 注册 id（子类覆盖；为空则按 ``name`` 归一化）
    canonical_id: str | None = None

    def capabilities(self) -> frozenset[str]:
        """返回该适配器声明可执行的能力集合。

        默认从 canonical Registry 推导（单一事实源）。子类可在上面
        收窄（例如仅声明自身 source 已实现的方法）。
        """
        if self.canonical_id or self.name:
            pid = normalize_provider_id(self.canonical_id or self.name)
            try:
                spec = PROVIDERS.get(pid)
            except Exception:
                return frozenset()
            return spec.capabilities()
        return frozenset()

    def metadata(self) -> dict[str, Any]:
        """返回适配器元信息（供观测/审计）。"""
        return {"provider": self.name, "adapter": type(self).__name__}

    @abstractmethod
    def query(self, request: Any) -> Any:
        """Execute a runtime request."""
        raise NotImplementedError

    def execute(self, request: Any) -> Any:
        """统一执行入口（v14 Phase 3 命名）。默认委托 query。

        子类如需在 execute 侧做额外包装（例如录制 provenance、
        校验 capability），可在此重写并调用 :meth:`query`。
        """
        return self.query(request)

    def health(self) -> bool:
        """Return whether the provider is currently eligible for execution."""
        return True

    def supports(self, operation: str) -> bool:
        """Return whether this provider can execute ``operation``.

        Generic providers default to accepting all operations. Concrete adapters
        should narrow this when they can inspect their wrapped backend cheaply.
        """
        return bool(operation)
