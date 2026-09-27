# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""交易模块错误（P2-1）。

错误码归入统一的 ``E4xxx 数据与领域`` 段（交易属于领域层），继承
:class:`~atst.errors.TdxError` 以接入 :func:`atst.errors.advice_for`
与 :func:`atst.errors.http_status_for` 的统一出口。
"""

from __future__ import annotations

from ..errors import TdxError

__all__ = [
    "TradeError",
    "TradeNotLoggedIn",
    "TradeRejected",
    "TradingUnavailable",
]


class TradeError(TdxError):
    """交易域错误基类。"""

    code = "E4000"
    http_status = 400


class TradeNotLoggedIn(TradeError):
    """未登录即发起需要会话的操作。"""

    code = "E4010"
    http_status = 401


class TradeRejected(TradeError):
    """服务端拒绝：凭证错误 / 资金或持仓不足 / 委托不可撤等。"""

    code = "E4020"
    http_status = 403


class TradingUnavailable(TradeError):
    """红线：实盘交易不可用。atst 不连接真实券商、不下真实委托。"""

    code = "E4030"
    http_status = 501
