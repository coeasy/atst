# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""Executor resolution layer.

Keeps capability binding resolution separate from Runtime execution. This layer
only resolves an implementation declared by the catalog; it never selects an
alternative Provider.
"""

from __future__ import annotations

from dataclasses import dataclass


class ExecutorResolutionError(RuntimeError):
    """Raised when a declared capability has no executable implementation."""


@dataclass(frozen=True, slots=True)
class ExecutorBinding:
    provider: str
    channel: str
    capability: str
    executor_name: str


def resolve_executor(
    provider: str,
    channel: str,
    capability: str,
) -> ExecutorBinding:
    """Resolve one exact Provider/Channel/Capability executor.

    No fallback is allowed here. Missing bindings are reported to the caller.
    """

    from .direct_provider import DIRECT_BINDINGS

    key = (provider, channel, capability)
    for binding in DIRECT_BINDINGS:
        if binding.key == key:
            return ExecutorBinding(
                provider=binding.provider,
                channel=binding.channel,
                capability=binding.capability,
                executor_name=binding.executor_name,
            )

    raise ExecutorResolutionError(
        f"missing executor binding: provider={provider!r} "
        f"channel={channel!r} capability={capability!r}"
    )
