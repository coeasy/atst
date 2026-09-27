# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""Capability catalog and executor binding consistency checks."""

from __future__ import annotations

from dataclasses import dataclass


class CapabilityAuditError(RuntimeError):
    """Raised when a declared capability has no exact execution path."""


@dataclass(frozen=True, slots=True)
class CapabilityAuditReport:
    migrated_capabilities: int
    executable_bindings: int
    declared_bindings: int


def audit_capability_bindings() -> CapabilityAuditReport:
    """Validate catalog and Provider registry declarations against executor bindings.

    The audit checks exact Provider/Channel/Capability identity only. It does
    not attempt another provider when a binding is absent.

    The capability catalog is generated from the same table the executor consumes,
    so catalog-vs-bindings alone cannot notice a capability leaving the surface:
    both sides shrink together and the audit stays green. The Provider registry is
    an independently authored declaration, so it is reconciled against the
    executor bindings in **both** directions here.
    """

    from ..providers import PROVIDERS
    from ..runtime.executor import DIRECT_BINDINGS
    from .capability import MIGRATED_BINDINGS

    migrated = {item.key for item in MIGRATED_BINDINGS}
    executable = {item.key for item in DIRECT_BINDINGS}
    declared = {
        (provider, channel.id, capability)
        for provider in PROVIDERS.ids()
        for channel in PROVIDERS.get(provider).channels
        for capability in channel.capabilities
    }

    if not migrated:
        raise CapabilityAuditError("capability catalog is empty")

    missing = sorted(migrated - executable)
    if missing:
        raise CapabilityAuditError(f"capabilities without executor bindings: {missing[:5]!r}")

    unbound = sorted(declared - executable)
    if unbound:
        raise CapabilityAuditError(
            f"registry-declared (provider, channel, capability) with no executor binding: {unbound[:5]!r}"
        )
    ghost = sorted(executable - declared)
    if ghost:
        raise CapabilityAuditError(
            f"executor bindings outside the Provider registry: {ghost[:5]!r}"
        )

    return CapabilityAuditReport(
        migrated_capabilities=len(migrated),
        executable_bindings=len(executable),
        declared_bindings=len(declared),
    )
