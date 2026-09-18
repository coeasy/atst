# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""Canonical zero-cache execution kernel (v13/v16 single-kernel line).

The retired v14 envelope layer (``Runtime`` / ``RuntimeGateway`` /
``QueryRequest`` / ``QueryResponse`` / DAG planner / provider router) was
physically removed; :class:`tstdx.client.api.Client` is the sole business
entrypoint on top of :class:`UnifiedRuntime`.
"""

from .kernel import KernelExecutor, UnifiedRuntime

__all__ = ["KernelExecutor", "UnifiedRuntime"]
