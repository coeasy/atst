# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""Unified startup audit entrypoint for the provider-first runtime."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RuntimeAuditReport:
    providers: int
    capabilities: int
    bindings: int


def audit_runtime() -> RuntimeAuditReport:
    """Run all structural runtime audits.

    This function validates declarations only. It never changes Provider
    selection and never introduces fallback behaviour.
    """

    from .capability_audit import audit_capability_bindings
    from .provider_audit import audit_provider_registry

    provider_report = audit_provider_registry()
    capability_report = audit_capability_bindings()

    return RuntimeAuditReport(
        providers=provider_report.providers,
        capabilities=capability_report.migrated_capabilities,
        bindings=capability_report.executable_bindings,
    )
