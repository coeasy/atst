# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""同步池 ↔ 异步池的熔断行为对拍（第 27 轮 A4，V19 §3 B-2 / §6 D-4）。

两张传输链各写了一份"OPEN 冷却 → HALF_OPEN 单探针 → 归还令牌不伪造健康"的状态机
（`pool.py` 与 `async_.py` 各自的 `_mark_failure` / `_circuit_allows` /
`_release_probe_token` / `_mark_success` 四件套），本轮逐行比对语义仍一致，但两边只共享
`retry_backoff_delay` 一个函数——第 25/26 轮那类修复（退避封顶 G38、熔断复位 F-92/F-94）
都要人肉打两遍，这正是漂移的入口。D-4 的裁决是**先钉一致性、不合并实现**：合并要重写
两张池，风险直接进传输主链。

对拍的口径：

* **不比墙钟**。每条链各挂一条脚本时钟（`_ScriptedClock`），时间只由脚本在事件之间推进，
  所以"冷却到期"是可复现的离散步，而不是 `sleep` 出来的竞态。
* **不比连接**。两张池都以 ``slots_per_host=1`` 构造但从不建连：驱动的是状态机本身，失败
  用异常对象表达，丢弃槽位走 ``conn=None`` 的路径。同步池的后台测速阈值被抬到脚本打不到的
  地方，否则连续连接失败会真的起线程拨主站。
* **比的是轨迹**：每个事件之后取一次运行期健康快照（熔断态、探测令牌、三类计数、加权值、
  错误类型、OPEN 距今几个冷却），两条链的轨迹必须逐项相等。
* 脚本自己也要被钉住：四个状态都必须在轨迹里出现过，否则"两边都没变化"也算相等——
  那是判据失明，不是通过。
"""

from __future__ import annotations

import asyncio
import time as _wall_clock
from typing import Any

from tstdx.errors import ConnectionFailed
from tstdx.protocol.commands import Family
from tstdx.transport import async_ as async_module
from tstdx.transport import pool as sync_module
from tstdx.transport.hosts import HostEntry

#: 阈值与冷却从同步池读；异步池那几个常数是否同一份，由本文件最后一条判据钉住。
COOLDOWN = sync_module.CIRCUIT_COOLDOWN_SECONDS
OPEN_AT = sync_module.CIRCUIT_OPEN_AT
DEGRADED_AT = sync_module.CIRCUIT_DEGRADED_AT


class _ScriptedClock:
    """一条只按脚本走的时钟：其余时间属性透传给真实 ``time`` 模块。"""

    def __init__(self) -> None:
        self.now = 1_000.0

    def time(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds

    def __getattr__(self, name: str) -> Any:
        return getattr(_wall_clock, name)


class _BizFailure(RuntimeError):
    """连接成功、交换失败——走 ``BIZ_FAILURE_WEIGHT`` 那条加权路径。"""


def _events() -> list[tuple[str, Any]]:
    """一条把状态机推过全部四个状态的脚本（加权累计写在注释里，便于复核覆盖）。

    贴着四条最容易各写一半的规则：按权重点升 DEGRADED / OPEN、HALF_OPEN 只放一个探针、
    HALF_OPEN 失败立即重开（重开会把 ``opened_at`` 推到当下）、归还令牌不写健康、
    成功把四类计数全量清零。
    """
    conn = ConnectionFailed("连接被拒绝")
    biz = _BizFailure("交换超时")
    return [
        ("fail", conn),  # 1.0 healthy
        ("fail", conn),  # 2.0
        ("allows", None),  # healthy 放行
        ("fail", conn),  # 3.0 → degraded
        ("fail", conn),  # 4.0
        ("fail", conn),  # 5.0
        ("fail", biz),  # 5.5（业务失败按 BIZ_FAILURE_WEIGHT 加权）
        ("fail", conn),  # 6.5
        ("allows", None),  # degraded 仍放行
        ("fail", conn),  # 7.5
        ("fail", conn),  # 8.5 → open，opened_at 记下当下
        ("allows", None),  # 冷却未到期 → 拦
        ("advance", COOLDOWN - 1.0),
        ("allows", None),  # 仍差 1 秒 → 拦
        ("advance", 2.0),
        ("allows", None),  # 到期 → half_open，领取唯一探针
        ("allows", None),  # 探针在飞 → 第二个请求拦
        ("release", None),  # 归还令牌，不写健康
        ("allows", None),  # 再次放行单次探测
        ("fail", conn),  # HALF_OPEN 的任何失败都重开 → open
        ("allows", None),  # 刚重开 → 拦
        ("advance", COOLDOWN + 1.0),
        ("allows", None),  # 又一次探测机会
        ("success", None),  # 探测成功 → healthy，计数与 opened_at 全清
        ("allows", None),
        ("fail", biz),
        ("fail", biz),
        ("fail", biz),
        ("fail", biz),  # 2.0
        ("fail", biz),  # 2.5
        ("fail", biz),  # 3.0 → 第二次进 degraded（走业务加权那条路）
        ("allows", None),
        ("fail", conn),  # 4.0
        ("success", None),
    ]


def _snapshot(host: HostEntry, clock: _ScriptedClock) -> tuple[Any, ...]:
    opened_ago = (
        0.0
        if not host.circuit_opened_at
        else round((clock.now - host.circuit_opened_at) / COOLDOWN, 6)
    )
    return (
        host.circuit,
        host.circuit_probe_inflight,
        host.failures,
        host.biz_failures,
        round(host.consec_weighted, 6),
        host.last_error.split(":", 1)[0],
        opened_ago,
    )


def _step(label: str, payload: Any) -> tuple[str, Any]:
    if label == "fail":
        return ("fail", type(payload).__name__)
    return (label, None)


async def _drive_async(events: list[tuple[str, Any]]) -> list[tuple[str, Any]]:
    pool = async_module.AsyncConnectionPool(
        [HostEntry("127.0.0.1", 7709, Family.STANDARD)],
        slots_per_host=1,
        handshake=False,
        heartbeat_interval=0,
    )
    clock = _ScriptedClock()
    original = async_module.time
    async_module.time = clock
    try:
        slot = pool._slots[0]
        trace: list[tuple[str, Any]] = []
        for label, payload in events:
            if label == "advance":
                clock.advance(payload)
                continue
            if label == "fail":
                await pool._mark_failure(slot, payload)
            elif label == "success":
                await pool._mark_success(slot)
            elif label == "release":
                await pool._release_probe_token(slot, None)
            elif label == "allows":
                allowed = await pool._circuit_allows(slot.host)
                trace.append(("allows", allowed, _snapshot(slot.host, clock)))
                continue
            else:  # pragma: no cover - 脚本写错时自曝
                raise AssertionError(f"未知事件 {label!r}")
            trace.append((*_step(label, payload), _snapshot(slot.host, clock)))
        return trace
    finally:
        async_module.time = original
        await pool.close()


def _drive_sync(events: list[tuple[str, Any]]) -> list[tuple[str, Any]]:
    pool = sync_module.ConnectionPool(
        [HostEntry("127.0.0.1", 7709, Family.STANDARD)],
        slots_per_host=1,
        handshake=False,
        heartbeat_interval=0,
        #: 连续连接失败累计到阈值会真起后台测速线程拨主站；本判据只量状态机，
        #: 所以把阈值抬到脚本打不到的地方。
        speedtest_threshold=10**6,
    )
    clock = _ScriptedClock()
    original = sync_module.time
    sync_module.time = clock
    try:
        slot = pool._slots[0]
        trace: list[tuple[str, Any]] = []
        for label, payload in events:
            if label == "advance":
                clock.advance(payload)
                continue
            if label == "fail":
                pool._mark_failure(slot, payload)
            elif label == "success":
                pool._mark_success(slot)
            elif label == "release":
                pool._release_probe_token(slot, None)
            elif label == "allows":
                allowed = pool._circuit_allows(slot.host)
                trace.append(("allows", allowed, _snapshot(slot.host, clock)))
                continue
            else:  # pragma: no cover - 脚本写错时自曝
                raise AssertionError(f"未知事件 {label!r}")
            trace.append((*_step(label, payload), _snapshot(slot.host, clock)))
        return trace
    finally:
        sync_module.time = original
        pool.close()


def test_sync_and_async_circuit_traces_are_identical() -> None:
    """同一串事件喂两张链，运行期健康轨迹必须逐项相等。"""
    events = _events()
    sync_trace = _drive_sync(events)
    async_trace = asyncio.run(_drive_async(events))
    assert len(sync_trace) == len(async_trace) > 10, "轨迹过短，对拍自身失效"
    diffs = [
        (index, left, right)
        for index, (left, right) in enumerate(zip(sync_trace, async_trace, strict=True))
        if left != right
    ]
    assert diffs == [], f"同步池与异步池的熔断轨迹从第 {diffs[0][0]} 步开始分叉：{diffs[:3]}"


def test_the_script_actually_walks_every_circuit_state() -> None:
    """脚本必须真的走过四个状态，否则两条链原地不动也算"相等"。"""
    trace = _drive_sync(_events())
    states = {entry[-1][0] for entry in trace}
    assert {"degraded", "open", "half_open", "healthy"} <= states, (
        f"脚本只走到 {sorted(states)}，对拍没有覆盖熔断状态机"
    )
    allowed = [entry[1] for entry in trace if entry[0] == "allows"]
    assert False in allowed and True in allowed, f"放行门禁只给出一边结果：{allowed}"
    opened = [entry[-1][6] for entry in trace if entry[-1][0] == "open"]
    assert any(value < 1.0 for value in opened), "OPEN 之后从未在冷却期内被拦，冷却没被走到"


def test_the_async_pool_shares_the_circuit_constants() -> None:
    """两张链必须用同一组阈值：常数分叉不会表现为轨迹分叉，只能单独钉。"""
    assert async_module.CIRCUIT_COOLDOWN_SECONDS == COOLDOWN
    assert async_module.CIRCUIT_OPEN_AT == OPEN_AT
    assert async_module.CIRCUIT_DEGRADED_AT == DEGRADED_AT
    assert async_module.BIZ_FAILURE_WEIGHT == sync_module.BIZ_FAILURE_WEIGHT
