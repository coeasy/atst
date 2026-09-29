# Copyright (c) 2026 atst contributors
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

from collections.abc import Iterable, Mapping
from typing import Any, Final

from ..errors import ValidationError

__all__ = [
    "QUERY_BODY_FIELDS",
    "QUERY_ROUTING_FIELDS",
    "WS_PARAMS_FIELDS",
    "as_request_bool",
    "as_request_int",
    "reject_reserved_kwargs",
    "reject_undeclared",
    "undeclared_fields",
]

#: ``Client.call`` 自己的关键字形参（第 29 轮）：``provider`` / ``channel`` / ``currentness``
#: 是**路由字段**，不是能力入参。四张 query 面都把它们从各自的顶层位置取出来显式递交，
#: 因此它们绝不能同时出现在 ``kwargs`` 里。
QUERY_ROUTING_FIELDS: Final[frozenset[str]] = frozenset({"provider", "channel", "currentness"})

#: ``POST /v13/query/{capability}`` 的 body 顶层键。
QUERY_BODY_FIELDS: Final[frozenset[str]] = frozenset(
    {"args", "kwargs", "provider", "channel", "currentness"}
)

#: WS JSON-RPC 每个方法的 ``params`` 键。分母是 ``RuntimeJsonRpcHandler.METHODS``，
#: 两个方向都不许有差（同一个测试文件把守）。
WS_PARAMS_FIELDS: Final[dict[str, frozenset[str]]] = {
    "quotes": frozenset({"symbols", "provider", "fallback"}),
    "bars": frozenset(
        {
            "symbol",
            "provider",
            "fallback",
            "period",
            "count",
            "start",
            "adjustment",
            "currentness",
            "strict",
            "start_date",
            "end_date",
        }
    ),
    "snapshot": frozenset({"symbol", "provider"}),
    "minute": frozenset({"symbol", "provider"}),
    "trades": frozenset({"symbol", "provider", "start", "count"}),
    "security.count": frozenset({"market", "provider"}),
    "security.list": frozenset({"market", "start", "provider"}),
    "query": frozenset({"capability", "args", "kwargs", "provider", "channel", "currentness"}),
    "runtime.capabilities": frozenset(),
    "runtime.health": frozenset(),
    # 流式控制面：把进程内实时流桥接到这条连接。
    "subscribe": frozenset({"symbols", "provider", "interval", "diff_only", "max_queue"}),
    "unsubscribe": frozenset({"id"}),
    "list": frozenset(),
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


def reject_reserved_kwargs(
    *,
    face: str,
    where: str,
    kwargs: Mapping[str, Any],
) -> None:
    """``kwargs`` 里混进了路由字段时唯一的拒绝口（第 29 轮）。

    四张 query 面都写成 ``client.call(cap, *args, provider=<顶层值>, channel=<顶层值>,
    currentness=<顶层值>, **kwargs)``。路由字段在**顶层**（HTTP body / WS ``params`` /
    MCP ``arguments`` / CLI 旋钮）才有决策权，``kwargs`` 是能力入参的口袋。同一个键两处都
    出现时，Python 在**调用表达式求值处**就抛裸 ``TypeError: got multiple values for
    keyword argument``——发生在进入 :meth:`Client.call` 之前，因此那层把入参不合签名
    翻译成 ``ValidationError`` 的包装根本接不到，四张面一致把它落到 E9000 / HTTP 500，
    而契约要求的是 E1010 / 422。

    这里就地判死，并且把话说清楚：路由字段该写在顶层，不是塞进 ``kwargs``。
    """
    conflicting = sorted(set(kwargs) & QUERY_ROUTING_FIELDS)
    if not conflicting:
        return
    raise ValidationError(
        f"{where} 的 kwargs 里出现了路由字段 {', '.join(conflicting)}："
        "它们只有顶层声明才有决策权，塞进 kwargs 会被当成重复关键字而落到 E9000；"
        "请改用顶层字段",
        context={
            "phase": "wire_validation",
            "face": face,
            "reserved_fields": conflicting,
        },
    )


def as_request_int(
    *,
    face: str,
    where: str,
    name: str,
    value: Any,
    default: int,
    lo: int | None = None,
    hi: int | None = None,
) -> int:
    """整数请求字段的唯一规整/拒绝口（第 25 轮 G34）。

    过去每个面各自处理 ``count``/``start`` 这类整数：HTTP 靠 FastAPI 的 ``Query`` 规整，
    WS 直接 ``int(params.get(...))``（一个非数字串就抛裸 ``ValueError``，被 :meth:`
    RuntimeJsonRpcHandler.handle_message` 的兜底 ``except Exception`` 误判成 **E9000 内部
    错误**——客户端打错一个字，服务端却怪自己、还把细节清空），MCP 过去那个已删除的整数钳位
    则把不合要求的值**静默换成**另一个数（``count="abc"`` 偷偷变 320、``count=0`` 偷偷变 1、
    ``count=99999999`` 偷偷夹到上限），于是工具自己 ``inputSchema`` 声明的 ``minimum``/
    ``maximum`` 成了一张没人按它行事的假告示（F-47 那一族）。本函数把这三样并成一条口径：

    * 缺席（``None``）→ 用 ``default``（这是对外声明的缺省，不是"把坏值改成 default"）；
    * :class:`int` → 原样；纯数字串 → 按整数解析（与 HTTP 查询串天然承载字符串一致）；
    * 其它任何形状（:class:`bool` / :class:`float` / 列表 / 非数字串 / ``None`` 以外的对象）
      → ``ValidationError``（E1010 / JSON-RPC -32602），**不当作内部错误、也不替换**；
    * 给了 ``lo``/``hi`` 而解析值越界 → 同样 ``ValidationError``，让声明的边界真的生效。

    调用方拿到的永远是已经过闸的 :class:`int`，所以再往 :meth:`Client.bars` 之类传下去时，
    内核的 :func:`atst.query` 那侧不会看到字符串。
    """
    if value is None:
        parsed = default
    elif isinstance(value, bool):
        raise _bad_int(face, where, name, value, lo, hi)
    elif isinstance(value, int):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = int(value.strip())
        except ValueError:
            raise _bad_int(face, where, name, value, lo, hi) from None
    else:
        raise _bad_int(face, where, name, value, lo, hi)
    if (lo is not None and parsed < lo) or (hi is not None and parsed > hi):
        raise _bad_int(face, where, name, value, lo, hi)
    return parsed


def as_request_bool(
    *,
    face: str,
    where: str,
    name: str,
    value: Any,
    default: bool = False,
) -> bool:
    """布尔请求字段唯一的规整/拒绝口（与 :func:`as_request_int` 同一条口径）。

    布尔格在 wire 上最容易出的错是"任何值都算真"：``strict=0``、``strict="false"``、
    ``strict="no"`` 被 Python 的真值规则一律读成 ``True``，调用方以为自己关掉了瑕疵严格
    模式，实际拿到的是最严的那一档。所以这里**只接受** :class:`bool` 与两族字面串
    （``true/false``、``1/0``），其它形状一律 :class:`ValidationError`——同样是当场拒绝，
    不静默替换。
    """
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        token = value.strip().lower()
        if token in {"true", "1", "yes", "on"}:
            return True
        if token in {"false", "0", "no", "off"}:
            return False
    raise ValidationError(
        f"{where} 的 {name}={value!r} 不是一个布尔：当场拒绝，"
        "而不是按 Python 真值规则静默当成 True",
        context={
            "phase": "wire_validation",
            "face": face,
            "field": name,
            "received": repr(value),
        },
    )


def _bad_int(
    face: str, where: str, name: str, value: Any, lo: int | None, hi: int | None
) -> ValidationError:
    bound = f"，允许区间 [{lo if lo is not None else '-∞'}, {hi if hi is not None else '+∞'}]"
    return ValidationError(
        f"{where} 的 {name}={value!r} 不是一个区间内的整数：当场拒绝，"
        f"而不是替换成另一个数或当成内部错误{bound}",
        context={
            "phase": "wire_validation",
            "face": face,
            "field": name,
            "received": repr(value),
            "minimum": lo,
            "maximum": hi,
        },
    )


def _context(face: str, unknown: list[str], declared: Iterable[str]) -> dict[str, Any]:
    return {
        "phase": "wire_validation",
        "face": face,
        "unknown_fields": unknown,
        "declared_fields": sorted(declared),
    }
