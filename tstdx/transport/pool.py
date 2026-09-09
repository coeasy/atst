# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""连接池与故障转移（§12.3）。

槽位模型
--------
::

    Pool
     ├── Host A  ── Slot #0  ── TcpConnection
     │            ├ Slot #1
     │            └ Slot #2
     └── Host B  ── Slot #0
                  └ Slot #1

``Slot = hosts × slots_per_host``。取用时按「健康分 + 轮询」挑一个槽位；
失败则依次换槽 → 换主站 → 抛 :class:`AllHostsUnreachable`。

故障转移策略由异常的 :class:`~tstdx.errors.RetryAdvice` 驱动
（``retryable / backoff / switch_host / fallback_to_offline / fallback_to_web``），
而不是硬编码 if-else。这样新增一种错误只需在 ``errors.py`` 里声明建议，
传输层自动获得正确的行为。
"""

from __future__ import annotations

import contextlib
import logging
import random
import threading
import time
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, field
from typing import Any

from ..codec.framing import FrameSpec, ResponseFrame, build_request
from ..errors import (
    ALL_HOSTS_UNREACHABLE_NEXT_STEPS,
    AllHostsUnreachable,
    ConnectionClosed,
    ConnectionFailed,
    TdxError,
)
from ..observability import metrics
from ..protocol.commands import Family
from .base import DEFAULT_HEARTBEAT_CMD, TcpConnection
from .hosts import HostEntry
from .ratelimit import SessionRateLimiter

__all__ = [
    "Slot",
    "ConnectionPool",
    "PoolStats",
]

#: 传输域统一 logger（只获取，不配置 handler——配置交给宿主应用）。
_LOG = logging.getLogger("tstdx.transport")


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


# --------------------------------------------------------------------------- #
# 槽位
# --------------------------------------------------------------------------- #
@dataclass
class Slot:
    """一个主站上的一个连接位。

    C2：并发语义落在**连接级租约锁**（:attr:`TcpConnection._lock`，可重入），
    覆盖 request 全生命周期；:attr:`lock` 只负责建连/弃连与心跳调度的串行化。
    （原 ``busy`` 字段是未完成的 checkout 协议残迹，全仓无消费，已删除。）
    """

    host: HostEntry
    index: int
    conn: TcpConnection | None = None
    lock: threading.Lock = field(default_factory=threading.Lock)
    uses: int = 0
    #: Pool generation in which this slot was published.
    generation: int = 0
    #: Number of request/heartbeat leases using this slot.
    leases: int = 0
    #: Removed from the published pool, but kept alive until its leases return.
    retired: bool = False

    @property
    def key(self) -> str:
        return f"{self.host.key}#{self.index}"

    def __repr__(self) -> str:  # pragma: no cover
        state = "up" if self.conn is not None and self.conn.connected else "down"
        return f"<Slot {self.key} {state} uses={self.uses}>"


@dataclass
class PoolStats:
    requests: int = 0
    failures: int = 0
    retries: int = 0
    host_switches: int = 0
    frames: int = 0
    #: B4：因熔断 OPEN 而被跳过的选主站次数（观测熔断收益）
    circuit_skips: int = 0

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


# --------------------------------------------------------------------------- #
# 连接池
# --------------------------------------------------------------------------- #
class ConnectionPool:
    """同步连接池。

    Parameters
    ----------
    hosts:
        主站条目列表（按优先级排列）。
    slots_per_host:
        每台主站的连接数。
    timeout:
        读写超时（秒）。
    rate_limiter:
        本地限流器；None 表示不限流。
    heartbeat_interval:
        探活周期（秒）。``0`` 或 ``None`` 关闭探活。
    max_retries:
        单次请求的最大重试次数（含换主站）。
    """

    def __init__(
        self,
        hosts: Sequence[HostEntry],
        *,
        family: str = Family.STANDARD,
        slots_per_host: int = 4,
        timeout: float = 3.0,
        #: 建连超时与读写超时分离：默认 2s，让不可达主站在故障转移时
        #: 快速失败、立即跳到下一台候选（U1）。``None`` 表示沿用 ``timeout``。
        connect_timeout: float | None = 2.0,
        rate_limiter: SessionRateLimiter | None = None,
        heartbeat_interval: int | None = 30,
        heartbeat_cmd: int = DEFAULT_HEARTBEAT_CMD,
        max_retries: int = 3,
        spec: FrameSpec | None = None,
        use_tls: bool = False,
        keepalive: bool = True,
        handshake: bool | None = None,
        handshake_strict: bool = False,
        on_host_down: Callable[[HostEntry, BaseException], None] | None = None,
        speedtest_threshold: int = 3,
        #: M6 空闲槽位回收：连接闲置超过该秒数（默认 5 分钟）即关闭，
        #: 降低修复后 8 主机 × 4 槽的常驻连接数；下次使用惰性重建。
        idle_timeout: float = 300.0,
    ) -> None:
        if not hosts:
            raise ValueError("hosts 不能为空")
        self.family = family
        #: 是否握手。``None`` → 按协议族推断（标准族 / MAC 需要，扩展市场不需要）
        self.handshake = (
            family in (Family.STANDARD, Family.MAC) if handshake is None else bool(handshake)
        )
        self.handshake_strict = bool(handshake_strict)
        self.hosts: list[HostEntry] = list(hosts)
        self.slots_per_host = max(1, int(slots_per_host))
        self.timeout = float(timeout)
        self.connect_timeout = connect_timeout
        self.rate_limiter = rate_limiter
        self.heartbeat_interval = heartbeat_interval
        self.heartbeat_cmd = heartbeat_cmd
        self.max_retries = max(0, int(max_retries))
        self.spec = spec
        self.use_tls = use_tls
        self.keepalive = keepalive
        self.on_host_down = on_host_down
        self.stats = PoolStats()

        self._slots: list[Slot] = []
        for host in self.hosts:
            for i in range(self.slots_per_host):
                self._slots.append(Slot(host=host, index=i))
        self._rr = 0
        self._lock = threading.RLock()
        self._generation = 0
        self._retired_slots: list[Slot] = []
        self._closed = False
        self._hb: threading.Thread | None = None
        # R1: 连续连接失败计数与后台测速触发开关（冷启动时无排名文件，
        # 首请求若连续失败到阈值，后台跑一次测速写排名文件，下次启动受益）
        self._connect_failures = 0
        self._speedtest_triggered = False
        self._speedtest_snapshot: tuple[HostEntry, ...] = ()
        self.speedtest_threshold = max(1, int(speedtest_threshold))
        #: M6 空闲回收阈值（秒）；``<=0`` 表示不回收。
        self.idle_timeout = float(idle_timeout) if idle_timeout else 0.0
        if self.heartbeat_interval:
            self._start_heartbeat()

    # -- 构造便捷入口 ------------------------------------------------------- #
    @classmethod
    def from_config(
        cls,
        cfg: Any,
        *,
        family: str = Family.STANDARD,
        hosts: Sequence[HostEntry] | None = None,
        rate_limiter: SessionRateLimiter | None = None,
    ) -> ConnectionPool:
        """从 :class:`~tstdx.config.schema.Config` 构造。"""
        from ..config.schema import Config
        from .hosts import resolve_hosts

        cfg = cfg if isinstance(cfg, Config) else None
        core = cfg.core if cfg else None
        hcfg = cfg.hosts if cfg else None

        entries = (
            list(hosts)
            if hosts
            else resolve_hosts(
                servers=(hcfg.servers if hcfg else None),
                family=family,
                ranking_file=(hcfg.ranking_file if hcfg else None),
                use_ranking=bool(hcfg.auto_speedtest if hcfg else True),
                max_hosts=(hcfg.max_hosts if hcfg else 8),
            )
        )
        rl = rate_limiter
        if rl is None and cfg is not None:
            rl = SessionRateLimiter.from_config(cfg.rate_limit)
        return cls(
            entries,
            family=family,
            slots_per_host=(hcfg.slots_per_host if hcfg else 4),
            timeout=(core.timeout if core else 3.0),
            rate_limiter=rl,
            heartbeat_interval=(core.heartbeat_interval if core else 30),
            max_retries=(core.max_retries if core else 3),
            use_tls=bool(cfg.security.use_tls) if cfg else False,
        )

    # -- 槽位选择 ----------------------------------------------------------- #
    def _ordered_slots(self) -> list[Slot]:
        """按「主机健康分 + 轮询起点」排序，避免总打同一台。"""
        with self._lock:
            n = len(self._slots)
            if n == 0:
                return []
            start = self._rr % n
            self._rr = (self._rr + 1) % n
            ordered = self._slots[start:] + self._slots[:start]
        # 同一主机内保持原顺序（槽位轮转），主机间按 score 稳定排序
        return sorted(ordered, key=lambda s: s.host.score)

    def _get_conn_locked(self, slot: Slot) -> TcpConnection:
        """Return a connection while ``slot.lock`` is held."""
        if slot.retired:
            raise ConnectionClosed("槽位已从连接池代际中移除")
        if slot.conn is None:
            conn = TcpConnection(
                slot.host.host,
                slot.host.port,
                timeout=self.timeout,
                connect_timeout=self.connect_timeout,
                use_tls=self.use_tls,
                keepalive=self.keepalive,
                slot_id=slot.index,
                family=self.family,
                handshake=self.handshake,
                handshake_strict=self.handshake_strict,
            )
            if self.spec is not None:
                conn.spec = self.spec
            slot.conn = conn
        if not slot.conn.connected:
            slot.conn.connect()
        slot.uses += 1
        return slot.conn

    def _get_conn(self, slot: Slot) -> TcpConnection:
        with slot.lock:
            return self._get_conn_locked(slot)

    @contextlib.contextmanager
    def _lease(self, slot: Slot) -> Iterator[tuple[TcpConnection, int]]:
        """Hold a slot lease for the complete connection session.

        ``update_hosts`` can publish a new generation while a request is in
        flight, but it may not rebind or close this slot until the lease is
        returned.  The generation travels with the caller so late health
        results cannot mutate the newly published host entry.
        """
        conn, generation = self._acquire_lease(slot)
        try:
            yield conn, generation
        finally:
            self._release_lease(slot, conn)

    def _acquire_lease(self, slot: Slot) -> tuple[TcpConnection, int]:
        with slot.lock:
            conn = self._get_conn_locked(slot)
            slot.leases += 1
            return conn, slot.generation

    def _release_lease(self, slot: Slot, conn: TcpConnection) -> None:
        should_drop = False
        with slot.lock:
            slot.leases = max(0, slot.leases - 1)
            should_drop = slot.retired and slot.leases == 0
        if should_drop:
            self._drop(slot, expected=conn)

    def _drop(self, slot: Slot, *, expected: TcpConnection | None = None) -> None:
        with slot.lock:
            conn = slot.conn
            if conn is None or (expected is not None and conn is not expected):
                return
            # Close under the connection lease lock.  This is the same lock
            # used by request/ping, so update/close/heartbeat cannot cut a
            # socket in the middle of a frame exchange.
            conn_lock = getattr(conn, "_lock", None)
            if conn_lock is None:
                with contextlib.suppress(Exception):
                    conn.close()
            else:
                with conn_lock, contextlib.suppress(Exception):
                    conn.close()
            if slot.conn is conn:
                slot.conn = None

    def _slot_is_current(self, slot: Slot, generation: int | None) -> bool:
        if generation is None:
            return True
        with self._lock:
            return (
                not slot.retired
                and slot.generation == generation
                and slot in self._slots
                and not self._closed
            )

    def _mark_failure(
        self,
        slot: Slot,
        exc: BaseException,
        *,
        generation: int | None = None,
        conn: TcpConnection | None = None,
    ) -> None:
        current = self._slot_is_current(slot, generation)
        if current:
            with self._lock:
                host = slot.host
                was_half_open = host.circuit == "half_open" or host.circuit_probe_inflight
                host.failures += 1
                # R2 业务级信号：连接成功但请求/响应交换失败 → 单独记数降权
                if not isinstance(exc, ConnectionFailed):
                    host.biz_failures += 1
                # B4：HALF_OPEN 的任何失败都重开；其余状态按加权阈值推进。
                host.consec_weighted += (
                    1.0 if isinstance(exc, ConnectionFailed) else BIZ_FAILURE_WEIGHT
                )
                host.circuit_probe_inflight = False
                if was_half_open or host.consec_weighted >= CIRCUIT_OPEN_AT:
                    if host.circuit != "open" or host.circuit_opened_at == 0.0:
                        host.circuit_opened_at = time.time()
                    host.circuit = "open"
                    _LOG.warning(
                        "主站 %s 熔断 OPEN（连续失败加权 %.1f ≥ %.1f），后续请求将跳过",
                        host.key,
                        host.consec_weighted,
                        CIRCUIT_OPEN_AT,
                    )
                elif host.consec_weighted >= CIRCUIT_DEGRADED_AT:
                    host.circuit = "degraded"
                host.last_error = f"{type(exc).__name__}: {exc}"
            _LOG.warning("槽位 %s 标记失败: %s", slot.key, exc)
            if self.on_host_down is not None:
                with contextlib.suppress(Exception):
                    self.on_host_down(host, exc)
        # A late result from a retired generation is intentionally discarded;
        # it may still close only the exact connection that produced it.
        metrics.record_error(type(exc).__name__)
        self._drop(slot, expected=conn)
        # R1: 连接类失败累计到阈值时，后台触发一次测速写排名文件，
        # 让下次启动（乃至本次后续请求）按真实 RTT 排序主站。
        if isinstance(exc, ConnectionFailed):
            self._connect_failures += 1
            if self._connect_failures >= self.speedtest_threshold:
                self._trigger_background_speedtest()

    def _trigger_background_speedtest(self) -> None:
        """后台异步测速（仅触发一次，防抖）。"""
        if self._speedtest_triggered:
            return
        with self._lock:
            if self._speedtest_triggered:
                return
            self._speedtest_triggered = True
            # Snapshot both candidates and generation.  The worker must never
            # observe a list that update_hosts is replacing underneath it.
            snapshot = tuple(getattr(self, "hosts", [slot.host for slot in self._slots]))

        def _run() -> None:
            try:
                from .speedtest import speedtest_and_save

                _LOG.info("后台测速：对 %d 台候选主站刷新排名文件", len(snapshot))
                # This worker only updates the persistent probe ranking.  It
                # must not call update_hosts or overwrite live request health.
                speedtest_and_save(
                    snapshot,
                    family=getattr(self, "family", Family.STANDARD),
                    timeout=min(getattr(self, "timeout", 3.0), 2.0),
                )
            except Exception as exc:  # pragma: no cover - 后台任务失败不应影响主流程
                _LOG.warning("后台测速失败: %s", exc)

        t = threading.Thread(target=_run, name="tstdx-speedtest", daemon=True)
        t.start()

    def _mark_success(
        self,
        slot: Slot,
        *,
        generation: int | None = None,
        rtt_ms: float | None = None,
    ) -> None:
        if not self._slot_is_current(slot, generation):
            return
        with self._lock:
            host = slot.host
            host.failures = 0
            host.biz_failures = 0
            host.last_error = ""
            host.last_ok = time.time()
            if rtt_ms is not None:
                host.live_rtt_ms = max(0.0, float(rtt_ms))
                host.live_ok_at = host.last_ok
            # B4：成功即复位熔断（含 HALF_OPEN 探测成功 → HEALTHY）
            host.circuit = "healthy"
            host.circuit_probe_inflight = False
            host.consec_weighted = 0.0
            host.circuit_opened_at = 0.0

    def _circuit_allows(self, host: Any) -> bool:
        """B4：判断主站是否放行本次请求。

        * healthy / degraded：放行；
        * open：冷却未到期 → 拦截；冷却到期 → 转 HALF_OPEN 并领取唯一探测令牌；
        * half_open：已有探测时拦截其余请求，探测成功由
          :meth:`_mark_success` 复位，失败立即重开。
        """
        with self._lock:
            if host.circuit == "open":
                if time.time() - host.circuit_opened_at < CIRCUIT_COOLDOWN_SECONDS:
                    return False
                if host.circuit_probe_inflight:
                    return False
                host.circuit = "half_open"
                host.circuit_probe_inflight = True
                _LOG.info("主站 %s 熔断冷却到期，转 HALF_OPEN 放行单次探测", host.key)
                return True
            if host.circuit == "half_open":
                if host.circuit_probe_inflight:
                    return False
                host.circuit_probe_inflight = True
                return True
            return True

    # -- 请求 --------------------------------------------------------------- #
    def request(
        self,
        method: int,
        body: bytes = b"",
        *,
        timeout: float | None = None,
        retry: bool | None = None,
        compress: bool = False,
    ) -> ResponseFrame:
        """发一帧收一帧，自动重试 / 换槽 / 换主站。"""
        self._ensure_open()
        # 每次重试在「换主站」建议下都会推进到下一台候选；因此尝试次数至少要
        # 覆盖池内每台主站一次，否则会出现「池里还有可用主站却提前放弃」。
        max_attempts = (self.max_retries + 1) if (retry is None or retry) else 1
        if retry is None or retry:
            distinct_hosts = len({s.host.key for s in self._slots})
            max_attempts = max(max_attempts, distinct_hosts)
        last_exc: BaseException | None = None
        tried_hosts: list[str] = []
        started = time.perf_counter()

        for attempt in range(max_attempts):
            # T5：close() 与在飞请求竞态——每轮复查，池已关则立即终止，
            # 不再为已关闭的池重建连接（重建即泄漏：无人再去关闭它）。
            self._ensure_open()
            if self.rate_limiter is not None:
                self.rate_limiter.acquire()
            slots = self._ordered_slots()
            if not slots:
                break
            # P1：tried_hosts 生效于选序——优先选本轮调用尚未试过的主机，
            # 不再因「快主机刚失败但分数仍占优」而重复敲已失败主机；
            # 全部试过时退回 slots[0]（保持既有重试深度，零回归）。
            # B4：OPEN 主站直接跳过（不再吃 3s 超时）；全部 OPEN 时退回
            # 原选序兜底（不因熔断把「可用池为空」误判为「无主站」）。
            candidates = [s for s in slots if s.host.key not in tried_hosts] or slots
            allowed = [s for s in candidates if self._circuit_allows(s.host)]
            if not allowed:
                self.stats.circuit_skips += 1
                # Never bypass OPEN/HALF_OPEN admission when every candidate
                # is gated.  The old fallback sent traffic straight through
                # an OPEN circuit and defeated the cooldown contract.
                last_exc = ConnectionFailed("所有候选主站均处于熔断门禁")
                break
            slot = allowed[0]
            tried_hosts.append(slot.host.key)

            leased_conn: TcpConnection | None = None
            leased_generation: int | None = None
            attempt_started = time.perf_counter()
            try:
                with self._lease(slot) as (conn, generation):
                    leased_conn = conn
                    leased_generation = generation
                    frame = conn.request(method, body, timeout=timeout, compress=compress)
            except TdxError as exc:
                last_exc = exc
                self.stats.failures += 1
                self._mark_failure(
                    slot, exc, generation=leased_generation, conn=leased_conn
                )
                advice = exc.advice
                if attempt + 1 >= max_attempts or not advice.retryable:
                    break
                if advice.switch_host:
                    self.stats.host_switches += 1
                    _LOG.info("换主站：离开 %s（attempt=%d）", slot.host.key, attempt)
                    self._rotate_away(slot.host.key)
                self.stats.retries += 1
                if advice.backoff:
                    delay = advice.backoff * (2**attempt) * (0.75 + 0.5 * random.random())
                    _LOG.debug("退避 %.3fs 后重试（%s）", delay, type(exc).__name__)
                    time.sleep(delay)
                continue
            except Exception as exc:  # pragma: no cover - 兜底
                last_exc = exc
                self._mark_failure(
                    slot, exc, generation=leased_generation, conn=leased_conn
                )
                if attempt + 1 >= max_attempts:
                    break
                self.stats.retries += 1
                time.sleep(0.05 * (attempt + 1))
                continue

            self.stats.requests += 1
            self.stats.frames += 1
            self._mark_success(
                slot,
                generation=leased_generation,
                rtt_ms=(time.perf_counter() - attempt_started) * 1000.0,
            )
            metrics.record_request(
                command=f"0x{method:04x}", ok=True, duration=time.perf_counter() - started
            )
            return frame

        metrics.record_request(command=f"0x{method:04x}", ok=False)
        raise AllHostsUnreachable(
            f"所有主站均不可达（已尝试 {tried_hosts}）。\n{ALL_HOSTS_UNREACHABLE_NEXT_STEPS}",
            context={
                "hosts": tried_hosts,
                "method": hex(method),
                "attempts": max_attempts,
                "last_error": str(last_exc) if last_exc else None,
            },
            cause=last_exc,
        )

    def _rotate_away(self, host_key: str) -> None:
        """把轮询起点挪到该主机之后，实现"换主站"。"""
        with self._lock:
            for i, slot in enumerate(self._slots):
                if slot.host.key != host_key:
                    self._rr = i
                    return

    # -- bestip 热更新 ------------------------------------------------------ #
    @staticmethod
    def _inherit_runtime_health(old: HostEntry, new: HostEntry) -> None:
        """Merge process-local health into a fresh probe entry.

        ``new.rtt_ms`` is a speed-test observation and is intentionally kept;
        live request health is copied to its separate fields.  Circuit and
        failure state is also retained so bestip cannot silently heal a host
        that the real request path has just opened.
        """
        new.live_rtt_ms = old.live_rtt_ms
        new.live_ok_at = old.live_ok_at
        new.failures = max(old.failures, new.failures)
        new.biz_failures = max(old.biz_failures, new.biz_failures)
        if old.last_ok is not None:
            new.last_ok = old.last_ok
        if old.last_error:
            new.last_error = old.last_error
        new.consec_weighted = old.consec_weighted
        if old.circuit_probe_inflight:
            # A half-open probe from the retired generation must not leave the
            # new generation permanently half-open with no owner.
            new.circuit = "open"
            new.circuit_opened_at = time.time()
            new.circuit_probe_inflight = False
        else:
            new.circuit = old.circuit
            new.circuit_opened_at = old.circuit_opened_at
            new.circuit_probe_inflight = False

    def update_hosts(self, hosts: Sequence[HostEntry]) -> list[HostEntry]:
        """bestip 热更新：按新序重建主站池，复用现有连接（不重启、不中断）。

        保留旧池中仍在新列表里的槽位连接（按 ``host:port#index`` 匹配复用），
        并刷新其 ``HostEntry`` 对象（把测速得到的新 RTT / 失败计数带进池内）；
        丢弃不在新列表中的槽位（关闭连接）。适合 :meth:`~tstdx.client.TdxClient.bestip`
        运行时测速后重新排序主站优先级。

        Returns
        -------
        更新后的主站列表（即入参 ``hosts``）。
        """
        if not hosts:
            return list(self.hosts)
        to_close: list[Slot] = []
        with self._lock:
            new_hosts = [h for h in hosts if h is not None]
            self._generation += 1
            generation = self._generation
            old_by_key = {s.key: s for s in self._slots}
            new_slots: list[Slot] = []
            for host in new_hosts:
                for i in range(self.slots_per_host):
                    key = f"{host.key}#{i}"
                    old = old_by_key.get(key)
                    if old is not None:
                        with old.lock:
                            self._inherit_runtime_health(old.host, host)
                            if old.leases == 0 and not old.retired:
                                old.host = host  # idle slot: safe connection reuse
                                old.generation = generation
                                new_slots.append(old)
                            else:
                                # Do not mutate a host/connection visible to an
                                # in-flight request. Publish a fresh slot and
                                # retire the old one after its lease returns.
                                old.retired = True
                                self._retired_slots.append(old)
                                new_slots.append(
                                    Slot(host=host, index=i, generation=generation)
                                )
                    else:
                        new_slots.append(Slot(host=host, index=i, generation=generation))
            # Retire removed hosts.  An active request owns its connection
            # until the lease returns; an idle one can be closed now.
            new_keys = {s.key for s in new_slots}
            for s in self._slots:
                if s.key not in new_keys:
                    with s.lock:
                        s.retired = True
                        if s not in self._retired_slots:
                            self._retired_slots.append(s)
                        if s.leases == 0:
                            to_close.append(s)
            self.hosts = new_hosts
            self._slots = new_slots
            self._rr = 0
            _LOG.info(
                "bestip 热更新主站池：%d 台（复用 %d 槽）",
                len(new_hosts),
                len([s for s in new_slots if s.conn is not None]),
            )
        for slot in to_close:
            self._drop(slot)
        return new_hosts

    # -- 多帧请求 ----------------------------------------------------------- #
    def request_multi(
        self,
        method: int,
        body: bytes = b"",
        *,
        record_size: int | None = None,
        expect_count: bool = True,
        max_frames: int = 512,
        timeout: float | None = None,
    ) -> ResponseFrame:
        """读取分帧响应并合并为单个 :class:`ResponseFrame`。

        TDX 的部分命令（证券列表、财务数据等）在记录数较多时会**分多帧**下发：
        第一帧开头是 ``uint16 总数``，后续帧是纯记录续体，没有数量头。

        合并策略：把首帧去掉 2 字节数量头后的 payload 与后续帧 payload 拼接，
        重组成一个逻辑帧交给解析器，从而对上层屏蔽分帧细节。

        .. note:: **同连接多帧取舍（C6 书面说明）**
            本方法固定在**单一连接**上按帧序依次收完再合并，不利用多槽位并发。
            原因：TDX 分帧协议依赖 TCP 字节序——「首帧数量头 + 续体帧」必须
            来自同一请求上下文，跨连接乱序收取无法重组；且同一连接上并发两个
            分帧请求会让续体帧交织，无法归属。因此多帧命令的吞吐瓶颈在单连接
            带宽而非槽位数，属**已知且接受的取舍**；如需并发应在上层对
            ``security_list`` 按 start 偏移分段后用多个客户端实例（各自独立
            连接）并发请求，再在调用方合并。

        Parameters
        ----------
        record_size:
            单条记录字节数；``None`` 时从首帧推断 ``(len-2)//count``。
        expect_count:
            首帧是否带 ``uint16`` 数量头（默认 True）。
        """
        self._ensure_open()
        # 首帧与后续帧必须来自同一连接，因此这里直连一个槽位读完
        if self.rate_limiter is not None:
            self.rate_limiter.acquire()
        slot = self._ordered_slots()[0]
        conn: TcpConnection | None = None
        generation: int | None = None
        try:
            conn, generation = self._acquire_lease(slot)
        except TdxError as exc:
            self._mark_failure(slot, exc)
            raise

        first_exc: TdxError | None = None
        cont_exc: TdxError | None = None
        merged: ResponseFrame | None = None
        started = time.perf_counter()
        # C2 连接租约：首帧 + 全部续帧在同一临界区内读完，期间其它请求 /
        # 心跳无法插入同一 socket 的读写序（conn.request/read_frame 可重入）。
        # 注意锁序恒为 slot.lock → conn._lock：_mark_failure 需要 slot.lock，
        # 因此一律延迟到锁外执行。
        with conn._lock:
            try:
                first = conn.request(method, body, timeout=timeout)
            except TdxError as exc:
                first_exc = exc
            else:
                payload = first.payload
                if not expect_count or len(payload) < 2:
                    self.stats.requests += 1
                    self.stats.frames += 1
                    self._release_lease(slot, conn)
                    return first

                count = int.from_bytes(payload[:2], "little")
                chunk = payload[2:]
                chunks = [chunk]
                got = len(chunk)

                if count > 0 and record_size is None and len(chunk) > 0:
                    record_size = max(1, len(chunk) // count)
                need = count * (record_size or 1) if record_size else None

                frames_read = 1
                while count > 0 and need is not None and got < need and frames_read < max_frames:
                    try:
                        nxt = conn.read_frame()
                    except TdxError as exc:
                        cont_exc = exc
                        # 已读到的数据不丢弃：降级返回已合并部分并附告警
                        _LOG.warning(
                            "request_multi 续帧中断（%s: %s），降级返回已合并的 %d 块",
                            type(exc).__name__,
                            exc,
                            len(chunks),
                        )
                        break
                    frames_read += 1
                    self.stats.frames += 1
                    if not nxt.payload:
                        break
                    chunks.append(nxt.payload)
                    got += len(nxt.payload)

                joined = b"".join(chunks)
                merged = ResponseFrame(
                    magic=first.magic,
                    zip_flag=first.zip_flag,
                    seq=first.seq,
                    method=first.method,
                    zip_size=len(joined),
                    unzip_size=len(joined),
                    body=b"",
                    payload=joined,
                    header_raw=first.header_raw,
                )
                if got < (need or 0):
                    # 深审 M1：保留已收到的部分数据（与 async 版一致）。
                    # 旧实现清空 payload：① 与 header 尺寸自相矛盾；
                    # ② 把「部分数据」伪装成「无数据」——payload 是顺序
                    # 前缀拼接，前缀数据本身有效（每帧 seq/格式已校验），
                    # 丢弃只会浪费可用信息。不完整性由本告警暴露。
                    _LOG.warning(
                        "request_multi 数据不完整（need=%s got=%d），降级返回已合并的 %d 块",
                        need,
                        got,
                        len(chunks),
                    )
                self.stats.requests += 1
                if cont_exc is None:
                    self._mark_success(
                        slot,
                        generation=generation,
                        rtt_ms=(time.perf_counter() - started) * 1000.0,
                    )
                    metrics.record_request(
                        command=f"0x{method:04x}",
                        ok=True,
                        duration=time.perf_counter() - started,
                    )
                    self._release_lease(slot, conn)
                    return merged
                # 续帧失败：保持「降级返回部分数据」语义，弃连在锁外执行
                # （不调用 _mark_success——读中断不是成功，host 失败计数应保留）。

        assert conn is not None
        self._release_lease(slot, conn)
        if first_exc is not None:
            self._mark_failure(slot, first_exc, generation=generation, conn=conn)
            metrics.record_request(command=f"0x{method:04x}", ok=False)
            raise first_exc
        assert cont_exc is not None and merged is not None
        self._mark_failure(slot, cont_exc, generation=generation, conn=conn)
        metrics.record_request(command=f"0x{method:04x}", ok=False)
        return merged

    def iter_frames(
        self, method: int, body: bytes = b"", *, max_frames: int = 512
    ) -> Iterator[ResponseFrame]:
        """在同一条连接上逐帧迭代（供抓包 / Golden 采集使用）。

        发出请求后持续读帧，直到读超时或达到 ``max_frames``。
        调用方需自行判断何时停止（例如累计记录数已达首帧声明的总数）。

        T#2 整体租约：seq 分配、写帧、逐帧读取全程持连接锁（RLock 可重入，
        read_frame 内层取锁安全）——心跳与并发请求无法插入读写序；逐帧
        touch ``last_used``，心跳空闲判定对活跃流让路。消费方弃读（生成器
        被 close/GeneratorExit）时 socket 内残留未读帧、连接不可复用，弃连。
        """
        self._ensure_open()
        if self.rate_limiter is not None:
            self.rate_limiter.acquire()
        slot = self._ordered_slots()[0]
        conn, generation = self._acquire_lease(slot)
        failed: TdxError | None = None
        try:
            with conn._lock:
                frame_bytes, seq = build_request(method, body, seq=conn.next_seq(), spec=conn.spec)
                conn._sendall(frame_bytes)
                read = 0
                while read < max_frames:
                    try:
                        yield conn.read_frame()
                    except TdxError as exc:
                        failed = exc
                        break
                    read += 1
                    self.stats.frames += 1
                    conn.stats.last_used = time.time()
            if failed is not None:
                self._mark_failure(slot, failed, generation=generation, conn=conn)
            else:
                self._mark_success(slot, generation=generation)
        except GeneratorExit:
            self._drop(slot, expected=conn)
            raise
        finally:
            self._release_lease(slot, conn)

    # -- 探活 --------------------------------------------------------------- #
    def _start_heartbeat(self) -> None:
        interval = max(1, int(self.heartbeat_interval or 0))

        def loop() -> None:
            # 首个周期先做一次空闲回收（此时多半无在飞请求，安全）。
            self._sweep_idle()
            while not self._closed:
                time.sleep(interval)
                if self._closed:
                    break
                self._sweep_idle()
                for slot in list(self._slots):
                    if self._closed:
                        break
                    failed: BaseException | None = None
                    rtt: float | None = None
                    generation = slot.generation
                    # C2：ping 全程持 slot.lock 调度、conn._lock 执行——
                    # 在飞请求持有 conn._lock 时，心跳在锁外等待，天然串行，
                    # 心跳帧不会再插入请求的读写序。
                    with slot.lock:
                        conn = slot.conn
                        if conn is None or not conn.connected:
                            continue
                        idle = time.time() - (conn.stats.last_used or conn.stats.created_at)
                        if idle < interval:
                            continue
                        try:
                            rtt = conn.ping(self.heartbeat_cmd)
                        except Exception as exc:
                            failed = exc
                    if failed is not None:
                        # 必须**先释放 slot.lock 再标记失败**：_mark_failure →
                        # _drop 会重入 slot.lock（非可重入锁，旧实现在此自死锁，
                        # 心跳线程与该槽位全部请求永久卡死）。
                        self._mark_failure(slot, failed, generation=generation, conn=conn)
                    elif rtt is not None:
                        self._mark_success(slot, generation=generation, rtt_ms=rtt)

        self._hb = threading.Thread(target=loop, name="tstdx-heartbeat", daemon=True)
        self._hb.start()

    def _sweep_idle(self) -> None:
        """M6：回收闲置连接（``idle_timeout`` 秒未使用即关闭，惰性重建）。

        以每个槽位的 ``last_used`` 判定。在飞请求会不断刷新 ``last_used``
        （见 :meth:`request` / :meth:`iter_frames` 成功路径），不会被误回收。
        """
        if self.idle_timeout <= 0:
            return
        cutoff = time.time() - self.idle_timeout
        reclaimed = 0
        for slot in list(self._slots):
            with slot.lock:
                conn = slot.conn
                if (
                    conn is None
                    or not conn.connected
                    or slot.leases
                    or slot.retired
                ):
                    continue
                last = conn.stats.last_used or conn.stats.created_at
                if last and last < cutoff:
                    conn_lock = getattr(conn, "_lock", None)
                    if conn_lock is None:
                        with contextlib.suppress(Exception):
                            conn.close()
                    else:
                        with conn_lock, contextlib.suppress(Exception):
                            conn.close()
                    if slot.conn is conn:
                        slot.conn = None
                    reclaimed += 1
        if reclaimed:
            _LOG.info("M6 空闲回收：关闭 %d 个闲置连接（%ds 未使用）", reclaimed, self.idle_timeout)

    # -- 生命周期 ----------------------------------------------------------- #
    def _ensure_open(self) -> None:
        if self._closed:
            raise ConnectionClosed("连接池已关闭")

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._generation += 1
            slots = list(self._slots) + list(self._retired_slots)
            for slot in slots:
                with slot.lock:
                    slot.retired = True
        _LOG.debug("连接池关闭（slots=%d）", len(slots))
        for slot in slots:
            self._drop(slot)

    def __enter__(self) -> ConnectionPool:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- 诊断 --------------------------------------------------------------- #
    def health(self) -> dict[str, Any]:
        return {
            "family": self.family,
            "hosts": [
                {
                    "host": h.host,
                    "port": h.port,
                    "rtt_ms": h.rtt_ms,
                    "live_rtt_ms": h.live_rtt_ms,
                    "failures": h.failures,
                    "last_error": h.last_error,
                    "score": h.score,
                    "circuit": h.circuit,
                }
                for h in self.hosts
            ],
            "slots": [
                {
                    "slot": s.key,
                    "connected": bool(s.conn and s.conn.connected),
                    "uses": s.uses,
                    "generation": s.generation,
                    "leases": s.leases,
                }
                for s in self._slots
            ],
            "stats": self.stats.to_dict(),
        }
