# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""高层门面（原生命名）：在 tstdx 自有协议栈之上提供便捷客户端。

.. deprecated::
    v15 起，``facade`` 统一门面进入「兼容层」状态。新代码应优先使用
    :class:`tstdx.Client` / :class:`tstdx.RuntimeGateway`（v14 DAG 编排 + v13
    执行引擎）。:class:`UnifiedQuoteAPI` 的构造已发出 ``DeprecationWarning``，
    并将在 v1.6.0 移除。历史 ``manager.tdx`` 接口可通过
    :class:`LegacyServiceAdapter` 继续访问。

当前提供：

* :mod:`tstdx.facade.planned` —— **官方**统一行情接口（:class:`UnifiedQuoteAPI`，
  继承 :class:`tstdx.facade.strict.UnifiedQuoteAPI`，走 QueryPlan/SingleFlight，
  Provider-bound 无跨 Provider 兜底）
* :mod:`tstdx.facade.api`    —— 历史三通路实现（:class:`UnifiedQuoteAPI`，
  仅以 ``LegacyUnifiedQuoteAPI`` 别名导出）
* :mod:`tstdx.facade.strict_async` —— 官方异步门面
  （:class:`AsyncUnifiedQuoteAPI`，``asyncio.to_thread`` 桥接 planned 门面）
* :mod:`tstdx.facade.binary`  —— TDX 二进制协议门面（:class:`BinaryClient`）
* :mod:`tstdx.facade.market`  —— 标准 / 扩展 / 期权市场门面
  （:class:`HqClient` / :class:`ExHqClient` / :class:`OptionClient`）
* :mod:`tstdx.facade.bridge`  —— 外部补充数据源桥接门面（:class:`BridgeClient`）
* :mod:`tstdx.facade.legacy_service_adapter` —— 历史 ``manager.tdx`` 兼容适配

所有门面均为 tstdx **自有实现**，方法名与字段名统一为 tstdx 原生命名，
不沿用任何第三方客户端的 API 约定，亦不复制任何第三方源码。
"""

from __future__ import annotations

from ..planned_service import UnifiedMarketDataService, market_data
from .api import UnifiedQuoteAPI as LegacyUnifiedQuoteAPI
from .binary import TDX_CATEGORY_TO_PERIOD, BinaryClient, binary_client
from .bridge import FREQUENCY_ALIASES, BridgeClient, bridge_client
from .legacy_service_adapter import LegacyServiceAdapter
from .market import (
    FREQUENCY_PERIOD_MAP,
    ExHqClient,
    HqClient,
    OptionClient,
    market_client,
)
from .planned import UnifiedQuoteAPI, quote_api
from .response import ApiResponse, err, ok, wrap
from .runtime_adapter import RuntimeFacadeAdapter
from .strict_async import AsyncUnifiedQuoteAPI

__all__ = [
    "UnifiedQuoteAPI",
    "LegacyUnifiedQuoteAPI",
    "AsyncUnifiedQuoteAPI",
    "UnifiedMarketDataService",
    "market_data",
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
    "RuntimeFacadeAdapter",
    "LegacyServiceAdapter",
]
