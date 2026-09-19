# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""三张服务面的「已声明请求字段」唯一真源，与唯一的拒绝口。

F-43/F-46 把 ``max_age``/``allow_partial`` 从构造面清掉之后，同一个调用在四个入口上的下场并不
一致（F-47，Phase 5 第 23 步实测）：``Client.bars(..., max_age=0)`` 与
``QuerySpec.build(..., max_age=0)`` 都 ``TypeError``，而 ``GET /v13/bars/600519?max_age=0``
返回 **200 + 正常结果**——FastAPI 只绑定声明过的形参，多余的查询串参数无人过问；
``POST /v13/query/{capability}`` 只 ``payload.get(...)``，body 里其他键直接蒸发；WS
``_dispatch`` 全部经 ``params.get(...)``；MCP 的 9 张 ``inputSchema`` 当时一张都没声明
``additionalProperties: false``。于是刚被删掉的旋钮在 wire 面上重新变成"看起来生效"。

用户裁决是三面 fail-closed（2026-09-19），所以这里只有一条判据：**wire 面上每个请求字段要么
被读走，要么当场被拒**，不存在第三种"收下但无人读"。三面的白名单来源各不相同，但都是
**声明本身**，不是第二份抄件：

* HTTP 查询串：FastAPI 的路由签名（``route.dependant.query_params``，运行时现取）；
* HTTP body 与 WS ``params``：本模块的 :data:`QUERY_BODY_FIELDS` / :data:`WS_PARAMS_FIELDS`，
  由 ``tests/runtime/test_wire_declared_fields.py`` 对分派代码的 AST 读取点求差把守；
* MCP ``arguments``：该工具的 ``inputSchema.properties``——同一份 schema 既对外声明也用于拒绝。
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any, Final

from ..errors import ValidationError

__all__ = ["QUERY_BODY_FIELDS", "WS_PARAMS_FIELDS", "reject_undeclared", "undeclared_fields"]

#: ``POST /v13/query/{capability}`` 的 body 顶层键。
QUERY_BODY_FIELDS: Final[frozenset[str]] = frozenset(
    {"args", "kwargs", "provider", "channel", "currentness"}
)

#: WS JSON-RPC 每个方法的 ``params`` 键。分母是 ``RuntimeJsonRpcHandler.METHODS``，
#: 两个方向都不许有差（同一个测试文件把守）。
WS_PARAMS_FIELDS: Final[dict[str, frozenset[str]]] = {
    "quotes": frozenset({"symbols", "provider", "fallback"}),
    "bars": frozenset({"symbol", "provider", "fallback", "period", "count", "start", "adjustment"}),
    "snapshot": frozenset({"symbol", "provider"}),
    "minute": frozenset({"symbol", "provider"}),
    "trades": frozenset({"symbol", "provider", "start", "count"}),
    "security.count": frozenset({"market", "provider"}),
    "security.list": frozenset({"market", "start", "provider"}),
    "query": frozenset({"capability", "args", "kwargs", "provider", "channel", "currentness"}),
    "runtime.capabilities": frozenset(),
    "runtime.health": frozenset(),
}


def undeclared_fields(declared: Iterable[str], received: Iterable[str]) -> list[str]:
    """``received`` 里那些没人声明过的键，按字典序（同一份输入永远同一个报错）。"""
    return sorted(set(received) - set(declared))


def reject_undeclared(
    *,
    face: str,
    where: str,
    declared: Iterable[str],
    received: Iterable[str],
) -> None:
    """未声明字段唯一的拒绝口：报 `ValidationError`（HTTP 422 / JSON-RPC -32602）。

    文案自己把未知键念出来（人读的那一句），机读侧同一条信息在 ``context`` 的两个键里。
    """
    unknown = undeclared_fields(declared, received)
    if not unknown:
        return
    raise ValidationError(
        f"{where} 收到了未声明的请求字段 {', '.join(unknown)}："
        "它们不会改变任何行为，所以当场拒绝而不是静默收下",
        context=_context(face, unknown, declared),
    )


def _context(face: str, unknown: list[str], declared: Iterable[str]) -> dict[str, Any]:
    return {
        "phase": "wire_validation",
        "face": face,
        "unknown_fields": unknown,
        "declared_fields": sorted(declared),
    }
