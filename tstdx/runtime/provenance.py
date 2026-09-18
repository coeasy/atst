# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""Runtime execution provenance guards.

Keeps Runtime execution identity aligned with returned Provider provenance.
Provider fallback is intentionally outside this module.
"""

from __future__ import annotations

from typing import Any

from ..catalog.provider_guard import (
    ProviderExecutionIdentity,
    ProviderIdentityMismatchError,
    validate_execution_identity,
)
from .identity import RuntimeExecutionIdentity


class RuntimeProvenanceMismatchError(RuntimeError):
    """Raised when a result provenance does not match execution identity."""


def validate_runtime_provenance(
    identity: RuntimeExecutionIdentity,
    result: Any,
) -> None:
    """Validate result provenance against the Runtime execution identity."""

    provenance = getattr(getattr(result, "meta", None), "provenance", None)
    provider = getattr(provenance, "provider", None)
    channel = getattr(provenance, "channel", None)
    capability = getattr(provenance, "capability", None)

    if provider is None:
        raise RuntimeProvenanceMismatchError("result provenance missing provider identity")

    try:
        validate_execution_identity(
            ProviderExecutionIdentity(
                provider=identity.provider,
                channel=identity.channel,
                capability=identity.capability,
            ),
            ProviderExecutionIdentity(
                provider=str(provider),
                channel=str(channel),
                capability=str(capability),
            ),
        )
    except ProviderIdentityMismatchError as exc:
        raise RuntimeProvenanceMismatchError(str(exc)) from exc
