from __future__ import annotations

import threading
import time
from typing import Any

import pytest

from tstdx.domain.models import Quote
from tstdx.errors import ReadTimeout, SourceUnavailable
from tstdx.planned_service import UnifiedMarketDataService


class BlockingQuoteAdapter:
    def __init__(self) -> None:
        self.calls = 0
        self.seen: list[tuple[str, ...]] = []
        self.started = threading.Event()
        self.release = threading.Event()
        self.block = False

    def fetch(self, symbols: list[str]) -> list[Quote]:
        self.calls += 1
        self.seen.append(tuple(symbols))
        if self.block:
            self.started.set()
            self.release.wait(timeout=2.0)
        return [Quote(code=symbol, price=10.0) for symbol in symbols]


class FakeManager:
    def __init__(self, adapter: BlockingQuoteAdapter) -> None:
        self.adapter = adapter
        self.closed = False

    def quote_adapter(self, provider: str) -> BlockingQuoteAdapter:
        assert provider == "tencent"
        return self.adapter

    def close(self) -> None:
        self.closed = True


def test_duplicate_symbols_are_fetched_once_and_fanned_out() -> None:
    adapter = BlockingQuoteAdapter()
    service = UnifiedMarketDataService(manager=FakeManager(adapter))

    rows = service.quotes(
        ["sh600519", "sh600519", "sz000001"],
        provider="tencent",
    )

    assert adapter.calls == 1
    assert adapter.seen == [("sh600519", "sz000001")]
    assert [row.code for row in rows] == ["sh600519", "sh600519", "sz000001"]
    service.close()


def test_concurrent_identical_queries_join_singleflight() -> None:
    adapter = BlockingQuoteAdapter()
    adapter.block = True
    service = UnifiedMarketDataService(manager=FakeManager(adapter))
    results: list[list[Quote]] = []
    errors: list[BaseException] = []

    def run() -> None:
        try:
            rows = service.quotes(["sh600519"], provider="tencent", deadline_ms=1000)
            results.append(rows)
        except BaseException as exc:  # test captures follower failures explicitly
            errors.append(exc)

    first = threading.Thread(target=run)
    second = threading.Thread(target=run)
    first.start()
    assert adapter.started.wait(timeout=1.0)
    second.start()
    time.sleep(0.05)
    adapter.release.set()
    first.join(timeout=2.0)
    second.join(timeout=2.0)

    assert errors == []
    assert len(results) == 2
    assert adapter.calls == 1
    assert service.singleflight.leaders == 1
    assert service.singleflight.joins == 1
    service.close()


def test_different_provider_semantics_do_not_join_same_flight() -> None:
    adapter = BlockingQuoteAdapter()
    service = UnifiedMarketDataService(manager=FakeManager(adapter))

    left = service.compile(
        __import__("tstdx.query", fromlist=["QuerySpec"]).QuerySpec.build(
            "quotes", symbols=["sh600519"], provider="tencent"
        )
    )
    right = service.compile(
        __import__("tstdx.query", fromlist=["QuerySpec"]).QuerySpec.build(
            "quotes", symbols=["sh600519"], provider="sina"
        )
    )
    assert left.fingerprint.value != right.fingerprint.value
    service.close()


def test_partial_batch_is_rejected_by_default() -> None:
    class PartialAdapter(BlockingQuoteAdapter):
        def fetch(self, symbols: list[str]) -> list[Quote]:
            self.calls += 1
            self.seen.append(tuple(symbols))
            return [Quote(code=symbols[0], price=10.0)]

    adapter = PartialAdapter()
    service = UnifiedMarketDataService(manager=FakeManager(adapter))
    with pytest.raises(SourceUnavailable):
        service.quotes(
            ["sh600519", "sz000001"],
            provider="tencent",
            allow_partial=False,
        )
    service.close()


def test_total_deadline_is_checked_after_provider_returns() -> None:
    class SlowAdapter(BlockingQuoteAdapter):
        def fetch(self, symbols: list[str]) -> list[Quote]:
            time.sleep(0.02)
            return [Quote(code=symbol, price=10.0) for symbol in symbols]

    service = UnifiedMarketDataService(manager=FakeManager(SlowAdapter()))
    with pytest.raises(ReadTimeout):
        service.quotes(["sh600519"], provider="tencent", deadline_ms=1)
    service.close()
