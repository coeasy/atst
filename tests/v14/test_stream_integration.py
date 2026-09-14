"""v14 Runtime Streaming 集成测试。

覆盖：
- StreamHandle 导出与 Runtime.subscribe 编排入口
- StreamPlanner fail-closed 校验（非 quotes capability / 非 tdx provider /
  空 symbols / 非法 interval / 非法 max_queue）通过 subscribe 传播
- StreamLifecycle 状态迁移（begin_start / begin_stop / close / fail / snapshot）
- Runtime 未注册 provider 时 fail-closed
- Runtime.unsubscribe 幂等清理
- subscriptions() 快照与重复 id 拒绝
"""

from __future__ import annotations

import pytest

from tstdx.errors import ValidationError
from tstdx.runtime import StreamHandle, create_runtime, runtime_subscribe
from tstdx.streaming.state import StreamState


# ---------------------------------------------------------------------------
# 辅助夹具
# ---------------------------------------------------------------------------


def _tdx_client():
    """最小可用的 tdx 客户端替身（subscribe 只走编排，不实际回源）。"""

    class _Client:
        def quotes(self, symbols):
            return list(symbols)

    return _Client()


# ---------------------------------------------------------------------------
# 导出与句柄结构
# ---------------------------------------------------------------------------


def test_stream_handle_is_exported_from_runtime_package() -> None:
    """StreamHandle 应从 tstdx.runtime 公共 API 导出。"""
    assert StreamHandle.__name__ == "StreamHandle"


def test_runtime_subscribe_builds_plan_and_lifecycle() -> None:
    """runtime_subscribe 编译 StreamPlan 并绑定 CREATED 生命状态。"""
    runtime = create_runtime(tdx=_tdx_client())
    handle = runtime_subscribe(runtime, ("sh600000", "sh600519"), provider="tdx")

    assert handle.plan.capability == "quotes"
    assert handle.plan.provider == "tdx"
    assert handle.plan.channel == "quotation"
    assert handle.plan.symbols == ("sh600000", "sh600519")
    assert handle.lifecycle.state is StreamState.CREATED


# ---------------------------------------------------------------------------
# Runtime.subscribe 编排入口
# ---------------------------------------------------------------------------


def test_runtime_subscribe_assigns_id_and_registers() -> None:
    """Runtime.subscribe 分配 id、注册到句柄集合、返回可寻址的 handle。"""
    runtime = create_runtime(tdx=_tdx_client())
    handle = runtime.subscribe("quotes", ("sh600000", "sh600519"))

    assert handle.id.startswith("stream-")
    assert runtime.get_subscription(handle.id) is handle
    assert handle.id in runtime.subscriptions()


def test_runtime_subscribe_with_explicit_id() -> None:
    """调用方可指定自定义 subscription_id。"""
    runtime = create_runtime(tdx=_tdx_client())
    handle = runtime.subscribe(
        "quotes", ("sh600000",), subscription_id="watchlist-1"
    )

    assert handle.id == "watchlist-1"
    assert runtime.get_subscription("watchlist-1") is handle


def test_runtime_subscribe_rejects_duplicate_id() -> None:
    """重复 subscription_id 抛 ValueError。"""
    runtime = create_runtime(tdx=_tdx_client())
    runtime.subscribe("quotes", ("sh600000",), subscription_id="dup")
    with pytest.raises(ValueError, match="already in use"):
        runtime.subscribe("quotes", ("sh600519",), subscription_id="dup")


def test_runtime_subscribe_auto_ids_are_monotonic() -> None:
    """默认 id 递增且互不冲突。"""
    runtime = create_runtime(tdx=_tdx_client())
    a = runtime.subscribe("quotes", ("sh600000",))
    b = runtime.subscribe("quotes", ("sh600519",))
    assert a.id != b.id
    assert int(a.id.rsplit("-", 1)[1]) < int(b.id.rsplit("-", 1)[1])


def test_runtime_subscribe_does_not_start_worker() -> None:
    """subscribe 只做编排，不自动启动 Worker；状态保持 CREATED。"""
    runtime = create_runtime(tdx=_tdx_client())
    handle = runtime.subscribe("quotes", ("sh600000",))
    assert handle.state is StreamState.CREATED
    assert handle.lifecycle.snapshot().state is StreamState.CREATED


# ---------------------------------------------------------------------------
# StreamPlanner fail-closed 校验传播
# ---------------------------------------------------------------------------


def test_subscribe_rejects_empty_symbols() -> None:
    """空 symbols 抛 ValidationError。"""
    runtime = create_runtime(tdx=_tdx_client())
    with pytest.raises(ValidationError, match="不能为空"):
        runtime.subscribe("quotes", ())


def test_subscribe_rejects_non_tdx_provider() -> None:
    """StreamPlanner 只允许 tdx provider；其他 provider fail-closed。"""
    runtime = create_runtime(tdx=_tdx_client())
    with pytest.raises(ValidationError, match="Stateful quotes stream"):
        runtime.subscribe("quotes", ("sh600000",), provider="eastmoney")


def test_subscribe_rejects_non_quotation_channel() -> None:
    """runtime.subscribe 通过 StreamSpec.build 走 canonical channel=quotation。"""
    # 直接构造 spec 验证 channel 白名单（subscribe 不暴露 channel 参数）。
    from tstdx.stream_contract import StreamPlanner, StreamSpec

    with pytest.raises(ValidationError, match="canonical channel"):
        StreamPlanner().compile(
            StreamSpec.build(("sh600000",), provider="tdx", channel="trade")
        )


def test_subscribe_rejects_non_positive_interval() -> None:
    """interval <= 0 抛 ValidationError。"""
    runtime = create_runtime(tdx=_tdx_client())
    with pytest.raises(ValidationError, match="interval"):
        runtime.subscribe("quotes", ("sh600000",), interval=0)


def test_subscribe_rejects_negative_max_queue() -> None:
    """max_queue < 0 抛 ValidationError。"""
    runtime = create_runtime(tdx=_tdx_client())
    with pytest.raises(ValidationError, match="max_queue"):
        runtime.subscribe("quotes", ("sh600000",), max_queue=-1)


# ---------------------------------------------------------------------------
# Provider 可用性预检（Runtime 侧）
# ---------------------------------------------------------------------------


def test_subscribe_fails_lifecycle_when_provider_not_registered() -> None:
    """Runtime 未注册 tdx provider 时，handle 立即 FAILED。"""
    runtime = create_runtime()  # 无 backend
    handle = runtime_subscribe(runtime, ("sh600000",), provider="tdx")

    # runtime_has_provider 判定失败 -> lifecycle.fail
    assert handle.state is StreamState.FAILED
    snapshot = handle.lifecycle.snapshot()
    assert snapshot.state is StreamState.FAILED
    assert "tdx" in (snapshot.failure_reason or "")


# ---------------------------------------------------------------------------
# 生命周期状态迁移
# ---------------------------------------------------------------------------


def test_handle_begin_start_is_idempotent() -> None:
    """begin_start：CREATED -> RUNNING；已 RUNNING 时返回 False（安全 no-op）。"""
    runtime = create_runtime(tdx=_tdx_client())
    handle = runtime.subscribe("quotes", ("sh600000",))

    assert handle.begin_start() is True
    assert handle.state is StreamState.RUNNING
    # 已 RUNNING：返回 False 表示「未发生状态迁移」，状态仍保持 RUNNING。
    assert handle.begin_start() is False
    assert handle.state is StreamState.RUNNING


def test_handle_begin_stop_preserves_failed_semantics() -> None:
    """FAILED 状态在 begin_stop/close 后仍保持 FAILED（fail-closed 语义）。"""
    runtime = create_runtime(tdx=_tdx_client())
    handle = runtime.subscribe("quotes", ("sh600000",))
    handle.fail("worker died")
    assert handle.state is StreamState.FAILED

    handle.begin_stop()
    handle.close()
    assert handle.state is StreamState.FAILED


def test_handle_close_from_running_preserves_running() -> None:
    """RUNNING 上直接 close() 会跳 CLOSED（跳过 STOPPING），仍合法。"""
    runtime = create_runtime(tdx=_tdx_client())
    handle = runtime.subscribe("quotes", ("sh600000",))
    handle.begin_start()
    assert handle.state is StreamState.RUNNING
    handle.close()
    assert handle.state is StreamState.CLOSED


def test_handle_snapshot_captures_state_and_metadata() -> None:
    """snapshot 返回不可变副本，含 state 与 failure_reason。"""
    runtime = create_runtime(tdx=_tdx_client())
    handle = runtime.subscribe("quotes", ("sh600000",))
    handle.begin_start()
    snap1 = handle.snapshot()
    handle.fail("boom")
    snap2 = handle.snapshot()

    assert snap1.state is StreamState.RUNNING
    assert snap1.failure_reason is None
    assert snap2.state is StreamState.FAILED
    assert snap2.failure_reason == "boom"


def test_stream_state_alias_matches_state() -> None:
    """stream_state() 别名与 state 一致。"""
    runtime = create_runtime(tdx=_tdx_client())
    handle = runtime.subscribe("quotes", ("sh600000",))
    assert handle.stream_state() is handle.state


def test_require_subscribable_rejects_terminal_states() -> None:
    """require_subscribable 在 STOPPING/CLOSED/FAILED 上抛 SubscriptionError。"""
    from tstdx.errors import SubscriptionError

    runtime = create_runtime(tdx=_tdx_client())
    handle = runtime.subscribe("quotes", ("sh600000",))
    handle.fail("terminated")

    with pytest.raises(SubscriptionError, match="终止状态"):
        handle.require_subscribable()


# ---------------------------------------------------------------------------
# Runtime.unsubscribe 与集合管理
# ---------------------------------------------------------------------------


def test_unsubscribe_stops_and_removes_handle() -> None:
    """unsubscribe 幂等清理：stop+close，并从集合移除。"""
    runtime = create_runtime(tdx=_tdx_client())
    handle = runtime.subscribe("quotes", ("sh600000",))
    handle.begin_start()

    removed = runtime.unsubscribe(handle.id)
    assert removed is handle
    assert removed.state is StreamState.CLOSED
    assert runtime.get_subscription(handle.id) is None
    assert handle.id not in runtime.subscriptions()


def test_unsubscribe_is_idempotent() -> None:
    """重复 unsubscribe 返回 None，不抛异常。"""
    runtime = create_runtime(tdx=_tdx_client())
    handle = runtime.subscribe("quotes", ("sh600000",))
    assert runtime.unsubscribe(handle.id) is handle
    assert runtime.unsubscribe(handle.id) is None
    assert runtime.unsubscribe("never-existed") is None


def test_unsubscribe_preserves_failed_semantics() -> None:
    """已 FAILED 的订阅 unsubscribe 后仍保持 FAILED（不擦除失败原因）。"""
    runtime = create_runtime(tdx=_tdx_client())
    handle = runtime.subscribe("quotes", ("sh600000",))
    handle.fail("worker crashed")

    removed = runtime.unsubscribe(handle.id)
    assert removed.state is StreamState.FAILED


def test_subscriptions_returns_snapshot() -> None:
    """subscriptions() 返回独立快照；句柄变更不影响快照。"""
    runtime = create_runtime(tdx=_tdx_client())
    h1 = runtime.subscribe("quotes", ("sh600000",))
    h2 = runtime.subscribe("quotes", ("sh600519",))

    snap = runtime.subscriptions()
    assert set(snap.keys()) == {h1.id, h2.id}
    runtime.unsubscribe(h1.id)
    # 原快照仍包含 h1（独立副本）
    assert h1.id in snap
    assert h1.id not in runtime.subscriptions()


def test_multiple_subscriptions_track_independently() -> None:
    """多个订阅的生命周期互相独立，状态不串扰。"""
    runtime = create_runtime(tdx=_tdx_client())
    h1 = runtime.subscribe("quotes", ("sh600000",))
    h2 = runtime.subscribe("quotes", ("sh600519",))

    h1.begin_start()
    h1.fail("h1 failed")

    assert h1.state is StreamState.FAILED
    assert h2.state is StreamState.CREATED

    h2.begin_start()
    h2.close()
    assert h2.state is StreamState.CLOSED
    # h1 的 FAILED 未被 h2 操作影响
    assert h1.state is StreamState.FAILED
