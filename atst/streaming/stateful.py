# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""Fail-closed streaming facades backed by the explicit lifecycle state machine."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from typing import Any

from .base import (
    AsyncQuoteStream,
    QuoteStream,
    Subscription,
    on_error_t,
    on_quote_t,
    validate_subscription,
)
from .engine import BackpressureQueue
from .state import StreamLifecycle, StreamState

__all__ = ["StatefulQuoteStream", "AsyncStatefulQuoteStream", "StreamState"]

_DEFAULT_QUEUE = 1024

#: P1-9/M3：Stateful 门面的重连预算。基类默认 0（永不放弃，保持旧语义）；门面给
#: 有限预算——生产入口（``Client.stream`` / WS 面都在这条链上）的永久失败流必须
#: 真的落 FAILED，而不是永远 RUNNING 地重试下去。30 次连续失败配 base=1s/cap=30s
#: 的指数退避，总量级在分钟级：足以跨过短暂抖动，不会把死配置拖成无限重连。
_DEFAULT_MAX_ATTEMPTS = 30


def _build_subscription(
    symbols: list[str],
    *,
    interval: float,
    diff_only: bool,
    max_queue: int,
    on_quote: on_quote_t | None,
    on_error: on_error_t | None,
) -> Subscription:
    queue = BackpressureQueue(max_queue) if max_queue > 0 else None
    sub = Subscription(
        symbols=symbols,
        interval=float(interval),
        diff_only=bool(diff_only),
        max_queue=int(max_queue),
        on_quote=on_quote,
        on_error=on_error,
        _queue=queue,
    )
    if queue is not None:

        def _on_drop(_item: Any, _sub: Subscription = sub) -> None:
            _sub._dropped += 1

        queue._on_drop = _on_drop
    return sub


class StatefulQuoteStream(QuoteStream):
    """Canonical synchronous stream with explicit one-shot lifecycle semantics."""

    def __init__(
        self, *args: Any, max_attempts: int = _DEFAULT_MAX_ATTEMPTS, **kwargs: Any
    ) -> None:
        # P1-9/M3：接 :class:`ReconnectPolicy` 的预算——耗尽时基类 ``_run`` 停 worker，
        # 这里的 ``_run`` ``finally`` 随后把状态机落 FAILED（原因里写明是预算耗尽）。
        super().__init__(*args, max_attempts=max_attempts, **kwargs)
        self._lifecycle = StreamLifecycle()
        # P1-9/M1：与基类 ``_sub_seq`` 同一口径的单调订阅序号（退订后不复用 key）。
        self._sub_seq = 0

    @property
    def state(self) -> StreamState:
        return self._lifecycle.state

    @property
    def failure_reason(self) -> str | None:
        return self._lifecycle.failure_reason

    def subscribe(
        self,
        symbols: str | Sequence[str],
        *,
        interval: float = 1.0,
        diff_only: bool = False,
        max_queue: int = _DEFAULT_QUEUE,
        on_quote: on_quote_t | None = None,
        on_error: on_error_t | None = None,
    ) -> str:
        values = validate_subscription(symbols, interval=interval, max_queue=max_queue)
        self._lifecycle.require_subscribable()
        sub = _build_subscription(
            values,
            interval=interval,
            diff_only=diff_only,
            max_queue=max_queue,
            on_quote=on_quote,
            on_error=on_error,
        )
        with self._lock:
            self._lifecycle.require_subscribable()
            key = f"sub{self._sub_seq}"
            self._sub_seq += 1
            self._subs[key] = sub
        return key

    def start(self) -> StatefulQuoteStream:
        if self.state is StreamState.RUNNING:
            thread = self._thread
            self._lifecycle.assert_running_worker(
                worker_alive=thread is not None and thread.is_alive()
            )
            return self
        self._lifecycle.begin_start()
        try:
            super().start()
        except BaseException:
            self._stop.set()
            self._lifecycle.fail("worker start failed")
            raise
        return self

    def stop(self, *, timeout: float = 2.0) -> None:
        if timeout < 0:
            raise ValueError("timeout must be >= 0")
        if not self._lifecycle.begin_stop():
            return
        super().stop(timeout=timeout)
        thread = self._thread
        if thread is None or not thread.is_alive():
            self._lifecycle.close()

    close = stop

    def _run(self) -> None:
        unexpected: BaseException | None = None
        try:
            super()._run()
        except BaseException as exc:
            unexpected = exc
            raise
        finally:
            if self.state is StreamState.RUNNING:
                if unexpected is not None:
                    reason = f"worker raised {type(unexpected).__name__}"
                elif self._reconnect.should_give_up():
                    # P1-9/M3：重连预算耗尽的正常退出路径——写清是预算耗尽，不是
                    # "worker 悄悄死了"。
                    reason = (
                        f"reconnect budget exhausted after {self._reconnect.attempts} "
                        f"consecutive failures (max_attempts={self._reconnect.max_attempts})"
                    )
                else:
                    reason = "worker exited unexpectedly"
                self._lifecycle.fail(reason)
                self._stop.set()


class AsyncStatefulQuoteStream(AsyncQuoteStream):
    """Canonical asyncio stream mirroring the synchronous lifecycle contract."""

    def __init__(
        self, *args: Any, max_attempts: int = _DEFAULT_MAX_ATTEMPTS, **kwargs: Any
    ) -> None:
        # P1-9/M3：与同步门面同一预算口径。
        super().__init__(*args, max_attempts=max_attempts, **kwargs)
        self._lifecycle = StreamLifecycle()
        # P1-9/M1：与同步门面同一口径的单调订阅序号。
        self._sub_seq = 0

    @property
    def state(self) -> StreamState:
        return self._lifecycle.state

    @property
    def failure_reason(self) -> str | None:
        return self._lifecycle.failure_reason

    def subscribe(
        self,
        symbols: str | Sequence[str],
        *,
        interval: float = 1.0,
        diff_only: bool = False,
        max_queue: int = _DEFAULT_QUEUE,
        on_quote: on_quote_t | None = None,
        on_error: on_error_t | None = None,
    ) -> str:
        values = validate_subscription(symbols, interval=interval, max_queue=max_queue)
        self._lifecycle.require_subscribable()
        sub = _build_subscription(
            values,
            interval=interval,
            diff_only=diff_only,
            max_queue=max_queue,
            on_quote=on_quote,
            on_error=on_error,
        )
        key = f"sub{self._sub_seq}"
        self._sub_seq += 1
        self._subs[key] = sub
        return key

    async def start(self) -> AsyncStatefulQuoteStream:
        if self.state is StreamState.RUNNING:
            task = self._task
            self._lifecycle.assert_running_worker(worker_alive=task is not None and not task.done())
            return self
        self._lifecycle.begin_start()
        try:
            await super().start()
        except BaseException:
            self._stop.set()
            self._lifecycle.fail("async worker start failed")
            raise
        return self

    async def stop(self) -> None:
        if not self._lifecycle.begin_stop():
            return
        try:
            await super().stop()
        finally:
            #: 基类只在 worker 确实排空之后才返回/抛回（取消也不例外），所以状态机
            #: 这一步在两种下场里都成立：正常停机 → CLOSED，调用方取消 → 收尾同样
            #: 已做完，只是随后把取消原样抛回去。
            self._lifecycle.close()

    close = stop

    async def _run(self) -> None:
        unexpected: BaseException | None = None
        try:
            await super()._run()
        except asyncio.CancelledError:
            if self.state is StreamState.RUNNING:
                unexpected = asyncio.CancelledError()
            raise
        except BaseException as exc:
            unexpected = exc
            raise
        finally:
            if self.state is StreamState.RUNNING:
                if unexpected is not None:
                    reason = f"worker raised {type(unexpected).__name__}"
                elif self._reconnect.should_give_up():
                    # P1-9/M3：重连预算耗尽的正常退出路径，与同步门面同一文案。
                    reason = (
                        f"reconnect budget exhausted after {self._reconnect.attempts} "
                        f"consecutive failures (max_attempts={self._reconnect.max_attempts})"
                    )
                else:
                    reason = "async worker exited unexpectedly"
                self._lifecycle.fail(reason)
                self._stop.set()
            #: 这里原本还有一格"``self._task.done()`` 且 STOPPING ⇒ ``_lifecycle.close()``"，
            #: 已按死码删除：``_run`` 只作为 ``self._task`` 自己那条任务运行，而任务在
            #: 自己的 ``finally`` 里读自己的 ``done()`` 恒为 False（``done()`` 在协程返回
            #: 之后才置位），所以那个条件不可满足。CLOSED 这一步的真源是
            #: :meth:`AsyncStatefulQuoteStream.stop`——它在 worker 排空之后显式推进状态机。
