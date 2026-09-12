from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class Provider(ABC):
    """Unified backend contract for runtime execution.

    Implementations may wrap TDX, HTTP, local reader or cache sources.
    """

    name: str = "provider"

    @abstractmethod
    def query(self, request: Any) -> Any:
        """Execute a runtime request."""
        raise NotImplementedError

    def health(self) -> bool:
        """Return whether the provider is currently eligible for execution."""
        return True

    def supports(self, operation: str) -> bool:
        """Return whether this provider can execute ``operation``.

        Generic providers default to accepting all operations. Concrete adapters
        should narrow this when they can inspect their wrapped backend cheaply.
        """
        return bool(operation)
