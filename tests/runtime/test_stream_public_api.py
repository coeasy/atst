from __future__ import annotations

import tstdx

from tstdx.streaming.state import StreamState
from tstdx.streaming.stateful import AsyncStatefulQuoteStream, StatefulQuoteStream


def test_top_level_exports_canonical_stateful_streaming_api() -> None:
    assert tstdx.StreamState is StreamState
    assert tstdx.StatefulQuoteStream is StatefulQuoteStream
    assert tstdx.AsyncStatefulQuoteStream is AsyncStatefulQuoteStream


def test_stream_state_values_are_stable_public_contract() -> None:
    assert [item.value for item in StreamState] == [
        "created",
        "running",
        "stopping",
        "closed",
        "failed",
    ]
