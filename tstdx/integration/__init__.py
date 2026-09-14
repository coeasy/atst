# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""Canonical external protocol adapters for tstdx v13.

Every transport delegates business semantics to Client/UnifiedRuntime. This
package contains framing, serialization and bounded task infrastructure only;
there are no legacy route/facade execution surfaces.
"""

from __future__ import annotations

from .runtime_http import create_runtime_app
from .runtime_tasks import RuntimeTaskStore, RuntimeTaskStoreFull
from .runtime_ws import RuntimeJsonRpcHandler
from .runtime_ws_server import RuntimeWsConfig, serve_runtime_ws

__all__ = [
    "create_runtime_app",
    "RuntimeJsonRpcHandler",
    "RuntimeWsConfig",
    "serve_runtime_ws",
    "RuntimeTaskStore",
    "RuntimeTaskStoreFull",
]
