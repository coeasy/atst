# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""Legacy service adapter (v15 兼容层).

Bridges the historical ``manager.tdx`` interface onto the v14/v13 runtime so
that code paths that previously reached ``UnifiedMarketDataService().manager.tdx``
keep working while the facade is deprecated.

This is a thin, opt-in shim: the canonical entry point is
:class:`tstdx.Client` / :class:`tstdx.RuntimeGateway` (v14 DAG 编排 + v13 执行
引擎). :class:`LegacyServiceAdapter` is only an optimization / compatibility
fallback and is not on any hot path.
"""

from __future__ import annotations

from typing import Any

from ..service import ProviderManager


class LegacyServiceAdapter:
    """Expose the legacy ``.manager.tdx`` surface over a :class:`ProviderManager`.

    Construction is lazy: the underlying :class:`ProviderManager` is created on
    first access of :attr:`manager`, so importing this module is cheap and no
    network I/O happens until the surface is actually used.
    """

    def __init__(
        self,
        *,
        hosts: list[str] | None = None,
        timeout: float = 5.0,
        manager: ProviderManager | None = None,
    ) -> None:
        self._hosts = hosts
        self._timeout = timeout
        self._manager = manager

    @property
    def manager(self) -> ProviderManager:
        """Lazily-built :class:`ProviderManager`; ``manager.tdx`` resolves here."""
        if self._manager is None:
            self._manager = ProviderManager(hosts=self._hosts, timeout=self._timeout)
        return self._manager

    def close(self) -> None:
        """Release the underlying :class:`ProviderManager` if we created it."""
        if self._manager is not None:
            self._manager.close()

    def __enter__(self) -> LegacyServiceAdapter:
        return self

    def __exit__(self, *exc: Any) -> bool:
        self.close()
        return False
