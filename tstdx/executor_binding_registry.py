# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""Single source of truth for executable Provider bindings."""

from __future__ import annotations

from .executor_bindings import ExecutorBinding, from_direct_binding


class ExecutorBindingRegistryError(RuntimeError):
    """Raised when executable bindings are inconsistent."""


_BINDINGS: dict[tuple[str, str, str], ExecutorBinding] = {}


def register_binding(binding: ExecutorBinding) -> None:
    if binding.key in _BINDINGS:
        raise ExecutorBindingRegistryError(
            f"duplicate executor binding: {binding.key!r}"
        )
    _BINDINGS[binding.key] = binding


def register_direct_binding(item: object) -> None:
    register_binding(from_direct_binding(item))


def resolve_binding(
    provider: str,
    channel: str,
    capability: str,
) -> ExecutorBinding:
    try:
        return _BINDINGS[(provider, channel, capability)]
    except KeyError as exc:
        raise ExecutorBindingRegistryError(
            f"missing executor binding: {provider}/{channel}/{capability}"
        ) from exc


def all_bindings() -> tuple[ExecutorBinding, ...]:
    return tuple(_BINDINGS.values())
