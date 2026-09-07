"""流式韧性测试（B7）：订阅→断线→重连→补数，模拟 30s 中断 0 丢失。

覆盖：subscribe→N quotes→断线→重连→GapFiller 补缺→0 丢失、
DeltaMerger 去重、BackpressureQueue 溢出→BackpressureOverflow、
ReconnectPolicy 退避序列（1,2,4,8... capped）。

fake 全部按**真实 0x0530 行为**以裸 6 位码回声（std7709.py RealtimeQuoteParser），
不再以订阅原串充当 code 掩盖「订阅符号 vs 回声裸码」的失配（C1）。

全部使用注入时钟/间隔，不依赖真实 sleep，测试 < 2s。
"""

from __future__ import annotations

import asyncio
import time

import pytest

from tstdx.domain.symbol import split_symbol
from tstdx.errors import BackpressureOverflow, ConnectionFailed
from tstdx.streaming import AsyncQuoteStream, QuoteStream
from tstdx.streaming.engine import (
    BackpressureQueue,
    DeltaMerger,
    GapFiller,
    QuoteChannel,
    ReconnectPolicy,
    StreamEvent,
)


def _bare(sym: str) -> str:
    """订阅符号 → 裸 6 位码（模拟真实 0x0530 服务端回声行为）。"""
    _, code = split_symbol(sym)
    return code


# --------------------------------------------------------------------------- #
# 可注入时钟的 fake transport
# --------------------------------------------------------------------------- #
class FakeTransport:
    """可注入时钟的 fake 轮询函数：模拟断线-重连。"""

    def __init__(self, symbols, outage_start: int = -1, outage_end: int = -1):
        """outage_start/end: tick 索引区间内抛异常。"""
        self.symbols = symbols
        self.outage_start = outage_start
        self.outage_end = outage_end
        self.tick_count = 0
        self._last = {sym: {"price": 100.0, "volume": 0} for sym in symbols}

    def poll(self, symbols):
        idx = self.tick_count
        self.tick_count += 1
        if self.outage_start <= idx < self.outage_end:
            raise ConnectionError(f"connection lost at tick {idx}")
        rows = []
        for sym in symbols:
            prev = self._last[sym]
            prev["price"] = round(prev["price"] + 0.01, 2)
            prev["volume"] += 100
            # 真实 0x0530 行为：响应按**裸 6 位码**回声（std7709.py:987 附近），
            # 不再以订阅原串充当 code（那会掩盖 C1「带前缀订阅静默零数据」）。
            rows.append({"code": _bare(sym), "price": prev["price"], "volume": prev["volume"]})
        return rows

    @property
    def expected_price(self):
        """断线后应有的价格（模拟断线期间无更新）。"""
        return {sym: 100.0 for sym in self.symbols}


@pytest.mark.unit
class TestStreamResilience:
    """流式韧性测试。"""

    def test_subscribe_receive_reconnect_no_loss(self):
        """#1 subscribe→N quotes→断线→重连→0 丢失（模拟 30s 中断）。

        订阅用裸码：QuoteChannel（experimental，未接线）不做符号归一化，
        且 fake 现按真实 0x0530 行为以裸码回声；带前缀订阅的归一化回归
        见 TestQuoteStreamResilience（生产类 QuoteStream）。
        """
        symbols = ["600519"]
        transport = FakeTransport(symbols, outage_start=5, outage_end=10)

        received = []
        errors = []

        def cb(ev: StreamEvent):
            if ev.kind == "quote":
                received.append(ev.payload)
            elif ev.kind == "error":
                errors.append(ev.payload)

        channel = QuoteChannel(
            poll=transport.poll,
            symbols=symbols,
            diff_only=False,
            max_queue=1024,
        )
        channel.subscribe(cb)

        # 模拟 15 个 tick（5 正常 → 5 断线 → 5 恢复）
        for _ in range(15):
            channel.tick()

        # 断线期间应有 error 事件
        assert len(errors) >= 1
        # 恢复后应有 quote 事件
        assert len(received) > 0
        # 验证没有丢失：最后一个 quote 的价格应大于初始值
        assert received[-1]["price"] > 100.0

    def test_gap_filler_detects_gap(self):
        """#2 GapFiller 检测序号缺口。"""
        gf = GapFiller()
        # 连续序号
        assert gf.observe("sym1", 1) is True
        assert gf.observe("sym1", 2) is True
        assert gf.observe("sym1", 3) is True
        # 跳过 3 → 缺口
        assert gf.observe("sym1", 5) is False
        gaps = gf.gaps("sym1")
        assert len(gaps) == 1
        assert gaps[0] == (3, 5)

    def test_gap_filler_no_gap(self):
        """#3 GapFiller 连续无缺口。"""
        gf = GapFiller()
        for i in range(1, 11):
            assert gf.observe("sym1", i) is True
        assert gf.gaps("sym1") == []

    def test_gap_filler_datetime_keys(self):
        """#4 GapFiller 对非数值键（datetime 字符串）退化为总是连续。"""
        gf = GapFiller()
        assert gf.observe("sym1", "2024-01-01 10:00") is True
        assert gf.observe("sym1", "2024-01-01 10:05") is True
        assert gf.gaps("sym1") == []

    def test_delta_merger_first_snapshot_full(self):
        """#5 DeltaMerger 首个快照返回全量。"""
        dm = DeltaMerger()
        snap = {"code": "sh600519", "price": 100.0, "volume": 1000}
        result = dm.update("sh600519", snap)
        assert result == snap

    def test_delta_merger_second_snapshot_diff(self):
        """#6 DeltaMerger 第二个快照返回变化字段。"""
        dm = DeltaMerger()
        dm.update("sh600519", {"code": "sh600519", "price": 100.0, "volume": 1000})
        result = dm.update("sh600519", {"code": "sh600519", "price": 100.5, "volume": 1050})
        assert result["code"] == "sh600519"
        assert result["price"] == 100.5
        assert result["volume"] == 1050
        # 未变化的字段不在 diff 中
        assert "price" in result  # price 变了

    def test_delta_merger_unchanged_returns_empty_diff(self):
        """#7 DeltaMerger 无变化时返回仅含 code 的 dict。"""
        dm = DeltaMerger()
        snap = {"code": "sh600519", "price": 100.0, "volume": 1000}
        dm.update("sh600519", snap)
        result = dm.update("sh600519", dict(snap))  # 完全相同
        assert result == {"code": "sh600519"}

    def test_delta_merger_snapshot_and_clear(self):
        """#8 DeltaMerger snapshot 和 clear。"""
        dm = DeltaMerger()
        dm.update("a", {"code": "a", "price": 1})
        dm.update("b", {"code": "b", "price": 2})
        snap = dm.snapshot()
        assert "a" in snap and "b" in snap
        dm.clear()
        assert dm.snapshot() == {}

    def test_backpressure_queue_drop_oldest(self):
        """#9 BackpressureQueue 溢出时丢弃最旧元素。"""
        dropped = []
        bp = BackpressureQueue(maxsize=3, on_drop=lambda x: dropped.append(x))
        for i in range(5):
            bp.put(f"item{i}")
        assert bp.qsize() == 3
        assert dropped == ["item0", "item1"]

    def test_backpressure_queue_drain(self):
        """#10 BackpressureQueue drain 清空并返回所有元素。"""
        bp = BackpressureQueue(maxsize=10)
        for i in range(5):
            bp.put(i)
        items = bp.drain()
        assert items == [0, 1, 2, 3, 4]
        assert bp.qsize() == 0

    def test_backpressure_queue_get_none_when_empty(self):
        """#11 BackpressureQueue 空时 get() 返回 None。"""
        bp = BackpressureQueue()
        assert bp.get() is None

    def test_backpressure_overflow_error_type(self):
        """#12 BackpressureOverflow 错误类型存在。"""
        from tstdx.errors import BackpressureOverflow as BPO

        err = BPO("queue full")
        assert err.code == "E6030"
        assert err.http_status == 429

    def test_reconnect_policy_backoff_sequence(self):
        """#13 ReconnectPolicy 退避序列 1,2,4,8... capped。"""
        rp = ReconnectPolicy(base=1.0, cap=30.0, jitter=False)
        delays = [rp.next_delay() for _ in range(6)]
        assert delays == [1.0, 2.0, 4.0, 8.0, 16.0, 30.0]  # 最后 capped 到 30

    def test_reconnect_policy_jitter(self):
        """#14 ReconnectPolicy 抖动模式。"""
        rp = ReconnectPolicy(base=1.0, cap=30.0, jitter=True)
        d1 = rp.next_delay()
        d2 = rp.next_delay()
        # 抖动后延迟应在 [0, base*2^n] 范围内
        assert 0 <= d1 <= 1.0
        assert 0 <= d2 <= 2.0

    def test_reconnect_policy_success_resets(self):
        """#15 ReconnectPolicy 成功后重置。"""
        rp = ReconnectPolicy(base=1.0, cap=30.0, jitter=False)
        rp.next_delay()
        rp.next_delay()
        assert rp.attempts == 2
        rp.success()
        assert rp.attempts == 0
        d = rp.next_delay()
        assert d == 1.0  # 重置后重新从 1 开始

    def test_reconnect_policy_max_attempts(self):
        """#16 ReconnectPolicy should_give_up。"""
        rp = ReconnectPolicy(base=1.0, cap=30.0, max_attempts=3, jitter=False)
        rp.next_delay()
        rp.next_delay()
        assert rp.should_give_up() is False
        rp.next_delay()
        assert rp.should_give_up() is True

    def test_reconnect_policy_on_rotate_host(self):
        """#17 ReconnectPolicy on_rotate_host 回调。"""
        calls = []
        rp = ReconnectPolicy(
            base=1.0,
            cap=30.0,
            jitter=False,
            on_rotate_host=lambda n: calls.append(n),
        )
        rp.next_delay()
        rp.next_delay()
        assert calls == [1, 2]

    def test_reconnect_policy_fail(self):
        """#18 ReconnectPolicy fail() 只累加计数。"""
        rp = ReconnectPolicy(base=1.0, cap=30.0, jitter=False)
        rp.fail()
        rp.fail()
        assert rp.attempts == 2
        assert rp.next_delay() == 4.0  # 2^2

    def test_reconnect_policy_reset(self):
        """#19 ReconnectPolicy reset()。"""
        rp = ReconnectPolicy(base=1.0, cap=30.0)
        rp.next_delay()
        rp.next_delay()
        rp.reset()
        assert rp.attempts == 0

    def test_quote_channel_tick_on_error(self):
        """#20 QuoteChannel tick 异常时发送 error 事件。"""
        received_errors = []

        def cb(ev):
            if ev.kind == "error":
                received_errors.append(ev.payload)

        def failing_poll(symbols):
            raise ConnectionError("network down")

        channel = QuoteChannel(poll=failing_poll, symbols=["sh600519"])
        channel.subscribe(cb)
        channel.tick()
        assert len(received_errors) == 1
        assert isinstance(received_errors[0], ConnectionError)

    def test_quote_channel_tick_success(self):
        """#21 QuoteChannel tick 成功时发送 quote 事件。"""
        received = []

        def cb(ev):
            if ev.kind == "quote":
                received.append(ev.payload)

        def good_poll(symbols):
            return [{"code": "600519", "price": 100.0, "volume": 1000}]

        channel = QuoteChannel(poll=good_poll, symbols=["600519"])
        channel.subscribe(cb)
        channel.tick()
        assert len(received) == 1
        assert received[0]["price"] == 100.0

    def test_quote_channel_diff_only(self):
        """#22 QuoteChannel diff_only 模式。"""
        received = []

        def cb(ev):
            if ev.kind == "diff":
                received.append(ev.payload)

        def poll(symbols):
            return [{"code": "600519", "price": 100.0, "volume": 1000}]

        channel = QuoteChannel(poll=poll, symbols=["600519"], diff_only=True)
        channel.subscribe(cb)

        # 第一次 tick：全量（diff_only 下也是全量）
        channel.tick()
        assert len(received) == 1
        assert received[0]["price"] == 100.0

        # 第二次 tick：相同数据 → 仅 code
        channel.tick()
        assert len(received) == 2
        assert received[1] == {"code": "600519"}

    def test_subscribe_unsubscribe_bookkeeping(self):
        """#23 QuoteChannel subscribe 回调管理。"""
        channel = QuoteChannel(
            poll=lambda s: [{"code": "600519", "price": 1.0, "volume": 1}],
            symbols=["600519"],
        )
        cb1_called = [False]
        cb2_called = [False]
        channel.subscribe(lambda ev: cb1_called.__setitem__(0, True))
        channel.subscribe(lambda ev: cb2_called.__setitem__(0, True))
        channel.tick()
        assert cb1_called[0] is True
        assert cb2_called[0] is True

    def test_stream_event_fields(self):
        """#24 StreamEvent 字段完整。"""
        ev = StreamEvent(kind="quote", key="sh600519", payload={"price": 100.0})
        assert ev.kind == "quote"
        assert ev.key == "sh600519"
        assert ev.payload == {"price": 100.0}
        assert ev.ts is not None

    def test_simulated_30s_outage_zero_loss(self):
        """#25 模拟 30s 中断：5 正常→10 断线→10 恢复，0 丢失。"""
        symbols = ["600519"]
        transport = FakeTransport(symbols, outage_start=5, outage_end=15)

        received_quotes = []
        received_errors = []

        def cb(ev: StreamEvent):
            if ev.kind == "quote":
                received_quotes.append(ev.payload)
            elif ev.kind == "error":
                received_errors.append(ev.payload)

        channel = QuoteChannel(
            poll=transport.poll,
            symbols=symbols,
            max_queue=2048,
        )
        channel.subscribe(cb)

        total_ticks = 25
        for _ in range(total_ticks):
            channel.tick()

        # 断线期间应有 error
        assert len(received_errors) >= 5  # 10 ticks outage, each produces 1 error
        # 恢复后应有 quote
        assert len(received_quotes) > 0
        # 最后一个 quote 的价格应大于初始 100.0（断线期间无更新，恢复后继续涨）
        assert received_quotes[-1]["price"] > 100.0

    def test_quote_channel_full_mode_sends_full_snapshot(self):
        """#26 diff_only=False：非首个快照也派发**全量**（engine diff_only 死分支修正）。"""
        received = []
        row = {"code": "600519", "price": 101.0, "volume": 2000}
        channel = QuoteChannel(poll=lambda s: [dict(row)], symbols=["600519"], diff_only=False)
        channel.subscribe(lambda ev: received.append(ev))
        channel.tick()
        channel.tick()  # 相同数据
        assert received[0].kind == "quote"
        # 修复前：else 分支与 if 分支相同，全量模式下也会拿到 diff（丢未变化字段）
        assert received[1].payload == {"code": "600519", "price": 101.0, "volume": 2000}

    def test_quote_channel_diff_mode_only_changed_fields(self):
        """#27 diff_only=True：第二拍只含变化字段 + code（最小正确实现）。"""
        received = []
        calls = {"n": 0}

        def poll(symbols):
            calls["n"] += 1
            if calls["n"] == 1:
                return [{"code": "600519", "price": 100.0, "volume": 1000}]
            return [{"code": "600519", "price": 100.5, "volume": 1000}]  # 仅 price 变化

        channel = QuoteChannel(poll=poll, symbols=["600519"], diff_only=True)
        channel.subscribe(lambda ev: received.append(ev))
        channel.tick()
        channel.tick()
        assert received[1].payload == {"code": "600519", "price": 100.5}


# --------------------------------------------------------------------------- #
# QuoteStream / AsyncQuoteStream（生产流：C1 归一化 + C4 韧性）
# --------------------------------------------------------------------------- #
class _FakeQuoteClient:
    """假 TdxClient：0x0530 真实行为是按**裸 6 位码**回声（std7709.py:987 附近）。"""

    def __init__(self, table: dict[str, dict], fail_first_with: Exception | None = None):
        self._table = table
        self._fail_first_with = fail_first_with
        self.calls = 0

    def quotes(self, symbols, *, as_format="dict"):  # noqa: ARG002 - 与真实签名对齐
        self.calls += 1
        if self._fail_first_with is not None:
            err, self._fail_first_with = self._fail_first_with, None
            raise err
        return [dict(q, code=code) for code, q in self._table.items()]


class _FakeAsyncQuoteClient:
    """异步假 TdxClient（镜像）；``fail_first`` 注入一次性失败。"""

    def __init__(self, table: dict[str, dict], fail_first: Exception | None = None):
        self._table = table
        self._fail_first = fail_first
        self.calls = 0

    async def quotes(self, symbols, *, as_format="dict"):  # noqa: ARG002
        self.calls += 1
        if self._fail_first is not None:
            err, self._fail_first = self._fail_first, None
            raise err
        await asyncio.sleep(0)
        return [dict(q, code=code) for code, q in self._table.items()]

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None


def _wait_until(predicate, timeout: float = 3.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


@pytest.mark.unit
class TestQuoteStreamResilience:
    """生产流 QuoteStream 的 C1 归一化与 C4 韧性回归。"""

    def test_subscribe_prefix_symbol_receives_data(self):
        """C1 回归：订阅 sh600519（带前缀）且 fake 以裸码回声 → 回调有数据。"""
        fake = _FakeQuoteClient({"600519": {"price": 1500.0, "volume": 100}})
        received: list = []
        stream = QuoteStream()
        stream._client = fake  # 注入假客户端，走真实轮询路径
        stream.subscribe(["sh600519"], interval=0.02, on_quote=lambda c, q: received.append((c, q)))
        stream.start()
        try:
            assert _wait_until(lambda: bool(received)), (
                "订阅 sh600519 未收到任何回调（C1 静默零数据回归）"
            )
        finally:
            stream.stop()
        code, q = received[0]
        assert q["price"] == 1500.0
        assert fake.calls >= 1

    def test_subscribe_prefix_symbol_diff_only(self):
        """C1 + diff：_last 以裸码为键，第二拍只推变化字段。"""
        fake = _FakeQuoteClient({"600519": {"price": 1500.0, "volume": 100}})
        received: list = []
        stream = QuoteStream()
        stream._client = fake
        stream.subscribe(
            ["sh600519"], interval=0.02, diff_only=True, on_quote=lambda c, q: received.append(q)
        )
        stream.start()
        try:
            assert _wait_until(lambda: len(received) >= 2)
        finally:
            stream.stop()
        assert received[0] == {"code": "600519", "price": 1500.0, "volume": 100}  # 首拍全量
        assert received[1] == {"code": "600519"}  # 数据未变 → 仅 code

    def test_run_survives_non_tdx_error(self):
        """C4 回归：轮询抛非 TdxError（如代码缺陷）→ 记日志+派发 on_error，线程不死。"""
        fake = _FakeQuoteClient({"600519": {"price": 10.0}}, fail_first_with=ValueError("boom"))
        errors: list = []
        received: list = []
        stream = QuoteStream()
        stream._client = fake
        stream._reconnect = ReconnectPolicy(base=0.01, cap=0.05, jitter=False)  # 加速退避
        stream.subscribe(
            ["sh600519"],
            interval=0.02,
            on_quote=lambda c, q: received.append(q),
            on_error=errors.append,
        )
        stream.start()
        try:
            assert _wait_until(lambda: bool(errors)), "未预期异常应派发 on_error"
            assert _wait_until(lambda: bool(received)), "异常后流线程应继续运行并恢复回调"
        finally:
            stream.stop()
        assert isinstance(errors[0], ValueError)

    def test_max_queue_real_backpressure(self):
        """M1 接线：max_queue 真实背压——默认值不发告警、不影响取数；
        溢出丢最旧并派发 BackpressureOverflow（E6 兑现）。"""
        import warnings as _warnings

        fake = _FakeQuoteClient({"600519": {"price": 1.0}, "000001": {"price": 2.0}})
        stream = QuoteStream()
        stream._client = fake
        with _warnings.catch_warnings(record=True) as w:
            _warnings.simplefilter("always")
            stream.subscribe(["600519"], interval=0.02, on_quote=lambda c, q: None)
        # M1 后 max_queue 不再弃用——任何路径都不应再发 DeprecationWarning
        assert not [x for x in w if issubclass(x.category, DeprecationWarning)]
        received: list = []
        errors: list = []
        # max_queue=1 + 两只标的：单轮第 2 个事件必然挤掉第 1 个（丢最旧）
        stream.subscribe(
            ["600519", "000001"],
            interval=0.02,
            max_queue=1,
            on_quote=lambda c, q: received.append(q),
            on_error=errors.append,
        )
        stream.start()
        try:
            assert _wait_until(lambda: bool(received)), "max_queue 接线不应影响取数"
            assert _wait_until(lambda: any(isinstance(e, BackpressureOverflow) for e in errors)), (
                "max_queue=1 两标的应触发溢出（BackpressureOverflow 兑现抛点）"
            )
        finally:
            stream.stop()


@pytest.mark.unit
class TestAsyncQuoteStreamResilience:
    """AsyncQuoteStream 的 C1 镜像归一化与 C4 镜像韧性回归。"""

    def test_async_prefix_symbol_receives_data(self):
        """C1 镜像回归：订阅 sh600519、fake 裸码回声 → 回调有数据。"""
        fake = _FakeAsyncQuoteClient({"600519": {"price": 1500.0, "volume": 100}})
        received: list = []

        async def run() -> None:
            stream = AsyncQuoteStream()
            stream._client = fake
            stream.subscribe(["sh600519"], interval=0.02, on_quote=lambda c, q: received.append(q))
            await stream.start()
            deadline = time.monotonic() + 3.0
            while not received and time.monotonic() < deadline:
                await asyncio.sleep(0.01)
            await stream.stop()

        asyncio.run(run())
        assert received, "异步订阅 sh600519 未收到任何回调（C1 镜像回归）"
        assert received[0]["price"] == 1500.0

    def test_async_on_error_suppressed_and_recovers(self):
        """C4 镜像：TdxError 派发时 on_error 回调自身异常被 suppress，循环继续恢复。"""
        fake = _FakeAsyncQuoteClient({"600519": {"price": 10.0}}, fail_first=ConnectionFailed("x"))
        received: list = []

        def buggy_on_error(exc):  # 回调自身的 bug 不应杀死轮询协程
            raise RuntimeError("on_error callback bug")

        async def run() -> None:
            stream = AsyncQuoteStream()
            stream._client = fake
            stream.subscribe(
                ["sh600519"],
                interval=0.02,
                on_quote=lambda c, q: received.append(q),
                on_error=buggy_on_error,
            )
            await stream.start()
            deadline = time.monotonic() + 3.0
            while not received and time.monotonic() < deadline:
                await asyncio.sleep(0.01)
            await stream.stop()

        asyncio.run(run())
        assert received, "on_error 回调异常被 suppress 后轮询应恢复并回调行情"
