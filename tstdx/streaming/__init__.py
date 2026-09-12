# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""Canonical v13 streaming subsystem.

The supported business stream implementations are StatefulQuoteStream and
AsyncStatefulQuoteStream. They poll through UnifiedRuntime, so streaming shares
the same Provider/Planner/Result semantics as ordinary queries. Historical
QuoteStream/AsyncQuoteStream implementations were removed from the public/core
surface.

Pure streaming components remain available for testing and advanced composition.
"""

from __future__ import annotations

from .engine import (
    BackpressureQueue,
    DeltaMerger,
    GapFiller,
    QuoteChannel,
    ReconnectPolicy,
    StreamEngine,
    StreamEvent,
)
from .push import PUSH_CMD, PushChannel, PushFrame
from .state import StreamLifecycle, StreamLifecycleSnapshot, StreamState
from .stateful import AsyncStatefulQuoteStream, StatefulQuoteStream

__all__ = [
    "StatefulQuoteStream",
    "AsyncStatefulQuoteStream",
    "StreamState",
    "StreamLifecycle",
    "StreamLifecycleSnapshot",
    "ReconnectPolicy",
    "DeltaMerger",
    "GapFiller",
    "BackpressureQueue",
    "QuoteChannel",
    "StreamEngine",
    "StreamEvent",
    "PushChannel",
    "PushFrame",
    "PUSH_CMD",
]
