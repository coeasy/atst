# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""G38：主站失败转移那把退避梯子的上界与归属。

第 25 轮真机量到：一次 ``quotes-snapshot`` 的 0x054C 批量帧在 8 台主站上各自
读超时之后，整次请求花掉 159.4 秒——同一份日志里七条 ``退避 …s`` 相加 =
121.143 秒纯 ``sleep``，而批量失败后的回退路径首台 131 毫秒就取回了数据。根因是 ``2**attempt`` 里的 ``attempt``
在换主站时也会增长（``max_attempts`` 会放大到"每台至少试一次"），于是退避
总时长只由主站数量决定：8 台 ≈ 127 秒，32 台是 ``2**31`` 秒量级。

这里的判据钉三件事：梯子有上界、梯子只兑现给刚失败过的那台、同步与异步池
共用同一处声明。
"""

from __future__ import annotations

import ast
import asyncio
import random
import time
from pathlib import Path

import pytest

from tstdx.errors import ReadTimeout
from tstdx.transport import ConnectionPool
from tstdx.transport.async_ import AsyncConnectionPool
from tstdx.transport.hosts import HostEntry
from tstdx.transport.pool import MAX_RETRY_BACKOFF_SECONDS, retry_backoff_delay

BULK_SNAPSHOT_CMD = 0x054C
TRANSPORT_DIR = Path(__file__).resolve().parents[2] / "tstdx" / "transport"
#: retry_backoff_delay 的抖动因子区间（0.75 ~ 1.25）
JITTER = (0.75, 1.25)


class _TimeRecorder:
    """替掉池模块里的 ``time``：只记账 sleep，其余原样转发给真模块。"""

    def __init__(self) -> None:
        self.sleeps: list[float] = []

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)

    def __getattr__(self, name: str):  # noqa: ANN201 - 转发
        return getattr(time, name)


class _AsyncioRecorder:
    """替掉 ``asyncio`` 命名空间里的 ``sleep``，其余原样转发。

    替身必须仍然是一个**会让出控制权的 await**：异步池的心跳/空闲回收线程（31-B1 起真的
    武装了）在 ``while not self._closed`` 里 await 这一句，若替身立刻返回就永远没有调度
    点，``asyncio.run`` 收尾时既切不断它也回不了事件循环——实测整个测试挂死在
    ``_heartbeat_loop``。记账照旧，让出照旧。
    """

    def __init__(self) -> None:
        self.sleeps: list[float] = []

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        await asyncio.sleep(0)

    def __getattr__(self, name: str):  # noqa: ANN201 - 转发
        return getattr(asyncio, name)


class _StubConn:
    """每次请求都读超时的主站——真机上 0x054C 对 8 台机器的表现就是这个形状。"""

    def __init__(self, host, port, **kw):  # noqa: ANN001, ARG002
        self.host = host
        self.port = port
        self.connected = True
        self.spec = None
        self.stats = type("S", (), {"last_used": 0.0, "created_at": time.time()})()

    def connect(self):  # noqa: ANN201
        return self

    def request(self, method, body=b"", **kw):  # noqa: ANN001, ARG002
        raise ReadTimeout(f"还需 16 字节（{self.host}）")

    def ping(self, *a, **kw):  # noqa: ANN001, ANN002, ARG002
        return 1.0

    def close(self) -> None:
        pass


class _AsyncStubConn(_StubConn):
    """异步池要 ``await connect()`` / ``await request()``，其余同 ``_StubConn``。"""

    def __init__(self, host, port, **kw):  # noqa: ANN001, ARG002
        super().__init__(host, port)
        self._lock = asyncio.Lock()

    async def connect(self):  # noqa: ANN201
        return self

    async def request(self, method, body=b"", **kw):  # noqa: ANN001, ARG002
        raise ReadTimeout(f"还需 16 字节（{self.host}）")

    async def close(self) -> None:
        pass


def _hosts(n: int) -> list[HostEntry]:
    return [HostEntry(f"10.0.0.{i}", 7709) for i in range(1, n + 1)]


# --------------------------------------------------------------------------- #
# 1. 梯子有上界，且上界之外的形状不被压平
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("base", [0.2, 0.5, 1.0, 5.0])
@pytest.mark.parametrize("attempt", [0, 1, 3, 8, 16, 31])
def test_the_ladder_never_outranks_the_cap(base: float, attempt: int) -> None:
    random.seed(20260925 + attempt)
    raw = min(base * (2**attempt), MAX_RETRY_BACKOFF_SECONDS)
    for _ in range(40):
        delay = retry_backoff_delay(base, attempt)
        assert raw * JITTER[0] - 1e-9 <= delay <= raw * JITTER[1] + 1e-9
    assert retry_backoff_delay(base, attempt) <= MAX_RETRY_BACKOFF_SECONDS * JITTER[1]


def test_the_ladder_still_grows_before_it_is_capped() -> None:
    """封顶不能把梯子压平：未触顶的那几步必须按 2 的幂放大（取抖动下界比较）。"""
    low = retry_backoff_delay(1.0, 0) / JITTER[1]  # 下界换算：仍小于下一步的上界
    mid = retry_backoff_delay(1.0, 1) / JITTER[0]
    assert low < mid
    assert retry_backoff_delay(1.0, 20) <= MAX_RETRY_BACKOFF_SECONDS * JITTER[1]


def test_uncapped_shape_would_be_unbounded_for_a_large_pool() -> None:
    """正控：不封顶时 32 台主站的一次失败请求要睡到 2**31 秒量级——上界必须
    由主站数量之外的东西给出，旧实现里没有这样的东西。"""
    uncapped = sum(1.0 * (2**attempt) for attempt in range(32))
    capped = sum(retry_backoff_delay(1.0, attempt) for attempt in range(32))
    assert capped <= 32 * MAX_RETRY_BACKOFF_SECONDS * JITTER[1]
    assert uncapped > 1e8


# --------------------------------------------------------------------------- #
# 2. 梯子只兑现给刚失败过的那台（同步池）
# --------------------------------------------------------------------------- #
def test_a_full_walk_over_distinct_hosts_pays_no_backoff(monkeypatch) -> None:
    """8 台主站每台试一次：一次失败请求不该睡任何一秒。"""
    recorder = _TimeRecorder()
    monkeypatch.setattr("tstdx.transport.pool.time", recorder)
    monkeypatch.setattr("tstdx.transport.pool.TcpConnection", _StubConn)
    pool = ConnectionPool(_hosts(8), slots_per_host=1, heartbeat_interval=0, max_retries=3)

    with pytest.raises(Exception) as exc:
        pool.request(BULK_SNAPSHOT_CMD, b"")
    assert type(exc.value).__name__ == "AllHostsUnreachable"
    assert recorder.sleeps == [], f"换到没试过的主站时不得退避：{recorder.sleeps}"


def test_returning_to_a_tried_host_still_cools_down(monkeypatch) -> None:
    """主站数少于尝试次数时必然回到已试过的机器——那时冷却仍然要付。"""
    recorder = _TimeRecorder()
    monkeypatch.setattr("tstdx.transport.pool.time", recorder)
    monkeypatch.setattr("tstdx.transport.pool.TcpConnection", _StubConn)
    pool = ConnectionPool(_hosts(1), slots_per_host=1, heartbeat_interval=0, max_retries=3)

    with pytest.raises(Exception) as exc:
        pool.request(BULK_SNAPSHOT_CMD, b"")
    assert type(exc.value).__name__ == "AllHostsUnreachable"
    assert len(recorder.sleeps) == 3, recorder.sleeps
    assert all(0 <= s <= MAX_RETRY_BACKOFF_SECONDS * JITTER[1] for s in recorder.sleeps)


# --------------------------------------------------------------------------- #
# 3. 异步池同口径，且梯子只有一处声明
# --------------------------------------------------------------------------- #
def test_async_walk_over_distinct_hosts_pays_no_backoff(monkeypatch) -> None:
    recorder = _AsyncioRecorder()
    monkeypatch.setattr("tstdx.transport.async_.asyncio", recorder)
    monkeypatch.setattr("tstdx.transport.async_.AsyncTcpConnection", _AsyncStubConn)
    #: ``idle_timeout=0`` 是把 31-B1 起真的武装起来的心跳/回收线程关掉的：那个线程每轮
    #: ``await asyncio.sleep(tick)``（默认阈值下 tick=75.0），与退避梯子共用同一个 recorder。
    #: 本条断言的对象是"请求路径换主站时睡不睡"，所以要让被记数的只有它自己。
    pool = AsyncConnectionPool(_hosts(8), slots_per_host=1, heartbeat_interval=0, idle_timeout=0)

    async def main() -> str:
        try:
            await pool.request(BULK_SNAPSHOT_CMD, b"")
        except Exception as exc:  # noqa: BLE001
            return type(exc).__name__
        return "NO-RAISE"

    assert asyncio.run(main()) == "AllHostsUnreachable"
    assert recorder.sleeps == [], f"异步池必须与同步池同口径：{recorder.sleeps}"


def test_only_the_shared_helper_holds_the_exponent() -> None:
    """``2**attempt`` 在整个传输域只许出现在 :func:`retry_backoff_delay` 里。

    两份手抄的梯子，正是同一次 159 秒能同时长在同步与异步两条链上的原因。
    """
    holders: list[str] = []
    for path in sorted(TRANSPORT_DIR.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.BinOp)
                and isinstance(node.op, ast.Pow)
                and isinstance(node.right, ast.Name)
                and node.right.id == "attempt"
            ):
                holders.append(path.name)
    assert holders == ["pool.py"], holders
    src = (TRANSPORT_DIR / "pool.py").read_text(encoding="utf-8")
    helper = src.split("def retry_backoff_delay", 1)[1].split("\n\n\n", 1)[0]
    assert "2**attempt" in helper
    assert "retry_backoff_delay" in (TRANSPORT_DIR / "async_.py").read_text(encoding="utf-8")
