# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""OpenTelemetry OTLP JSON 导出器（Tier C / C5）。

将 :mod:`tstdx.observability.metrics` 内部指标注册表转换为 OTLP-compatible
JSON 结构（与官方 ``opentelemetry-otlp-proto-json`` 编码兼容），并提供
可选的 HTTP POST 发送能力。本模块**零硬依赖**：仅用标准库
:mod:`urllib.request` / :mod:`urllib.error`。

设计原则
--------
* **纯标准库实现**：无需安装 ``opentelemetry-*`` 包即可生成合法 OTLP JSON；
* **优雅降级**：endpoint 未配置或发送失败时返回错误 dict（不抛异常），
  调用方可据此判断重试 / 告警；
* **结构忠实**：严格遵循 OpenTelemetry 0.9+ proto3 JSON 映射规则
  （snake_case 字段名、``stringValue`` / ``intValue`` 等包装类型）；
* **线程安全**：所有可变状态（如有）受 :class:`threading.Lock` 保护。

OTLP JSON 顶层结构::

    {
      "resourceMetrics": [
        {
          "resource": {"attributes": [{"key": "service.name", "value": {"stringValue": "tstdx"}}]},
          "scopeMetrics": [
            {
              "scope": {"name": "tstdx.observability", "version": "0.1"},
              "metrics": [ ... ]
            }
          ]
        }
      ]
    }

Span 导出使用对称的 ``resourceSpans`` / ``scopeSpans`` 结构。

典型用法::

    from tstdx.observability import OtelExporter, metrics

    exp = OtelExporter(endpoint="http://localhost:4318/v1/metrics", service_name="tstdx")
    payload = exp.export_metrics()
    result = exp.send(payload)  # 无 endpoint 时返回 {"error": "..."}
"""

from __future__ import annotations

import json
import logging
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

__all__ = [
    "OtelExporter",
    "OTLP_METRICS_PATH",
    "OTLP_SPANS_PATH",
]

logger = logging.getLogger(__name__)

#: OTLP HTTP 默认路径。
OTLP_METRICS_PATH = "/v1/metrics"
OTLP_SPANS_PATH = "/v1/traces"

#: OpenTelemetry 数据类型常量（proto3 JSON）。
_OTLP_DATA_TYPE_METRICS = "metrics"

_SCOPE_NAME = "tstdx.observability"
_SCOPE_VERSION = "0.1"


def _now_nanos() -> int:
    """返回当前 Unix 时间（纳秒）。"""
    return int(time.time() * 1_000_000_000)


def _value_string(s: str) -> dict[str, Any]:
    """构造 OTLP AnyValue（stringValue 变体）。"""
    return {"stringValue": s}


def _value_int(n: int) -> dict[str, Any]:
    """构造 OTLP AnyValue（intValue 变体；JSON 编码为字符串以保持精度）。"""
    return {"intValue": str(int(n))}


def _value_double(d: float) -> dict[str, Any]:
    """构造 OTLP AnyValue（doubleValue 变体）。"""
    return {"doubleValue": float(d)}


def _value_bool(b: bool) -> dict[str, Any]:
    """构造 OTLP AnyValue（boolValue 变体）。"""
    return {"boolValue": bool(b)}


def _attr(key: str, value: Any) -> dict[str, Any]:
    """构造 OTLP KeyValue attribute。

    根据 ``value`` 类型自动选择 ``stringValue`` / ``intValue`` /
    ``doubleValue`` / ``boolValue`` 变体。
    """
    if isinstance(value, bool):
        v = _value_bool(value)
    elif isinstance(value, int):
        v = _value_int(value)
    elif isinstance(value, float):
        v = _value_double(value)
    elif isinstance(value, str):
        v = _value_string(value)
    elif isinstance(value, (list, tuple)):
        # 数组：递归构造 ArrayValue
        arr = []
        for item in value:
            if isinstance(item, bool):
                arr.append(_value_bool(item))
            elif isinstance(item, int):
                arr.append(_value_int(item))
            elif isinstance(item, float):
                arr.append(_value_double(item))
            else:
                arr.append(_value_string(str(item)))
        v = {"arrayValue": {"values": arr}}
    else:
        v = _value_string(str(value))
    return {"key": key, "value": v}


def _kv_list(attrs: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    """将 ``{k: v}`` 转为 OTLP ``KeyValue`` 列表；None 返回空列表。"""
    if not attrs:
        return []
    return [_attr(k, v) for k, v in attrs.items()]


def _time_nanos_to_str(ns: int | None) -> str:
    """将纳秒时间转 OTLP 格式（19 位整数时间戳字符串）。

    Unix 纪元起的纳秒数当前为 19 位十进制（如 ``1735689600000000000``），
    OTLP JSON 中以字符串编码以保持精度。
    """
    if ns is None:
        return ""
    return str(int(ns))


class OtelExporter:
    """OpenTelemetry OTLP JSON 导出器。

    Parameters
    ----------
    endpoint : str, optional
        OTLP HTTP endpoint（如 ``http://localhost:4318/v1/metrics`` 或仅
        base URL ``http://localhost:4318``）。未配置时 :meth:`send` 返回
        ``{"error": "endpoint not configured"}``。
    service_name : str, optional
        OpenTelemetry ``service.name`` resource attribute。默认 ``tstdx``。
    scope_name : str, optional
        Instrumentation scope 名称。默认 ``tstdx.observability``。
    timeout : float, optional
        HTTP 请求超时秒数。默认 ``5.0``。

    Examples
    --------
    >>> from tstdx.observability import OtelExporter, metrics
    >>> exp = OtelExporter(service_name="my-app")
    >>> payload = exp.export_metrics()
    >>> payload["resourceMetrics"][0]["resource"]["attributes"]
    [{'key': 'service.name', 'value': {'stringValue': 'my-app'}}]
    """

    def __init__(
        self,
        endpoint: str | None = None,
        service_name: str = "tstdx",
        scope_name: str = _SCOPE_NAME,
        scope_version: str = _SCOPE_VERSION,
        timeout: float = 5.0,
        metrics: Any = None,
    ) -> None:
        self.endpoint = (endpoint or "").rstrip("/")
        self.service_name = service_name
        self.scope_name = scope_name
        self.scope_version = scope_version
        self.timeout = timeout
        # export_metrics 的缺省指标源；None 时用全局单例（懒解析）
        self._metrics = metrics

        self._lock = threading.Lock()
        self._last_error: str | None = None

    # -- 资源 / Scope ------------------------------------------------------- #
    def _resource(self) -> dict[str, Any]:
        """构造 OTLP Resource（含 ``service.name`` 等基础属性）。"""
        return {
            "attributes": [
                _attr("service.name", self.service_name),
                _attr("telemetry.sdk.name", "tstdx"),
                _attr("telemetry.sdk.language", "python"),
                _attr("telemetry.sdk.version", _SCOPE_VERSION),
            ]
        }

    def _scope(self) -> dict[str, Any]:
        """构造 OTLP InstrumentationScope。"""
        return {"name": self.scope_name, "version": self.scope_version}

    # -- 指标导出 ----------------------------------------------------------- #
    def export_metrics(self, metrics: Any = None) -> dict[str, Any]:
        """将内部指标注册表转换为 OTLP-compatible JSON 结构。

        Returns
        -------
        dict
            形如 ``{"resourceMetrics": [...]}`` 的 OTLP JSON dict，
            可直接 ``json.dumps`` 后 POST 到 OTLP HTTP endpoint。

        失败时返回 ``{"resourceMetrics": [], "error": "..."}``。
        """
        from .metrics import (
            Counter,
            Gauge,
            Histogram,
            Summary,
        )
        from .metrics import (
            metrics as _default_metrics,
        )

        m = metrics if metrics is not None else self._metrics
        m = m if m is not None else _default_metrics
        out_metrics: list[dict[str, Any]] = []
        now_ns = _now_nanos()

        try:
            # 注册表锁内拷贝快照再遍历，避免导出期间注册/注销竞态
            for metric_obj in m.registry.snapshot_metrics():
                if isinstance(metric_obj, Counter):
                    out_metrics.append(self._counter_to_otlp(metric_obj, now_ns))
                elif isinstance(metric_obj, Gauge):
                    out_metrics.append(self._gauge_to_otlp(metric_obj, now_ns))
                elif isinstance(metric_obj, Histogram):
                    out_metrics.append(self._histogram_to_otlp(metric_obj, now_ns))
                elif isinstance(metric_obj, Summary):
                    out_metrics.append(self._summary_to_otlp(metric_obj, now_ns))
        except Exception as exc:  # noqa: BLE001
            return {
                "resourceMetrics": [],
                "error": f"export_metrics failed: {exc}",
            }

        return {
            "resourceMetrics": [
                {
                    "resource": self._resource(),
                    "scopeMetrics": [{"scope": self._scope(), "metrics": out_metrics}],
                }
            ]
        }

    @staticmethod
    def _counter_to_otlp(m: Any, now_ns: int) -> dict[str, Any]:
        """Counter → OTLP ``Sum`` metric。"""
        dps: list[dict[str, Any]] = []
        with m._lock:
            for labels, value in m._series.items():
                attrs = OtelExporter._labels_to_attrs(m.labelnames, labels)
                dps.append(
                    {
                        "timeUnixNano": _time_nanos_to_str(now_ns),
                        "asInt": str(int(value)),
                        "attributes": attrs,
                    }
                )
        return {
            "name": m.name,
            "description": m.documentation,
            "unit": "",
            "sum": {
                "dataPoints": dps,
                "aggregationTemporality": 1,  # CUMULATIVE
                "isMonotonic": True,
            },
        }

    @staticmethod
    def _gauge_to_otlp(m: Any, now_ns: int) -> dict[str, Any]:
        """Gauge → OTLP ``Gauge`` metric。"""
        dps: list[dict[str, Any]] = []
        with m._lock:
            for labels, value in m._series.items():
                attrs = OtelExporter._labels_to_attrs(m.labelnames, labels)
                dps.append(
                    {
                        "timeUnixNano": _time_nanos_to_str(now_ns),
                        "asDouble": float(value),
                        "attributes": attrs,
                    }
                )
        return {
            "name": m.name,
            "description": m.documentation,
            "unit": "",
            "gauge": {"dataPoints": dps},
        }

    @staticmethod
    def _histogram_to_otlp(m: Any, now_ns: int) -> dict[str, Any]:
        """Histogram → OTLP ``Histogram`` metric。"""
        dps: list[dict[str, Any]] = []
        with m._lock:
            for labels, s in m._series.items():
                attrs = OtelExporter._labels_to_attrs(m.labelnames, labels)
                # bucket counts（含 +Inf）
                bucket_counts: list[int] = []
                explicit_bounds: list[float] = []
                for b in m._buckets:
                    if b == "+Inf":
                        bucket_counts.append(int(s["le_+Inf"]))
                    else:
                        bucket_counts.append(int(s[f"le_{b}"]))
                        explicit_bounds.append(float(b))
                dps.append(
                    {
                        "timeUnixNano": _time_nanos_to_str(now_ns),
                        "count": int(s["_count"]),
                        "sum": float(s["_sum"]),
                        "bucketCounts": bucket_counts,
                        "explicitBounds": explicit_bounds,
                        "attributes": attrs,
                    }
                )
        return {
            "name": m.name,
            "description": m.documentation,
            "unit": "s",
            "histogram": {"dataPoints": dps, "aggregationTemporality": 1},
        }

    @staticmethod
    def _summary_to_otlp(m: Any, now_ns: int) -> dict[str, Any]:
        """Summary → OTLP ``Summary`` metric（仅 count + sum）。"""
        dps: list[dict[str, Any]] = []
        with m._lock:
            for labels, s in m._series.items():
                attrs = OtelExporter._labels_to_attrs(m.labelnames, labels)
                dps.append(
                    {
                        "timeUnixNano": _time_nanos_to_str(now_ns),
                        "count": int(s["_count"]),
                        "sum": float(s["_sum"]),
                        "attributes": attrs,
                    }
                )
        return {
            "name": m.name,
            "description": m.documentation,
            "unit": "",
            "summary": {"dataPoints": dps},
        }

    @staticmethod
    def _labels_to_attrs(
        labelnames: Sequence[str], labels: tuple[str, ...]
    ) -> list[dict[str, Any]]:
        """将 Prometheus 标签元组转为 OTLP attributes。"""
        attrs: list[dict[str, Any]] = []
        for n, v in zip(labelnames, labels, strict=False):
            attrs.append(_attr(n, str(v)))
        return attrs

    # -- Span 导出 ---------------------------------------------------------- #
    def export_spans(self, spans: Iterable[Mapping[str, Any]] | None = None) -> dict[str, Any]:
        """将 span 列表转换为 OTLP-compatible JSON 结构。

        Parameters
        ----------
        spans : iterable of dict, optional
            每项形如::

                {
                    "trace_id": "abcdef0123456789abcdef0123456789abcdef0123456789abcdef01",
                    "span_id": "0123456789abcdef",
                    "name": "tdx.request",
                    "kind": 1,            # 0=UNSPECIFIED,1=INTERNAL,2=SERVER,3=CLIENT,4=PRODUCER,5=CONSUMER
                    "start_time_unix_nano": 1234567890000000000,
                    "end_time_unix_nano": 1234567891000000000,
                    "status": {"code": 0, "message": ""},  # 0=OK,1=ERROR,2=UNSET
                    "attributes": {"command": "0x0530", "ok": True},
                    "parent_id": "0123456789abcdef",  # 可选
                }

            ``trace_id`` / ``span_id`` 必须是 32 / 16 字符十六进制字符串
            （无冒号、无连字符）。

        Returns
        -------
        dict
            形如 ``{"resourceSpans": [...]}`` 的 OTLP JSON dict。

        Examples
        --------
        >>> payload = exp.export_spans([
        ...     {"trace_id": "a" * 32, "span_id": "b" * 16, "name": "tdx.parse"}
        ... ])
        """
        out_spans: list[dict[str, Any]] = []
        if spans is None:
            return {"resourceSpans": []}

        try:
            for span in spans:
                out_spans.append(self._span_to_otlp(span))
        except Exception as exc:  # noqa: BLE001
            return {
                "resourceSpans": [],
                "error": f"export_spans failed: {exc}",
            }

        return {
            "resourceSpans": [
                {
                    "resource": self._resource(),
                    "scopeSpans": [{"scope": self._scope(), "spans": out_spans}],
                }
            ]
        }

    @staticmethod
    def _span_to_otlp(span: Mapping[str, Any]) -> dict[str, Any]:
        """单条 span → OTLP Span JSON。"""
        result: dict[str, Any] = {
            "traceId": span.get("trace_id", ""),
            "spanId": span.get("span_id", ""),
            "name": span.get("name", ""),
            "kind": int(span.get("kind", 0)),
            "startTimeUnixNano": _time_nanos_to_str(
                int(span.get("start_time_unix_nano", _now_nanos()))
            ),
            "endTimeUnixNano": _time_nanos_to_str(
                int(span.get("end_time_unix_nano", _now_nanos()))
            ),
            "attributes": _kv_list(span.get("attributes")),
        }
        parent_id = span.get("parent_id")
        if parent_id:
            result["parentSpanId"] = parent_id

        status = span.get("status")
        if status:
            if isinstance(status, Mapping):
                result["status"] = {
                    "code": int(status.get("code", 0)),
                    "message": str(status.get("message", "")),
                }
            else:
                result["status"] = {"code": 0}

        events = span.get("events")
        if events:
            result["events"] = [
                {
                    "timeUnixNano": _time_nanos_to_str(int(e.get("time_unix_nano", _now_nanos()))),
                    "name": e.get("name", ""),
                    "attributes": _kv_list(e.get("attributes")),
                }
                for e in events
            ]

        links = span.get("links")
        if links:
            result["links"] = [
                {
                    "traceId": link.get("trace_id", ""),
                    "spanId": link.get("span_id", ""),
                    "attributes": _kv_list(link.get("attributes")),
                }
                for link in links
            ]

        return result

    # -- 发送 --------------------------------------------------------------- #
    def send(
        self,
        payload: Mapping[str, Any],
        *,
        kind: str = _OTLP_DATA_TYPE_METRICS,
        headers: Mapping[str, str] | None = None,
    ) -> dict[str, Any]:
        """通过 HTTP POST 发送 OTLP JSON payload 到 endpoint。

        Parameters
        ----------
        payload : dict
            :meth:`export_metrics` 或 :meth:`export_spans` 返回的 dict。
        kind : str, optional
            ``"metrics"`` 或 ``"spans"``，用于选择默认 path 后缀。
        headers : dict, optional
            附加 HTTP 头。

        Returns
        -------
        dict
            成功：``{"status": 200, "body": "..."}``；
            失败：``{"error": "..."}``（不抛异常）。
        """
        if not self.endpoint:
            with self._lock:
                self._last_error = "endpoint not configured"
            return {"error": "endpoint not configured"}

        # 拼接 path（若 endpoint 未含 path 则自动补）
        url = self.endpoint
        if not (url.endswith(OTLP_METRICS_PATH) or url.endswith(OTLP_SPANS_PATH)):
            suffix = OTLP_METRICS_PATH if kind == _OTLP_DATA_TYPE_METRICS else OTLP_SPANS_PATH
            url = url + suffix

        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req_headers = {
            "Content-Type": "application/json",
            "Content-Length": str(len(body)),
        }
        if headers:
            req_headers.update(headers)

        req = urllib.request.Request(
            url,
            data=body,
            headers=req_headers,
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                resp_body = resp.read().decode("utf-8", errors="replace")
                with self._lock:
                    self._last_error = None
                return {"status": resp.status, "body": resp_body}
        except urllib.error.HTTPError as exc:
            try:
                err_body = exc.read().decode("utf-8", errors="replace")
            except Exception:  # noqa: BLE001
                err_body = ""
            msg = f"HTTP {exc.code}: {err_body[:500]}"
            with self._lock:
                self._last_error = msg
            logger.warning("OtelExporter.send(%s) failed: %s", url, msg)
            return {"error": msg}
        except urllib.error.URLError as exc:
            msg = f"URL error: {exc.reason}"
            with self._lock:
                self._last_error = msg
            logger.warning("OtelExporter.send(%s) failed: %s", url, msg)
            return {"error": msg}
        except OSError as exc:
            msg = f"OS error: {exc}"
            with self._lock:
                self._last_error = msg
            logger.warning("OtelExporter.send(%s) failed: %s", url, msg)
            return {"error": msg}
        except Exception as exc:  # noqa: BLE001
            msg = f"unexpected error: {exc}"
            with self._lock:
                self._last_error = msg
            logger.warning("OtelExporter.send(%s) failed: %s", url, msg)
            return {"error": msg}

    @property
    def last_error(self) -> str | None:
        """最近一次 :meth:`send` 的错误描述；成功或无错误时为 None。"""
        with self._lock:
            return self._last_error
