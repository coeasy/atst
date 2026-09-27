# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""投递口错误必须真的能投递：为 ``BackpressureOverflow`` 补上行为判据。

第 44 步执行 F-68 裁决 (a) 时，``BackpressureOverflow`` 是五个"删除名额"里唯一被取证否掉
的那个——``tests/architecture/test_error_promises.py`` 的后缀过滤把它在
``atst/streaming/base.py`` 的 ``on_error(BackpressureOverflow(...))`` 整条看不见，于是台账
把它和四个真幻影一起记成了"运行期永不发生"。AST 站点只是"这行代码在"，本模块要的是
**这条溢出信号确实会走到用户回调**：订阅两个标的、队列容量 1，一轮内必然丢最旧一条，
丢弃计数一旦上升就必须发出该异常。

同一条判据也钉住反面：`BackpressureQueue.put` 的语义是丢最旧并计数、从不抛
（``docs/errors.md`` 此前据此说"信号不存在"），所以异常只有一个出口——
``on_error``。若哪天有人把它改成抛进 ``_poll_once``，本测试会立刻和文档一起分叉。
"""

from __future__ import annotations

import threading

from atst.errors import BackpressureOverflow
from atst.query import QueryPlanner, QuerySpec
from atst.result import Provenance, QueryResult
from atst.streaming import StatefulQuoteStream


class TwoSymbolRuntime:
    """每轮为两个标的各回一条行情——队列容量 1 时第二轮的第二个 put 必然溢出。"""

    def __init__(self) -> None:
        self.closed = False

    def quotes(self, symbols, **kwargs):  # noqa: ANN001, ANN201
        del kwargs
        values = [symbols] if isinstance(symbols, str) else list(symbols)
        plan = QueryPlanner().compile(
            QuerySpec.build("quotes", symbols=values, provider="tdx", currentness="live")
        )
        return QueryResult.from_plan(
            [{"code": item[-6:], "price": 10.0} for item in values],
            plan=plan,
            provenance=Provenance.direct(plan),
        )

    def close(self) -> None:
        self.closed = True


def test_queue_overflow_is_delivered_to_the_subscriber_on_error() -> None:
    errors: list[Exception] = []
    overflow = threading.Event()

    def collect(exc: Exception) -> None:
        errors.append(exc)
        if isinstance(exc, BackpressureOverflow):
            overflow.set()

    stream = StatefulQuoteStream(runtime=TwoSymbolRuntime())  # type: ignore[arg-type]
    stream.subscribe(
        ["sh600519", "sz000001"],
        interval=0.01,
        max_queue=1,
        on_quote=lambda symbol, quote: None,
        on_error=collect,
    )
    stream.start()
    try:
        assert overflow.wait(2.0), f"背压溢出没有投递到 on_error，实收：{errors!r}"
    finally:
        stream.stop(timeout=1.0)

    exc = next(e for e in errors if isinstance(e, BackpressureOverflow))
    assert exc.code == "E6030"
    assert exc.context["max_queue"] == 1
    assert exc.context["dropped_total"] >= 1
    assert exc.advice.retryable is True
