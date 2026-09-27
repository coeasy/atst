# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""C2 回归：连接级租约锁下，同 socket 帧交织不可达。

* 16 线程 × 各 200 请求打同一 4-slot 池（8 workers > 4 slots 的放大版），
  罐头 server 回显 seq + body——``check_seq`` 全过 + 每个响应的 payload
  与请求 token 逐一比对，任何帧错位立刻显形；
* server 侧坏头检测（半包交织在 12 字节头解析处显形）；
* 心跳与在飞请求不交叉：在飞请求期间触发的心跳 ping 必须等在锁外，
  server 记录的帧序为「请求 → 心跳」且请求 payload 完整。
"""

from __future__ import annotations

import struct
import sys
import threading
import time
from pathlib import Path

import pytest

from atst.transport import ConnectionPool
from atst.transport.hosts import HostEntry

sys.path.insert(0, str(Path(__file__).parent))
from fake_server import ECHO_CMD, HB_CMD, FakeTdxServer  # noqa: E402

WORKERS = 16
REQUESTS_PER_WORKER = 200
SLOTS = 4


def _make_pool(server: FakeTdxServer, **kw) -> ConnectionPool:
    defaults = dict(
        slots_per_host=SLOTS,
        timeout=15.0,
        heartbeat_interval=0,
        handshake=False,
        max_retries=1,
    )
    defaults.update(kw)
    return ConnectionPool([HostEntry("127.0.0.1", server.port)], **defaults)


@pytest.mark.slow
def test_concurrent_requests_no_frame_interleave():
    """16 线程 × 200 请求 > 4 slots：零错帧、零串数据。"""
    with FakeTdxServer() as server:
        pool = _make_pool(server)
        errors: list[BaseException] = []
        barrier = threading.Barrier(WORKERS)

        def worker(wid: int) -> None:
            try:
                barrier.wait(timeout=10)
                for i in range(REQUESTS_PER_WORKER):
                    token = struct.pack("<HHI", wid, i, 0xCAFE)
                    frame = pool.request(ECHO_CMD, token)
                    # frame.seq 已由 check_seq 与请求 seq 比对（server 回显 seq）
                    if frame.payload[4:] != token:
                        raise AssertionError(
                            f"帧错位: worker={wid} i={i} "
                            f"got={frame.payload[4:].hex()} want={token.hex()}"
                        )
            except BaseException as exc:  # noqa: BLE001 - 收集后统一断言
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(w,)) for w in range(WORKERS)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=60)
        pool.close()

        assert not errors, f"并发请求出现错误: {errors[:3]}"
        assert pool.stats.requests == WORKERS * REQUESTS_PER_WORKER
        assert pool.stats.failures == 0
        # server 侧：无坏头（帧交织/半包在 12 字节头处显形）
        assert server.all_misframed() == []
        # 每个 worker 恰好收到自己的 token：统计 server 记录的 ECHO 帧数
        echo_frames = [f for f in server.all_frames() if f[0] == ECHO_CMD]
        assert len(echo_frames) == WORKERS * REQUESTS_PER_WORKER


def test_heartbeat_waits_outside_inflight_request():
    """心跳与在飞请求共享 socket：ping 在锁外等待，帧序不交叉。"""
    with FakeTdxServer() as server:
        server.delay[ECHO_CMD] = 1.0  # 在飞请求拉长到 1s，覆盖心跳唤醒点
        pool = _make_pool(server, slots_per_host=1, heartbeat_interval=1, max_retries=0)
        token = b"\x11\x22\x33\x44"
        frame = pool.request(ECHO_CMD, token)
        assert frame.payload[4:] == token

        # 等心跳轮转一轮（interval=1s，请求结束后 idle 足够触发 ping）
        deadline = time.time() + 5.0
        while time.time() < deadline:
            methods = server.connections[0].methods
            if HB_CMD in methods:
                break
            time.sleep(0.1)
        pool.close()

        assert server.all_misframed() == []
        methods = server.connections[0].methods
        assert methods[0] == ECHO_CMD, f"请求帧应最先到达: {methods}"
        assert HB_CMD in methods, "心跳应实际发生（在飞结束后补发）"
        assert methods.count(ECHO_CMD) == 1
        # 心跳成功回写 rtt，且心跳失败路径未误标记
        assert pool.hosts[0].failures == 0
        # 全程只有一条连接：心跳未触发重建
        assert server.conn_count() == 1


def test_heartbeat_failure_does_not_deadlock_slot():
    """心跳 ping 失败不得自死锁（旧实现持 slot.lock 重入 _drop）。"""
    with FakeTdxServer() as server:
        server.close_on.add(HB_CMD)  # 心跳即断连 → ping 必失败
        pool = _make_pool(server, slots_per_host=1, heartbeat_interval=1, max_retries=0)
        frame = pool.request(ECHO_CMD, b"\x01")
        assert frame.payload[4:] == b"\x01"

        # 心跳失败 → _mark_failure 弃连；弃连落地后并发请求必须仍能完成（重建）
        deadline = time.time() + 5.0
        saw_heartbeat = False
        while time.time() < deadline:
            methods = server.connections[0].methods if server.connections else []
            if HB_CMD in methods:
                saw_heartbeat = True
                break
            time.sleep(0.05)
        assert saw_heartbeat, "心跳帧应已到达 server"
        # 等 _mark_failure → _drop 落地（slot.conn 置空），证明心跳失败被处理
        deadline = time.time() + 5.0
        while time.time() < deadline and pool._slots[0].conn is not None:
            time.sleep(0.05)
        assert pool._slots[0].conn is None, "心跳失败应触发弃连"
        # 弃连后的请求必须能完成（槽位未被 slot.lock 自死锁卡死）
        frame2 = pool.request(ECHO_CMD, b"\x02", timeout=5.0)
        assert frame2.payload[4:] == b"\x02"
        pool.close()

        assert server.conn_count() >= 2, "弃连后应有重建连接"
