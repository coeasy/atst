# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""tstdx v13 — Provider-first market-data runtime.

The supported business API is Client/AsyncClient. Low-level protocol, reader,
web and transport modules remain implementation building blocks and can be
imported from their explicit submodules, but they are not parallel public
business runtimes.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

__version__ = "1.0.0"

__all__ = [
    "__version__",
    "Client",
    "AsyncClient",
    "CurrentnessMode",
    "QuerySpec",
    "QueryFingerprint",
    "QueryPlan",
    "QueryPlanner",
    "StreamSpec",
    "StreamPlan",
    "StreamPlanner",
    "ProvenanceKind",
    "Provenance",
    "ResultMeta",
    "QueryResult",
    "ProviderRegistry",
    "PROVIDERS",
    "SemanticResultCache",
    "PersistentSemanticCache",
    "UnifiedRuntime",
    "FallbackPolicy",
    "ProviderAttempt",
    "OrchestratedResult",
    "ProviderOrchestrator",
    "BatchItem",
    "BatchResult",
    "SingleFlight",
    "NegativeCache",
    "ErrorEnvelope",
    "to_error_envelope",
    "StreamState",
    "StatefulQuoteStream",
    "AsyncStatefulQuoteStream",
    "configure",
    "get_config",
    "load_config",
]

from . import codec, errors, protocol  # noqa: E402,F401
from .errors import TdxError  # noqa: E402,F401


def configure(**kwargs: Any) -> Any:
    from .config import load_config

    return load_config(overrides=kwargs)


def get_config() -> Any:
    from .config import get_config as _get

    return _get()


if TYPE_CHECKING:  # pragma: no cover
    from .batch import BatchItem, BatchResult, NegativeCache, SingleFlight
    from .cache_persistent import PersistentSemanticCache
    from .cache_semantic import SemanticResultCache
    from .client_api import AsyncClient, Client
    from .config import load_config
    from .error_envelope import ErrorEnvelope, to_error_envelope
    from .orchestration import (
        FallbackPolicy,
        OrchestratedResult,
        ProviderAttempt,
        ProviderOrchestrator,
    )
    from .providers import PROVIDERS, ProviderRegistry
    from .query import CurrentnessMode, QueryFingerprint, QueryPlan, QueryPlanner, QuerySpec
    from .result import Provenance, ProvenanceKind, QueryResult, ResultMeta
    from .runtime import UnifiedRuntime
    from .stream_contract import StreamPlan, StreamPlanner, StreamSpec
    from .streaming.state import StreamState
    from .streaming.stateful import AsyncStatefulQuoteStream, StatefulQuoteStream


_LAZY: dict[str, tuple[str, str]] = {
    "Client": ("tstdx.client_api", "Client"),
    "AsyncClient": ("tstdx.client_api", "AsyncClient"),
    "CurrentnessMode": ("tstdx.query", "CurrentnessMode"),
    "QuerySpec": ("tstdx.query", "QuerySpec"),
    "QueryFingerprint": ("tstdx.query", "QueryFingerprint"),
    "QueryPlan": ("tstdx.query", "QueryPlan"),
    "QueryPlanner": ("tstdx.query", "QueryPlanner"),
    "StreamSpec": ("tstdx.stream_contract", "StreamSpec"),
    "StreamPlan": ("tstdx.stream_contract", "StreamPlan"),
    "StreamPlanner": ("tstdx.stream_contract", "StreamPlanner"),
    "ProvenanceKind": ("tstdx.result", "ProvenanceKind"),
    "Provenance": ("tstdx.result", "Provenance"),
    "ResultMeta": ("tstdx.result", "ResultMeta"),
    "QueryResult": ("tstdx.result", "QueryResult"),
    "ProviderRegistry": ("tstdx.providers", "ProviderRegistry"),
    "PROVIDERS": ("tstdx.providers", "PROVIDERS"),
    "SemanticResultCache": ("tstdx.cache_semantic", "SemanticResultCache"),
    "PersistentSemanticCache": ("tstdx.cache_persistent", "PersistentSemanticCache"),
    "UnifiedRuntime": ("tstdx.runtime", "UnifiedRuntime"),
    "FallbackPolicy": ("tstdx.orchestration", "FallbackPolicy"),
    "ProviderAttempt": ("tstdx.orchestration", "ProviderAttempt"),
    "OrchestratedResult": ("tstdx.orchestration", "OrchestratedResult"),
    "ProviderOrchestrator": ("tstdx.orchestration", "ProviderOrchestrator"),
    "BatchItem": ("tstdx.batch", "BatchItem"),
    "BatchResult": ("tstdx.batch", "BatchResult"),
    "SingleFlight": ("tstdx.batch", "SingleFlight"),
    "NegativeCache": ("tstdx.batch", "NegativeCache"),
    "ErrorEnvelope": ("tstdx.error_envelope", "ErrorEnvelope"),
    "to_error_envelope": ("tstdx.error_envelope", "to_error_envelope"),
    "StreamState": ("tstdx.streaming.state", "StreamState"),
    "StatefulQuoteStream": ("tstdx.streaming.stateful", "StatefulQuoteStream"),
    "AsyncStatefulQuoteStream": ("tstdx.streaming.stateful", "AsyncStatefulQuoteStream"),
    "load_config": ("tstdx.config", "load_config"),
}


def __getattr__(name: str) -> Any:
    if name in _LAZY:
        mod_path, attr = _LAZY[name]
        import importlib

        mod = importlib.import_module(mod_path)
        value = getattr(mod, attr)
        globals()[name] = value
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(_LAZY))
