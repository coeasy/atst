# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""批量行情三态账本的并行/串行等价（W9/F2，P1-6 后的实现位置）。

P1-6 把 ``quotes_batch`` 收敛为内核唯一实现（``UnifiedRuntime.quotes_batch``，
``workers`` 参数承接原 Client 编排层的并发）：钉死 provider 的批量由
``Client.quotes_batch`` 委托内核，``auto`` 批量仍由 Client 逐只走完整候选链。
因此本文件钉两件事：

1. **内核实现**：同一批符号在 ``workers=0``（串行）与 ``workers>1``（并行）下
   必须给出完全相同的三态账本——顺序、去重、失败归类一个都不能变；并且
   "并行确实在并行"（否则把 ``ThreadPoolExecutor`` 删掉也不会有人发现）。
2. **委托面**：``Client.quotes_batch``（非 auto）真的调的是
   ``runtime.quotes_batch``，并把 ``core.batch_workers`` 传下去。
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any

from atst.client.api import AsyncClient, Client
from atst.domain.models import Quote
from atst.errors import ValidationError
from atst.runtime.kernel import UnifiedRuntime


class _Kernel:
    """只装一个 ``quotes`` 替身的最小内核；``quotes_batch`` 复用真实现（未绑定函数）。"""

    #: 复用 UnifiedRuntime.quotes_batch 的真身，保证钉的是生产实现而不是测试副本。
    quotes_batch = UnifiedRuntime.quotes_batch

    def __init__(self) -> None:
        self.quotes: Callable[..., Any] = lambda *a, **kw: (_ for _ in ()).throw(
            AssertionError("quotes 替身未注入")
        )


def _kernel(quote_one: Callable[..., Any]) -> _Kernel:
    k = _Kernel()
    k.quotes = quote_one  # type: ignore[method-assign]
    return k


def _quote_result(symbol: str) -> SimpleNamespace:
    return SimpleNamespace(data=[Quote(code=symbol[2:], price=1.0)], meta=None)


def _ledger(batch) -> tuple:
    """把批次压成可比对的形状（``BatchItem`` 的值本身带不可比对象）。"""

    return (
        {symbol: item.status for symbol, item in batch.items.items()},
        {symbol: envelope.code for symbol, envelope in batch.errors.items()},
        batch.requested,
        batch.partial,
    )


def test_parallel_and_serial_batches_are_equivalent() -> None:
    calls: list[str] = []
    lock = threading.Lock()

    def quote_one(symbol, *, provider=None, currentness="live"):
        with lock:
            calls.append(symbol)
        if symbol == "sz000002":
            raise ValidationError("boom", context={"symbol": symbol})
        if symbol == "sz000003":
            return SimpleNamespace(data=[], meta=None)
        return _quote_result(symbol)

    symbols = ("sh600519", "sz000002", "sz000001", "sh600519", "sz000003")
    serial = _kernel(quote_one).quotes_batch(symbols, provider="tencent", workers=0)
    calls.clear()
    parallel = _kernel(quote_one).quotes_batch(symbols, provider="tencent", workers=4)

    assert _ledger(parallel) == _ledger(serial)
    # 归一化 + 去重之后才分发：两条路都只打 4 只，重复的 sh600519 只出现一次
    assert sorted(calls) == sorted(["sh600519", "sz000001", "sz000002", "sz000003"])
    assert parallel.requested == ("sh600519", "sz000002", "sz000001", "sz000003")
    assert parallel.items["sz000003"].status == "missing"
    assert parallel.items["sz000002"].status == "failed"


def test_parallel_workers_really_run_concurrently() -> None:
    """串行实现下这个 barrier 会超时 → 两只都记 failed，本判据当场红。"""

    barrier = threading.Barrier(2, timeout=5)

    def quote_one(symbol, *, provider=None, currentness="live"):
        barrier.wait()
        return _quote_result(symbol)

    batch = _kernel(quote_one).quotes_batch(("sh600519", "sz000001"), provider="tencent", workers=2)
    assert [item.status for item in batch.items.values()] == ["ok", "ok"]
    assert batch.errors == {}


def test_workers_zero_stays_serial_and_preserves_isolated_failures() -> None:
    def quote_one(symbol, *, provider=None, currentness="live"):
        if symbol == "sz000002":
            raise ValidationError("boom")
        return _quote_result(symbol)

    batch = _kernel(quote_one).quotes_batch(("sh600519", "sz000002"), provider="tencent", workers=0)
    assert [item.status for item in batch.items.values()] == ["ok", "failed"]
    assert list(batch.errors) == ["sz000002"]


def test_normalized_bad_symbol_is_isolated_not_fatal() -> None:
    """P0-11：归一失败只落该符号一项，同批其余标的照常出数。"""

    def quote_one(symbol, *, provider=None, currentness="live"):
        return _quote_result(symbol)

    batch = _kernel(quote_one).quotes_batch(("sh600519", "no-such-symbol"), workers=0)
    assert batch.status_for("sh600519") == "ok"
    # P0-11：归一失败发生在发包前，语义是「未尝试」而非「尝试后失败」。
    assert batch.status_for("no-such-symbol") == "not_attempted"


class _Runtime:
    """钉委托面的最小 runtime：只暴露 quotes_batch，记录调用参数。"""

    def __init__(self, workers: int) -> None:
        self.config = SimpleNamespace(core=SimpleNamespace(batch_workers=workers))
        self.planner = SimpleNamespace(default_provider="tdx")
        self.calls: list[dict[str, Any]] = []

    def quotes_batch(self, symbols, *, provider=None, currentness="live", workers=0):
        self.calls.append(
            {
                "symbols": tuple(symbols),
                "provider": provider,
                "currentness": currentness,
                "workers": workers,
            }
        )
        return SimpleNamespace(items={}, errors={}, requested=(), partial=False)


def test_client_batch_delegates_to_the_kernel_implementation() -> None:
    """P1-6：非 auto 批量由 Client 委托 ``runtime.quotes_batch``，并发参数下沉。"""

    runtime = _Runtime(4)
    client = Client(runtime=runtime)  # type: ignore[arg-type]
    client.quotes_batch(("sh600519", "sz000001"), provider="tencent")

    assert len(runtime.calls) == 1
    call = runtime.calls[0]
    assert call["symbols"] == ("sh600519", "sz000001")
    assert call["provider"] == "tencent"
    assert call["workers"] == 4


def test_async_quotes_batch_uses_the_same_orchestration() -> None:
    import asyncio

    def quote_one(symbol, *, provider=None, currentness="live"):
        return _quote_result(symbol)

    runtime = _Runtime(4)
    client = Client(runtime=runtime)  # type: ignore[arg-type]
    async_client = AsyncClient(client=client)
    batch = asyncio.run(async_client.quotes_batch(("sh600519",)))
    assert len(runtime.calls) == 1, "AsyncClient 批量与同步面走同一条委托链"
    assert batch is not None
