from __future__ import annotations

import atst
from atst.streaming.state import StreamState
from atst.streaming.stateful import AsyncStatefulQuoteStream, StatefulQuoteStream


def test_top_level_exports_canonical_stateful_streaming_api() -> None:
    assert atst.StreamState is StreamState
    assert atst.StatefulQuoteStream is StatefulQuoteStream
    assert atst.AsyncStatefulQuoteStream is AsyncStatefulQuoteStream


def test_stream_state_values_are_stable_public_contract() -> None:
    assert [item.value for item in StreamState] == [
        "created",
        "running",
        "stopping",
        "closed",
        "failed",
    ]
