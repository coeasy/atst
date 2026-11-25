# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""P3-2 低覆盖模块补离线单测：三个导出器（observability/otel_exporter.py、
statsd_exporter.py、prometheus_exporter.py）。

全部离线：HTTP 发送走 monkeypatch 的 urlopen、UDP 走捕获 sendto 的假 socket、
Prometheus serve 走 127.0.0.1 回环临时端口。验证 OTLP JSON 结构、DogStatsD
报文格式、Counter 增量差分、干净失败语义等公开行为。
"""

from __future__ import annotations

import io
import json
import threading
import time
import urllib.error
import urllib.request
from typing import Any

import pytest

from atst.observability.metrics import (
    Counter,
    Gauge,
    Histogram,
    Metrics,
    Summary,
)
from atst.observability.otel_exporter import OtelExporter, _attr, _time_nanos_to_str
from atst.observability.prometheus_exporter import (
    PROMETHEUS_CONTENT_TYPE,
    PrometheusExporter,
)
from atst.observability.statsd_exporter import StatsdExporter

pytestmark = pytest.mark.unit


# --------------------------------------------------------------------------- #
# OTLP JSON 导出器
# --------------------------------------------------------------------------- #
class TestOtlpHelpers:
    def test_attr_type_variants(self) -> None:
        """#1 _attr：bool 优先于 int；int 编码为字符串保持精度；list 递归。"""
        assert _attr("b", True) == {"key": "b", "value": {"boolValue": True}}
        assert _attr("i", 42) == {"key": "i", "value": {"intValue": "42"}}
        assert _attr("d", 1.5) == {"key": "d", "value": {"doubleValue": 1.5}}
        assert _attr("s", "x") == {"key": "s", "value": {"stringValue": "x"}}
        mixed = _attr("a", [True, 1, 2.5, "s"])
        values = list(mixed["value"]["arrayValue"]["values"])
        assert values == [
            {"boolValue": True},
            {"intValue": "1"},
            {"doubleValue": 2.5},
            {"stringValue": "s"},
        ]
        assert _attr("n", None) == {"key": "n", "value": {"stringValue": "None"}}

    def test_time_nanos_none(self) -> None:
        """#2 _time_nanos_to_str(None) → 空串。"""
        assert _time_nanos_to_str(None) == ""
        assert _time_nanos_to_str(1735689600000000000) == "1735689600000000000"


class TestOtelExport:
    def test_export_metrics_all_types(self) -> None:
        """#3 export_metrics：Sum/Gauge/Histogram/Summary 四类齐全、OTLP 结构合法。"""
        m = Metrics()
        m.parse_total.inc(labels={"tier": "L1", "family": "f", "command": "c"})
        m.request_total.inc(labels={"command": "0x0530", "status": "ok"})
        m.request_duration.observe(0.1, labels={"command": "0x0530"})
        m.stream_backpressure.set(3)
        exp = OtelExporter(metrics=m)
        payload = exp.export_metrics()
        assert "error" not in payload
        rm = payload["resourceMetrics"][0]
        attrs = {a["key"]: a["value"]["stringValue"] for a in rm["resource"]["attributes"]}
        assert attrs["service.name"] == "atst"
        metrics = {mm["name"]: mm for mm in rm["scopeMetrics"][0]["metrics"]}
        assert {"atst_protocol_parse_total", "atst_request_duration_seconds"} <= set(metrics)
        parse = metrics["atst_protocol_parse_total"]["sum"]
        assert parse["isMonotonic"] is True and parse["aggregationTemporality"] == 1
        assert parse["dataPoints"][0]["asInt"] == "1"
        hist = metrics["atst_request_duration_seconds"]["histogram"]
        dp = hist["dataPoints"][0]
        assert dp["count"] == 1 and dp["sum"] == pytest.approx(0.1)
        assert dp["explicitBounds"] == sorted(dp["explicitBounds"])
        assert dp["bucketCounts"][-1] == 1  # +Inf 桶计入最后
        json.dumps(payload)  # OTLP JSON 必须可整体序列化

    def test_export_metrics_error_is_returned_not_raised(self) -> None:
        """#4 注册表快照失败：返回 error dict，不抛异常。"""

        class _Boom:
            registry = property(lambda self: (_ for _ in ()).throw(RuntimeError("boom")))

        exp = OtelExporter(metrics=_Boom())
        out = exp.export_metrics()
        assert out["resourceMetrics"] == [] and "boom" in out["error"]

    def test_export_spans_full_shape(self) -> None:
        """#5 export_spans：parent/status/events/links 全量映射；None → 空结构。"""
        exp = OtelExporter(service_name="svc")
        payload = exp.export_spans(
            [
                {
                    "trace_id": "a" * 32,
                    "span_id": "b" * 16,
                    "name": "tdx.request",
                    "kind": 3,
                    "start_time_unix_nano": 1735689600000000000,
                    "end_time_unix_nano": 1735689601000000000,
                    "status": {"code": 1, "message": "timeout"},
                    "attributes": {"command": "0x0530", "ok": True},
                    "parent_id": "c" * 16,
                    "events": [{"time_unix_nano": 1735689600500000000, "name": "retry"}],
                    "links": [{"trace_id": "d" * 32, "span_id": "e" * 16}],
                }
            ]
        )
        span = payload["resourceSpans"][0]["scopeSpans"][0]["spans"][0]
        assert span["traceId"] == "a" * 32 and span["kind"] == 3
        assert span["parentSpanId"] == "c" * 16
        assert span["status"] == {"code": 1, "message": "timeout"}
        assert span["events"][0]["name"] == "retry"
        assert span["links"][0]["spanId"] == "e" * 16
        assert exp.export_spans(None) == {"resourceSpans": []}

    def test_export_spans_status_non_mapping(self) -> None:
        """#6 status 非 Mapping → 退化为 {"code": 0}。"""
        exp = OtelExporter()
        payload = exp.export_spans(
            [{"trace_id": "a" * 32, "span_id": "b" * 16, "name": "n", "status": "odd"}]
        )
        assert payload["resourceSpans"][0]["scopeSpans"][0]["spans"][0]["status"] == {"code": 0}

    def test_export_spans_error_is_returned_not_raised(self) -> None:
        """#7 单条 span 不可转换：返回 error dict。"""

        class _Boom:
            def get(self, *a: Any, **k: Any) -> Any:
                raise RuntimeError("boom")

        exp = OtelExporter()
        out = exp.export_spans([_Boom()])
        assert out["resourceSpans"] == [] and "boom" in out["error"]


class TestOtelSend:
    def _exp_with_urlopen(self, monkeypatch: pytest.MonkeyPatch, urlopen: Any) -> OtelExporter:
        exp = OtelExporter(endpoint="http://collector:4318", timeout=0.1)
        monkeypatch.setattr(urllib.request, "urlopen", urlopen)
        return exp

    def test_send_success_appends_path(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """#8 send 成功：endpoint 无 path 时按 kind 自动补 /v1/metrics。"""
        seen: dict[str, Any] = {}

        class _Resp:
            status = 200

            def read(self) -> bytes:
                return b"ok"

            def __enter__(self) -> _Resp:
                return self

            def __exit__(self, *a: Any) -> None:
                return None

        def fake_urlopen(req: Any, timeout: float | None = None) -> _Resp:
            seen["url"] = req.full_url
            seen["method"] = req.get_method()
            seen["data"] = req.data
            return _Resp()

        exp = self._exp_with_urlopen(monkeypatch, fake_urlopen)
        result = exp.send({"resourceMetrics": []}, headers={"X-Token": "t"})
        assert result == {"status": 200, "body": "ok"}
        assert seen["url"] == "http://collector:4318/v1/metrics"
        assert seen["method"] == "POST"
        assert exp.last_error is None

    def test_send_existing_path_not_appended(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """#9 endpoint 已含 /v1/traces：不再追加路径。"""
        seen: list[str] = []

        class _Resp(io.BytesIO):
            status = 200

            def __enter__(self) -> _Resp:
                return self

            def __exit__(self, *a: Any) -> None:
                return None

        exp = OtelExporter(endpoint="http://collector:4318/v1/traces")
        monkeypatch.setattr(
            urllib.request,
            "urlopen",
            lambda req, timeout=None: seen.append(req.full_url) or _Resp(b"{}"),
        )
        result = exp.send({}, kind="spans")
        assert result["status"] == 200
        assert seen == ["http://collector:4318/v1/traces"]

    def test_send_no_endpoint(self) -> None:
        """#10 未配置 endpoint：返回 error dict 且记录 last_error。"""
        exp = OtelExporter()
        assert exp.send({}) == {"error": "endpoint not configured"}
        assert exp.last_error == "endpoint not configured"

    def test_send_http_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """#11 HTTPError → error dict（body 读取失败也安全）。"""
        exp = OtelExporter(endpoint="http://collector:4318/v1/metrics")
        err = urllib.error.HTTPError("http://collector:4318/v1/metrics", 503, "unavail", None, None)

        def fake_urlopen(req: Any, timeout: float | None = None) -> Any:
            raise err

        monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
        out = exp.send({})
        assert out["error"].startswith("HTTP 503")
        assert "503" in (exp.last_error or "")

    def test_send_url_error_and_os_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """#12 URLError / OSError → error dict。"""
        exp = OtelExporter(endpoint="http://collector:4318/v1/metrics")
        monkeypatch.setattr(
            urllib.request,
            "urlopen",
            lambda req, timeout=None: (_ for _ in ()).throw(urllib.error.URLError("refused")),
        )
        assert "URL error" in exp.send({})["error"]
        monkeypatch.setattr(
            urllib.request,
            "urlopen",
            lambda req, timeout=None: (_ for _ in ()).throw(OSError("net down")),
        )
        assert "OS error" in exp.send({})["error"]

    def test_send_unexpected_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """#13 其它异常 → unexpected error dict，不外溢。"""
        exp = OtelExporter(endpoint="http://collector:4318/v1/metrics")
        monkeypatch.setattr(
            urllib.request,
            "urlopen",
            lambda req, timeout=None: (_ for _ in ()).throw(RuntimeError("weird")),
        )
        assert "unexpected error" in exp.send({})["error"]


# --------------------------------------------------------------------------- #
# StatsD 导出器
# --------------------------------------------------------------------------- #
class _FakeUdpSocket:
    """捕获 sendto 的假 UDP socket。"""

    def __init__(self) -> None:
        self.sent: list[tuple[bytes, tuple[str, int]]] = []
        self.closed = False

    def setblocking(self, flag: bool) -> None:
        return None

    def sendto(self, data: bytes, addr: tuple[str, int]) -> int:
        self.sent.append((data, addr))
        return len(data)

    def close(self) -> None:
        self.closed = True


class TestStatsdFormatting:
    def test_format_metric_variants(self) -> None:
        """#14 报文格式：整数/浮点、gauge_inc/dec 符号、tag 拼接、未知类型回落 g。"""
        fmt = StatsdExporter._format_metric
        assert fmt("req", 5, "counter", None, "atst") == "atst.req:5|c"
        assert fmt("g", 1.5, "gauge", {"a": "b"}, "") == "g:1.5|g|#a:b"
        assert fmt("g", -2.0, "gauge_inc", None, "atst") == "atst.g:+2|g"
        assert fmt("g", 3.0, "gauge_dec", None, "atst") == "atst.g:-3|g"
        assert fmt("t", 0.12, "timing", None, "") == "t:0.12|d"
        assert fmt("h", 0.5, "mystery", None, "atst") == "atst.h:0.5|g"


class TestStatsdPush:
    def _exp_with_socket(self) -> tuple[StatsdExporter, _FakeUdpSocket]:
        exp = StatsdExporter(host="127.0.0.1", port=8125, prefix="atst")
        sock = _FakeUdpSocket()
        exp._sock = sock  # noqa: SLF001 - 直接注入懒加载 socket
        return exp, sock

    def test_push_success_and_target(self) -> None:
        """#15 push：报文发往 (host, port)；空名 no-op。"""
        exp, sock = self._exp_with_socket()
        exp.push("errors_total", 2, metric_type="counter", tags={"error_type": "Timeout"})
        assert sock.sent == [(b"atst.errors_total:2|c|#error_type:Timeout", ("127.0.0.1", 8125))]
        exp.push("", 1)
        assert len(sock.sent) == 1

    def test_push_disabled_port_zero(self, caplog: pytest.LogCaptureFixture) -> None:
        """#16 port=0 禁用：push 静默 no-op、不建 socket。"""
        with caplog.at_level("INFO", logger="atst.observability.statsd_exporter"):
            exp = StatsdExporter(port=0)
        exp.push("x", 1)
        assert exp._sock is None  # noqa: SLF001
        exp.push_all(Metrics())
        assert exp._sock is None  # noqa: SLF001

    def test_push_socket_error_swallowed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """#17 sendto 失败：记一次日志后静默吞掉，绝不外溢。"""
        exp = StatsdExporter()
        sent: list[bytes] = []

        class _FailSock:
            def sendto(self, data: bytes, addr: Any) -> int:
                sent.append(data)
                raise OSError("refused")

        exp._sock = _FailSock()  # type: ignore[assignment]  # noqa: SLF001
        for _ in range(3):
            exp.push("x", 1)  # 后续 push 也不抛（去重告警）
        assert len(sent) == 3

    def test_push_all_counter_delta_and_reset(self) -> None:
        """#18 push_all：Counter 推增量；计数器回退视为重置推当前值。"""
        m = Metrics()
        exp = StatsdExporter(prefix="", port=8125)
        sock = _FakeUdpSocket()
        exp._sock = sock  # noqa: SLF001

        m.record_request(command="0x0530", ok=True, duration=None)
        m.record_request(command="0x0530", ok=True, duration=None)
        exp.push_all(m)  # 首推：基线 0 → 全部累计
        sent = [s.decode() for s, _ in sock.sent]
        assert "atst_request_total:2|c|#command:0x0530,status:ok" in sent

        sock.sent.clear()
        m.record_request(command="0x0530", ok=True, duration=None)
        exp.push_all(m)
        sent = [s.decode() for s, _ in sock.sent]
        assert "atst_request_total:1|c|#command:0x0530,status:ok" in sent  # 只有增量
        assert not any("atst_request_total:3" in s for s in sent)

        sock.sent.clear()
        m.request_total._series.clear()  # noqa: SLF001 - 模拟进程内指标重置
        m.record_request(command="0x0530", ok=True, duration=None)
        exp.push_all(m)
        sent = [s.decode() for s, _ in sock.sent]
        assert "atst_request_total:1|c|#command:0x0530,status:ok" in sent  # 回退→推当前值

    def test_push_all_gauge_histogram_summary(self) -> None:
        """#19 push_all：Gauge 直推、Histogram 出 bucket/count/sum、Summary → timing。"""
        m = Metrics()
        exp = StatsdExporter(prefix="", port=8125)
        sock = _FakeUdpSocket()
        exp._sock = sock  # noqa: SLF001
        m.stream_backpressure.set(3)
        m.record_request(command="0x0530", ok=True, duration=0.05)
        exp.push_all(m)
        sent = [s.decode() for s, _ in sock.sent]
        assert any(s.startswith("atst_stream_backpressure:3|g") for s in sent)
        assert any("atst_request_duration_seconds_count" in s for s in sent)
        assert any("atst_request_duration_seconds_sum" in s for s in sent)
        assert any("atst_request_duration_seconds_bucket" in s for s in sent)
        assert any("le:0.005" in s for s in sent)  # bucket 带 le tag

    def test_push_all_metric_type_map_override(self) -> None:
        """#20 metric_type_map 覆盖默认类型推断。"""
        m = Metrics()
        exp = StatsdExporter(prefix="", port=8125)
        sock = _FakeUdpSocket()
        exp._sock = sock  # noqa: SLF001
        m.stream_backpressure.set(7)
        exp.push_all(m, metric_type_map={"atst_stream_backpressure": "counter"})
        sent = [s.decode() for s, _ in sock.sent]
        assert "atst_stream_backpressure:7|c" in sent

    def test_push_all_snapshot_failure_swallowed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """#21 snapshot 失败：静默吞掉。"""

        class _Boom:
            def snapshot(self) -> dict[str, Any]:
                raise RuntimeError("boom")

            registry = property(lambda self: self)

            def snapshot_metrics(self) -> list[Any]:
                return []

        exp = StatsdExporter()
        exp.push_all(_Boom())  # 不抛

    def test_parse_labels_length_mismatch_fallback(self) -> None:
        """#22 _parse_labels：k:v 形态回退与无法解析返回 None。"""
        assert StatsdExporter._parse_labels("cmd:0x0530,status:ok") == {
            "cmd": "0x0530",
            "status": "ok",
        }
        assert StatsdExporter._parse_labels("0x0530,ok") is None  # 长度不匹配且非 k:v
        assert StatsdExporter._parse_labels("") is None

    def test_periodic_push_lifecycle(self) -> None:
        """#23 周期推送：启动后至少推一轮；stop 后线程退出；幂等。"""
        exp = StatsdExporter(interval=0.05)
        sock = _FakeUdpSocket()
        exp._sock = sock  # noqa: SLF001
        period_metrics = Metrics()
        period_metrics.stream_backpressure.set(3)  # 快照里有值才推得出报文
        exp.start_pushing(interval=0.05, metrics=period_metrics)
        assert exp._push_thread is not None
        deadline = time.monotonic() + 2.0
        while not sock.sent and time.monotonic() < deadline:
            time.sleep(0.01)
        assert sock.sent  # 至少推过一轮
        exp.stop_pushing()
        exp.stop_pushing()  # 幂等
        assert exp._push_thread is None
        time.sleep(0.1)
        assert not exp._push_thread or not exp._push_thread.is_alive()
        exp.close()

    def test_periodic_push_disabled_paths(self, caplog: pytest.LogCaptureFixture) -> None:
        """#24 interval<=0 或 port=0：不启动线程。"""
        exp = StatsdExporter(interval=0)
        exp.start_pushing()
        assert exp._push_thread is None
        with caplog.at_level("INFO", logger="atst.observability.statsd_exporter"):
            exp0 = StatsdExporter(port=0)
        exp0.start_pushing(interval=0.05)
        assert exp0._push_thread is None

    def test_flush_and_close(self) -> None:
        """#25 flush no-op；close 关 socket 并停线程（幂等）。"""
        exp, sock = self._exp_with_socket()
        assert exp.flush() is None
        exp.close()
        assert sock.closed
        exp.close()  # 幂等


# --------------------------------------------------------------------------- #
# Prometheus 导出器
# --------------------------------------------------------------------------- #
class TestPrometheusRender:
    def test_render_text_format(self) -> None:
        """#26 render： exposition 文本含 HELP/TYPE。"""
        m = Metrics()
        m.stream_backpressure.set(3)  # 无标签指标 → 裸值行
        exp = PrometheusExporter(m)
        text = exp.render()
        assert "# HELP atst_stream_backpressure" in text
        assert "# TYPE atst_stream_backpressure gauge" in text
        assert "atst_stream_backpressure 3" in text

    def test_render_failure_returns_empty(self) -> None:
        """#27 渲染失败 → 空串，不抛。"""

        class _Boom:
            def render_prometheus(self) -> str:
                raise RuntimeError("boom")

        assert PrometheusExporter(_Boom()).render() == ""

    def test_write_to_file(self, tmp_path: Any) -> None:
        """#28 write_to_file：快照落盘；不可写路径只记日志不抛。"""
        m = Metrics()
        m.parse_total.inc()
        path = tmp_path / "metrics.txt"
        PrometheusExporter(m).write_to_file(str(path))
        assert "# HELP atst_protocol_parse_total" in path.read_text(encoding="utf-8")
        # 不可写路径：目录不存在 → 只记日志
        PrometheusExporter(m).write_to_file(str(tmp_path / "no/such/dir/m.txt"))


class _NoServeMetrics:
    """render_prometheus 可控失败的门面，用于触发 handler 500。"""

    def __init__(self, ok: bool = True) -> None:
        self.ok = ok

    def render_prometheus(self) -> str:
        if not self.ok:
            raise RuntimeError("render boom")
        return "# HELP atst_test_metric 1\n# TYPE atst_test_metric counter\natst_test_metric 1.0\n"


class TestPrometheusServe:
    """HTTP 服务走 127.0.0.1 回环临时端口（离线 loopback，非外网）。"""

    def _serve(self, metrics: Any, extra_headers: dict[str, str] | None = None):
        exp = PrometheusExporter(metrics, extra_headers=extra_headers)
        t = threading.Thread(target=exp.serve, kwargs={"host": "127.0.0.1", "port": 0}, daemon=True)
        t.start()
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            server = exp._server  # noqa: SLF001
            if server is not None:
                break
            time.sleep(0.02)
        assert exp._server is not None  # noqa: SLF001
        port = exp._server.server_address[1]  # type: ignore[index]  # noqa: SLF001
        return exp, port

    def test_get_metrics_ok(self) -> None:
        """#29 GET /metrics：200、标准 Content-Type、额外响应头。"""
        exp, port = self._serve(_NoServeMetrics(), extra_headers={"X-Env": "test"})
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/metrics", timeout=5) as resp:
                body = resp.read()
                assert resp.status == 200
                assert resp.headers["Content-Type"] == PROMETHEUS_CONTENT_TYPE
                assert resp.headers["X-Env"] == "test"
                assert b"atst_test_metric 1.0" in body
            # 带尾斜杠同样可达
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/metrics/", timeout=5) as resp:
                assert resp.status == 200
        finally:
            exp.stop()

    def test_get_unknown_path_404(self) -> None:
        """#30 其它路径 → 404。"""
        exp, port = self._serve(_NoServeMetrics())
        try:
            with pytest.raises(urllib.error.HTTPError) as exc_info:
                urllib.request.urlopen(f"http://127.0.0.1:{port}/other", timeout=5)
            assert exc_info.value.code == 404
        finally:
            exp.stop()

    def test_render_error_500(self) -> None:
        """#31 provider 渲染失败 → 500 + '# render error'，绝不裸抛。

        exporter.render() 本身吞异常返回空串，所以这里直接构造一个
        metrics_provider 会抛异常的 handler，覆盖 do_GET 的兜底分支。
        """
        from http.server import ThreadingHTTPServer

        from atst.observability.prometheus_exporter import _MetricsHandler

        def _boom() -> str:
            raise RuntimeError("render boom")

        class _BoomHandler(_MetricsHandler):
            metrics_provider = staticmethod(_boom)

        server = ThreadingHTTPServer(("127.0.0.1", 0), _BoomHandler)
        server.daemon_threads = True
        port = server.server_address[1]
        th = threading.Thread(target=server.serve_forever, daemon=True)
        th.start()
        try:
            with pytest.raises(urllib.error.HTTPError) as exc_info:
                urllib.request.urlopen(f"http://127.0.0.1:{port}/metrics", timeout=5)
            assert exc_info.value.code == 500
            assert exc_info.value.read() == b"# render error\n"
        finally:
            server.shutdown()
            server.server_close()

    def test_head_metrics(self) -> None:
        """#32 HEAD /metrics：200 无响应体；HEAD 其它路径 → 404。"""
        exp, port = self._serve(_NoServeMetrics())
        try:
            req = urllib.request.Request(f"http://127.0.0.1:{port}/metrics", method="HEAD")
            with urllib.request.urlopen(req, timeout=5) as resp:
                assert resp.status == 200
                assert resp.read() == b""
            req404 = urllib.request.Request(f"http://127.0.0.1:{port}/x", method="HEAD")
            with pytest.raises(urllib.error.HTTPError) as exc_info:
                urllib.request.urlopen(req404, timeout=5)
            assert exc_info.value.code == 404
        finally:
            exp.stop()

    def test_stop_is_idempotent(self) -> None:
        """#33 stop 幂等；expected 不匹配时不误关新服务。"""
        exp, _ = self._serve(_NoServeMetrics())
        current = exp._server  # noqa: SLF001
        exp.stop(expected=object())  # 不匹配 → 不动
        assert exp._server is current  # noqa: SLF001
        exp.stop()
        assert exp._server is None  # noqa: SLF001
        exp.stop()  # 幂等


def _unused_counter_guard() -> None:  # pragma: no cover - 保持导入面完整
    _ = (Counter, Gauge, Histogram, Summary)
