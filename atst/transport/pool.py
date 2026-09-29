# Copyright (c) 2026 atst contributors
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

故障转移策略由异常的 :class:`~atst.errors.RetryAdvice` 驱动
（``retryable / backoff / switch_host / fallback_to_offline / fallback_to_web``），
而不是硬编码 if-else。这样新增一种错误只需在 ``errors.py`` 里声明建议，
传输层自动获得正确的行为。
"""

from __future__ import annotations

import contextlib
import logging
import math
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
    ConfigError,
    ConnectionClosed,
    ConnectionFailed,
    FramingError,
    TdxError,
)
from ..observability import metrics
from ..protocol.commands import Family
from ._validation import (
    canonical_family_hosts,
    validate_common_pool_options,
    validate_sync_pool_only_options,
)
from .base import DEFAULT_HEARTBEAT_CMD, TcpConnection
from .hosts import (
    HostEntry,
    RankingStore,
    new_endpoint_entry,
    next_generation_host,
    validate_host_updates,
)
from .ratelimit import SessionRateLimiter

__all__ = [
    "Slot",
    "ConnectionPool",
    "PoolStats",
    "pool_settings_from_config",
]

#: 传输域统一 logger（只获取，不配置 handler——配置交给宿主应用）。
_LOG = logging.getLogger("atst.transport")


def pool_settings_from_config(cfg: Any) -> dict[str, Any]:
    """把 :class:`~atst.config.schema.Config` 翻译成 ``ConnectionPool`` 构造参数。

    这是配置面到传输面的**唯一**翻译点：单一内核的执行器与任何手工建池的
    调用方都走这里，因此同一个 TOML 键不可能在两处含义漂移。
    ``cfg is None`` 表示"无配置"，返回空 dict（即使用连接池自身默认值）；
    传入非 ``Config`` 对象一律 fail closed，绝不静默退回默认值。
    """
    from ..config.schema import Config

    if cfg is None:
        return {}
    if not isinstance(cfg, Config):
        raise ConfigError(
            f"pool_settings_from_config 需要 Config 或 None；收到 {type(cfg).__name__}",
            context={"source": "pool_settings_from_config", "value_type": type(cfg).__name__},
        )
    return {
        "slots_per_host": cfg.hosts.slots_per_host,
        "timeout": cfg.core.timeout,
        "heartbeat_interval": cfg.core.heartbeat_interval,
        "max_retries": cfg.core.max_retries,
        "rate_limiter": SessionRateLimiter.from_config(cfg.rate_limit),
        "use_tls": cfg.security.use_tls,
    }


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
# 入参校验（同步/异步池共用同一套 fail-closed 口径）
# --------------------------------------------------------------------------- #
def _require_positive_frame_limit(value: Any, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ConfigError(f"{field} 必须是正整数，收到 {value!r}")
    return value


def _require_bool_option(value: Any, *, field: str) -> bool:
    if not isinstance(value, bool):
        raise ConfigError(f"{field} 必须是 bool，收到 {value!r}")
    return value


def _require_optional_bool_option(value: Any, *, field: str) -> bool | None:
    if value is not None and not isinstance(value, bool):
        raise ConfigError(f"{field} 必须是 bool 或 None，收到 {value!r}")
    return value


def _require_request_timeout(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigError(f"timeout 必须是正有限数值或 None，收到 {value!r}")
    timeout = float(value)
    if not math.isfinite(timeout) or timeout <= 0:
        raise ConfigError(f"timeout 必须是正有限数值或 None，收到 {value!r}")
    return timeout


#: 换主站退避的单步上限（秒）——与 web 面 :data:`atst.web._base_retry.MAX_BACKOFF_SECONDS` 同值。
#: 流式面的 :class:`~atst.streaming.engine.ReconnectPolicy` 不在此列：它等的是
#: "服务回来"，上限 30 秒是那条链路的语义，不是这里要抄的口径。
MAX_RETRY_BACKOFF_SECONDS = 8.0

#: ``ConnectionPool.close()`` 等心跳线程退出的上限（秒）。
#: 睡眠本身由 :attr:`ConnectionPool._hb_wakeup` 兑现，``close()`` 一置位它就醒；
#: 这 2 秒只留给"醒来时正卡在一次 ``ping`` 上"的情况。等不到不撒谎：日志点名它还在跑。
_HEARTBEAT_JOIN_SECONDS = 2.0

#: ``close()`` 等后台测速线程退出的上限（秒）。这不是"保证收干净"：一次全表测速的
#: 真实长度由 :func:`atst.transport.speedtest.speedtest` 自己的并发度决定（可达数秒），
#: 而停机路径不该替它买单。这 0.5 秒只留给"其实早就跑完了"的常见情形，等不到就在日志
#: 里点名它还在校——它写不回观测（``_run`` 两处都复查 :attr:`ConnectionPool._closed`），
#: 但在那之前仍会占着套接字拨号（第 26 轮 F-97）。
_SPEEDTEST_JOIN_SECONDS = 0.5


def retry_backoff_delay(base: float, attempt: int) -> float:
    """指数退避 + 抖动，封顶 :data:`MAX_RETRY_BACKOFF_SECONDS`（同步/异步池共用）。

    为什么必须封顶：``max_attempts`` 会随主站数放大到"每台至少试一次"，于是
    ``attempt`` 不是重试次数而是**已走过的主站数**。不封顶时 8 台主站的一次失败
    请求要睡 1+2+4+8+16+32+64 ≈ 127 秒（第 25 轮真机量到 159 秒整次请求），
    32 台是 ``2**31`` 秒——一次"5 秒超时"的调用方请求实际变成无人可预告的等待。
    """
    return min(base * (2**attempt), MAX_RETRY_BACKOFF_SECONDS) * (0.75 + 0.5 * random.random())


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
        # --- fail-closed contract validation (merged from hardening) --------- #
        # 1. hosts 归一化 + family 匹配 + 去重（pool 持有快照，隔离外部修改）
        self.hosts = canonical_family_hosts(hosts, family=family)
        self.family = family
        # 2. 公共 options 校验
        validate_common_pool_options(
            dict(
                slots_per_host=slots_per_host,
                timeout=timeout,
                connect_timeout=connect_timeout,
                rate_limiter=rate_limiter,
                heartbeat_interval=heartbeat_interval,
                heartbeat_cmd=heartbeat_cmd,
                max_retries=max_retries,
                use_tls=use_tls,
                handshake=handshake,
                handshake_strict=handshake_strict,
                idle_timeout=idle_timeout,
            ),
            async_pool=False,
        )
        # 3. sync-only options 校验
        validate_sync_pool_only_options(
            dict(
                keepalive=keepalive,
                speedtest_threshold=speedtest_threshold,
                on_host_down=on_host_down,
            )
        )

        #: 是否握手。``None`` → 按协议族推断（标准族 / MAC 需要，扩展市场不需要）
        self.handshake = (
            family in (Family.STANDARD, Family.MAC) if handshake is None else bool(handshake)
        )
        self.handshake_strict = bool(handshake_strict)
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
        #: 心跳线程的睡眠用 Event 而不是 ``time.sleep``：``close()`` 必须能把一个
        #: 正在睡 ``heartbeat_interval``（默认 30 秒）的线程当场叫醒并join回来，
        #: 否则每个已关闭的池都留下一条最长 30 秒的孤儿线程（第 26 轮 F-87）。
        self._hb_wakeup = threading.Event()
        # R1: 连续连接失败计数与后台测速触发开关（冷启动时无排名文件，
        # 首请求若连续失败到阈值，后台跑一次测速写排名文件，下次启动受益）
        self._connect_failures = 0
        self._speedtest_triggered = False
        #: 在跑的后台测速线程句柄（每代最多一条，起跑时顺手清掉已退出的）。
        #: 早先这一格躺的是 ``_speedtest_snapshot``——写进去再没人读（真快照活在闭包
        #: 局部），而线程本体连句柄都没留，于是 ``close()`` 追不上它：已关闭的池会留下
        #: 一条继续向主站拨号的线程（第 26 轮 F-97/F-98）。
        self._speedtest_threads: list[threading.Thread] = []
        self.speedtest_threshold = max(1, int(speedtest_threshold))
        #: M6 空闲回收阈值（秒）；``<=0`` 表示不回收。
        self.idle_timeout = float(idle_timeout) if idle_timeout else 0.0
        #: 回收线程与探活线程是同一条，启动条件覆盖两个 knob（见 F-88）。
        if self.heartbeat_interval or self.idle_timeout > 0:
            self._start_heartbeat()

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
            self._prune_retired_slots()

    def _prune_retired_slots(self) -> int:
        """把已经排空的退役槽位从 :attr:`_retired_slots` 里摘掉。

        这张表曾经**只进不出**（第 26 轮 F-99）：每次 bestip 热更新都把上一代槽位挂上去，直到
        ``close()`` 才整体读一次——长期跑 ``update_hosts`` 的池会攒下整条历史的
        ``Slot`` 对象（每个自带一把锁），``close()`` 的遍历也跟着变长。异步池同名
        表一样只进不出（它的 ``_drain_retired_slots(slots)`` 只负责关连接，从不缩短
        这张表），所以两边在这一刀之后各有一份同判据的 ``_prune_retired_slots``。
        判据是"零租约 + 连接已放下"：退役槽位不可能再拿到新租约
        （:meth:`_get_conn_locked` 对 ``retired`` 直接抛 ``ConnectionClosed``），
        ``slot.conn`` 也只会被 :meth:`_drop` 置空，所以这里不取 ``slot.lock``
        也不会误判成"还能用"。
        """
        with self._lock:
            if not self._retired_slots:
                return 0
            kept = [slot for slot in self._retired_slots if slot.leases or slot.conn is not None]
            removed = len(self._retired_slots) - len(kept)
            if removed:
                self._retired_slots = kept
            return removed

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

    def _slot_is_current(self, slot: Slot, generation: int) -> bool:
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
        generation: int,
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
        """后台测速：只把观测写回它出发时的那一代。"""
        if self._closed or self._speedtest_triggered:
            return
        with self._lock:
            if self._closed or self._speedtest_triggered:
                return
            self._speedtest_triggered = True
            generation = self._generation
            snapshot = tuple(self.hosts)
            family = self.family
            timeout = min(self.timeout, 2.0)

        def _run() -> None:
            try:
                if self._closed:
                    # 出发与起跑之间可能已经 close()：那时这条池不再服务任何调用方，
                    # 照拨整表主站就是"停机不停工"（第 26 轮 F-97）。
                    return
                from .speedtest import _apply_probe_observations, rank_hosts, speedtest

                results = speedtest(
                    snapshot,
                    family=family,
                    timeout=timeout,
                )
                with self._lock:
                    if self._closed or self._generation != generation:
                        _LOG.info(
                            "丢弃旧 generation 后台测速：started=%d current=%d",
                            generation,
                            self._generation,
                        )
                        return
                    _apply_probe_observations(snapshot, results, family=family)
                    if family == Family.STANDARD:
                        RankingStore().update(rank_hosts(results))
            except Exception as exc:  # pragma: no cover - 优化路径必须 fail-open
                _LOG.warning("后台测速失败: %s", exc)

        thread = threading.Thread(target=_run, name="atst-speedtest", daemon=True)
        # 句柄必须在 start 之前登记：``close()`` 靠它点名还在校的线程，登记时也顺手把
        # 已退出的旧句柄清掉——一代一条，不留只进不出的线程历史（F-97）。
        with self._lock:
            self._speedtest_threads = [t for t in self._speedtest_threads if t.is_alive()]
            self._speedtest_threads.append(thread)
        thread.start()

    def _mark_success(
        self,
        slot: Slot,
        *,
        generation: int,
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

    def _select_allowed_slot(self, *, exclude_hosts: set[str] | None = None) -> Slot | None:
        """按选序逐个尝试领取唯一的熔断探测令牌。"""
        slots = self._ordered_slots()
        if exclude_hosts:
            preferred = [slot for slot in slots if slot.host.key not in exclude_hosts]
            candidates = preferred or slots
        else:
            candidates = slots
        for candidate in candidates:
            if self._circuit_allows(candidate.host):
                return candidate
        return None

    def _release_probe_token(self, slot: Slot, generation: int) -> None:
        """归还 HALF_OPEN 令牌，不伪造任何主站健康证据。"""
        if not self._slot_is_current(slot, generation):
            return
        with self._lock:
            if slot.host.circuit == "half_open":
                slot.host.circuit_probe_inflight = False

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
        """发一帧收一帧：单次 HALF_OPEN 放行 + 自动重试 / 换槽 / 换主站。"""
        retry_enabled = _require_optional_bool_option(retry, field="retry")
        compress_enabled = _require_bool_option(compress, field="compress")
        request_timeout = _require_request_timeout(timeout)
        self._ensure_open()
        max_attempts = (self.max_retries + 1) if (retry_enabled is None or retry_enabled) else 1
        if retry_enabled is None or retry_enabled:
            distinct_hosts = len({slot.host.key for slot in self._slots})
            max_attempts = max(max_attempts, distinct_hosts)
        last_exc: BaseException | None = None
        tried_hosts: list[str] = []
        started = time.perf_counter()
        pending_backoff = 0.0

        for attempt in range(max_attempts):
            self._ensure_open()
            if self.rate_limiter is not None:
                #: 同 request()：限流等待吃调用方预算，不留给无界等待的余地。
                self.rate_limiter.acquire(timeout=request_timeout)
            slot = self._select_allowed_slot(exclude_hosts=set(tried_hosts))
            if slot is None:
                self.stats.circuit_skips += 1
                last_exc = ConnectionFailed("所有候选主站均处于熔断门禁")
                break
            # 冷却只对"刚失败过的那台"兑现：选序排除已试主站，所以拿到一台没试过
            # 的机器时，上一轮的退避已经不该再花调用方的时间——睡 64 秒再去拨一个
            # 从没拨过的号码，延长的只是等待（第 25 轮真机：8 台主站的一次失败请求
            # 159.4 秒，日志里七条退避相加 = 121.143 秒纯 sleep，而回退路径首台
            # 131 毫秒就取回了数据）。
            if pending_backoff and slot.host.key in tried_hosts:
                _LOG.debug("回到已试主站 %s，退避 %.3fs", slot.host.key, pending_backoff)
                time.sleep(pending_backoff)
            pending_backoff = 0.0
            tried_hosts.append(slot.host.key)

            leased_conn: TcpConnection | None = None
            leased_generation: int | None = None
            attempt_started = time.perf_counter()
            try:
                with self._lease(slot) as (conn, generation):
                    leased_conn = conn
                    leased_generation = generation
                    frame = conn.request(
                        method,
                        body,
                        timeout=request_timeout,
                        compress=compress_enabled,
                    )
            except TdxError as exc:
                last_exc = exc
                self.stats.failures += 1
                self._mark_failure(slot, exc, generation=slot.generation, conn=leased_conn)
                advice = exc.advice
                if attempt + 1 >= max_attempts or not advice.retryable:
                    break
                if advice.switch_host:
                    self.stats.host_switches += 1
                    _LOG.info("换主站：离开 %s（attempt=%d）", slot.host.key, attempt)
                    self._rotate_away(slot.host.key)
                self.stats.retries += 1
                if advice.backoff:
                    pending_backoff = retry_backoff_delay(advice.backoff, attempt)
                continue
            except Exception as exc:
                last_exc = exc
                self._mark_failure(slot, exc, generation=slot.generation, conn=leased_conn)
                if attempt + 1 >= max_attempts:
                    break
                self.stats.retries += 1
                pending_backoff = 0.05 * (attempt + 1)
                continue
            except BaseException:
                # KeyboardInterrupt/SystemExit 属于控制流：租约已由上下文管理器归还，
                # 只释放本请求的 HALF_OPEN 令牌，原样传播。
                self._release_probe_token(slot, slot.generation)
                raise

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

    def update_hosts(self, hosts: Sequence[HostEntry]) -> list[HostEntry]:
        """按新序发布一代主站，复用空闲槽位、退役在飞槽位。

        ``bestip`` 测速后调用。整段替换在池锁内完成，锁序恒为 pool._lock →
        slot.lock（与 :meth:`_drop` 一致），避免与请求路径的弃连判定互相死锁。
        发布的 ``HostEntry`` 是池自有副本：只采纳探测层证据，运行期健康与身份
        由 :func:`~atst.transport.hosts.next_generation_host` 延续。
        """
        self._ensure_open()
        if not hosts:
            return list(self.hosts)
        observed = validate_host_updates(hosts, family=self.family)
        to_close: list[Slot] = []

        with self._lock:
            self._ensure_open()
            self._generation += 1
            generation = self._generation
            self._speedtest_triggered = False
            old_slots = list(self._slots)
            old_by_slot_key = {slot.key: slot for slot in old_slots}
            old_host_by_key = {slot.host.key: slot.host for slot in old_slots}

            published_hosts: list[HostEntry] = []
            for item in observed:
                old_host = old_host_by_key.get(item.key)
                published_hosts.append(
                    next_generation_host(old_host, item)
                    if old_host is not None
                    else new_endpoint_entry(item, family=self.family)
                )

            new_slots: list[Slot] = []
            for host in published_hosts:
                for index in range(self.slots_per_host):
                    key = f"{host.key}#{index}"
                    old = old_by_slot_key.get(key)
                    if old is None:
                        new_slots.append(Slot(host=host, index=index, generation=generation))
                        continue
                    with old.lock:
                        if old.leases == 0 and not old.retired:
                            old.host = host
                            old.generation = generation
                            new_slots.append(old)
                        else:
                            old.retired = True
                            if old not in self._retired_slots:
                                self._retired_slots.append(old)
                            new_slots.append(Slot(host=host, index=index, generation=generation))

            new_keys = {slot.key for slot in new_slots}
            for old in old_slots:
                if old.key in new_keys:
                    continue
                with old.lock:
                    old.retired = True
                    if old not in self._retired_slots:
                        self._retired_slots.append(old)
                    if old.leases == 0:
                        to_close.append(old)

            self.hosts = published_hosts
            self._slots = new_slots
            self._rr = 0
            _LOG.info(
                "bestip 热更新主站池：%d 台（复用 %d 槽，generation=%d）",
                len(published_hosts),
                len([slot for slot in new_slots if slot.conn is not None]),
                generation,
            )

        for slot in to_close:
            self._drop(slot)
        self._prune_retired_slots()
        return published_hosts

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
        """多帧合并请求：不绕熔断、不把截断的脏 socket 归还池。"""
        frame_limit = _require_positive_frame_limit(max_frames, field="max_frames")
        if record_size is not None:
            _require_positive_frame_limit(record_size, field="record_size")
        expect_count_enabled = _require_bool_option(expect_count, field="expect_count")
        request_timeout = _require_request_timeout(timeout)
        self._ensure_open()
        if self.rate_limiter is not None:
            #: 限流等待吃调用方预算：它发生在 socket 收发之前，不吃就绕过了 timeout。
            self.rate_limiter.acquire(timeout=request_timeout)
        slot = self._select_allowed_slot()
        if slot is None:
            self.stats.circuit_skips += 1
            raise AllHostsUnreachable(
                f"所有主站均处于熔断门禁。\n{ALL_HOSTS_UNREACHABLE_NEXT_STEPS}",
                context={"hosts": [], "method": hex(method), "attempts": 0},
                cause=ConnectionFailed("所有候选主站均处于熔断门禁"),
            )

        conn: TcpConnection | None = None
        generation: int | None = None
        try:
            conn, generation = self._acquire_lease(slot)
        except TdxError as exc:
            #: 租约没建立起来，就没有"租约代际"可归属；槽位对象在手，按它自己的代际推进。
            self._mark_failure(slot, exc, generation=slot.generation, conn=conn)
            raise
        except BaseException:
            self._release_probe_token(slot, slot.generation)
            raise

        first_exc: TdxError | None = None
        stream_exc: TdxError | None = None
        merged: ResponseFrame | None = None
        started = time.perf_counter()
        try:
            with conn._lock:
                try:
                    first = conn.request(method, body, timeout=request_timeout)
                except TdxError as exc:
                    first_exc = exc
                else:
                    self.stats.frames += 1
                    payload = first.payload
                    if not expect_count_enabled or len(payload) < 2:
                        merged = first
                    else:
                        count = int.from_bytes(payload[:2], "little")
                        chunks = [payload[2:]]
                        got = len(chunks[0])
                        if count > 0 and record_size is None and got > 0:
                            record_size = max(1, got // count)
                        need = count * (record_size or 1) if record_size else None
                        frames_read = 1
                        while (
                            count > 0
                            and need is not None
                            and got < need
                            and frames_read < frame_limit
                        ):
                            try:
                                nxt = conn.read_frame()
                            except TdxError as exc:
                                stream_exc = exc
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
                        if need is not None and got < need and stream_exc is None:
                            stream_exc = FramingError(
                                "request_multi 响应截断: "
                                f"need={need} got={got} max_frames={frame_limit}",
                                context={
                                    "method": hex(method),
                                    "need": need,
                                    "got": got,
                                    "max_frames": frame_limit,
                                },
                            )
        except BaseException:
            self._release_lease(slot, conn)
            self._release_probe_token(slot, generation)
            self._drop(slot, expected=conn)
            raise

        self._release_lease(slot, conn)
        if first_exc is not None:
            self._mark_failure(slot, first_exc, generation=generation, conn=conn)
            metrics.record_request(command=f"0x{method:04x}", ok=False)
            raise first_exc

        assert merged is not None
        self.stats.requests += 1
        if stream_exc is not None:
            _LOG.warning("request_multi 数据不完整，返回已收到前缀并弃连: %s", stream_exc)
            self._mark_failure(slot, stream_exc, generation=generation, conn=conn)
            metrics.record_request(
                command=f"0x{method:04x}",
                ok=False,
                duration=time.perf_counter() - started,
            )
            return merged

        self._mark_success(
            slot,
            generation=generation,
            rtt_ms=(time.perf_counter() - started) * 1000.0,
        )
        metrics.record_request(
            command=f"0x{method:04x}", ok=True, duration=time.perf_counter() - started
        )
        return merged

    def iter_frames(
        self, method: int, body: bytes = b"", *, max_frames: int = 512
    ) -> Iterator[ResponseFrame]:
        """逐帧迭代：走同一套熔断放行 + 保守的连接复用判定。"""
        frame_limit = _require_positive_frame_limit(max_frames, field="max_frames")
        self._ensure_open()
        if self.rate_limiter is not None:
            self.rate_limiter.acquire()
        slot = self._select_allowed_slot()
        if slot is None:
            self.stats.circuit_skips += 1
            raise AllHostsUnreachable(
                f"所有主站均处于熔断门禁。\n{ALL_HOSTS_UNREACHABLE_NEXT_STEPS}",
                context={"hosts": [], "method": hex(method), "attempts": 0},
                cause=ConnectionFailed("所有候选主站均处于熔断门禁"),
            )

        conn: TcpConnection | None = None
        generation: int | None = None
        try:
            conn, generation = self._acquire_lease(slot)
        except TdxError as exc:
            #: 租约没建立起来，就没有"租约代际"可归属；槽位对象在手，按它自己的代际推进。
            self._mark_failure(slot, exc, generation=slot.generation, conn=conn)
            raise
        except BaseException:
            self._release_probe_token(slot, slot.generation)
            raise

        failed: TdxError | None = None
        frames_read = 0
        try:
            with conn._lock:
                frame_bytes, _ = build_request(
                    method,
                    body,
                    seq=conn.next_seq(),
                    spec=conn.spec,
                )
                conn._sendall(frame_bytes)
                while frames_read < frame_limit:
                    try:
                        frame = conn.read_frame()
                    except TdxError as exc:
                        failed = exc
                        break
                    frames_read += 1
                    self.stats.frames += 1
                    conn.stats.last_used = time.time()
                    yield frame
        except BaseException:
            self._release_lease(slot, conn)
            self._release_probe_token(slot, generation)
            self._drop(slot, expected=conn)
            raise

        self._release_lease(slot, conn)
        if failed is not None:
            self._mark_failure(slot, failed, generation=generation, conn=conn)
            return

        self._mark_success(slot, generation=generation)
        # 命中调用方上限并不能证明服务端流已结束：复用这条 socket 会把未读的
        # 旧帧暴露给下一个请求，因此不计失败地直接关掉。
        if frames_read >= frame_limit:
            self._drop(slot, expected=conn)

    # -- 探活 --------------------------------------------------------------- #
    def _start_heartbeat(self) -> None:
        """心跳线程同样受 OPEN/HALF_OPEN 放行门禁约束。

        这个线程同时是 **M6 空闲回收** 的唯一执行方，所以它的启动条件不能只看
        ``heartbeat_interval``：把心跳调成 0（合法取值，意为"不主动探活"）曾经把
        空闲回收一起关掉，``idle_timeout`` 就此变成一句空话（第 26 轮 F-88）。
        现在两种 knob 各自成立：只开回收时线程照跑，只是不探活。
        """
        probe_interval = max(1, int(self.heartbeat_interval or 0))
        #: 唤醒节奏：心跳开着跟心跳，只开回收时跟回收阈值的四分之一（不早于 1 秒）。
        tick = float(probe_interval) if self.heartbeat_interval else max(1.0, self.idle_timeout / 4)

        def loop() -> None:
            self._sweep_idle()
            while not self._closed:
                if self._hb_wakeup.wait(tick):
                    break
                if self._closed:
                    break
                self._sweep_idle()
                if not self.heartbeat_interval:
                    continue
                for slot in list(self._slots):
                    if self._closed:
                        break
                    if not self._circuit_allows(slot.host):
                        continue
                    failed: BaseException | None = None
                    rtt: float | None = None
                    generation = slot.generation
                    try:
                        with slot.lock:
                            conn = slot.conn
                            if conn is None or not conn.connected:
                                self._release_probe_token(slot, generation)
                                continue
                            idle = time.time() - (conn.stats.last_used or conn.stats.created_at)
                            if idle < probe_interval:
                                self._release_probe_token(slot, generation)
                                continue
                            try:
                                rtt = conn.ping(self.heartbeat_cmd)
                            except Exception as exc:
                                failed = exc
                    except BaseException:
                        self._release_probe_token(slot, generation)
                        raise
                    if failed is not None:
                        self._mark_failure(slot, failed, generation=generation, conn=conn)
                    elif rtt is not None:
                        self._mark_success(slot, generation=generation, rtt_ms=rtt)

        self._hb = threading.Thread(target=loop, name="atst-heartbeat", daemon=True)
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
                if conn is None or not conn.connected or slot.leases or slot.retired:
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
            heartbeat = self._hb
            speedtests = list(self._speedtest_threads)
            self._speedtest_threads.clear()
        self._hb_wakeup.set()
        _LOG.debug("连接池关闭（slots=%d）", len(slots))
        for slot in slots:
            self._drop(slot)
        # join 在 ``self._lock`` 之外：心跳线程自己要走 ``_slot_is_current``，
        # 握着池锁等它就是把锁交给一个正在被我们等的线程——那是死锁，不是慢。
        if heartbeat is not None and heartbeat is not threading.current_thread():
            heartbeat.join(timeout=_HEARTBEAT_JOIN_SECONDS)
            if heartbeat.is_alive():
                _LOG.warning(
                    "心跳线程在 %.1fs 内未退出（interval=%ss）：池已标记关闭，"
                    "该线程只会在下一轮唤醒自检时退出",
                    _HEARTBEAT_JOIN_SECONDS,
                    self.heartbeat_interval,
                )
        # 后台测速线程：它在池锁外自己拨号，等不到也只说明它还在路上——观测已经作废，
        # 但套接字还开着，所以这条必须说出来而不是沉默（F-97）。
        for probe_thread in speedtests:
            if probe_thread is threading.current_thread():
                continue
            probe_thread.join(timeout=_SPEEDTEST_JOIN_SECONDS)
            if probe_thread.is_alive():
                _LOG.info(
                    "后台测速线程在 %.1fs 内未退出：池已关闭，它的观测会被丢弃，"
                    "但仍会把手上这一轮拨号跑完",
                    _SPEEDTEST_JOIN_SECONDS,
                )

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
