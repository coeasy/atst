# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""高层门面（原生命名）：在 tstdx 自有协议栈之上提供便捷客户端。

当前提供：

* :mod:`tstdx.facade.runtime` —— Provider-first strict runtime（新 canonical 入口）
* :mod:`tstdx.facade.compat_runtime` —— 旧 UnifiedQuoteAPI 表面 + runtime-backed quotes/bars
* :mod:`tstdx.facade.api`    —— 历史实现，保留其余辅助能力
* :mod:`tstdx.facade.binary` —— TDX 二进制协议门面（BinaryClient）
* :mod:`tstdx.facade.market` —— 标准 / 扩展 / 期权市场门面
* :mod:`tstdx.facade.bridge` —— 外部补充数据源桥接门面

新代码应优先使用 ``runtime_api()``。公共 ``UnifiedQuoteAPI`` 的 canonical
``quotes``/``bars`` 已委托 Provider-first runtime；``route='auto'`` 只负责
显式 orchestration，不再定义底层 Provider 执行语义。
"""

from .async_api import AsyncUnifiedQuoteAPI
from .binary import TDX_CATEGORY_TO_PERIOD, BinaryClient, binary_client
from .bridge import FREQUENCY_ALIASES, BridgeClient, bridge_client
from .compat_runtime import UnifiedQuoteAPI, quote_api
from .market import (
    FREQUENCY_PERIOD_MAP,
    ExHqClient,
    HqClient,
    OptionClient,
    market_client,
)
from .response import ApiResponse, err, ok, wrap
from .runtime import UnifiedRuntime, runtime_api

__all__ = [
    "UnifiedRuntime",
    "runtime_api",
    "UnifiedQuoteAPI",
    "AsyncUnifiedQuoteAPI",
    "quote_api",
    "BinaryClient",
    "binary_client",
    "TDX_CATEGORY_TO_PERIOD",
    "BridgeClient",
    "bridge_client",
    "FREQUENCY_ALIASES",
    "HqClient",
    "ExHqClient",
    "OptionClient",
    "market_client",
    "FREQUENCY_PERIOD_MAP",
    "ApiResponse",
    "ok",
    "err",
    "wrap",
]
