# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""Provider adapter contract for v14 runtime evolution.

The runtime should depend on this contract rather than concrete TDX/Web
clients. Concrete providers remain responsible for transport and source
specific behavior.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class ProviderMetadata:
    """Stable provider identity exposed to runtime and observability."""

    provider: str
    version: str = ""
    description: str = ""


class ProviderAdapter(ABC):
    """Canonical v14 provider boundary."""

    @property
    @abstractmethod
    def metadata(self) -> ProviderMetadata:
        """Return provider identity metadata."""

    @abstractmethod
    def capabilities(self) -> frozenset[str]:
        """Return capabilities implemented by this adapter."""

    @abstractmethod
    def execute(self, plan: Any) -> Any:
        """Execute one already compiled QueryPlan."""

    def health(self) -> dict[str, Any]:
        """Optional health information without affecting execution."""
        return {"provider": self.metadata.provider, "healthy": True}
