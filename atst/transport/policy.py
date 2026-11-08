"""传输策略核：同步池与异步池共享的纯策略逻辑（v2/W6 §2.5）。

两池的差别只在 I/O 与并发外壳（线程锁 vs ``asyncio.Lock``）。熔断状态机、槽位选序、
失败/成功记账这些判据必须是**同一份**实现：第 25/26 轮两次事故（G38 退避封顶、
F-92/F-94 熔断复位）都长在"两份手抄的同一判据"上。本模块因此只做三件事：

* 常量（熔断阈值/冷却时长/业务失败权重）；
* 对 ``HostEntry`` 的纯状态推进（不持锁、不做 I/O、不记录指标）；
* 槽位排序与探测令牌领取的选序逻辑。

调用方负责锁、日志、指标与连接生命周期——那是"I/O 壳"的职责。
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable
from typing import Any, Protocol, TypeVar

# --------------------------------------------------------------------------- #
# B4 熔断状态机常量
# --------------------------------------------------------------------------- #
#: 连续失败加权值达到该值 → DEGRADED（仍发请求，仅记录）
CIRCUIT_DEGRADED_AT = 3.0
#: 连续失败加权值达到该值 → OPEN（请求直接跳过该主站，不再吃超时）
CIRCUIT_OPEN_AT = 8.0
#: OPEN 冷却时长（秒）；到期转 HALF_OPEN，放行单次探测
CIRCUIT_COOLDOWN_SECONDS = 30.0
#: 业务失败（连接成功但交换失败）的加权系数
BIZ_FAILURE_WEIGHT = 0.5


class _HasHost(Protocol):
    """槽位的最小形状：同步池与异步池各自的槽位都带一个 ``host``。"""

    host: Any


_SlotT = TypeVar("_SlotT", bound=_HasHost)


def rank_slots(slots: Iterable[_SlotT]) -> list[_SlotT]:
    """主机间按健康分稳定排序；同一主机内保持传入顺序（槽位轮转）。"""
    return sorted(slots, key=lambda slot: slot.host.score)


def select_allowed_slot(
    slots: Iterable[_SlotT],
    *,
    allows: Callable[[Any], bool],
    exclude_hosts: set[str] | None = None,
) -> _SlotT | None:
    """按选序逐个尝试领取唯一的熔断探测令牌。

    ``exclude_hosts`` 优先：有可排除项时先只在剩余主站里选，全部被排除时回落到
    完整选序（与 v1 行为一致：候选用尽不是"无源可用"）。
    """
    ordered = rank_slots(slots)
    if exclude_hosts:
        preferred = [slot for slot in ordered if slot.host.key not in exclude_hosts]
        candidates = preferred or ordered
    else:
        candidates = ordered
    for candidate in candidates:
        if allows(candidate.host):
            return candidate
    return None


def circuit_allows(host: Any, *, now: float | None = None) -> bool:
    """B4：判断主站是否放行本次请求（同步/异步池共用）。

    * healthy / degraded：放行；
    * open：冷却未到期 → 拦截；冷却到期 → 转 HALF_OPEN 并领取唯一探测令牌；
    * half_open：已有探测时拦截其余请求，探测成功由 :func:`record_success` 复位，
      失败立即重开。
    """
    at = time.time() if now is None else now
    if host.circuit == "open":
        if at - host.circuit_opened_at < CIRCUIT_COOLDOWN_SECONDS:
            return False
        if host.circuit_probe_inflight:
            return False
        host.circuit = "half_open"
        host.circuit_probe_inflight = True
        return True
    if host.circuit == "half_open":
        if host.circuit_probe_inflight:
            return False
        host.circuit_probe_inflight = True
        return True
    return True


def record_failure(
    host: Any,
    exc: BaseException,
    *,
    connection_level: bool,
    now: float | None = None,
) -> bool:
    """推进一次失败记账，返回"本次是否把熔断推进到 OPEN"。

    ``connection_level`` 为真表示连接根本没建立（``ConnectionFailed``）；否则是
    "连上了但交换失败"的业务级失败，按 :data:`BIZ_FAILURE_WEIGHT` 降权。
    """
    at = time.time() if now is None else now
    was_half_open = host.circuit == "half_open" or host.circuit_probe_inflight
    host.failures += 1
    if not connection_level:
        host.biz_failures += 1
    host.consec_weighted += 1.0 if connection_level else BIZ_FAILURE_WEIGHT
    host.circuit_probe_inflight = False
    if was_half_open or host.consec_weighted >= CIRCUIT_OPEN_AT:
        if host.circuit != "open" or host.circuit_opened_at == 0.0:
            host.circuit_opened_at = at
        host.circuit = "open"
        host.last_error = f"{type(exc).__name__}: {exc}"
        return True
    if host.consec_weighted >= CIRCUIT_DEGRADED_AT:
        host.circuit = "degraded"
    host.last_error = f"{type(exc).__name__}: {exc}"
    return False


def record_success(host: Any, *, now: float | None = None, rtt_ms: float | None = None) -> None:
    """记账一次成功：清零失败计数并复位熔断（HALF_OPEN 探测成功 → HEALTHY）。"""
    at = time.time() if now is None else now
    host.failures = 0
    host.biz_failures = 0
    host.last_error = ""
    host.last_ok = at
    if rtt_ms is not None:
        host.live_rtt_ms = max(0.0, float(rtt_ms))
        host.live_ok_at = host.last_ok
    host.circuit = "healthy"
    host.circuit_probe_inflight = False
    host.consec_weighted = 0.0
    host.circuit_opened_at = 0.0
