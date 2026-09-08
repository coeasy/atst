from __future__ import annotations

import tstdx
from tstdx.streaming.planned import StreamState


def test_stream_state_is_part_of_top_level_public_api() -> None:
    assert "StreamState" in tstdx.__all__
    assert tstdx.StreamState is StreamState
    assert set(StreamState) == {
        StreamState.CREATED,
        StreamState.RUNNING,
        StreamState.STOPPING,
        StreamState.CLOSED,
        StreamState.FAILED,
    }
