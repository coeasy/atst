# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""流式引擎组件（§13 增强）：增量合并 / 缺口补数 / 重连策略 / 通道抽象。

.. note::
   **M1 决断已落地（v1.4.0，用户拍板「接线」）**：生产流
   :class:`tstdx.streaming.QuoteStream` / :class:`~tstdx.streaming.AsyncQuoteStream`
   内核已由本模块组件构成——DeltaMerger（增量合并）、BackpressureQueue
   （背压）、ReconnectPolicy（退避）接线完毕，E6 错误分类
   （SubscriptionError / GapUnfilledError / BackpressureOverflow）具备库内
   真实抛点。QuoteChannel / StreamEngine / GapFiller 仍是独立公开组件
   （专测覆盖，通道级用法见 cookbook/04_streaming.md）。

通达信实时行情（``0x0530``）是**请求-响应**模型，没有服务端主动推送，因此
「流」本质是**客户端轮询 + 增量合并 + 断线重连**。本模块把这些横切关注点拆成
可复用、可单测的组件：

* :class:`ReconnectPolicy` —— 指数退避 + 抖动 + 最大尝试 + 可选换主站回调；
* :class:`DeltaMerger`    —— 维护每个标的的上一快照，产出变化字段增量；
* :class:`GapFiller`      —— 检测序号 / 时间单调性缺口，标记需回补的区间；
* :class:`BackpressureQueue` —— 有界队列，溢出时丢弃最旧（防内存膨胀）；
* :class:`QuoteChannel`   —— 把「轮询函数 + 增量合并 + 重连策略 + 背压」封装成
  统一的推送/拉取通道抽象；
* :class:`StreamEngine`   —— 用上述组件驱动一个完整的轮询循环。

所有组件都是**纯逻辑、无网络依赖**，可独立单元测试（本模块刻意不 import 任何
网络/客户端代码，避免循环依赖）。
"""

from __future__ import annotations

import contextlib
import random
import threading
import time
from collections.abc import Callable, Mapping, MutableMapping, Sequence
from dataclasses import dataclass, field
from typing import Any

__all__ = [
    "ReconnectPolicy",
    "DeltaMerger",
    "GapFiller",
    "BackpressureQueue",
    "QuoteChannel",
    "StreamEngine",
    "StreamEvent",
]


# --------------------------------------------------------------------------- #
# 重连策略
# --------------------------------------------------------------------------- #
@dataclass
class ReconnectPolicy:
    """指数退避重连策略（带全抖动与可选换主站回调）。

    Parameters
    ----------
    base, cap:
        退避基准与上限（秒）。第 ``n`` 次失败后延迟 ``∈ [0, min(cap, base*2^n)]``。
    max_attempts:
        最大连续失败次数；超过则 :meth:`should_give_up` 返回 ``True``（建议降级到
        Web 源 / 离线），``0`` 表示永不放弃。
    jitter:
        是否启用全抖动（full jitter），默认开启，避免多客户端同步重连「惊群」。
    on_rotate_host:
        每次退避前调用，参数为已失败次数；可用于通知上层换主站。
    """

    base: float = 1.0
    cap: float = 30.0
    max_attempts: int = 0
    jitter: bool = True
    on_rotate_host: Callable[[int], None] | None = None

    def __post_init__(self) -> None:
        self._attempts = 0
        self._lock = threading.Lock()

    @property
    def attempts(self) -> int:
        with self._lock:
            return self._attempts

    def next_delay(self) -> float:
        """返回下次重连前应等待的秒数（含抖动），并累加失败计数。"""
        with self._lock:
            exp = min(self.cap, self.base * (2**self._attempts))
            self._attempts += 1
            if self.on_rotate_host is not None:
                with contextlib.suppress(Exception):
                    self.on_rotate_host(self._attempts)
            if self.jitter:
                return random.uniform(0.0, exp)
            return exp

    def success(self) -> None:
        """成功一次即重置失败计数。"""
        with self._lock:
            self._attempts = 0

    def fail(self) -> None:
        """仅累加失败计数（不计算延迟）。"""
        with self._lock:
            self._attempts += 1

    def should_give_up(self) -> bool:
        with self._lock:
            return self.max_attempts > 0 and self._attempts >= self.max_attempts

    def reset(self) -> None:
        with self._lock:
            self._attempts = 0


# --------------------------------------------------------------------------- #
# 增量合并
# --------------------------------------------------------------------------- #
class DeltaMerger:
    """维护每个键的上一快照，产出变化字段增量。

    调用 :meth:`update` 传入当前全量快照，返回与上一快照的差异（仅含变化的字段）。
    首个快照返回全量；``code`` 字段始终保留以便路由。
    """

    def __init__(self) -> None:
        self._state: MutableMapping[str, Mapping[str, Any]] = {}
        self._lock = threading.Lock()

    def update(self, key: str, snapshot: Mapping[str, Any]) -> Mapping[str, Any]:
        snap = dict(snapshot)
        with self._lock:
            prev = self._state.get(key)
            self._state[key] = snap
        if prev is None:
            return snap
        changed = {k: v for k, v in snap.items() if prev.get(k) != v}
        changed["code"] = snap.get("code")
        return changed

    def get(self, key: str) -> Mapping[str, Any] | None:
        with self._lock:
            return dict(self._state.get(key, {})) or None

    def snapshot(self) -> dict[str, Mapping[str, Any]]:
        with self._lock:
            return {k: dict(v) for k, v in self._state.items()}

    def clear(self) -> None:
        with self._lock:
            self._state.clear()


# --------------------------------------------------------------------------- #
# 缺口检测
# --------------------------------------------------------------------------- #
class GapFiller:
    """检测序号 / 时间的单调性缺口。

    适用于两类场景：

    * **K 线续传**：把 ``datetime`` 作为单调键，若新棒的 datetime 不等于
      ``上一根 + 周期步长``，则中间缺失若干根，需回补；
    * **逐笔/逐笔推送**：把自增 ``seq`` 作为键，若 ``seq != last+1`` 即出现缺口。

    用法::

        gf = GapFiller()
        if gf.observe("sh600519", seq=42):     # 返回 True 表示这是「连续」的
            ...
        gaps = gf.gaps("sh600519")             # 返回 [(last_seen, current), ...]
    """

    def __init__(self) -> None:
        # key -> 最近一次见到的单调值
        self._last: MutableMapping[str, Any] = {}
        self._gaps: MutableMapping[str, list[tuple[Any, Any]]] = {}
        self._lock = threading.Lock()

    def observe(self, key: str, value: Any) -> bool:
        """喂入一个单调值；返回 ``True`` 表示连续（无缺口），``False`` 表示发现缺口。"""
        with self._lock:
            prev = self._last.get(key)
            self._last[key] = value
            if prev is None:
                return True
            try:
                continuous = value == prev + 1 or value == prev
            except TypeError:
                # 非数值（如 datetime 字符串）无法做 +1 比较：退化为「总是连续」，
                # 缺口交给上层基于周期步长判断。
                continuous = True
            if not continuous:
                self._gaps.setdefault(key, []).append((prev, value))
                return False
            return True

    def gaps(self, key: str) -> list[tuple[Any, Any]]:
        with self._lock:
            return list(self._gaps.get(key, []))

    def clear(self, key: str | None = None) -> None:
        with self._lock:
            if key is None:
                self._last.clear()
                self._gaps.clear()
            else:
                self._last.pop(key, None)
                self._gaps.pop(key, None)


# --------------------------------------------------------------------------- #
# 背压队列
# --------------------------------------------------------------------------- #
class BackpressureQueue:
    """有界队列，溢出时丢弃**最旧**元素（避免内存膨胀，符合行情「最新优先」语义）。

    Parameters
    ----------
    maxsize:
        队列容量；``0`` 表示无限（慎用）。
    on_drop:
        每次丢弃时回调，参数为被丢弃的对象（用于埋点 / 日志）。
    """

    def __init__(self, maxsize: int = 1024, on_drop: Callable[[Any], None] | None = None) -> None:
        self._maxsize = maxsize
        self._on_drop = on_drop
        self._q: list[Any] = []
        self._lock = threading.Lock()

    def put(self, item: Any) -> None:
        # 修复（v1.2.0 深审 T#1）：旧实现持锁调用 on_drop —— QuoteChannel 的
        # on_drop 里会上报 qsize()（再次抢同一把不可重入 Lock），队列首次溢出
        # 即 100% 确定性死锁。现在锁内只摘下待弃元素，回调在锁外执行。
        dropped: list[Any] = []
        with self._lock:
            self._q.append(item)
            if self._maxsize > 0 and len(self._q) > self._maxsize:
                dropped.append(self._q.pop(0))
        if dropped and self._on_drop is not None:
            with contextlib.suppress(Exception):
                self._on_drop(dropped[0])

    def get(self) -> Any | None:
        with self._lock:
            return self._q.pop(0) if self._q else None

    def qsize(self) -> int:
        with self._lock:
            return len(self._q)

    def drain(self) -> list[Any]:
        with self._lock:
            out, self._q = self._q, []
            return out


# --------------------------------------------------------------------------- #
# 通道抽象
# --------------------------------------------------------------------------- #
@dataclass
class StreamEvent:
    """通道吐出的一次事件。"""

    kind: str  # "quote" | "diff" | "reconnect" | "gap" | "error"
    key: str
    payload: Any = None
    ts: float = field(default_factory=time.time)


class QuoteChannel:
    """统一的「推 / 拉」行情通道抽象。

    把 :class:`DeltaMerger` + :class:`ReconnectPolicy` + :class:`BackpressureQueue`
    组合成一个可订阅对象。底层轮询函数由调用方注入（同步 / 异步皆可），本类不关心
    数据来源，只负责**增量合并、重连退避、背压、事件派发**。

    Parameters
    ----------
    poll:
        形如 ``Callable[[list[str]], Sequence[Mapping[str, Any]]]`` 的拉取函数，
        给定标的列表返回当前快照列表（每个含 ``code`` 字段）。
    symbols:
        订阅标的列表。
    diff_only:
        是否只派发变化字段（默认 ``False`` = 全量）。
    """

    def __init__(
        self,
        poll: Callable[[Sequence[str]], Sequence[Mapping[str, Any]]],
        symbols: Sequence[str],
        *,
        diff_only: bool = False,
        max_queue: int = 1024,
        reconnect: ReconnectPolicy | None = None,
    ) -> None:
        self._poll = poll
        self._symbols = list(symbols)
        self._diff_only = diff_only
        self._merger = DeltaMerger()
        self._bp = BackpressureQueue(maxsize=max_queue, on_drop=None)
        self._reconnect = reconnect or ReconnectPolicy()
        self._subs: list[Callable[[StreamEvent], None]] = []
        self._lock = threading.Lock()
        # R4 断线恢复：上次 tick 是否失败；恢复后置位让引擎立即补拉一轮
        self._last_failed = False
        self._reconnected = False
        # 背压丢弃埋点
        if max_queue > 0:
            self._bp._on_drop = lambda _: self._on_backpressure_drop()

    def _on_backpressure_drop(self) -> None:
        try:
            from ..observability.metrics import metrics

            metrics.set_backpressure(self._bp.qsize())
            metrics.record_stream_event("drop")
        except Exception:  # noqa: BLE001
            pass

    def subscribe(self, cb: Callable[[StreamEvent], None]) -> None:
        with self._lock:
            self._subs.append(cb)

    def _dispatch(self, ev: StreamEvent) -> None:
        for cb in self._subs:
            with contextlib.suppress(Exception):  # 订阅者异常不应中断通道
                cb(ev)

    def tick(self) -> None:
        """执行一次轮询 + 合并 + 派发。

        网络错误交给 :class:`ReconnectPolicy` 退避；成功则重置并重发事件。
        """
        try:
            rows = self._poll(self._symbols)
            if self._last_failed:
                # R4 断线恢复：发重连事件并置位，引擎据此立即补拉一轮
                # （不必等下个轮询周期）。
                self._reconnected = True
                self._dispatch(
                    StreamEvent(
                        kind="reconnect",
                        key="",
                        payload={"symbols": list(self._symbols)},
                    )
                )
            self._last_failed = False
            self._reconnect.success()
        except Exception as exc:  # noqa: BLE001
            self._last_failed = True
            self._reconnect.fail()
            self._dispatch(StreamEvent(kind="error", key="", payload=exc))
            return

        # 按 code 建索引（深审 M15：上游返回的 code 可能是裸码「600519」，
        # 而订阅符号是「sh600519」——只按原样索引会让带前缀的订阅永不
        # 命中、通道静默无输出。裸码与原码都登记，查找时双向尝试。）
        by_code: dict[str, Mapping[str, Any]] = {}
        for r in rows:
            code = str(r.get("code", ""))
            if code:
                by_code[code] = r
                bare = code[2:] if len(code) > 2 and code[:2].isalpha() else code
                by_code.setdefault(bare, r)

        for sym in self._symbols:
            cur = by_code.get(sym)
            if cur is None:
                bare = sym[2:] if len(sym) > 2 and sym[:2].isalpha() else sym
                cur = by_code.get(bare)
            if cur is None:
                continue
            # diff_only：只派发变化字段（首个快照为全量，``code`` 恒保留以便路由）；
            # 全量模式：直接派发当前快照（此前两分支相同，diff 开关形同虚设）
            payload = self._merger.update(sym, cur) if self._diff_only else cur
            self._bp.put(
                StreamEvent(kind="diff" if self._diff_only else "quote", key=sym, payload=payload)
            )
        # 派发队列
        for ev in self._bp.drain():
            self._dispatch(ev)

    def poll_delay(self) -> float:
        """下一次 tick 前的建议等待（受重连策略影响）。

        深审 M14：旧实现恒 0——:class:`ReconnectPolicy` 的指数退避完全
        失效，tick 失败后立即重试形成风暴。现在按连续失败次数给出退避
        （与 :meth:`ReconnectPolicy.next_delay` 同公式；成功时为 0）。
        """
        attempts = self._reconnect.attempts
        if attempts <= 0:
            return 0.0
        exp = min(self._reconnect.cap, self._reconnect.base * (2 ** (attempts - 1)))
        if self._reconnect.jitter:
            return random.uniform(0.0, exp)
        return exp

    def take_reconnect(self) -> bool:
        """R4：取出「刚断线恢复」标志（供引擎立即补拉一轮），取后复位。"""
        with self._lock:
            if self._reconnected:
                self._reconnected = False
                return True
            return False


# --------------------------------------------------------------------------- #
# 引擎编排
# --------------------------------------------------------------------------- #
class StreamEngine:
    """用上述组件驱动一个完整轮询循环（后台线程）。

    Examples
    --------
    >>> def poll(syms): ...
    >>> eng = StreamEngine(poll, ["sh600519"], interval=1.0)
    >>> eng.subscribe(lambda ev: print(ev.kind, ev.key, ev.payload))
    >>> eng.start()
    >>> time.sleep(5)
    >>> eng.stop()
    """

    def __init__(
        self,
        poll: Callable[[Sequence[str]], Sequence[Mapping[str, Any]]],
        symbols: Sequence[str],
        *,
        interval: float = 1.0,
        diff_only: bool = False,
        max_queue: int = 1024,
        reconnect: ReconnectPolicy | None = None,
    ) -> None:
        self._channel = QuoteChannel(
            poll, symbols, diff_only=diff_only, max_queue=max_queue, reconnect=reconnect
        )
        self._interval = interval
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    def subscribe(self, cb: Callable[[StreamEvent], None]) -> None:
        self._channel.subscribe(cb)

    def start(self) -> StreamEngine:
        if self._thread and self._thread.is_alive():
            return self
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="tstdx-engine", daemon=True)
        self._thread.start()
        return self

    def stop(self, *, timeout: float = 2.0) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=timeout)
        self._thread = None

    def __enter__(self) -> StreamEngine:
        return self.start()

    def __exit__(self, *exc: Any) -> None:
        self.stop()

    def _run(self) -> None:
        while not self._stop.is_set():
            self._channel.tick()
            self._stop.wait(self._interval)
