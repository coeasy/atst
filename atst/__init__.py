# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""atst —— 通达信（TDX）行情数据通用协议库。

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
* **零缓存直达数据源**：每次公开查询都编译为唯一 ``QueryPlan`` 并直接请求绑定的
  Provider；不存在结果级缓存、结果级负缓存或请求合并层（web 传输层对失败主机有
  进程级 TTL 排序，只改变尝试顺序、不省掉任何一次数据请求），provenance 始终反映
  真实直连。
* **原创实现**：洁净室流程，协议事实源于自有抓包与本地文件分析。

分层（自底向上）::

    codec          报文帧 / 变长数值
    charset        字符集自动探测（GBK/GB18030/Big5/UTF-8）
    protocol       命令登记 + 三级解析（L1/L2/L3）
    config         6 源合并 + 严格校验（配置面即执行面契约）
    transport      TCP 连接 / 连接池 Slot / 心跳 / 限流 / 主站测速
    client         同步 + 异步客户端（Standard / Extended / MAC / Goods）
    reader         本地 vipdoc 二进制（.day/.lc1/.lc5/.dat/gpcw）
    profile        数据规格探测（帧 / 文件双探测器 + presets）
    sink           LocalDaySink：写回 vipdoc .day 二进制
    domain         数据模型 / 复权 / 日历 / 时区
    providers      Provider / Channel / Capability 单一事实源
    catalog        能力目录 + 规划期签名校验 + channel→adapter 绑定（无执行）
    query          QuerySpec / QueryPlan / QueryFingerprint
    result         QueryResult / Provenance
    runtime        零缓存 provider-first 执行内核（UnifiedRuntime，唯一内核）
    streaming      流式订阅 + 显式生命周期状态机
    web            HTTP Web 行情源（新浪/腾讯/东财/集思录/港股/中行）
    output         DataFrame / Parquet / DuckDB 输出层
    observability  Prometheus 风格指标 / 埋点（零硬依赖）
    feedback       错误 / 用量上报 + 使用统计
    tools          协议账本审计 / 代码生成 / 原创性检查
    trade          交易协议模拟器（实验性：纯内存模拟，不接入内核）
    cli            命令行面（31 子命令，只翻译不执行）
    integration    HTTP / WS / MCP 服务面（只翻译不执行）

Quick start（离线，读取本地通达信数据）::

    from atst.reader import DayBarReader
    bars = DayBarReader().read(r"D:/tdx/vipdoc/sh/lday/sh600519.day")

Quick start（在线，TDX 协议）::

    from atst import Client
    with Client(default_provider="tdx") as c:
        bars = c.bars("sh600519", period="day", count=30)

Quick start（HTTP Web 源，无需 TDX 主站）::

    from atst.web import get_quotes
    quotes = get_quotes(["sh600519", "sz000001"], source="tencent")
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ._version import __version__

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
    "StreamState",
    "StatefulQuoteStream",
    "AsyncStatefulQuoteStream",
    "BatchItem",
    "BatchResult",
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
    """以关键字参数覆盖进程级配置，并立即写回 :func:`get_config` 单例。

    键是配置段名，值是字段字典，例如 ``configure(core={"timeout": 8})``。
    返回合并后的 :class:`~atst.config.schema.Config`；后续 ``Client()``
    与 ``WebQuoteClient()`` 都读取到该值，显式构造参数仍然优先。
    """
    from .config import load_config

    return load_config(overrides=kwargs, set_global=True)


def get_config() -> Any:
    """获取当前生效的全局配置对象。"""
    from .config import get_config as _get

    return _get()


if TYPE_CHECKING:  # pragma: no cover
    from .config import load_config
    from .providers import PROVIDERS, ProviderRegistry
    from .query import CurrentnessMode, QueryFingerprint, QueryPlan, QueryPlanner, QuerySpec
    from .reader import BlockReader, DataProfile, DayBarReader, FinanceReader, MinBarReader
    from .result import Provenance, ProvenanceKind, QueryResult, ResultMeta
    from .runtime.kernel import UnifiedRuntime
    from .runtime.orchestration import FallbackPolicy, ProviderOrchestrator
    from .stream_contract import StreamSpec
    from .streaming.state import StreamState
    from .streaming.stateful import AsyncStatefulQuoteStream, StatefulQuoteStream


_LAZY: dict[str, tuple[str, str]] = {
    "Client": ("atst.client.api", "Client"),
    "AsyncClient": ("atst.client.api", "AsyncClient"),
    "DayBarReader": ("atst.reader", "DayBarReader"),
    "MinBarReader": ("atst.reader", "MinBarReader"),
    "BlockReader": ("atst.reader", "BlockReader"),
    "FinanceReader": ("atst.reader", "FinanceReader"),
    "DataProfile": ("atst.reader", "DataProfile"),
    "CurrentnessMode": ("atst.query", "CurrentnessMode"),
    "QuerySpec": ("atst.query", "QuerySpec"),
    "QueryFingerprint": ("atst.query", "QueryFingerprint"),
    "QueryPlan": ("atst.query", "QueryPlan"),
    "QueryPlanner": ("atst.query", "QueryPlanner"),
    "ProvenanceKind": ("atst.result", "ProvenanceKind"),
    "Provenance": ("atst.result", "Provenance"),
    "ResultMeta": ("atst.result", "ResultMeta"),
    "QueryResult": ("atst.result", "QueryResult"),
    "ProviderRegistry": ("atst.providers", "ProviderRegistry"),
    "PROVIDERS": ("atst.providers", "PROVIDERS"),
    "StreamState": ("atst.streaming.state", "StreamState"),
    "StatefulQuoteStream": ("atst.streaming.stateful", "StatefulQuoteStream"),
    "AsyncStatefulQuoteStream": (
        "atst.streaming.stateful",
        "AsyncStatefulQuoteStream",
    ),
    "BatchItem": ("atst.batch", "BatchItem"),
    "BatchResult": ("atst.batch", "BatchResult"),
    "StreamSpec": ("atst.stream_contract", "StreamSpec"),
    "UnifiedRuntime": ("atst.runtime.kernel", "UnifiedRuntime"),
    "FallbackPolicy": ("atst.runtime.orchestration", "FallbackPolicy"),
    "ProviderOrchestrator": ("atst.runtime.orchestration", "ProviderOrchestrator"),
    "ErrorEnvelope": ("atst.error_envelope", "ErrorEnvelope"),
    "to_error_envelope": ("atst.error_envelope", "to_error_envelope"),
    "records_from_response": ("atst.typed_query", "records_from_response"),
    "FinancialRecord": ("atst.domain.records", "FinancialRecord"),
    "FundRecord": ("atst.domain.records", "FundRecord"),
    "BondRecord": ("atst.domain.records", "BondRecord"),
    "NewsRecord": ("atst.domain.records", "NewsRecord"),
    "ResearchRecord": ("atst.domain.records", "ResearchRecord"),
    "OptionRecord": ("atst.domain.records", "OptionRecord"),
    "MarketDataRecord": ("atst.domain.records", "MarketDataRecord"),
    "SearchRecord": ("atst.domain.records", "SearchRecord"),
    "MacroRecord": ("atst.domain.records", "MacroRecord"),
    "load_config": ("atst.config", "load_config"),
    "observability": ("atst.observability", ""),
    "streaming": ("atst.streaming", ""),
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
