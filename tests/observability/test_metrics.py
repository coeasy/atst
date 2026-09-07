"""可观测性指标测试（§37）：验证 zero-dep Prometheus 风格指标子系统。

覆盖：Counter/Gauge/Histogram 递增、render() 输出格式、
instrument_client() 埋点、线程安全、零依赖导入。
"""

from __future__ import annotations

import threading

import pytest

from tstdx.observability.metrics import (
    Counter,
    Gauge,
    Histogram,
    Metrics,
    Registry,
    Summary,
    instrument_client,
)
from tstdx.observability.metrics import (
    metrics as global_metrics,
)


@pytest.mark.unit
class TestMetrics:
    """指标子系统测试。"""

    def test_counter_increment(self):
        """#1 Counter 递增。"""
        c = Counter("test_counter", "test", labelnames=("label1",))
        assert c.value() == 0.0
        c.inc()
        assert c.value() == 1.0
        c.inc(5)
        assert c.value() == 6.0
        c.inc(labels={"label1": "a"})
        assert c.value(labels={"label1": "a"}) == 1.0
        assert c.value() == 6.0  # 不同标签独立

    def test_counter_negative_rejected(self):
        """#2 Counter 不允许负数。"""
        c = Counter("test_counter", "test")
        with pytest.raises(ValueError):
            c.inc(-1)

    def test_gauge_set_inc_dec(self):
        """#3 Gauge set/inc/dec。"""
        g = Gauge("test_gauge", "test")
        g.set(10)
        assert g.value() == 10.0
        g.inc(5)
        assert g.value() == 15.0
        g.dec(3)
        assert g.value() == 12.0

    def test_gauge_negative(self):
        """#4 Gauge 可以为负。"""
        g = Gauge("test_gauge", "test")
        g.set(5)
        g.dec(10)
        assert g.value() == -5.0

    def test_histogram_observe(self):
        """#5 Histogram 观测。"""
        h = Histogram("test_hist", "test", buckets=(0.1, 0.5, 1.0, 2.0))
        h.observe(0.05)
        h.observe(0.3)
        h.observe(1.5)
        # 检查内部状态
        k = h._key({})
        assert k == ()
        s = h._series[()]
        assert s["_count"] == 3
        assert s["_sum"] == pytest.approx(1.85)

    def test_summary_observe(self):
        """#6 Summary 观测。"""
        s = Summary("test_summary", "test")
        s.observe(1.0)
        s.observe(2.0)
        s.observe(3.0)
        assert s._series[()]["_count"] == 3.0
        assert s._series[()]["_sum"] == 6.0

    def test_render_help_type_lines(self):
        """#7 render() 输出包含 # HELP 和 # TYPE 行。"""
        c = Counter("my_counter", "my description")
        c.inc()
        output = c.render_prometheus()
        assert "# HELP my_counter my description" in output
        assert "# TYPE my_counter counter" in output
        assert "my_counter 1.0" in output

    def test_render_with_labels(self):
        """#8 render() 带标签输出。"""
        c = Counter("my_counter", "desc", labelnames=("host",))
        c.inc(labels={"host": "web01"})
        output = c.render_prometheus()
        assert 'my_counter{host="web01"} 1.0' in output

    def test_registry_register_and_names(self):
        """#9 Registry 注册和列表。"""
        reg = Registry()
        c = Counter("a", "x")
        g = Gauge("b", "y")
        reg.register(c)
        reg.register(g)
        assert sorted(reg.names()) == ["a", "b"]

    def test_registry_idempotent_register(self):
        """#10 Registry 重复注册同名指标是幂等的。"""
        reg = Registry()
        c1 = Counter("same", "x")
        c2 = Counter("same", "y")
        reg.register(c1)
        returned = reg.register(c2)
        assert returned is c1  # 返回已注册的

    def test_registry_snapshot(self):
        """#11 Registry snapshot 可 JSON 序列化。"""
        import json

        reg = Registry()
        c = Counter("cnt", "x")
        c.inc(5)
        reg.register(c)
        snap = reg.snapshot()
        assert "cnt" in snap
        json.dumps(snap)  # 不应抛异常

    def test_instrument_client(self):
        """#12 instrument_client 无侵入埋点。"""
        call_count = [0]

        class FakeClient:
            def request(self, cmd, body, **kw):
                call_count[0] += 1
                if call_count[0] == 2:
                    raise RuntimeError("fail")
                return {"data": "ok"}

        # 重置全局指标
        metrics = Metrics()
        client = FakeClient()
        instrumented = instrument_client(client, metrics=metrics)
        assert instrumented is client  # 原地修改

        # 第一次成功
        result = instrumented.request(0x0530, b"body")
        assert result == {"data": "ok"}

        # 第二次失败
        with pytest.raises(RuntimeError):
            instrumented.request(0x0530, b"body")

        # 验证计数
        ok_count = metrics.request_total.value(labels={"command": "0x0530", "status": "ok"})
        err_count_val = metrics.request_total.value(labels={"command": "0x0530", "status": "err"})
        assert ok_count >= 1
        assert err_count_val >= 1

    def test_instrument_client_no_request_method(self):
        """#13 instrument_client 对无 request 方法的对象原样返回。"""
        obj = object()
        result = instrument_client(obj)
        assert result is obj

    def test_instrument_client_idempotent(self):
        """#14 instrument_client 重复埋点是幂等的。"""

        class FakeClient:
            def request(self, cmd, body, **kw):
                return "ok"

        client = FakeClient()
        instrument_client(client)
        instrument_client(client)  # 第二次不应报错
        # 验证 request 方法只被包装一次
        assert getattr(client.request, "_tstdx_instrumented", False) is True

    def test_thread_safety(self):
        """#15 线程安全：10 线程 × 100 次递增。"""
        c = Counter("thread_safe", "test")
        g = Gauge("thread_safe_g", "test")

        def worker():
            for _ in range(100):
                c.inc()
                g.set(1.0)

        threads = [threading.Thread(target=worker) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert c.value() == 1000.0  # 10 × 100

    def test_zero_dep_import(self):
        """#16 零依赖导入：不依赖 prometheus_client。"""
        # 确认模块可以正常导入（已在此处导入）
        assert global_metrics is not None
        assert isinstance(global_metrics, Metrics)
        # 确认 registry 可以渲染
        output = global_metrics.render()
        assert isinstance(output, str)
        assert "tstdx_protocol_parse_total" in output

    def test_record_parse(self):
        """#17 record_parse 便捷方法。"""
        metrics = Metrics()
        metrics.record_parse(tier="L1", family="quotation", command="0x0530", confidence=1.0)
        assert (
            metrics.parse_total.value(
                labels={"tier": "L1", "family": "quotation", "command": "0x0530"}
            )
            == 1.0
        )

    def test_record_request(self):
        """#18 record_request 便捷方法。"""
        metrics = Metrics()
        metrics.record_request(command="0x0530", ok=True, duration=0.1)
        metrics.record_request(command="0x0530", ok=False, duration=0.5)
        assert metrics.request_total.value(labels={"command": "0x0530", "status": "ok"}) == 1.0
        assert metrics.request_total.value(labels={"command": "0x0530", "status": "err"}) == 1.0

    def test_record_error(self):
        """#19 record_error 便捷方法。"""
        metrics = Metrics()
        metrics.record_error("TimeoutError")
        assert metrics.errors_total.value(labels={"error_type": "TimeoutError"}) == 1.0

    def test_render_full_metrics(self):
        """#20 完整渲染包含所有预置指标。"""
        output = global_metrics.render()
        # 应包含所有内置指标名
        assert "tstdx_protocol_parse_total" in output
        assert "tstdx_request_total" in output
        assert "tstdx_active_connections" in output
        assert "tstdx_stream_events_total" in output
        assert "tstdx_errors_total" in output
