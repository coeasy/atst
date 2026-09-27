# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""解码行的值域尺子：拿库自己声明的取值域，反过来量库自己解出来的值。

这条尺子的方向与 :func:`atst.client.core._standard_market_id` 相反但同源：那张
尺子在**入口**拒绝声明域外的旋钮值（G6/第 13 轮），本模块在**出口**认出解出来的值
落在自己声明的域外。两边共用同一份域，所以"市场只有三个编号"这句话只存在一处。

为什么要长在出口上：``0x000F``/``0x0010`` 的记录布局由公开资料推断而来，尚未被真机
golden 锁定（G3）。它们解出的行在今天看起来是"成功"的——页内字节数对得上，所以解码层
自己不会记任何告警；只有把值放回域里量，才知道 ``market`` 读到的是 ASCII 数字的字节值
（48 就是 ``'0'``）、``code`` 是错位剩下的半个代码。这个判断此前只有读源码 docstring
的人才知道，wire 上的形状与一条干净结果一字不差（G7）。
"""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import date
from typing import Any

from ..errors import SymbolError
from .symbol import Market, Symbol

__all__ = [
    "FIELD_CHECKERS",
    "illegal_code",
    "illegal_market",
    "row_violations",
    "tdx_market_ids",
]


def tdx_market_ids() -> frozenset[int]:
    """库自己承认的 TDX 二进制市场编号——由 symbol 引擎现推，不是抄来的 ``(0, 1, 2)``。"""
    out: set[int] = set()
    for token in Market.ALL:
        try:
            out.add(Symbol(market=token, code="000000").tdx_market)
        except SymbolError:
            continue
    return frozenset(out)


#: 本模块唯一的市场域。空集意味着引擎不再认识任何市场，那时越域判据会全体红——
#: 宁可当场响，也不要静默变成一条永不触发的判据。
_TDX_MARKET_IDS = tdx_market_ids()

_DATE_PREFIX = re.compile(r"^\d{4}-\d{2}-\d{2}")

#: 每一条 ``code`` 都必须是一串可见的 ASCII 代码：非空、无控制字节、无空白、无分隔符。
#: 这里刻意不用 symbol 引擎判"是不是 A 股代码"，也不用"6 位"判——两个都会误伤：
#: 7727 扩展市场族交出的港股代码是 5 位（``00700``）、商品代码是字母开头 6 位
#: （``rb2010``），把它们判成越域等于把噪声混进信号。而记录错位后剩下的形状
#: （``519\x01`` 带着控制字节、空串）在这里一条也躲不过。
_VISIBLE_CODE = re.compile(r"^[\x21-\x7e]+$")


def illegal_market(value: Any) -> str | None:
    """``market`` 必须是 TDX 二进制市场编号或 canonical token，否则给出理由。"""
    if value in _TDX_MARKET_IDS or value in Market.ALL:
        return None
    return (
        f"市场 {value!r} 既不是 TDX 二进制市场编号 {sorted(_TDX_MARKET_IDS)}，"
        "也不是 canonical token"
    )


def illegal_code(value: Any) -> str | None:
    """``code`` 必须是非空的可见 ASCII 代码，否则给出理由。"""
    if not isinstance(value, str):
        return f"代码 {value!r} 不是字符串"
    if not _VISIBLE_CODE.match(value):
        return f"代码 {value!r} 不是非空的可见 ASCII 代码（错位/截断的记录才会落在这里）"
    return None


#: 按字段名挂尺子：行的键叫什么就用哪把，不按命令挑。域属于字段，不属于命令号。
FIELD_CHECKERS: dict[str, Callable[[Any], str | None]] = {
    "market": illegal_market,
    "code": illegal_code,
}


def row_violations(value: Any, path: str = "row") -> list[str]:
    """递归走进行形状，返回「哪里、哪个字段、什么值、为什么不合法」。"""
    out: list[str] = []
    if isinstance(value, dict):
        for key, sub in value.items():
            checker = FIELD_CHECKERS.get(key)
            if checker is not None and (problem := checker(sub)) is not None:
                out.append(f"{path}.{key}: {problem}")
            out.extend(row_violations(sub, f"{path}.{key}"))
    elif isinstance(value, (list, tuple)):
        for index, sub in enumerate(value):
            out.extend(row_violations(sub, f"{path}[{index}]"))
    elif isinstance(value, str):
        head = _DATE_PREFIX.match(value)
        if head:
            try:
                date.fromisoformat(head.group(0))
            except ValueError:
                out.append(f"{path}: 值 {value!r} 写成日期形状却不是真日历日")
    return out
