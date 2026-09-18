# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""Standalone executor binding definitions.

This module owns the generated binding model. It intentionally does not import
Runtime or execute Providers.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ExecutorBinding:
    provider: str
    channel: str
    capability: str
    executor_name: str

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.provider, self.channel, self.capability)


def from_direct_binding(item: object) -> ExecutorBinding:
    """Convert a binding declaration into an immutable resolver model."""

    return ExecutorBinding(
        provider=str(item.provider),
        channel=str(item.channel),
        capability=str(item.capability),
        executor_name=str(item.executor_name),
    )
