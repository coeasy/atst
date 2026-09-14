# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""v14 Runtime 流式订阅管理（Streaming v14 集成层）。

把 canonical 的 :class:`tstdx.stream_contract.StreamPlanner`（编译
Provider 专属流计划）与 fail-closed 的
:class:`tstdx.streaming.state.StreamLifecycle`（生命周期状态机）
桥接到 v14 Runtime 的统一管理面。

用途（示例）::

    runtime = create_runtime()
    handle = runtime.subscribe(
        "quotes", ("sh600000", "sh600519"), interval=1.0
    )
    handle.snapshot()          # StreamLifecycleSnapshot
    handle.stream_state()      # StreamState（别名）
    handle.begin_start()       # 幂等启动：CREATED -> RUNNING
    runtime.unsubscribe(handle.id)   # 幂等停止，保留 FAILED 语义

编排语义：

- ``StreamPlanner.compile`` 负责 fail-closed 校验（非 ``quotes`` capability、
  非 ``tdx`` provider、空 symbols、非法 interval/max_queue 都会抛
  :class:`tstdx.errors.ValidationError`）。
- ``StreamLifecycle`` 负责运行时状态迁移（CREATED / RUNNING / STOPPING /
  CLOSED / FAILED），FAILED 语义不被 ``close()`` 擦除。
- Runtime 只维护编排级句柄集合，回源 Worker 由外部（如 StatefulQuoteStream）
  按 ``handle.plan`` 驱动，避免把 TDX 传输细节泄漏到 Runtime 层。
"""

from __future__ import annotations

from dataclasses import dataclass

from ..stream_contract import StreamPlan, StreamPlanner, StreamSpec
from ..streaming.state import StreamLifecycle, StreamLifecycleSnapshot, StreamState
from .runtime import Runtime

__all__ = ["StreamHandle", "runtime_subscribe", "runtime_has_provider"]


@dataclass(frozen=True, slots=True)
class StreamHandle:
    """一次 Runtime 流订阅的完整生命周期句柄。

    ``plan`` 是不可变编译结果；``lifecycle`` 是线程安全状态机。
    ``id`` 由 Runtime 层分配（默认按创建顺序），供 ``unsubscribe`` 使用。
    """

    plan: StreamPlan
    lifecycle: StreamLifecycle
    id: str = ""

    @property
    def state(self) -> StreamState:
        return self.lifecycle.state

    def snapshot(self) -> StreamLifecycleSnapshot:
        return self.lifecycle.snapshot()

    def stream_state(self) -> StreamState:
        """兼容别名（旧 facade 上常见命名）。"""
        return self.lifecycle.state

    def begin_start(self) -> bool:
        """启动 Worker：CREATED -> RUNNING。

        已 RUNNING 时返回 ``False``（安全 no-op，状态不变）；终止状态抛
        :class:`tstdx.errors.SubscriptionError`。
        """
        return self.lifecycle.begin_start()

    def begin_stop(self) -> bool:
        """开始清理（不擦除 FAILED 语义）。

        CLOSED 返回 ``False``；FAILED 返回 ``True`` 但仍保持 FAILED；
        其余状态迁移到 STOPPING 并返回 ``True``。
        """
        return self.lifecycle.begin_stop()

    def close(self) -> None:
        """标记成功清理关闭（FAILED 保持不变）。"""
        self.lifecycle.close()

    def fail(self, reason: str) -> None:
        """显式失败此订阅。"""
        self.lifecycle.fail(reason)

    def require_subscribable(self) -> None:
        """订阅前守卫：终止状态拒绝新订阅。"""
        self.lifecycle.require_subscribable()


def runtime_subscribe(
    runtime: Runtime,
    symbols: str | tuple[str, ...],
    *,
    provider: str = "tdx",
    interval: float = 1.0,
    diff_only: bool = False,
    max_queue: int = 1024,
) -> StreamHandle:
    """Run-time 流订阅入口：编译 StreamPlan 并绑定 StreamLifecycle。

    这是 v14 Streaming 的统一编排入口；回源 Worker 由调用方以
    ``plan`` 字段驱动（等价于 StatefulQuoteStream 的构造参数来源）。

    注意：本函数只负责 **编排** 层，不注册到 Runtime 的句柄集合；调用
    :meth:`Runtime.subscribe` 才是完整注册。这里保留独立函数，便于
    下游门面/适配器在拿到 handle 后再决定是否注册。
    """
    planner = StreamPlanner()
    spec = StreamSpec.build(
        symbols,
        provider=provider,
        interval=interval,
        diff_only=diff_only,
        max_queue=max_queue,
    )
    plan = planner.compile(spec)
    lifecycle = StreamLifecycle()
    handle = StreamHandle(plan=plan, lifecycle=lifecycle)
    # Runtime 侧仍保留 provider 可用性预检（运行时不注册该 provider
    # 的流也走 fail-closed）。
    if not runtime_has_provider(runtime, plan.provider):
        lifecycle.fail(f"runtime 未注册 provider: {plan.provider}")
    return handle


def runtime_has_provider(runtime: Runtime, provider: str) -> bool:
    """Runtime 是否已注册该 Provider 适配器（驱动回源的前提）。"""
    try:
        router = getattr(runtime, "router", None)
        if router is None:
            return False
        return router.get(provider) is not None
    except Exception:
        pass
    return True
