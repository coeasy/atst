# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""tstdx —— 通达信（TDX）行情数据通用协议库。

设计目标
--------
* **协议全覆盖**：7709 标准 / 7727 扩展市场 / MAC 专属 / F10 资料 / 商品语义，
  未知命令走 L2 启发式 + L3 原始透传，**永不丢包**。
* **数据全兼容**：市场 × 品种 × 周期 × 口径差异全部参数化为 ``DataProfile``。
* **实时为一等公民**：PushChannel + 增量合并 + 断线补数 + 背压 + 重连。
* **Provider-first 运行时**：公开查询先编译为单 Provider / 单 Channel 的
  ``QueryPlan``，跨 Provider fallback 只能由显式策略层触发。
* **Fail-closed Streaming**：canonical stream 采用显式 ``StreamState``。
* **语义缓存**：canonical cache 以完整 ``QueryFingerprint`` 隔离 Provider /
  Channel / Capability，并保持原始 provenance。
* **统一执行**：``UnifiedRuntime`` 把 Planner / Direct Provider / Cache /
  SingleFlight / negative cache 收口到同一主体链路。
* **原创实现**：洁净室流程，协议事实源于自有抓包与本地文件分析。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

__version__ = "1.0.0"

__all__ = [
    "__version__",
    "TdxClient",
    "AsyncTdxClient",
    "WebQuoteClient",
    "DayBarReader",
    "MinBarReader",
    "BlockReader",
    "FinanceReader",
    "DataProfile",
    "CurrentnessMode",
    "QuerySpec",
    "QueryFingerprint",
    "QueryPlan",
    "QueryPlanner",
    "ProvenanceKind",
    "Provenance",
    "ResultMeta",
    "QueryResult",
    "ProviderRegistry",
    "PROVIDERS",
    "SemanticResultCache",
    "DirectBinding",
    "DirectProviderExecutor",
    "DIRECT_BINDINGS",
    "audit_direct_bindings",
    "UnifiedRuntime",
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
    "facade",
    "observability",
    "streaming",
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
    from .cache_semantic import SemanticResultCache
    from .client import AsyncTdxClient, TdxClient
    from .config import load_config
    from .direct_provider import (
        DIRECT_BINDINGS,
        DirectBinding,
        DirectProviderExecutor,
        audit_direct_bindings,
    )
    from .error_envelope import ErrorEnvelope, to_error_envelope
    from .providers import PROVIDERS, ProviderRegistry
    from .query import CurrentnessMode, QueryFingerprint, QueryPlan, QueryPlanner, QuerySpec
    from .reader import BlockReader, DataProfile, DayBarReader, FinanceReader, MinBarReader
    from .result import Provenance, ProvenanceKind, QueryResult, ResultMeta
    from .runtime import UnifiedRuntime
    from .streaming.state import StreamState
    from .streaming.stateful import AsyncStatefulQuoteStream, StatefulQuoteStream
    from .web import WebQuoteClient


_LAZY: dict[str, tuple[str, str]] = {
    "TdxClient": ("tstdx.client", "TdxClient"),
    "AsyncTdxClient": ("tstdx.client", "AsyncTdxClient"),
    "WebQuoteClient": ("tstdx.web", "WebQuoteClient"),
    "DayBarReader": ("tstdx.reader", "DayBarReader"),
    "MinBarReader": ("tstdx.reader", "MinBarReader"),
    "BlockReader": ("tstdx.reader", "BlockReader"),
    "FinanceReader": ("tstdx.reader", "FinanceReader"),
    "DataProfile": ("tstdx.reader", "DataProfile"),
    "CurrentnessMode": ("tstdx.query", "CurrentnessMode"),
    "QuerySpec": ("tstdx.query", "QuerySpec"),
    "QueryFingerprint": ("tstdx.query", "QueryFingerprint"),
    "QueryPlan": ("tstdx.query", "QueryPlan"),
    "QueryPlanner": ("tstdx.query", "QueryPlanner"),
    "ProvenanceKind": ("tstdx.result", "ProvenanceKind"),
    "Provenance": ("tstdx.result", "Provenance"),
    "ResultMeta": ("tstdx.result", "ResultMeta"),
    "QueryResult": ("tstdx.result", "QueryResult"),
    "ProviderRegistry": ("tstdx.providers", "ProviderRegistry"),
    "PROVIDERS": ("tstdx.providers", "PROVIDERS"),
    "SemanticResultCache": ("tstdx.cache_semantic", "SemanticResultCache"),
    "DirectBinding": ("tstdx.direct_provider", "DirectBinding"),
    "DirectProviderExecutor": ("tstdx.direct_provider", "DirectProviderExecutor"),
    "DIRECT_BINDINGS": ("tstdx.direct_provider", "DIRECT_BINDINGS"),
    "audit_direct_bindings": ("tstdx.direct_provider", "audit_direct_bindings"),
    "UnifiedRuntime": ("tstdx.runtime", "UnifiedRuntime"),
    "BatchItem": ("tstdx.batch", "BatchItem"),
    "BatchResult": ("tstdx.batch", "BatchResult"),
    "SingleFlight": ("tstdx.batch", "SingleFlight"),
    "NegativeCache": ("tstdx.batch", "NegativeCache"),
    "ErrorEnvelope": ("tstdx.error_envelope", "ErrorEnvelope"),
    "to_error_envelope": ("tstdx.error_envelope", "to_error_envelope"),
    "StreamState": ("tstdx.streaming.state", "StreamState"),
    "StatefulQuoteStream": ("tstdx.streaming.stateful", "StatefulQuoteStream"),
    "AsyncStatefulQuoteStream": (
        "tstdx.streaming.stateful",
        "AsyncStatefulQuoteStream",
    ),
    "load_config": ("tstdx.config", "load_config"),
    "facade": ("tstdx.facade", ""),
    "observability": ("tstdx.observability", ""),
    "streaming": ("tstdx.streaming", ""),
    "deprecated": ("tstdx.deprecation", "deprecated"),
    "DeprecationPolicy": ("tstdx.deprecation", "DeprecationPolicy"),
    "FeedbackReporter": ("tstdx.feedback", "FeedbackReporter"),
    "TelemetryCollector": ("tstdx.feedback", "TelemetryCollector"),
    "UserStats": ("tstdx.feedback", "UserStats"),
    "detect_encoding": ("tstdx.charset.encoding", "detect_encoding"),
    "decode_bytes": ("tstdx.charset.encoding", "decode_bytes"),
}


def __getattr__(name: str) -> Any:
    if name in _LAZY:
        mod_path, attr = _LAZY[name]
        import importlib

        mod = importlib.import_module(mod_path)
        value = mod if not attr else getattr(mod, attr)
        globals()[name] = value
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(_LAZY))
