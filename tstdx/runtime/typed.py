from __future__ import annotations

from dataclasses import fields
from typing import Any, Mapping

from ..errors import ValidationError
from ..providers import normalize_provider_id
from ..typed_query import CapabilityQuery
from .request import QueryRequest

_RESERVED_FIELDS = frozenset({"capability", "provider", "options"})


def request_from_typed(
    query: CapabilityQuery,
    *,
    metadata: Mapping[str, Any] | None = None,
) -> QueryRequest:
    """Translate one immutable typed query into the Runtime boundary envelope.

    This adapter deliberately refuses typed contracts that have not yet been
    admitted to the canonical Provider registry. It therefore cannot become a
    second capability namespace beside :mod:`tstdx.providers`.
    """
    if not isinstance(query, CapabilityQuery):
        raise TypeError("query must be a CapabilityQuery")
    if not query.semantic_ready:
        raise ValidationError(
            f"typed capability {query.capability!r} is not registered for semantic runtime execution",
            context={
                "capability": query.capability,
                "semantic_ready": False,
            },
        )

    params = dict(query.options)
    for item in fields(query):
        if item.name in _RESERVED_FIELDS:
            continue
        params[item.name] = getattr(query, item.name)

    runtime_metadata = dict(metadata or {})
    if query.provider:
        query_provider = normalize_provider_id(query.provider)
        existing = runtime_metadata.get("provider")
        if existing is not None:
            metadata_provider = normalize_provider_id(str(existing))
            if metadata_provider != query_provider:
                raise ValidationError(
                    "typed query provider conflicts with runtime metadata provider",
                    context={
                        "query_provider": query_provider,
                        "metadata_provider": metadata_provider,
                    },
                )
        runtime_metadata["provider"] = query_provider

    return QueryRequest(
        operation=query.capability,
        params=params,
        metadata=runtime_metadata,
    )
