"""F4 可观测性测试：V3 累积直方图黄金值、标签转义、带锁快照、
prometheus_client 桥接多标签修复、V1 serve 默认绑定、statsd 增量差分、
start_exporter 薄工厂。全部离线（本地回环 socket 除外）。"""

from __future__ import annotations

import inspect
import json
import sys
import threading
import time
import types
import urllib.error
import urllib.request

import pytest

pytestmark = pytest.mark.unit

from tstdx.observability import (  # noqa: E402
    OtelExporter,
    PrometheusExporter,
    StatsdExporter,
    start_exporter,
)
from tstdx.observability.metrics import (  # noqa: E402
    Counter,
    Gauge,
    Histogram,
    Metrics,
    Registry,
)


# --------------------------------------------------------------------------- #
# V3：Histogram 累积语义（黄金值）
# --------------------------------------------------------------------------- #
class TestHistogramCumulative:
    def _make(self) -> Histogram:
        h = Histogram("gold_hist", "golden", buckets=(1.0, 2.0, 5.0, 8.0))
        for v in (0.5, 1.5, 3.0, 6.0):
            h.observe(v)
        return h

    def test_golden_bucket_counts_cumulative(self):
        """黄金序列 [0.5,1.5,3.0,6.0] → 各桶累积计数。"""
        s = self._make()._series[()]
        assert s["le_1.0"] == 1  # 仅 0.5
        assert s["le_2.0"] == 2  # 0.5, 1.5
        assert s["le_5.0"] == 3  # 0.5, 1.5, 3.0
        assert s["le_8.0"] == 4  # 全部
        assert s["le_+Inf"] == 4

    def test_plus_inf_equals_count(self):
        h = self._make()
        s = h._series[()]
        assert s["le_+Inf"] == s["_count"] == 4.0
        assert s["_sum"] == pytest.approx(11.0)

    def test_render_cumulative_lines(self):
        out = self._make().render_prometheus()
        assert 'gold_hist_bucket{le="1.0"} 1.0' in out
        assert 'gold_hist_bucket{le="2.0"} 2.0' in out
        assert 'gold_hist_bucket{le="5.0"} 3.0' in out
        assert 'gold_hist_bucket{le="8.0"} 4.0' in out
        assert 'gold_hist_bucket{le="+Inf"} 4.0' in out
        assert "gold_hist_count 4.0" in out

    def _quantile_from_render(self, out: str, q: float) -> float:
        """按 Prometheus histogram_quantile 算法从渲染文本手算分位数。"""
        buckets: list[tuple[float, float]] = []
        for line in out.splitlines():
            if "_bucket{" in line and "le=" in line:
                le = line.split('le="')[1].split('"')[0]
                val = float(line.rsplit(" ", 1)[1])
                buckets.append((float("inf") if le == "+Inf" else float(le), val))
        buckets.sort()
        total = buckets[-1][1]
        rank = q * total
        prev_bound, prev_count = 0.0, 0.0
        for bound, count in buckets:
            if count >= rank:
                if bound == float("inf"):
                    return prev_bound  # 超出全部有限桶
                in_bucket = count - prev_count
                if in_bucket == 0:
                    return bound
                return prev_bound + (bound - prev_bound) * (rank - prev_count) / in_bucket
            prev_bound, prev_count = bound, count
        raise AssertionError("unreachable")

    def test_quantile_hand_computed(self):
        """分位数黄金值：0.5→2.0、0.9→6.8、0.25→1.0。"""
        out = self._make().render_prometheus()
        assert self._quantile_from_render(out, 0.5) == pytest.approx(2.0)
        assert self._quantile_from_render(out, 0.9) == pytest.approx(6.8)
        assert self._quantile_from_render(out, 0.25) == pytest.approx(1.0)

    def test_histogram_with_labels_cumulative(self):
        h = Histogram("lab_hist", "d", labelnames=("cmd",), buckets=(1.0, 10.0))
        h.observe(0.5, {"cmd": "a"})
        h.observe(5.0, {"cmd": "a"})
        s = h._series[("a",)]
        assert s["le_1.0"] == 1
        assert s["le_10.0"] == 2
        assert s["le_+Inf"] == 2 == s["_count"]


# --------------------------------------------------------------------------- #
# 标签值转义
# --------------------------------------------------------------------------- #
def test_label_value_escaping():
    c = Counter("esc_c", "d", labelnames=("who",))
    c.inc(labels={"who": 'a"b\\c\nd'})
    out = c.render_prometheus()
    assert 'who="a\\"b\\\\c\\nd"' in out
    # 原始换行不得出现在样本行内
    sample_lines = [ln for ln in out.splitlines() if ln.startswith("esc_c{")]
    assert len(sample_lines) == 1


def test_histogram_label_escaping():
    h = Histogram("esc_h", "d", labelnames=("cmd",))
    h.observe(1.0, {"cmd": 'x"y'})
    assert 'cmd="x\\"y"' in h.render_prometheus()


# --------------------------------------------------------------------------- #
# Registry 带锁快照
# --------------------------------------------------------------------------- #
def test_snapshot_metrics_returns_copy():
    reg = Registry()
    c = Counter("c1", "d")
    reg.register(c)
    snap = reg.snapshot_metrics()
    assert c in snap
    reg.unregister("c1")
    assert c in snap  # 快照是拷贝，不随注册表变化
    assert reg.snapshot_metrics() == []


def test_snapshot_concurrent_register_safe():
    reg = Registry()
    stop = threading.Event()

    def churn():
        i = 0
        while not stop.is_set():
            reg.register(Counter(f"c{i}", "d"))
            reg.unregister(f"c{i}")
            i += 1

    th = threading.Thread(target=churn, daemon=True)
    th.start()
    try:
        for _ in range(200):
            reg.snapshot_metrics()  # 不应抛 RuntimeError: dictionary changed size
            reg.snapshot()
    finally:
        stop.set()
        th.join(timeout=2)


# --------------------------------------------------------------------------- #
# render_prometheus_client 多标签修复（注入假 prometheus_client）
# --------------------------------------------------------------------------- #
def test_render_prometheus_client_multilabel_no_crash(monkeypatch):
    created: list[str] = []

    class FakePCMetric:
        def __init__(self, name, doc, labelnames, registry):
            created.append(name)
            self.labelnames = list(labelnames)

        def labels(self, *vals):
            return self

        def inc(self, v):
            pass

        def set(self, v):
            pass

    fake_main = types.ModuleType("prometheus_client")
    fake_main.CollectorRegistry = lambda: object()
    fake_main.generate_latest = lambda reg: b"# fake exposition\n"
    fake_core = types.ModuleType("prometheus_client.core")
    fake_core.Counter = FakePCMetric
    fake_core.Gauge = FakePCMetric
    monkeypatch.setitem(sys.modules, "prometheus_client", fake_main)
    monkeypatch.setitem(sys.modules, "prometheus_client.core", fake_core)

    reg = Registry()
    c = Counter("multi", "d", labelnames=("a", "b"))
    c.inc(1, {"a": "1", "b": "x"})
    c.inc(2, {"a": "2", "b": "y"})  # 第二条序列：旧实现必崩（Duplicated timeseries）
    g = Gauge("gg", "d", labelnames=("a",))
    g.set(3, {"a": "1"})
    h = Histogram("hh", "d")  # Histogram/Summary 显式跳过，不崩
    h.observe(1.0)
    reg.register(c)
    reg.register(g)
    reg.register(h)

    out = reg.render_prometheus_client()
    assert "fake exposition" in out
    # Collector 每个指标只创建一次（循环外），多序列走 labels() 子系列
    assert created.count("multi") == 1
    assert created.count("gg") == 1
    assert "hh" not in created  # Histogram 显式跳过


def test_render_prometheus_client_fallback_without_lib(monkeypatch):
    import builtins

    real_import = builtins.__import__

    def _no_pc(name, *args, **kwargs):
        if name.startswith("prometheus_client"):
            raise ImportError("simulated missing")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _no_pc)
    reg = Registry()
    c = Counter("fb", "d")
    c.inc(1)
    reg.register(c)
    assert "fb 1.0" in reg.render_prometheus_client()


# --------------------------------------------------------------------------- #
# V1：PrometheusExporter serve 默认绑定与线程模型
# --------------------------------------------------------------------------- #
def test_serve_default_host_is_loopback():
    sig = inspect.signature(PrometheusExporter.serve)
    assert sig.parameters["host"].default == "127.0.0.1"
    assert sig.parameters["port"].default == 9090


def test_serve_serves_metrics_on_ephemeral_port():
    exp = PrometheusExporter()
    th = threading.Thread(target=exp.serve, kwargs={"port": 0}, daemon=True)
    th.start()
    try:
        for _ in range(200):
            if exp._server is not None:
                break
            time.sleep(0.02)
        assert exp._server is not None
        port = exp._server.server_address[1]
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/metrics", timeout=5) as resp:
            body = resp.read().decode("utf-8")
            assert resp.status == 200
        assert "# HELP" in body
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/other", timeout=5)
            raise AssertionError("expected 404")
        except urllib.error.HTTPError as exc:
            assert exc.code == 404
    finally:
        exp.stop()
        th.join(timeout=5)
    assert not th.is_alive()


# --------------------------------------------------------------------------- #
# statsd：Counter 增量差分 + 标签解析 + 禁用
# --------------------------------------------------------------------------- #
class _FakeSocket:
    def __init__(self) -> None:
        self.sent: list[str] = []

    def sendto(self, data, addr):
        self.sent.append(data.decode("utf-8"))


def _counter_values(sent: list[str], name: str, tag: str) -> list[float]:
    vals = []
    for s in sent:
        if s.startswith(f"tstdx_{name}:") and tag in s:
            vals.append(float(s.split(":")[1].split("|")[0]))
    return vals


def test_statsd_counter_pushes_delta_not_cumulative():
    m = Metrics()
    exp = StatsdExporter(prefix="")  # 去前缀便于断言
    sock = _FakeSocket()
    exp._get_socket = lambda: sock  # type: ignore[method-assign]
    m.request_total.inc(labels={"command": "0x0530", "status": "ok"})  # +1 → 累计 1
    exp.push_all(metrics=m)
    first = _counter_values(sock.sent, "request_total", "command:0x0530,status:ok")
    assert first == [1.0]
    m.request_total.inc(4, labels={"command": "0x0530", "status": "ok"})  # +4 → 累计 5
    sock.sent.clear()
    exp.push_all(metrics=m)
    second = _counter_values(sock.sent, "request_total", "command:0x0530,status:ok")
    assert second == [4.0]  # 推增量而非累计 5


def test_statsd_counter_zero_delta_not_pushed():
    m = Metrics()
    exp = StatsdExporter(prefix="")
    sock = _FakeSocket()
    exp._get_socket = lambda: sock  # type: ignore[method-assign]
    m.request_total.inc(labels={"command": "0x0530", "status": "ok"})
    exp.push_all(metrics=m)
    sock.sent.clear()
    exp.push_all(metrics=m)  # 无增量 → 不推 request_total
    assert _counter_values(sock.sent, "request_total", "command:0x0530,status:ok") == []


def test_statsd_parse_labels_real_parsing():
    # 带标签名：内部值序列按下标配对还原
    assert StatsdExporter._parse_labels("0x0530,ok", ("command", "status")) == {
        "command": "0x0530",
        "status": "ok",
    }
    # 显式 k:v 形态
    assert StatsdExporter._parse_labels("cmd:0x0530,status:ok") == {
        "cmd": "0x0530",
        "status": "ok",
    }
    # 无法解析
    assert StatsdExporter._parse_labels("") is None
    assert StatsdExporter._parse_labels("a,b") is None


def test_statsd_port_zero_disabled_logs_once(caplog):
    with caplog.at_level("INFO", logger="tstdx.observability.statsd_exporter"):
        exp = StatsdExporter(port=0)
        exp.push("x", 1)
        exp.push_all()
    assert exp._disabled is True
    infos = [r for r in caplog.records if r.levelno == 20 and "disabled" in r.getMessage()]
    assert len(infos) == 1  # 只记一次 info


def test_statsd_histogram_structure_not_double_pushed():
    """histogram 快照结构不再以 `name` 名重复推送 count/sum（结构路由修复）。"""
    m = Metrics()
    exp = StatsdExporter(prefix="")
    sock = _FakeSocket()
    exp._get_socket = lambda: sock  # type: ignore[method-assign]
    m.request_duration.observe(0.5, labels={"command": "0x0530"})
    exp.push_all(metrics=m)
    names = {s.split(":")[0].split("|")[0] for s in sock.sent if s}
    assert "tstdx_request_duration_seconds_bucket" in names
    assert "tstdx_request_duration_seconds_count" in names
    assert "tstdx_request_duration_seconds_sum" in names
    # 不应出现以裸指标名推送的 count 值
    raw = [s for s in sock.sent if s.startswith("tstdx_request_duration_seconds:")]
    assert raw == []


# --------------------------------------------------------------------------- #
# otel docstring 修正
# --------------------------------------------------------------------------- #
def test_otel_time_nanos_docstring_says_19_digits():
    from tstdx.observability.otel_exporter import _time_nanos_to_str

    assert "19" in _time_nanos_to_str.__doc__
    assert _time_nanos_to_str(1_735_689_600_000_000_000) == "1735689600000000000"


def test_otel_export_uses_registry_snapshot():
    m = Metrics()
    m.request_total.inc(labels={"command": "0x0530", "status": "ok"})
    exp = OtelExporter(metrics=m)
    payload = exp.export_metrics()
    names = [x["name"] for x in payload["resourceMetrics"][0]["scopeMetrics"][0]["metrics"]]
    assert "tstdx_request_total" in names


# --------------------------------------------------------------------------- #
# start_exporter 薄工厂
# --------------------------------------------------------------------------- #
def test_start_exporter_factory():
    assert isinstance(start_exporter("prom"), PrometheusExporter)
    assert isinstance(start_exporter("prometheus"), PrometheusExporter)
    assert isinstance(start_exporter("statsd"), StatsdExporter)
    assert isinstance(start_exporter("otel"), OtelExporter)
    with pytest.raises(ValueError):
        start_exporter("unknown-kind")


def test_start_exporter_binds_metrics_instance():
    m = Metrics()
    exp = start_exporter("prom", metrics=m)
    assert exp._metrics is m
    se = start_exporter("statsd", port=0, metrics=m)
    assert se._metrics is m
    assert se._disabled is True
    oe = start_exporter("otel", metrics=m)
    assert oe._metrics is m


# --------------------------------------------------------------------------- #
# render 输出仍为合法 JSON 可序列化快照（回归护栏）
# --------------------------------------------------------------------------- #
def test_metrics_snapshot_json_serializable():
    m = Metrics()
    m.record_request(command="0x0530", ok=True, duration=0.1)
    snap = m.snapshot()
    json.dumps(snap)  # 不抛
    assert "tstdx_request_total" in snap
