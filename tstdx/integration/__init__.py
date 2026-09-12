# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""External protocol adapters for tstdx.

Legacy HTTP/WS/MCP surfaces remain available for compatibility. New canonical
surfaces live in ``runtime_http`` / ``runtime_ws`` / ``runtime_tasks`` and use
UnifiedRuntime + ErrorEnvelope without implicit cross-Provider fallback.
Optional server dependencies are still imported only when their factories run.
"""

from __future__ import annotations

from .runtime_http import create_runtime_app
from .runtime_tasks import RuntimeTaskStore, RuntimeTaskStoreFull
from .runtime_ws import RuntimeJsonRpcHandler

__all__ = [
    "create_runtime_app",
    "RuntimeJsonRpcHandler",
    "RuntimeTaskStore",
    "RuntimeTaskStoreFull",
]
