# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""流式订阅（§13）：实时行情的「推 / 拉」统一抽象。

通达信实时行情命令 ``0x0530`` 是**请求-响应**模型（一次一只），没有服务端
主动推送。因此 :class:`QuoteStream` 的「流」本质是**客户端轮询 + 增量合并**：

* **轮询**：按 ``interval`` 周期调用底层 ``quotes`` 获取快照；
* **增量合并**：``diff_only=True`` 时由 :class:`~tstdx.streaming.engine.DeltaMerger`
  保留每个标的的上一次快照，回调只推送**变化字段**（首个快照仍为全量）；
  默认（``False``）回调全量快照；
* **断线容错**：底层抛 :class:`~tstdx.errors.TdxError` 时指数退避后重试，
  任何未预期异常都会记日志并派发 ``on_error``，不再无声杀死流线程。但
  **没有**断线期间的补数——``0x0530`` 只返回当前快照，历史缺口需调用方
  自行用 ``bars`` 回补；标的连续 3 轮未见于响应时派发
  :class:`~tstdx.errors.GapUnfilledError`（可观测信号，不自动补数）；
* **背压（M1 接线）**：``max_queue`` 参数已真实接线——每订阅一个
  :class:`~tstdx.streaming.engine.BackpressureQueue`，溢出丢弃**最旧**事件
  （行情「最新优先」语义）并派发 :class:`~tstdx.errors.BackpressureOverflow`；
  ``max_queue=0`` 关闭队列直发（最高性能路径）。默认 1024 在单轮批量拉取
  模型下不会溢出，默认行为与旧版一致；
* **E6 错误分类（M1 接线）**：订阅符号不可解析 →
  :class:`~tstdx.errors.SubscriptionError`；缺口 → :class:`~tstdx.errors.GapUnfilledError`；
  溢出 → :class:`~tstdx.errors.BackpressureOverflow`。errors.py 的 E6xxx
  占位分类自此具备库内真实抛点。

同步用 :class:`QuoteStream`（线程），异步用 :class:`AsyncQuoteStream`（协程）。
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..client import AsyncTdxClient, TdxClient

from ..domain.symbol import split_symbol as _symbol_split
from ..errors import (
    BackpressureOverflow,
    GapUnfilledError,
    SubscriptionError,
    TdxError,
)
from .engine import (
    BackpressureQueue,
    DeltaMerger,
    GapFiller,
    QuoteChannel,
    ReconnectPolicy,
    StreamEngine,
    StreamEvent,
)
from .push import PUSH_CMD, PushChannel, PushFrame

__all__ = [
    "QuoteStream",
    "AsyncQuoteStream",
    "Subscription",
    "ReconnectPolicy",
    "DeltaMerger",
    "GapFiller",
    "BackpressureQueue",
    "QuoteChannel",
    "StreamEngine",
    "StreamEvent",
    "PushChannel",
    "PushFrame",
    "PUSH_CMD",
    "on_quote_t",
    "on_error_t",
]

logger = logging.getLogger(__name__)

on_quote_t = Callable[[str, dict[str, Any]], None]
on_error_t = Callable[[Exception], None]

#: ``max_queue`` 参数的默认值：单轮批量拉取的事件数上限以内（见模块
#: docstring「背压」节——默认容量下队列永不溢出，行为与旧版一致）。
_MAX_QUEUE_DEFAULT = 1024

#: E6 兑现（M1）：标的连续缺失多少轮后派发 :class:`GapUnfilledError`
_MISSING_ALERT_AFTER = 3


def _bare_code(sym: str) -> str:
    """订阅符号 → 裸 6 位码（C1：0x0530 响应按裸码回声）。

    真实主站对 ``0x0530`` 的响应回声是裸 6 位代码（见
    :class:`tstdx.protocol.parsers.std7709.RealtimeQuoteParser` 的回声
    校验），因此 qmap 查找键与 ``_last`` 键都必须用裸码，否则
    ``sh600519`` / ``600519.SH`` 等书写变种会静默零数据。
    """
    _, code = _symbol_split(sym)
    return code


@dataclass
class Subscription:
    """一个订阅请求的配置。"""

    symbols: list[str]
    interval: float = 1.0
    #: 是否只回调变化字段（默认 False = 回调全量快照）
    diff_only: bool = False
    #: 背压队列容量（M1 接线）：>0 时事件经
    #: :class:`~tstdx.streaming.engine.BackpressureQueue` 缓冲，溢出丢弃
    #: 最旧并派发 :class:`~tstdx.errors.BackpressureOverflow`；``0`` 直发。
    max_queue: int = 1024
    on_quote: on_quote_t | None = None
    on_error: on_error_t | None = None
    #: 内部：增量合并器（M1 接线——engine.DeltaMerger 替换旧 ``_diff/_last``）
    _merger: DeltaMerger = field(default_factory=DeltaMerger, repr=False)
    #: 内部：背压队列（``max_queue>0`` 时非 None）
    _queue: BackpressureQueue | None = field(default=None, repr=False)
    #: 内部：各裸码连续缺失轮数（E6 GapUnfilledError 计数）
    _missing: dict[str, int] = field(default_factory=dict, repr=False)
    #: 内部：背压累计丢弃数（溢出可观测）
    _dropped: int = field(default=0, repr=False)


class QuoteStream:
    """同步实时行情流（后台线程轮询）。

    Examples
    --------
    >>> stream = QuoteStream()
    >>> stream.subscribe(["sh600519"], on_quote=lambda c, q: print(c, q["price"]))
    >>> stream.start()
    >>> time.sleep(5)
    >>> stream.stop()
    """

    def __init__(self, *, hosts: Sequence[Any] | None = None, timeout: float = 5.0) -> None:
        self._hosts = hosts
        self._timeout = timeout
        self._subs: dict[str, Subscription] = {}
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._client: TdxClient | None = None  # 延迟创建，避免无谓连接；测试可注入假客户端
        self._reconnect = ReconnectPolicy(base=1.0, cap=30.0)
        self._warned_bad_symbols: set[str] = set()

    # -- 订阅管理 ----------------------------------------------------------- #
    def subscribe(
        self,
        symbols: str | Sequence[str],
        *,
        interval: float = 1.0,
        diff_only: bool = False,
        max_queue: int = _MAX_QUEUE_DEFAULT,
        on_quote: on_quote_t | None = None,
        on_error: on_error_t | None = None,
    ) -> str:
        # M1 接线：max_queue>0 时挂真实背压队列；0 关闭队列直发
        queue: BackpressureQueue | None = None
        if max_queue > 0:
            queue = BackpressureQueue(max_queue)
        with self._lock:  # C4：与 unsubscribe 对称加锁
            key = f"sub{len(self._subs)}"
            if isinstance(symbols, str):
                symbols = [symbols]
            sub = Subscription(
                symbols=list(symbols),
                interval=interval,
                diff_only=diff_only,
                max_queue=max_queue,
                on_quote=on_quote,
                on_error=on_error,
                _queue=queue,
            )
            if queue is not None:
                # T#1 修复语义：on_drop 在锁外调用，此处仅做轻量计数
                def _on_drop(_item: Any, _s: Subscription = sub) -> None:
                    _s._dropped += 1

                queue._on_drop = _on_drop
            self._subs[key] = sub
        return key

    def unsubscribe(self, key: str) -> None:
        with self._lock:
            self._subs.pop(key, None)

    # -- 生命周期 ----------------------------------------------------------- #
    def start(self) -> QuoteStream:
        if self._thread and self._thread.is_alive():
            return self
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="tstdx-stream", daemon=True)
        self._thread.start()
        return self

    def stop(self, *, timeout: float = 2.0) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=timeout)
            if thread.is_alive():
                # 深审 M2b：join 超时**保留**线程引用——旧实现置 None 会让
                # 随后的 start() 认为无线程在跑而再启一条（孤儿双跑共享
                # _client/_stop）。_stop 已置位，线程会在本轮 poll 后自退。
                logger.warning("QuoteStream 轮询线程未在 %ss 内退出，等待其自行收尾", timeout)
                return
        self._thread = None
        # 深审 M2：关闭轮询线程持有的客户端（内含连接池 socket）——
        # 旧实现只 join 线程，client 泄漏至进程退出。仅在确认线程已退出
        # （或从未启动）后执行，避免关闭线程正在使用的 socket。
        with contextlib.suppress(Exception):
            client = self._client
            self._client = None
            if client is not None and hasattr(client, "close"):
                client.close()

    def __enter__(self) -> QuoteStream:
        return self.start()

    def __exit__(self, *exc: Any) -> None:
        self.stop()

    # -- 主循环 ------------------------------------------------------------- #
    def _run(self) -> None:
        try:
            client = self._client
            if client is None:
                from ..client import TdxClient

                client = TdxClient(hosts=self._hosts, timeout=self._timeout)
                self._client = client
        except Exception as exc:
            logger.error("QuoteStream 创建客户端失败: %s", exc)
            self._dispatch_error(exc)
            return

        while not self._stop.is_set():
            try:
                self._poll_once(client)
            except Exception as exc:  # noqa: BLE001 - C4：兜底，未预期异常不再无声杀线程
                logger.exception("QuoteStream 轮询循环未预期异常（线程继续运行）")
                self._dispatch_error(exc)
                # 指数退避，避免异常紧循环刷屏；用 Event.wait 保证 stop() 可及时打断
                self._stop.wait(self._reconnect.next_delay())

    def _poll_once(self, client: Any) -> None:
        """执行一轮「拉取 → 合并 → 背压 → 回调」（M1 接线：内核由 engine 组件构成）。

        TdxError 内部消化（退避重试）。E6 分类在本方法内兑现真实抛点：

        * 订阅符号不可解析 → :class:`SubscriptionError`（每符号首次）；
        * 标的连续 :data:`_MISSING_ALERT_AFTER` 轮未见于响应 →
          :class:`GapUnfilledError`（可观测信号，不自动补数）；
        * 背压队列溢出 → :class:`BackpressureOverflow`（每订阅每轮至多一次）。
        """
        with self._lock:
            subs = list(self._subs.values())
        if not subs:
            self._stop.wait(0.2)
            return
        # 聚合所有订阅的标的，一次批量拉取（请求侧书写变种由 client 归一化）
        all_syms: list[str] = []
        for s in subs:
            all_syms.extend(s.symbols)
        try:
            quotes = client.quotes(all_syms, as_format="dict")
            self._reconnect.success()
        except TdxError as exc:
            self._dispatch_error(exc)
            # 指数退避重连（ReconnectPolicy 提供抖动 + 上限）
            self._stop.wait(self._reconnect.next_delay())
            return

        # C1：0x0530 回声为裸 6 位码，qmap 必须按裸码查
        qmap = {q.get("code", ""): q for q in quotes}
        for s in subs:
            dropped_before = s._dropped
            if s._queue is not None:
                for sym in s.symbols:
                    payload = _resolve_payload(s, sym, qmap, self._warned_bad_symbols)
                    if payload is not None:
                        s._queue.put((sym, payload))
            else:
                for sym in s.symbols:
                    payload = _resolve_payload(s, sym, qmap, self._warned_bad_symbols)
                    if payload is not None and s.on_quote is not None:
                        with contextlib.suppress(Exception):  # 回调错误不应中断流
                            s.on_quote(sym, payload)
            if s._queue is not None:
                for sym, payload in s._queue.drain():
                    if s.on_quote is not None:
                        with contextlib.suppress(Exception):
                            s.on_quote(sym, payload)
                if s._dropped > dropped_before and s.on_error is not None:
                    with contextlib.suppress(Exception):
                        s.on_error(
                            BackpressureOverflow(
                                f"背压队列溢出，丢弃 {s._dropped - dropped_before} 个最旧事件",
                                context={
                                    "dropped_total": s._dropped,
                                    "max_queue": s.max_queue,
                                },
                            )
                        )
        # 按最小 interval 休眠
        min_interval = min((s.interval for s in subs), default=1.0)
        self._stop.wait(min_interval)

    def _dispatch_error(self, exc: Exception) -> None:
        with self._lock:
            subs = list(self._subs.values())
        for s in subs:
            if s.on_error is not None:
                with contextlib.suppress(Exception):
                    s.on_error(exc)


def _resolve_payload(
    s: Subscription,
    sym: str,
    qmap: dict[str, dict[str, Any]],
    warned_bad_symbols: set[str],
) -> dict[str, Any] | None:
    """单标的「解析 → 缺失检测 → 合并」。返回 None 表示本轮无有效载荷。

    E6 兑现（M1）抛点（同步/异步流共用）：

    * 符号不可解析 → :class:`SubscriptionError`（每符号首次，同步经日志）；
    * 标的连续 :data:`_MISSING_ALERT_AFTER` 轮缺失 → :class:`GapUnfilledError`
      （可观测信号，不自动补数——0x0530 仅回当前快照，历史缺口仍需调用方
      ``bars`` 回补）。
    """
    try:
        bare = _bare_code(sym)
    except TdxError as exc:
        if sym not in warned_bad_symbols:
            warned_bad_symbols.add(sym)
            logger.warning("流式订阅符号无法解析，已跳过: %r (%s)", sym, exc)
            if s.on_error is not None:
                with contextlib.suppress(Exception):
                    s.on_error(
                        SubscriptionError(
                            f"订阅符号无法解析: {sym!r}",
                            context={"symbol": sym, "cause": str(exc)},
                            cause=exc,
                        )
                    )
        return None
    cur = qmap.get(bare)
    if cur is None:
        s._missing[bare] = s._missing.get(bare, 0) + 1
        if s._missing[bare] == _MISSING_ALERT_AFTER and s.on_error is not None:
            with contextlib.suppress(Exception):
                s.on_error(
                    GapUnfilledError(
                        f"标的连续 {_MISSING_ALERT_AFTER} 轮未见于响应: {sym!r}",
                        context={"symbol": sym, "missing_rounds": s._missing[bare]},
                    )
                )
        return None
    s._missing.pop(bare, None)
    return dict(s._merger.update(bare, cur)) if s.diff_only else cur


class AsyncQuoteStream:
    """异步实时行情流（asyncio 协程轮询）。

    Examples
    --------
    >>> async def main():
    ...     stream = AsyncQuoteStream()
    ...     stream.subscribe(["sh600519"], on_quote=lambda c, q: print(c, q))
    ...     await stream.start()
    ...     await asyncio.sleep(5)
    ...     await stream.stop()
    """

    def __init__(self, *, hosts: Sequence[Any] | None = None, timeout: float = 5.0) -> None:
        self._hosts = hosts
        self._timeout = timeout
        self._subs: dict[str, Subscription] = {}
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        self._lock = asyncio.Lock()
        self._client: AsyncTdxClient | None = None  # 延迟创建；测试可注入假客户端
        # M1 接线：裸 _backoff 换 ReconnectPolicy（与同步版同策略：指数 + 抖动 + 上限）
        self._reconnect = ReconnectPolicy(base=1.0, cap=30.0)
        self._warned_bad_symbols: set[str] = set()

    def subscribe(
        self,
        symbols: str | Sequence[str],
        *,
        interval: float = 1.0,
        diff_only: bool = False,
        max_queue: int = _MAX_QUEUE_DEFAULT,
        on_quote: on_quote_t | None = None,
        on_error: on_error_t | None = None,
    ) -> str:
        # M1 接线：max_queue>0 时挂真实背压队列（与同步版同语义）
        queue: BackpressureQueue | None = None
        if max_queue > 0:
            queue = BackpressureQueue(max_queue)
        # 保持同步 API（与文档示例一致）；asyncio 单循环内无 await 点，
        # subscribe/unsubscribe 相对 _run 的快照读取天然原子。
        key = f"sub{len(self._subs)}"
        if isinstance(symbols, str):
            symbols = [symbols]
        sub = Subscription(
            symbols=list(symbols),
            interval=interval,
            diff_only=diff_only,
            max_queue=max_queue,
            on_quote=on_quote,
            on_error=on_error,
            _queue=queue,
        )
        if queue is not None:

            def _on_drop(_item: Any, _s: Subscription = sub) -> None:
                _s._dropped += 1

            queue._on_drop = _on_drop
        self._subs[key] = sub
        return key

    def unsubscribe(self, key: str) -> None:
        self._subs.pop(key, None)

    async def start(self) -> AsyncQuoteStream:
        if self._task is not None and not self._task.done():
            return self
        self._stop.clear()
        self._task = asyncio.create_task(self._run())
        return self

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            try:
                await asyncio.wait_for(self._task, timeout=2.0)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                self._task.cancel()
            self._task = None

    async def __aenter__(self) -> AsyncQuoteStream:
        return await self.start()

    async def __aexit__(self, *exc: Any) -> None:
        await self.stop()

    async def _run(self) -> None:
        from ..client import AsyncTdxClient

        client = self._client
        if client is None:
            client = AsyncTdxClient(hosts=self._hosts, timeout=self._timeout)
            self._client = client
        async with client:
            while not self._stop.is_set():
                try:
                    await self._poll_once(client)
                except Exception as exc:  # noqa: BLE001 - C4 镜像：未预期异常不再无声死协程
                    logger.exception("AsyncQuoteStream 轮询循环未预期异常（协程继续运行）")
                    self._dispatch_error(exc)
                    # asyncio.Event.wait() 不接受超时参数（原实现此处即 TypeError），
                    # 退避用 sleep（M1 接线：ReconnectPolicy 指数 + 抖动 + 上限）；
                    # stop() 经 wait_for 超时后 cancel 兜底。
                    await asyncio.sleep(self._reconnect.next_delay())

    async def _poll_once(self, client: Any) -> None:
        """执行一轮「拉取 → 合并 → 背压 → 回调」（M1 接线，与同步版同语义）。

        E6 抛点见 :func:`_resolve_payload` 与本方法的溢出检测。
        """
        async with self._lock:
            subs = list(self._subs.values())
        if not subs:
            await asyncio.sleep(0.2)
            return
        all_syms: list[str] = []
        for s in subs:
            all_syms.extend(s.symbols)
        try:
            quotes = await client.quotes(all_syms, as_format="dict")
        except TdxError as exc:
            for s in subs:
                if s.on_error:
                    # C4 镜像：与同步版对齐，回调异常不中断退避循环
                    with contextlib.suppress(Exception):
                        s.on_error(exc)
            await asyncio.sleep(self._reconnect.next_delay())
            return
        self._reconnect.success()

        # C1 镜像：0x0530 回声为裸 6 位码，qmap 必须按裸码查
        qmap = {q.get("code", ""): q for q in quotes}
        for s in subs:
            dropped_before = s._dropped
            if s._queue is not None:
                for sym in s.symbols:
                    payload = _resolve_payload(s, sym, qmap, self._warned_bad_symbols)
                    if payload is not None:
                        s._queue.put((sym, payload))
            else:
                for sym in s.symbols:
                    payload = _resolve_payload(s, sym, qmap, self._warned_bad_symbols)
                    if payload is not None and s.on_quote is not None:
                        with contextlib.suppress(Exception):
                            s.on_quote(sym, payload)
            if s._queue is not None:
                for sym, payload in s._queue.drain():
                    if s.on_quote is not None:
                        with contextlib.suppress(Exception):
                            s.on_quote(sym, payload)
                if s._dropped > dropped_before and s.on_error is not None:
                    with contextlib.suppress(Exception):
                        s.on_error(
                            BackpressureOverflow(
                                f"背压队列溢出，丢弃 {s._dropped - dropped_before} 个最旧事件",
                                context={
                                    "dropped_total": s._dropped,
                                    "max_queue": s.max_queue,
                                },
                            )
                        )
        # 按最小 interval 休眠（asyncio.Event.wait() 无超时参数，用 sleep；
        # stop() 的 wait_for 超时 + cancel 兜底）
        min_interval = min((s.interval for s in subs), default=1.0)
        await asyncio.sleep(min_interval)

    def _dispatch_error(self, exc: Exception) -> None:
        for s in list(self._subs.values()):
            if s.on_error is not None:
                with contextlib.suppress(Exception):
                    s.on_error(exc)
