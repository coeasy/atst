# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""T-4 regression: quick helpers reuse one lazily-initialized, thread-safe Client.

Every ``quick.*`` call used to construct a fresh ``Client`` (full runtime audit) and
closed it on exit.  Now the default-constructed client is shared process-wide, and
the helpers deliberately do not close it.
"""

from __future__ import annotations

import threading
import time
from typing import Any

import pytest

from atst.client import quick


class _Result:
    def __init__(self, data: Any) -> None:
        self.data = data


class _FakeClient:
    def __init__(self) -> None:
        self.closes = 0

    def bars(self, *_args: Any, **_kwargs: Any) -> _Result:
        return _Result([])

    def quotes(self, *_args: Any, **_kwargs: Any) -> _Result:
        return _Result([{"code": "600519"}])

    def minute(self, *_args: Any, **_kwargs: Any) -> _Result:
        return _Result([])

    def trades(self, *_args: Any, **_kwargs: Any) -> _Result:
        return _Result([])

    def close(self) -> None:
        self.closes += 1


def test_quick_helpers_do_not_close_the_shared_client(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient()
    monkeypatch.setattr(quick, "_client", lambda: fake)

    quick.history("600519")
    quick.quotes("600519")
    quick.quote("600519")
    quick.minute("600519")
    quick.trades("600519")

    assert fake.closes == 0, "a helper closed the process-wide client"


def test_shared_client_is_initialized_once_under_concurrency(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(quick, "_shared_client", None)
    created: list[Any] = []

    class _Counted:
        def __init__(self) -> None:
            created.append(self)
            time.sleep(0.01)

    monkeypatch.setattr(quick, "Client", _Counted)
    barrier = threading.Barrier(8)
    results: list[Any] = []

    def _worker() -> None:
        barrier.wait()
        results.append(quick._client())

    threads = [threading.Thread(target=_worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=3.0)

    assert len(created) == 1, "concurrent first calls constructed more than one Client"
    assert len(results) == 8
    assert all(result is results[0] for result in results)
