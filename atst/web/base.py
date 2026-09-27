# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""HTTP Web 源基础设施门面（P10-3 自单文件拆分为四模块，本文件为组合与 re-export 层）。

历史导入路径 ``from atst.web.base import X`` 全部保持可用：

* :mod:`atst.web._base_http` —— HTTP 客户端 / 限流器 / ``num_f``/``num_i``
* :mod:`atst.web._base_retry` —— 重试退避常量 + 失败双桶 Mixin
* :mod:`atst.web._base_core` —— :class:`BaseWebSource`（组合 ``_BaseRetryMixin``）
* :mod:`atst.web._base_em` —— :class:`_EastmoneyJson`（东财主机池 failover / 黑名单）

HTTP 后端
---------
* 默认用标准库 ``urllib.request``，**零依赖**即可工作。
* 若装了 ``httpx``（``pip install atst[web]``）则自动优先使用（连接复用、HTTP/2）。

反爬对策（6 类）
---------------
1. Referer       —— 新浪无 Referer 直接 403
2. User-Agent    —— 伪装浏览器 UA
3. 限流          —— 按源令牌桶，腾讯/东财高频会封 IP
4. 重试退避      —— 指数退避 + 抖动
5. 源切换        —— 单源失败自动切下一源（由 router 编排）
6. 接口下线检测  —— 连续失败达阈值抛 :class:`SourceDeprecated`
"""

from __future__ import annotations

from typing import Any

from ._base_core import BaseWebSource
from ._base_em import (
    _EM_HOST_BLACKLIST,
    _EastmoneyJson,
    _em_blacklist_active,
    _em_blacklist_add,
    _em_blacklist_clear,
    reset_em_blacklist,
)
from ._base_http import (
    HttpClient,
    HttpResponse,
    HttpxClient,
    RateLimiter,
    StdlibPooledClient,
    TokenBucket,
    UrllibClient,
    build_client,
    num_f,
    num_i,
    reset_shared_buckets,
    shared_bucket,
)
from ._base_retry import (
    DEFAULT_ACQUIRE_TIMEOUT,
    MAX_BACKOFF_SECONDS,
    MAX_RETRIES_CAP,
    _backoff_delay,
    _BaseRetryMixin,
    _is_retryable_status,
)

__all__ = [
    "num_f",
    "num_i",
    "HttpResponse",
    "HttpClient",
    "UrllibClient",
    "StdlibPooledClient",
    "HttpxClient",
    "build_client",
    "TokenBucket",
    "RateLimiter",
    "BaseWebSource",
    "normalize_symbol",
    "split_symbol",
    "shared_bucket",
    "reset_shared_buckets",
]


# --------------------------------------------------------------------------- #
# 代码规范化（委托统一引擎 atst.domain.symbol）
# --------------------------------------------------------------------------- #
def split_symbol(symbol: str) -> tuple[str, str]:
    """``sh600519`` / ``600519.SH`` / ``600519SH`` → ``("sh", "600519")``。"""
    from ..domain.symbol import split_symbol as _engine_split

    return _engine_split(symbol)


def normalize_symbol(symbol: str, market: str | None = None) -> str:
    """统一为 ``{market}{code}`` 小写形式（支持全部书写变种）。"""
    from ..domain.symbol import normalize_symbol as _engine_norm

    return _engine_norm(symbol, market=market)


def to_sina_symbol(symbol: str) -> str:
    """新浪格式：``sh600519``。"""
    return normalize_symbol(symbol)


def to_tencent_symbol(symbol: str) -> str:
    """腾讯格式：``sh600519``（港股为 ``hk00700``）。"""
    return normalize_symbol(symbol)


def to_eastmoney_secid(symbol: str) -> str:
    """东财 secid：沪市 ``1.600519``，深市/北交所 ``0.000001``。"""
    m, code = split_symbol(symbol)
    prefix = "1" if m == "sh" else "0"
    return f"{prefix}.{code}"
