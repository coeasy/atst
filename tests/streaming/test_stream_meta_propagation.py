# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""流式 payload 必须把结果 ``meta`` 带到推送面上（观测时刻 + 瑕疵）。

第 47 轮审计确认的断链：``RuntimeJsonRpcHandler._on_quote`` 会读 ``data["warnings"]``
与 ``data["observed_at_ns"]``，但流式层从未往 payload 里写过这两个键——
``_drain_warnings()`` 读的 ``contextvars`` sink 在轮询线程里本来就是空的。结果是
「推送帧带 meta.warnings」这句声明在真实连接上永远为假：请求-响应面有瑕疵信号，
流式面没有，同一份数据换个入口就少一条信息。

本模块钉三件事：

1. ``_round_meta`` 只收有值的字段（没有 provenance / 没有告警时返回空字典）；
2. ``_dispatch_round`` 的 overlay 不写回 ``qmap`` 原始行（多订阅共享一行），也不覆盖
   行情字段；
3. WS 推送帧把元信息放在 ``meta`` 里、``data`` 里不再重复。
"""

from __future__ import annotations

from typing import Any

from atst.diagnostics import ResultWarning, WarningCode
from atst.query import QueryPlanner, QuerySpec
from atst.result import Provenance, QueryResult
from atst.server.runtime_ws import RuntimeJsonRpcHandler
from atst.streaming.base import (
    Subscription,
    _BoundedSymbolSet,
    _dispatch_round,
    _round_meta,
)


def _result(
    *,
    warnings: tuple[ResultWarning, ...] = (),
    observed_at_ns: int = 1_700_000_000_000_000_000,
):
    plan = QueryPlanner().compile(
        QuerySpec.build("quotes", symbols=["sh600519"], provider="tdx", currentness="live")
    )
    return QueryResult.from_plan(
        [{"code": "600519", "price": 10.0}],
        plan=plan,
        provenance=Provenance.direct(plan, observed_at_ns=observed_at_ns),
        warnings=warnings,
    )


def test_round_meta_is_empty_when_there_is_nothing_to_say() -> None:
    # 裸列表（部分调用方直接回 list）既没有 provenance 也没有 meta：不能凭空造字段。
    assert _round_meta([{"code": "600519"}]) == {}
    assert _round_meta(_result()) == {"observed_at_ns": 1_700_000_000_000_000_000}


def test_round_meta_renders_warning_codes_as_plain_strings() -> None:
    warning = ResultWarning(code=WarningCode.QUOTES_PARTIAL_FAILURE, message="只取回 1/2")
    meta = _round_meta(_result(warnings=(warning,)))
    assert meta["warnings"] == [{"code": "quotes_partial_failure", "message": "只取回 1/2"}]


def test_dispatch_overlay_does_not_mutate_the_shared_row() -> None:
    row: dict[str, Any] = {"code": "600519", "price": 10.0}
    qmap = {"600519": row}
    sub = Subscription(symbols=["sh600519"], interval=1.0, diff_only=False, max_queue=8)
    received: list[dict[str, Any]] = []
    sub.on_quote = lambda symbol, payload: received.append(payload)

    _dispatch_round(
        [sub],
        qmap,
        _BoundedSymbolSet(),
        {"warnings": [{"code": "x", "message": "y"}]},
    )

    assert received and received[0]["warnings"] == [{"code": "x", "message": "y"}]
    assert received[0]["price"] == 10.0
    # 原始行保持干净：同一行会被其它订阅复用，写回就是跨订阅副作用。
    assert row == {"code": "600519", "price": 10.0}


def test_dispatch_overlay_never_overrides_provider_fields() -> None:
    qmap = {"600519": {"code": "600519", "price": 10.0}}
    sub = Subscription(symbols=["sh600519"], interval=1.0, diff_only=False, max_queue=8)
    received: list[dict[str, Any]] = []
    sub.on_quote = lambda symbol, payload: received.append(payload)

    _dispatch_round([sub], qmap, _BoundedSymbolSet(), {"price": 999.0, "warnings": []})

    assert received[0]["price"] == 10.0


def test_ws_push_frame_puts_meta_beside_data(monkeypatch: Any) -> None:
    handler = RuntimeJsonRpcHandler(client=object())  # type: ignore[arg-type]
    frames: list[dict[str, Any]] = []
    handler._loop = object()  # type: ignore[assignment]
    handler._send = object()  # type: ignore[assignment]
    monkeypatch.setattr(handler, "_schedule_push", frames.append)

    handler._on_quote(
        "sh600519",
        {
            "code": "600519",
            "price": 10.0,
            "observed_at_ns": 1_700_000_000_000_000_000,
            "warnings": [{"code": "quotes_partial_failure", "message": "只取回 1/2"}],
        },
    )

    assert len(frames) == 1
    params = frames[0]["params"]
    assert params["meta"]["observed_at_ns"] == 1_700_000_000_000_000_000
    assert params["meta"]["warnings"] == [
        {"code": "quotes_partial_failure", "message": "只取回 1/2"}
    ]
    assert params["meta"]["provider"] == "tdx"
    # 同一事实不在 data 里重复出现。
    assert "warnings" not in params["data"]
    assert "observed_at_ns" not in params["data"]
    assert params["data"]["price"] == 10.0
