# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""``core.batch_workers`` 并行/串行等价（W9/F2）。

并行只发生在 :meth:`Client.quotes_batch` 的**编排层**：逐 symbol 的取数、provenance
与错误隔离仍由内核逐项完成。因此同一批符号在 ``batch_workers=0``（串行）与
``batch_workers>1``（并行）下必须给出完全相同的三态账本（``items`` / ``errors`` /
``requested`` / ``partial``）——顺序、去重、失败归类一个都不能变。本文件同时钉住
"并行确实在并行"，否则把 ``ThreadPoolExecutor`` 删掉也不会有人发现。
"""

from __future__ import annotations

import threading
from types import SimpleNamespace

from atst.client.api import AsyncClient, Client
from atst.domain.models import Quote
from atst.errors import ValidationError


class _Runtime:
    """只提供 ``quotes_batch`` 读取的两格配置面（无需真实内核）。"""

    def __init__(self, workers: int) -> None:
        self.config = SimpleNamespace(core=SimpleNamespace(batch_workers=workers))
        self.planner = SimpleNamespace(default_provider="tdx")


def _client(workers: int, quote_one) -> Client:
    """把逐只取数替换成测试替身；实例属性是普通函数，调用处 ``self.quotes(...)`` 不绑 self。"""

    client = Client(runtime=_Runtime(workers))
    client.quotes = quote_one  # type: ignore[method-assign]
    return client


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
    serial = _client(0, quote_one).quotes_batch(symbols, provider="tencent")
    calls.clear()
    parallel = _client(4, quote_one).quotes_batch(symbols, provider="tencent")

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

    batch = _client(2, quote_one).quotes_batch(("sh600519", "sz000001"), provider="tencent")
    assert [item.status for item in batch.items.values()] == ["ok", "ok"]
    assert batch.errors == {}


def test_workers_zero_stays_serial_and_preserves_isolated_failures() -> None:
    def quote_one(symbol, *, provider=None, currentness="live"):
        if symbol == "sz000002":
            raise ValidationError("boom")
        return _quote_result(symbol)

    batch = _client(0, quote_one).quotes_batch(("sh600519", "sz000002"), provider="tencent")
    assert [item.status for item in batch.items.values()] == ["ok", "failed"]
    assert list(batch.errors) == ["sz000002"]


def test_async_quotes_batch_uses_the_same_orchestration() -> None:
    import asyncio

    def quote_one(symbol, *, provider=None, currentness="live"):
        return _quote_result(symbol)

    async_client = AsyncClient(client=_client(4, quote_one))
    batch = asyncio.run(async_client.quotes_batch(("sh600519",)))
    assert batch.items["sh600519"].status == "ok"
