# Copyright (c) 2026 tstdx contributors
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


def audit_capability_bindings() -> CapabilityAuditReport:
    """Validate catalog entries against generated direct bindings.

    The audit checks exact Provider/Channel/Capability identity only. It does
    not attempt another provider when a binding is absent.
    """

    from ..runtime.executor import DIRECT_BINDINGS
    from .capability import MIGRATED_BINDINGS

    migrated = {item.key for item in MIGRATED_BINDINGS}
    executable = {item.key for item in DIRECT_BINDINGS}

    if not migrated:
        raise CapabilityAuditError("capability catalog is empty")

    missing = sorted(migrated - executable)
    if missing:
        raise CapabilityAuditError(f"capabilities without executor bindings: {missing[:5]!r}")

    return CapabilityAuditReport(
        migrated_capabilities=len(migrated),
        executable_bindings=len(executable),
    )
