from __future__ import annotations

import atst
from atst.streaming.state import StreamState


def test_stream_state_is_part_of_top_level_public_api() -> None:
    assert "StreamState" in atst.__all__
    assert atst.StreamState is StreamState
    assert set(StreamState) == {
        StreamState.CREATED,
        StreamState.RUNNING,
        StreamState.STOPPING,
        StreamState.CLOSED,
        StreamState.FAILED,
    }
