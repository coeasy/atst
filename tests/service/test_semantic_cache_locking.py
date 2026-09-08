from __future__ import annotations

import threading

from tstdx.semantic_cache import SemanticQueryCache


class BlockingCopy:
    block = False
    entered = threading.Event()
    release = threading.Event()

    def __init__(self, value: int) -> None:
        self.value = value

    def __deepcopy__(self, memo):  # noqa: ANN001,ANN201,ARG002
        clone = BlockingCopy(self.value)
        if type(self).block:
            type(self).entered.set()
            type(self).release.wait(1.0)
        return clone


def test_get_deepcopy_does_not_hold_global_lru_lock() -> None:
    cache = SemanticQueryCache(max_entries=4)
    BlockingCopy.block = False
    BlockingCopy.entered.clear()
    BlockingCopy.release.clear()
    cache.put("slow", BlockingCopy(1))

    get_done = threading.Event()
    put_done = threading.Event()

    def read_slow() -> None:
        try:
            cache.get("slow", max_age=10.0)
        finally:
            get_done.set()

    def write_other_key() -> None:
        cache.put("fast", {"value": 2})
        put_done.set()

    BlockingCopy.block = True
    reader = threading.Thread(target=read_slow)
    reader.start()
    assert BlockingCopy.entered.wait(1.0)

    writer = threading.Thread(target=write_other_key)
    writer.start()
    assert put_done.wait(0.25), "cache deepcopy must not hold the global LRU lock"

    BlockingCopy.release.set()
    reader.join(1.0)
    writer.join(1.0)
    assert get_done.is_set()
    assert len(cache) == 2
