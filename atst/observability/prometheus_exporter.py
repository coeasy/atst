# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""Prometheus 导出器（Tier C / C5）。

将 :mod:`atst.observability.metrics` 内部指标注册表桥接到 Prometheus
exposition 格式（标准文本格式，Content-Type ``text/plain; version=0.0.4``）。
本模块**零硬依赖**：仅用标准库 :mod:`http.server` 提供独立 HTTP 服务。

设计原则
--------
* **复用内置渲染**：直接调用 :meth:`Metrics.render_prometheus`（已在
  ``metrics.py`` 实现），避免重复实现文本拼装逻辑；
* **零硬依赖**：HTTP 服务用标准库 :class:`http.server.ThreadingHTTPServer`
  （每连接独立线程，慢客户端不阻塞其他抓取）；
* **默认只绑本机**：:meth:`serve` 默认 ``host="127.0.0.1"``——指标含内部
  维度且端点无鉴权，公网暴露必须**显式**传 ``host="0.0.0.0"`` 并自行
  配置网络层访问控制；
* **可降级**：文件写入 / 服务启动失败时只记日志，不抛出污染业务路径；
* **线程安全**：:meth:`serve` 可被后台线程安全调用；指标读取本身
  由 :class:`Metrics` 的锁保护。

典型用法::

    from atst.observability import PrometheusExporter, metrics

    # 1. 渲染文本（拉取模式）
    text = PrometheusExporter(metrics).render()

    # 2. 文件快照
    PrometheusExporter(metrics).write_to_file("metrics.txt")

    # 3. 启动 HTTP 端点（阻塞；建议放后台线程；默认仅监听 127.0.0.1）
    exporter = PrometheusExporter(metrics)
    threading.Thread(target=exporter.serve, kwargs={"port": 9090}, daemon=True).start()

    # 4. 公网暴露（显式 opt-in；请自行配置防火墙/反代鉴权）
    threading.Thread(
        target=exporter.serve, kwargs={"host": "0.0.0.0", "port": 9090}, daemon=True
    ).start()
"""

from __future__ import annotations

import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

__all__ = [
    "PROMETHEUS_CONTENT_TYPE",
    "PrometheusExporter",
]

logger = logging.getLogger(__name__)

#: Prometheus exposition 标准 Content-Type。
PROMETHEUS_CONTENT_TYPE = "text/plain; version=0.0.4; charset=utf-8"


class _MetricsHandler(BaseHTTPRequestHandler):
    """处理 ``GET /metrics`` 的 HTTP handler。

    其余路径返回 404；非 GET 返回 405。所有异常被吞掉并记录，绝不向客户端
    抛出 5xx（指标失败不应影响业务）。
    """

    #: 由 :class:`PrometheusExporter` 注入的指标提供者（callable → str）。
    metrics_provider: Any = None

    def do_GET(self) -> None:  # noqa: N802 - 标准库约定的方法名
        if self.path.rstrip("/") not in ("/metrics", "/metrics/"):
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b"404 Not Found\n")
            return

        try:
            body = (self.metrics_provider() or "").encode("utf-8")
        except Exception as exc:  # noqa: BLE001 - 指标失败绝不污染业务
            logger.warning("PrometheusExporter render failed: %s", exc)
            body = b"# render error\n"
            self.send_response(500)
        else:
            self.send_response(200)

        self.send_header("Content-Type", PROMETHEUS_CONTENT_TYPE)
        self.send_header("Content-Length", str(len(body)))
        # 附加头（由 PrometheusExporter 注入）
        for k, v in getattr(self, "extra_headers", {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def do_HEAD(self) -> None:  # noqa: N802
        if self.path.rstrip("/") not in ("/metrics", "/metrics/"):
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", PROMETHEUS_CONTENT_TYPE)
        for k, v in getattr(self, "extra_headers", {}).items():
            self.send_header(k, v)
        self.end_headers()

    def log_message(self, fmt: str, *args: Any) -> None:  # noqa: D102
        # 使用本模块 logger，便于统一配置；保留标准 handler 的行为
        logger.debug("prometheus_exporter: " + fmt, *args)


class PrometheusExporter:
    """将内部指标注册表导出为 Prometheus 文本格式的桥接器。

    Parameters
    ----------
    metrics : Metrics, optional
        目标 :class:`atst.observability.metrics.Metrics` 实例。默认使用
        全局单例 ``atst.observability.metrics.metrics``。
    extra_headers : dict[str, str], optional
        在 ``serve`` 时附加到响应的额外 HTTP 头（默认空）。

    Examples
    --------
    >>> from atst.observability import PrometheusExporter, metrics
    >>> exp = PrometheusExporter(metrics)
    >>> exp.render()[:40]
    '# HELP atst_protocol_parse_total 三级解析分派总次数\\n#'
    """

    def __init__(self, metrics: Any = None, extra_headers: dict[str, str] | None = None) -> None:
        # 延迟 import 避免循环（metrics 模块在 import 时已构建单例）
        from .metrics import metrics as _default_metrics

        self._metrics = metrics if metrics is not None else _default_metrics
        self._extra_headers: dict[str, str] = dict(extra_headers or {})
        self._server: ThreadingHTTPServer | None = None
        self._server_lock = threading.Lock()

    # -- 渲染 --------------------------------------------------------------- #
    def render(self) -> str:
        """返回完整 Prometheus 文本 exposition 格式。

        等价于 :meth:`Metrics.render_prometheus`。渲染失败时返回空串
        （不抛异常，便于调用方安全使用）。
        """
        try:
            return self._metrics.render_prometheus()
        except Exception as exc:  # noqa: BLE001
            logger.warning("PrometheusExporter.render failed: %s", exc)
            return ""

    # -- 文件快照 ----------------------------------------------------------- #
    def write_to_file(self, path: str) -> None:
        """将当前指标快照以文本格式写入文件。

        写入失败只记日志，不抛异常。
        """
        try:
            body = self.render()
            with open(path, "w", encoding="utf-8") as f:
                f.write(body)
        except Exception as exc:  # noqa: BLE001
            logger.warning("PrometheusExporter.write_to_file(%s) failed: %s", path, exc)

    # -- HTTP 服务 ---------------------------------------------------------- #
    def serve(self, host: str = "127.0.0.1", port: int = 9090) -> None:
        """启动一个独立的 HTTP 服务，暴露 ``GET /metrics``。

        本方法**阻塞**。建议放在后台 daemon 线程中运行：::

            threading.Thread(target=exp.serve, daemon=True).start()

        安全默认值：只绑 ``127.0.0.1``（指标端点无鉴权，且暴露内部维度）。
        公网暴露必须显式传 ``host="0.0.0.0"``——调用方需自行配置网络层
        访问控制（防火墙 / 反向代理鉴权）。

        再次调用（在已有服务运行时）会先关闭旧服务再启动新服务，便于热更新。
        """
        # 先关闭旧服务（必须在获取锁之前调用 stop()，避免非重入锁死锁）
        self.stop()

        # 构造动态 handler 类：注入 metrics_provider 和 extra_headers
        handler_cls = type(
            "BoundMetricsHandler",
            (_MetricsHandler,),
            {
                "metrics_provider": self.render,
                "extra_headers": dict(self._extra_headers),
            },
        )

        # 创建服务器（短暂持有锁）；ThreadingHTTPServer：每请求一线程，
        # 慢客户端不再阻塞其他抓取（V1）
        with self._server_lock:
            try:
                self._server = ThreadingHTTPServer((host, port), handler_cls)
                self._server.daemon_threads = True
            except OSError as exc:
                logger.warning("PrometheusExporter.serve(%s:%s) failed: %s", host, port, exc)
                return
            server = self._server

        logger.info("PrometheusExporter serving on %s:%s/metrics", host, port)
        try:
            server.serve_forever()
        finally:
            # 只关**自己**这一台：热更新时 ``stop()`` 已经把旧服务摘掉了，旧线程
            # 的收尾若无判据再关一次，关掉的是刚换上的新服务（第 26 轮 F-102）。
            self.stop(expected=server)

    def stop(self, *, expected: Any = None) -> None:
        """关闭 :meth:`serve` 启动的 HTTP 服务（幂等、线程安全）。

        Parameters
        ----------
        expected:
            只有关的是这一台时才动手。与连接池 ``_drop(slot, expected=conn)``
            同一口径：``serve()`` 的 ``finally`` 由那条服务线程执行，而它看到的
            ``self._server`` 可能已经被调用方换成了新一代。
        """
        with self._server_lock:
            if self._server is None or (expected is not None and self._server is not expected):
                return
            try:
                self._server.shutdown()
                self._server.server_close()
            except Exception as exc:  # noqa: BLE001
                logger.warning("PrometheusExporter.stop failed: %s", exc)
            self._server = None
