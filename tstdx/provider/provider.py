"""V14 provider runtime base abstraction.

Providers are runtime execution backends. Existing TDX/Web/Local sources
can gradually migrate behind this contract without breaking public APIs.
"""

from abc import ABC, abstractmethod
from typing import Any


class Provider(ABC):
    """Unified provider interface."""

    name: str = "provider"

    @abstractmethod
    def query(self, request: Any) -> Any:
        """Execute a query request."""
        raise NotImplementedError

    def health(self) -> dict[str, Any]:
        return {"name": self.name, "available": True}
