# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""P0-11 regression: a symbol that cannot be normalized is ``not_attempted``.

It is never sent to a Provider, so it must not be reported as a failed request
(``errors``) nor counted as actually requested. The original exception stays
on the ``BatchItem`` for auditability.
"""

from __future__ import annotations

from atst.client.api import Client
from atst.domain.models import Quote
from atst.errors import ValidationError
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


def test_kernel_bad_symbol_is_not_attempted_and_not_requested(monkeypatch) -> None:
    runtime = UnifiedRuntime()
    monkeypatch.setattr(runtime, "quotes", lambda symbol, **kwargs: _quote_result(symbol))

    batch = runtime.quotes_batch(["sh600519", "bad-symbol", "sh600519"])

    assert batch.items["sh600519"].status == "ok"
    bad_item = batch.items["bad-symbol"]
    assert bad_item.status == "not_attempted"
    assert isinstance(bad_item.error, Exception)
    assert batch.status_for("bad-symbol") == "not_attempted"
    assert batch.not_attempted == ("bad-symbol",)
    assert batch.status_counts["not_attempted"] == 1
    assert batch.requested == ("sh600519",)
    assert "bad-symbol" not in batch.errors
    assert set(batch.errors) <= set(batch.requested)


def test_kernel_keeps_error_semantics_for_attempted_failures(monkeypatch) -> None:
    runtime = UnifiedRuntime()

    def fake_quotes(symbol, **kwargs):
        if symbol == "sh600000":
            raise ValidationError("provider rejected the request")
        return _quote_result(symbol)

    monkeypatch.setattr(runtime, "quotes", fake_quotes)

    batch = runtime.quotes_batch(["sh600519", "sh600000", "bad-symbol"])

    assert batch.items["sh600000"].status == "failed"
    assert batch.status_for("sh600000") == "failed"
    assert set(batch.errors) == {"sh600000"}
    assert batch.errors["sh600000"].code == "E1010"
    assert batch.requested == ("sh600519", "sh600000")
    assert batch.partial is True
    assert batch.not_attempted == ("bad-symbol",)


def test_client_auto_batch_bad_symbol_does_not_raise(monkeypatch) -> None:
    client = Client(default_provider="auto")
    monkeypatch.setattr(client, "execute", lambda spec, **kwargs: _quote_result(spec.symbols[0]))

    batch = client.quotes_batch(["sh600519", "bad-symbol"])

    assert batch.items["bad-symbol"].status == "not_attempted"
    assert batch.requested == ("sh600519",)
    assert "bad-symbol" not in batch.errors
