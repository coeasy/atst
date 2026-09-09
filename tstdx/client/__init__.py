# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""同步 / 异步 TDX 在线客户端（§4）。

这是「在线」路径的主入口。它站在 :mod:`tstdx.transport.pool` 之上，
把「命令号 + 请求体 + 解析上下文」封装成面向用户的语义化方法
（``bars`` / ``quotes`` / ``security_count`` / ``finance_info`` …），
对上层屏蔽握手、分帧、重试、换主站、限流、三级解析等全部细节。

REFACTOR_PLAN_v8 P5：原单模块 ``tstdx/client.py``（1715 行）拆为本包：

* :mod:`tstdx.client.sync`    —— ``TdxClient`` + 4 个同步子客户端；
* :mod:`tstdx.client.async_`  —— ``AsyncTdxClient`` + 4 个异步镜像；
* :mod:`tstdx.client.factory` —— ``get_client`` 工厂。

**公开 API 不变**：本 ``__init__`` re-export 原模块全部模块级符号，
``from tstdx.client import TdxClient`` 等所有既有导入路径照常工作；
对外部消费者 ``monkeypatch.setattr(tstdx.client, "X", ...)`` 的语义
与拆分前等价（类由各消费方在调用时经 ``from ..client import X``
惰性解析；``dispatch`` 在 sync/async 内经包级符号调用时转发）。

设计要点
--------
* **零硬依赖网络**：客户端不开 socket，全部委托给 ``ConnectionPool``。
* **单位契约**：所有输出都已是「元 / 股 / 元」全局契约。
* **三态输出**：``as_format="dict" | "tuple" | "dataframe"``。
* **异步镜像**：:class:`AsyncTdxClient` 的方法签名与同步版一一对应。

.. note::
   实时行情命令 ``0x0530`` 一次只能查一只（实测 2 只超时不返回），
   因此 ``quotes`` 内部**逐只请求**再汇总；这是服务端约束，不是本库限制。
"""

from __future__ import annotations

from ..client_core import (  # B1：共享核心（纯协议构造，无 I/O）
    _PREFIX_MARKET,
    _bars_body,
    _emit,
    _guard_offline,
    _quote_body,
    _row_to_bar,
    _row_to_capital,
    _row_to_quote,
    period_to_category,
    split_symbol,
)
from ..codec.framing import ResponseFrame
from ..domain.finance import FINANCE_INFO_FIELDS, map_finance_values
from ..domain.models import Bar, CapitalChange, Quote
from ..errors import (
    ParseError,
    TdxError,
    TruncatedDataError,
)
from ..protocol.commands import CMD, Family
from ..protocol.parsers.std7709 import (
    SecurityBarsParser,
    build_realtime_quote_body,
)
from ..protocol.registry import TIER_L3, ParseResult, dispatch
from .async_ import (
    AsyncExMarketClient,
    AsyncF10Client,
    AsyncGoodsClient,
    AsyncMacClient,
    AsyncTdxClient,
)

# Install behavior/provenance patches before factory/public bindings are exposed.
# Order is intentional: concurrent output semantics, base pool identity binding,
# detached bestip snapshots, then fixed-family subclass argument validation.
from . import (
    _async_concurrency_hardening,
    _bestip_hardening,
    _pool_binding_hardening,
    _subclient_family_hardening,
)
from .factory import _CLIENT_REGISTRY, get_client
from .sync import (
    _QUOTES_SNAPSHOT_BATCH,
    MAX_BARS_PER_REQUEST,
    ExMarketClient,
    F10Client,
    GoodsClient,
    MacClient,
    OutputFormat,
    TdxClient,
)

del (
    _async_concurrency_hardening,
    _bestip_hardening,
    _pool_binding_hardening,
    _subclient_family_hardening,
)

__all__ = [
    "TdxClient",
    "AsyncTdxClient",
    "GoodsClient",
    "ExMarketClient",
    "MacClient",
    "F10Client",
    "get_client",
    "OutputFormat",
    "period_to_category",
    "split_symbol",
]
