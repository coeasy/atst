# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""可观测性子系统（§37）。

集中导出指标门面、埋点工具与三种导出器：

* :class:`PrometheusExporter`：标准 Prometheus 文本 exposition + HTTP 服务；
* :class:`StatsdExporter`：UDP StatsD / DogStatsD 推送（零依赖）；
* :class:`OtelExporter`：OTLP JSON 导出 + HTTP 发送（零依赖）。

零硬依赖：未安装 ``prometheus_client`` / ``opentelemetry-*`` 时仍可用
内置渲染 / 标准库 socket 输出标准格式。

一行装配::

    from tstdx.observability import start_exporter
    exporter = start_exporter("prom")          # prom | statsd | otel
"""

from typing import Any

from .metrics import (
    Counter,
    Gauge,
    Histogram,
    Metric,
    Metrics,
    Registry,
    Summary,
    instrument_client,
    metrics,
    record_parse,
    record_reconnect,
    record_request,
    record_stream_event,
)
from .otel_exporter import OtelExporter
from .prometheus_exporter import PrometheusExporter
from .statsd_exporter import StatsdExporter

__all__ = [
    "Metric",
    "Counter",
    "Gauge",
    "Histogram",
    "Summary",
    "Registry",
    "Metrics",
    "metrics",
    "record_parse",
    "record_request",
    "record_stream_event",
    "record_reconnect",
    "instrument_client",
    # Tier C / C5 导出器
    "PrometheusExporter",
    "StatsdExporter",
    "OtelExporter",
    "start_exporter",
]

#: :func:`start_exporter` 支持的 kind → 导出器类。
_EXPORTER_KINDS: dict[str, type] = {
    "prom": PrometheusExporter,
    "prometheus": PrometheusExporter,
    "statsd": StatsdExporter,
    "otel": OtelExporter,
}


def start_exporter(kind: str, metrics: Any = None, **kwargs: Any) -> Any:  # noqa: ANN401
    """统一装配入口：按 ``kind``（``prom`` / ``statsd`` / ``otel``）构建导出器。

    ``metrics`` 缺省绑全局单例；其余关键字参数原样传给对应导出器构造函数。
    仅构建并返回实例——``serve()`` / ``start_pushing()`` / ``send()`` 由调用方
    按需驱动；未知 ``kind`` 抛 :class:`ValueError`。
    """
    cls = _EXPORTER_KINDS.get(kind)
    if cls is None:
        raise ValueError(
            f"unknown exporter kind {kind!r}; expected one of {sorted(set(_EXPORTER_KINDS))}"
        )
    if metrics is not None:
        return cls(metrics=metrics, **kwargs)
    return cls(**kwargs)
