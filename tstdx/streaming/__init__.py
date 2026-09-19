# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""Canonical v13 streaming subsystem.

The supported entry point is ``Client.stream`` / ``AsyncClient.stream``: they
compile one exact plan through ``StreamPlanner`` and return a
:class:`StatefulQuoteStream` / :class:`AsyncStatefulQuoteStream` bound to the
caller's runtime. Both poll through ``UnifiedRuntime``, so streaming shares the
same Provider/Planner/Result semantics as ordinary queries.

:class:`QuoteStream` and :class:`AsyncQuoteStream` are the polling bases those
two extend with an explicit lifecycle state machine; nothing outside this
package should build them directly. Pure streaming components remain available
for testing and advanced composition.
"""

from __future__ import annotations

from .base import AsyncQuoteStream, QuoteStream
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
    "QuoteStream",
    "AsyncQuoteStream",
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
