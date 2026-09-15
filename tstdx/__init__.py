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
* **Fail-closed Streaming**：canonical stream 采用显式 ``StreamState``，
  worker 半死、启动失败、stop 超时与终态重启都不能静默生成第二 worker。
* **语义缓存**：canonical cache 以完整 ``QueryFingerprint`` 隔离 Provider /
  Channel / Capability，并保持原始 provenance，不把缓存命中伪装成真实直连。
* **原创实现**：洁净室流程，协议事实源于自有抓包与本地文件分析。

分层（自底向上）::

    codec       报文帧 / 变长数值 / 字符集
    protocol    命令登记 + 三级解析（L1/L2/L3）
    transport   TCP 连接 / 连接池 Slot / 心跳 / 限流 / 主站测速
    client      同步 + 异步客户端（Standard / Extended / MAC / Goods）
    reader      本地 vipdoc 二进制（.day/.lc1/.lc5/.dat/gpcw）
    domain      数据模型 / 复权 / 日历 / 时区
    providers   Provider / Channel / Capability 单一事实源
    query       QuerySpec / QueryPlan / QueryFingerprint
    result      QueryResult / Provenance
    cache       legacy compatibility caches + v11 semantic result cache
    streaming   流式订阅 + 显式生命周期状态机
    web         HTTP Web 行情源（新浪/腾讯/东财/集思录/港股/中行）
    sinks       DataFrame / Parquet / DuckDB
    sources     兼容 DataSourceRouter（后续收敛为显式策略层）
    facade      TDX 二进制协议与行情高层门面（原生命名）
    observability  Prometheus 风格指标 / 埋点（零硬依赖）

Quick start（离线，读取本地通达信数据）::

    from tstdx.reader import DayBarReader
    bars = DayBarReader().read(r"D:/tdx/vipdoc/sh/lday/sh600519.day")

Quick start（在线，TDX 协议）::

    from tstdx import Client
    with Client(provider="tdx") as c:
        bars = c.bars("sh600519", period="day", count=30)

Quick start（HTTP Web 源，无需 TDX 主站）::

    from tstdx.web import get_quotes
    quotes = get_quotes(["sh600519", "sz000001"], source="tencent")
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

__version__ = "1.0.0"

__all__ = [
    "__version__",
    # v13 canonical business client surface（唯一推荐入口）。
    "Client",
    "AsyncClient",
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
    "PersistentSemanticCache",
    "StreamState",
    "StatefulQuoteStream",
    "AsyncStatefulQuoteStream",
    "BatchSpec",
    "BatchItem",
    "BatchResult",
    "SingleFlight",
    "NegativeCache",
    "StreamSpec",
    "UnifiedRuntime",
    "FallbackPolicy",
    "ProviderOrchestrator",
    "ErrorEnvelope",
    "to_error_envelope",
    "records_from_response",
    "FinancialRecord",
    "FundRecord",
    "BondRecord",
    "NewsRecord",
    "ResearchRecord",
    "OptionRecord",
    "MarketDataRecord",
    "SearchRecord",
    "MacroRecord",
    "configure",
    "get_config",
    "load_config",
    "observability",
    "streaming",
]

from . import codec, errors, protocol  # noqa: E402,F401
from .errors import TdxError  # noqa: E402,F401


def configure(**kwargs: Any) -> Any:
    """以关键字参数覆盖全局配置（等价于 :func:`load_config` 的高优先级源）。"""
    from .config import load_config

    return load_config(overrides=kwargs)


def get_config() -> Any:
    """获取当前生效的全局配置对象。"""
    from .config import get_config as _get

    return _get()


if TYPE_CHECKING:  # pragma: no cover
    from .cache_semantic import SemanticResultCache
    from .config import load_config
    from .orchestration import FallbackPolicy, ProviderOrchestrator
    from .providers import PROVIDERS, ProviderRegistry
    from .query import CurrentnessMode, QueryFingerprint, QueryPlan, QueryPlanner, QuerySpec
    from .reader import BlockReader, DataProfile, DayBarReader, FinanceReader, MinBarReader
    from .result import Provenance, ProvenanceKind, QueryResult, ResultMeta
    from .runtime_v13 import UnifiedRuntime
    from .stream_contract import StreamSpec
    from .streaming.state import StreamState
    from .streaming.stateful import AsyncStatefulQuoteStream, StatefulQuoteStream


_LAZY: dict[str, tuple[str, str]] = {
    "Client": ("tstdx.client_api", "Client"),
    "AsyncClient": ("tstdx.client_api", "AsyncClient"),
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
    "PersistentSemanticCache": ("tstdx.cache_persistent", "PersistentSemanticCache"),
    "StreamState": ("tstdx.streaming.state", "StreamState"),
    "StatefulQuoteStream": ("tstdx.streaming.stateful", "StatefulQuoteStream"),
    "AsyncStatefulQuoteStream": (
        "tstdx.streaming.stateful",
        "AsyncStatefulQuoteStream",
    ),
    "BatchSpec": ("tstdx.batch", "BatchSpec"),
    "BatchItem": ("tstdx.batch", "BatchItem"),
    "BatchResult": ("tstdx.batch", "BatchResult"),
    "SingleFlight": ("tstdx.batch", "SingleFlight"),
    "NegativeCache": ("tstdx.batch", "NegativeCache"),
    "StreamSpec": ("tstdx.stream_contract", "StreamSpec"),
    "UnifiedRuntime": ("tstdx.runtime_v13", "UnifiedRuntime"),
    "FallbackPolicy": ("tstdx.orchestration", "FallbackPolicy"),
    "ProviderOrchestrator": ("tstdx.orchestration", "ProviderOrchestrator"),
    "ErrorEnvelope": ("tstdx.error_envelope", "ErrorEnvelope"),
    "to_error_envelope": ("tstdx.error_envelope", "to_error_envelope"),
    "records_from_response": ("tstdx.typed_query", "records_from_response"),
    "FinancialRecord": ("tstdx.domain.records", "FinancialRecord"),
    "FundRecord": ("tstdx.domain.records", "FundRecord"),
    "BondRecord": ("tstdx.domain.records", "BondRecord"),
    "NewsRecord": ("tstdx.domain.records", "NewsRecord"),
    "ResearchRecord": ("tstdx.domain.records", "ResearchRecord"),
    "OptionRecord": ("tstdx.domain.records", "OptionRecord"),
    "MarketDataRecord": ("tstdx.domain.records", "MarketDataRecord"),
    "SearchRecord": ("tstdx.domain.records", "SearchRecord"),
    "MacroRecord": ("tstdx.domain.records", "MacroRecord"),
    "load_config": ("tstdx.config", "load_config"),
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
