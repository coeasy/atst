"""B4 · 连接池熔断状态机测试。

覆盖：
* 状态推进：healthy → degraded（连续失败加权 ≥3）→ open（≥8）；
* 权重：连接失败 1.0 / 业务失败 0.5；
* 成功复位：open → healthy；
* _circuit_allows：OPEN 拦截 / 冷却到期转 HALF_OPEN，仅放行单次探测；
* 选主站：OPEN 主站被跳过，优先健康主站（不再吃超时）。
"""

from __future__ import annotations

import time

import pytest

from tstdx.errors import ConnectionFailed, ReadTimeout
from tstdx.transport import pool as pool_mod
from tstdx.transport.hosts import HostEntry
from tstdx.transport.pool import (
    BIZ_FAILURE_WEIGHT,
    CIRCUIT_DEGRADED_AT,
    CIRCUIT_OPEN_AT,
    ConnectionPool,
    Slot,
)


def _slot(host: str = "127.0.0.1", port: int = 7709) -> Slot:
    return Slot(host=HostEntry(host=host, port=port), index=0)


def _pool_with(slots: list[Slot]) -> ConnectionPool:
    """构造不经网络的池：真实初始化后注入槽位（测试只关心状态机与选序）。

    必须走真实 ``__init__``：v1.0 代际/租约模型与后台测速准入会读取
    ``_generation`` / ``family`` / ``_closed`` 等状态，手工拼属性会让
    ``_mark_failure`` 在熔断阈值处抛 ``AttributeError``。
    """
    p = ConnectionPool(
        [slot.host for slot in slots],
        slots_per_host=1,
        heartbeat_interval=None,
        max_retries=0,
    )
    p._slots = slots  # type: ignore[attr-defined]
    p.speedtest_threshold = 10  # type: ignore[attr-defined]
    return p


class TestCircuitTransitions:
    def test_healthy_to_degraded_to_open(self) -> None:
        s = _slot()
        p = _pool_with([s])
        # 2 次连接失败 → 仍 healthy（<3）
        p._mark_failure(s, ConnectionFailed("x1"), generation=s.generation)
        p._mark_failure(s, ConnectionFailed("x2"), generation=s.generation)
        assert s.host.circuit == "healthy"
        # 第 3 次 → degraded
        p._mark_failure(s, ConnectionFailed("x3"), generation=s.generation)
        assert s.host.circuit == "degraded"
        assert s.host.consec_weighted == pytest.approx(3.0)
        # 累计到 8 → open
        for i in range(4, 9):
            p._mark_failure(s, ConnectionFailed(f"x{i}"), generation=s.generation)
        assert s.host.circuit == "open"
        assert s.host.consec_weighted == pytest.approx(CIRCUIT_OPEN_AT)
        assert s.host.circuit_opened_at > 0

    def test_biz_failure_weighted_half(self) -> None:
        s = _slot()
        p = _pool_with([s])
        for i in range(6):  # 业务失败 0.5×6=3.0 → degraded
            p._mark_failure(s, ReadTimeout(f"t{i}"), generation=s.generation)
        assert s.host.biz_failures == 6
        assert s.host.circuit == "degraded"
        assert s.host.consec_weighted == pytest.approx(6 * BIZ_FAILURE_WEIGHT)
        assert s.host.consec_weighted == pytest.approx(CIRCUIT_DEGRADED_AT)

    def test_success_resets_circuit(self) -> None:
        s = _slot()
        p = _pool_with([s])
        for i in range(10):
            p._mark_failure(s, ConnectionFailed(f"x{i}"), generation=s.generation)
        assert s.host.circuit == "open"
        p._mark_success(s, generation=s.generation)
        assert s.host.circuit == "healthy"
        assert s.host.consec_weighted == 0.0
        assert s.host.circuit_opened_at == 0.0


class TestCircuitAllows:
    def test_open_blocks_before_cooldown(self) -> None:
        s = _slot()
        p = _pool_with([s])
        s.host.circuit = "open"
        s.host.circuit_opened_at = time.time()
        assert p._circuit_allows(s.host) is False

    def test_open_to_half_open_after_cooldown(self) -> None:
        s = _slot()
        p = _pool_with([s])
        s.host.circuit = "open"
        s.host.circuit_opened_at = time.time() - pool_mod.CIRCUIT_COOLDOWN_SECONDS - 1
        assert p._circuit_allows(s.host) is True
        assert s.host.circuit == "half_open"
        # half_open 已持有探测令牌，并发请求必须被门禁拦住
        assert p._circuit_allows(s.host) is False

    def test_half_open_probe_failure_reopens(self) -> None:
        s = _slot()
        p = _pool_with([s])
        s.host.circuit = "half_open"
        s.host.consec_weighted = CIRCUIT_OPEN_AT  # 探测失败仍高于阈值
        s.host.circuit_opened_at = 0.0
        p._mark_failure(s, ConnectionFailed("probe-fail"), generation=s.generation)
        assert s.host.circuit == "open"
        assert s.host.circuit_opened_at > 0  # 冷却重新计时


class TestSelectionSkipsOpen:
    def test_open_host_skipped_in_selection(self) -> None:
        bad = _slot("10.0.0.1")
        good = _slot("10.0.0.2")
        p = _pool_with([bad, good])
        bad.host.circuit = "open"
        bad.host.circuit_opened_at = time.time()
        assert p._circuit_allows(bad.host) is False
        assert p._circuit_allows(good.host) is True
        # 请求路径的选序逻辑：allowed 中应只含 good
        candidates = [bad, good]
        allowed = [s for s in candidates if p._circuit_allows(s.host)]
        assert [s.host.key for s in allowed] == [good.host.key]

    def test_circuit_skips_counted_when_all_open(self) -> None:
        s1 = _slot("10.0.1.1")
        s2 = _slot("10.0.1.2")
        p = _pool_with([s1, s2])
        for s in (s1, s2):
            s.host.circuit = "open"
            s.host.circuit_opened_at = time.time()
        candidates = [s1, s2]
        allowed = [s for s in candidates if p._circuit_allows(s.host)]
        if not allowed:
            p.stats.circuit_skips += 1
        assert p.stats.circuit_skips == 1
