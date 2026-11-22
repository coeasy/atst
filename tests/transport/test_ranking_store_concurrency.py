# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""RankingStore 的 load→merge→save 必须是互斥复合操作（2026-11-22 丢更新缺陷）。

此前 save() 用 temp+replace 保证单份文件完整，但两个并发 probe 线程各自读到同一
基线后分别 save，后写者静默抹掉前写者条目——冷启动主站排名因此随机丢失。
"""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

from atst.protocol.commands import Family
from atst.transport.hosts import HostEntry, RankingStore

pytestmark = pytest.mark.unit

_HOSTS = [f"10.0.0.{i}" for i in range(1, 33)]


def _probe(host: str) -> HostEntry:
    return HostEntry(host=host, port=7709, family=Family.STANDARD, rtt_ms=1.0)


def test_concurrent_updates_never_lose_entries(tmp_path: Path) -> None:
    store = RankingStore(tmp_path / "ranking.json")
    barrier = threading.Barrier(len(_HOSTS))
    errors: list[BaseException] = []

    def worker(host: str) -> None:
        try:
            barrier.wait()
            store.update([_probe(host)])
        except BaseException as exc:  # noqa: BLE001 - 测试要收集所有线程异常
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(h,)) for h in _HOSTS]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"并发 update 抛异常: {errors[:3]}"
    persisted = store.load()
    missing = [h for h in _HOSTS if f"{h}:7709" not in persisted]
    assert not missing, f"并发丢更新：{len(missing)}/{len(_HOSTS)} 台主站排名被覆盖：{missing[:5]}"
