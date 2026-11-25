# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""P0-11: kernel and client agree on requested/errors for bad raw symbols."""

from __future__ import annotations

from atst.client.api import Client
from atst.domain.models import Quote
from atst.query import QueryPlanner, QuerySpec
from atst.result import Provenance, QueryResult
from atst.runtime.kernel import UnifiedRuntime


def _quote_result(symbol: str) -> QueryResult[list[Quote]]:
    plan = QueryPlanner().compile(
        QuerySpec.build("quotes", symbols=symbol, provider="tdx", currentness="live")
    )
    return QueryResult.from_plan(
        [Quote(code="600519", price=1.0)], plan=plan, provenance=Provenance.direct(plan)
    )


def test_kernel_requested_excludes_bad_and_duplicate_symbols(monkeypatch) -> None:
    runtime = UnifiedRuntime()
    monkeypatch.setattr(runtime, "quotes", lambda symbol, **kwargs: _quote_result(symbol))

    batch = runtime.quotes_batch(["sh600519", "bad-symbol", "sh600519"])

    assert batch.requested == ("sh600519",)
    assert set(batch.errors) <= set(batch.requested)
    assert batch.items["bad-symbol"].status == "not_attempted"
    assert "bad-symbol" not in batch.errors


def test_client_auto_and_kernel_share_the_same_requested_contract(monkeypatch) -> None:
    client = Client(default_provider="auto")
    monkeypatch.setattr(client, "execute", lambda spec, **kwargs: _quote_result(spec.symbols[0]))

    batch = client.quotes_batch(["sh600519", "bad-symbol", "sh600519"])

    assert batch.requested == ("sh600519",)
    assert batch.items["bad-symbol"].status == "not_attempted"
    assert set(batch.errors) <= set(batch.requested)
