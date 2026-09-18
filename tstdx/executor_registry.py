# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""Executor resolution layer.

Resolves exact Provider/Channel/Capability bindings. This layer never selects
another Provider and never performs fallback.
"""

from __future__ import annotations

from .executor_binding_registry import (
    ExecutorBindingRegistryError,
    resolve_binding,
)
from .executor_bindings import ExecutorBinding


class ExecutorResolutionError(RuntimeError):
    """Raised when a declared capability has no executable implementation."""


def resolve_executor(
    provider: str,
    channel: str,
    capability: str,
) -> ExecutorBinding:
    """Resolve one exact Provider/Channel/Capability executor."""

    try:
        return resolve_binding(
            provider,
            channel,
            capability,
        )
    except ExecutorBindingRegistryError as exc:
        raise ExecutorResolutionError(str(exc)) from exc
