# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""Provider registry consistency checks.

This module keeps registry, binding and capability declarations aligned. It does
not perform provider selection and never introduces fallback behaviour.
"""

from __future__ import annotations

from dataclasses import dataclass


class ProviderRegistryAuditError(RuntimeError):
    """Raised when Provider declarations are internally inconsistent."""


@dataclass(frozen=True, slots=True)
class ProviderAuditReport:
    providers: int
    bindings: int


def audit_provider_registry() -> ProviderAuditReport:
    """Validate the provider registry and executable binding surface.

    The import is intentionally local to avoid circular imports during module
    initialization. The actual registry remains the single source of truth.
    """

    from ..providers import PROVIDERS
    from ..runtime.executor import DIRECT_BINDINGS

    providers = tuple(PROVIDERS.ids())
    if not providers:
        raise ProviderRegistryAuditError("provider registry is empty")

    seen: set[tuple[str, str, str]] = set()
    for binding in DIRECT_BINDINGS:
        if binding.key in seen:
            raise ProviderRegistryAuditError(f"duplicate provider binding: {binding.key!r}")
        seen.add(binding.key)
        if binding.provider not in providers:
            raise ProviderRegistryAuditError(
                f"binding references unknown provider: {binding.provider!r}"
            )

    return ProviderAuditReport(
        providers=len(providers),
        bindings=len(DIRECT_BINDINGS),
    )
