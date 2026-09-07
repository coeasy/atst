# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""StatsD / DogStatsD 导出器（Tier C / C5）。

将 :mod:`tstdx.observability.metrics` 内部指标注册表以 UDP datagram 推送到
StatsD 兼容端点（默认 ``127.0.0.1:8125``，DogStatsD 格式支持 tag）。
本模块**零硬依赖**：仅用标准库 :mod:`socket` 发送 UDP 报文。

设计原则
--------
* **非阻塞 / 不崩溃**：UDP 发送失败（端口未开、网卡不可用等）仅记录一次日志
  后静默吞掉，**绝不抛出**污染业务路径；
* **懒加载 socket**：首次 :meth:`push` 时才创建 UDP socket；
* **线程安全**：socket 与日志状态均受 :class:`threading.Lock` 保护；
* **前缀自动加**：所有指标名自动加 ``prefix.`` 前缀（可通过构造参数覆盖或置空）。

DogStatsD 格式（Datagram 字符串）::

    name[:type]:value|type[#tag1:v1,tag2:v2]

    # 例：
    tstdx.request_total:5|c|#command:0x0530,status:ok
    tstdx.active_connections:3|g
    tstdx.request_duration_seconds:0.12|d|#command:0x0530
    tstdx.parse_confidence:0.95|h|#family:f10

metric_type 支持：``counter`` (c) / ``gauge`` (g) / ``timing`` (d) /
``histogram`` (h) / ``set`` (s) / ``gauge_inc`` (g, +) / ``gauge_dec`` (g, -)。

**Counter 增量语义**：StatsD 的 ``c`` 是「自上次推送以来的增量」，而内部
注册表是累计值——:meth:`push_all` 内部做快照差分，只推送增量（修复此前
把累计值当增量推送导致的统计膨胀）。

典型用法::

    from tstdx.observability import StatsdExporter, metrics

    exp = StatsdExporter(host="127.0.0.1", port=8125, prefix="tstdx")
    exp.push("request_total", 5, metric_type="counter", tags={"command": "0x0530"})
    exp.push_all()  # 推送整个注册表快照（Counter 自动差分为增量）
"""

from __future__ import annotations

import logging
import socket
import threading
from collections.abc import Mapping, Sequence
from typing import Any

__all__ = [
    "StatsdExporter",
    "STATSD_TYPE_SHORTCODES",
]

logger = logging.getLogger(__name__)

#: metric_type → DogStatsD 短代码映射。
STATSD_TYPE_SHORTCODES: dict[str, str] = {
    "counter": "c",
    "gauge": "g",
    "timing": "d",
    "histogram": "h",
    "set": "s",
    "gauge_inc": "g",
    "gauge_dec": "g",
}


class StatsdExporter:
    """UDP StatsD / DogStatsD 推送器。

    Parameters
    ----------
    host : str, optional
        StatsD 服务主机地址。默认 ``127.0.0.1``。
    port : int, optional
        StatsD 服务端口。默认 ``8125``；``0`` 表示**禁用推送**
        （构造时记一条 info 日志，后续 push/push_all 静默 no-op）。
    prefix : str, optional
        指标名前缀。默认 ``tstdx``；设为空字符串可禁用前缀。
    sock_family : int, optional
        Socket 地址族。默认 :attr:`socket.AF_INET`（IPv4）。
    interval : float, optional
        周期推送间隔（秒）。``0``（默认）= 不启用周期推送，仅手动
        :meth:`push` / :meth:`push_all`；``> 0`` 时需显式调用
        :meth:`start_pushing` 启动后台推送线程。

    Examples
    --------
    >>> exp = StatsdExporter()
    >>> exp.push("errors_total", 2, metric_type="counter", tags={"error_type": "Timeout"})
    >>> exp.push_all()  # 推送全局 metrics 单例的当前快照
    """

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 8125,
        prefix: str = "tstdx",
        sock_family: int = socket.AF_INET,
        interval: float = 0.0,
        metrics: Any = None,
    ) -> None:
        self.host = host
        self.port = port
        self.prefix = prefix
        self.sock_family = sock_family
        self.interval = max(float(interval), 0.0)
        # push_all / start_pushing 的缺省指标源；None 时用全局单例（懒解析）
        self._metrics = metrics

        self._sock: socket.socket | None = None
        self._lock = threading.Lock()
        # 失败日志去重：同一 (host, port) 只记一次
        self._warned: bool = False
        # 禁用状态（port<=0）：记一次 info，此后推送静默 no-op
        self._disabled: bool = port <= 0
        if self._disabled:
            logger.info("StatsdExporter disabled (port=%s); all pushes will be dropped.", port)
        # Counter 增量差分基线：((metric_name, label_key) -> 上次累计值)
        self._counter_baseline: dict[tuple[str, str], float] = {}
        # 周期推送线程
        self._push_thread: threading.Thread | None = None
        self._push_stop = threading.Event()

    # -- 内部 --------------------------------------------------------------- #
    def _get_socket(self) -> socket.socket | None:
        """懒加载 UDP socket；失败返回 None（不抛异常）。"""
        with self._lock:
            if self._sock is not None:
                return self._sock
            try:
                sock = socket.socket(self.sock_family, socket.SOCK_DGRAM)
                sock.setblocking(False)
                self._sock = sock
                return sock
            except OSError as exc:
                logger.warning(
                    "StatsdExporter socket create failed (%s:%s): %s", self.host, self.port, exc
                )
                if not self._warned:
                    logger.error(
                        "StatsdExporter: UDP socket unavailable; "
                        "all pushes will be dropped (host=%s port=%s).",
                        self.host,
                        self.port,
                    )
                    self._warned = True
                return None

    @staticmethod
    def _format_metric(
        name: str,
        value: float,
        metric_type: str,
        tags: Mapping[str, str] | None,
        prefix: str,
    ) -> str:
        """组装单条 StatsD 报文（含 tag）。

        支持 gauge_inc / gauge_dec 的 ``+`` / ``-`` 前缀。
        """
        full_name = f"{prefix}.{name}" if prefix else name
        code = STATSD_TYPE_SHORTCODES.get(metric_type, "g")
        # gauge_inc / gauge_dec 需前缀符号
        if metric_type == "gauge_inc":
            value = abs(value)
            prefix_sym = "+"
        elif metric_type == "gauge_dec":
            value = abs(value)
            prefix_sym = "-"
        else:
            prefix_sym = ""

        # 数值格式：整数不带小数点（如 5），浮点保留原值
        if isinstance(value, int) or (isinstance(value, float) and value.is_integer()):
            val_str = str(int(value))
        else:
            val_str = repr(value)

        payload = f"{full_name}:{prefix_sym}{val_str}|{code}"
        if tags:
            tag_parts = ",".join(f"{k}:{v}" for k, v in tags.items())
            payload += f"|#{tag_parts}"
        return payload

    # -- 推送 --------------------------------------------------------------- #
    def push(
        self,
        metric_name: str,
        value: float,
        metric_type: str = "gauge",
        tags: Mapping[str, str] | None = None,
    ) -> None:
        """推送单条指标到 StatsD 端点。

        非阻塞：socket 失败或发送失败仅记日志一次后静默吞掉。
        禁用状态（``port=0``）下静默 no-op。
        """
        if not metric_name or self._disabled:
            return
        sock = self._get_socket()
        if sock is None:
            return

        try:
            payload = self._format_metric(metric_name, value, metric_type, tags, self.prefix)
            sock.sendto(payload.encode("utf-8"), (self.host, self.port))
        except OSError as exc:
            if not self._warned:
                logger.warning("StatsdExporter push(%s) failed: %s", metric_name, exc)
                self._warned = True
        except Exception as exc:  # noqa: BLE001 - 绝不外溢
            if not self._warned:
                logger.warning("StatsdExporter push(%s) unexpected error: %s", metric_name, exc)
                self._warned = True

    def push_all(
        self,
        metrics: Any = None,
        metric_type_map: Mapping[str, str] | None = None,
    ) -> None:
        """推送整个指标注册表的当前快照。

        Parameters
        ----------
        metrics : Metrics, optional
            目标 :class:`tstdx.observability.metrics.Metrics` 实例。默认全局
            单例。
        metric_type_map : dict[str, str], optional
            指标名 → metric_type 的覆盖表；默认按指标类型推断（Counter →
            counter，Gauge → gauge，Histogram → histogram，Summary → timing）。

        Counter 语义：内部注册表是**累计值**，而 StatsD ``c`` 期望
        「自上次推送以来的增量」——本方法做快照差分，只推增量
        （计数器回退视为重置，直接推当前值）。

        失败静默；返回 None。
        """
        if self._disabled:
            return
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
        try:
            snapshot = m.snapshot()
        except Exception as exc:  # noqa: BLE001
            if not self._warned:
                logger.warning("StatsdExporter.push_all snapshot failed: %s", exc)
                self._warned = True
            return

        if metric_type_map is None:
            metric_type_map = {}

        # 从 registry 快照取每个指标的原始类型与标签名（注册表锁内拷贝，
        # 避免遍历期间注册/注销竞态）
        type_hints: dict[str, str] = {}
        labelnames_by_name: dict[str, tuple[str, ...]] = {}
        try:
            for metric_obj in m.registry.snapshot_metrics():
                name = metric_obj.name
                labelnames_by_name[name] = tuple(metric_obj.labelnames)
                if isinstance(metric_obj, Counter):
                    type_hints[name] = "counter"
                elif isinstance(metric_obj, Gauge):
                    type_hints[name] = "gauge"
                elif isinstance(metric_obj, Histogram):
                    type_hints[name] = "histogram"
                elif isinstance(metric_obj, Summary):
                    type_hints[name] = "timing"
        except Exception as exc:  # noqa: BLE001
            if not self._warned:
                logger.warning("StatsdExporter.push_all type inference failed: %s", exc)
                self._warned = True

        # 解析 snapshot 结构：{name: {labelkey: value} | {count/sum/buckets}}
        for name, data in snapshot.items():
            real_kind = type_hints.get(name, "gauge")
            mt = metric_type_map.get(name) or real_kind
            if not isinstance(data, Mapping):
                continue
            if real_kind in ("histogram", "timing"):
                # 深审 M21 适配：Registry.snapshot 的 Histogram 现按序列
                # （标签组合）输出 {labelkey: {buckets, count, sum}}——
                # 旧实现只消费顶层单一结构，多序列时其余序列被丢弃。
                for label_key, series in data.items():
                    if not isinstance(series, Mapping):
                        continue
                    tags = self._parse_labels(label_key, labelnames_by_name.get(name, ()))
                    if isinstance(series.get("buckets"), Mapping):
                        for bucket, val in series["buckets"].items():
                            self.push(
                                f"{name}_bucket",
                                val,
                                metric_type="histogram",
                                tags={**(tags or {}), "le": str(bucket)},
                            )
                    if "count" in series:
                        self.push(
                            f"{name}_count", series["count"], metric_type="histogram", tags=tags
                        )
                    if "sum" in series:
                        self.push(f"{name}_sum", series["sum"], metric_type="histogram", tags=tags)
                continue
            # 简单 metric：{label_key: value}
            for label_key, val in data.items():
                if isinstance(val, (int, float)):
                    tags = self._parse_labels(label_key, labelnames_by_name.get(name, ()))
                    if mt == "counter":
                        val = self._counter_delta(name, str(label_key), val)
                        if val is None:
                            continue  # 增量为 0 无需推送
                    self.push(name, val, metric_type=mt, tags=tags)

    # -- Counter 增量差分 ---------------------------------------------------- #
    def _counter_delta(self, name: str, label_key: str, current: float) -> float | None:
        """计算 Counter 相对上次快照的增量；返回 None 表示无增量可推。

        - 首次推送：以 0 为基线（差分 = 累计值，即「自启动以来发生的量」；
          该口径经 F4 轮测试契约锚定——exporter 重启后的首次推送携带全部
          累计，属**已知设计**而非缺陷，下游按 StatsD 计数器语义归一化）；
        - 正常增长：推 ``current - baseline``；
        - 计数器回退（current < baseline，如进程内指标被重置）：视为重置，
          推当前值并重设基线。
        """
        key = (name, label_key)
        with self._lock:
            baseline = self._counter_baseline.get(key, 0.0)
            self._counter_baseline[key] = current
            if current < baseline:  # 重置
                return current
            delta = current - baseline
            return delta if delta > 0 else None

    @staticmethod
    def _parse_labels(label_key: str, labelnames: Sequence[str] = ()) -> dict[str, str] | None:
        """把内部快照的序列键还原为 DogStatsD tag 字典。

        内部序列键有两种形态：

        * 带标签名指标：``",".join(标签值序列)``（如 ``"0x0530,ok"``）——
          配合 ``labelnames``（如 ``("command", "status")``）按下标配对，
          还原为 ``{"command": "0x0530", "status": "ok"}``；
        * 无标签名但显式 ``k:v`` 形态的键（如 ``"cmd:0x0530,status:ok"``）——
          直接按 ``k:v`` 解析。

        无法解析（空键、长度不匹配且非 ``k:v`` 形态）时返回 ``None``。
        """
        if not label_key:
            return None
        parts = label_key.split(",")
        if labelnames and len(parts) == len(labelnames):
            return dict(zip(labelnames, parts, strict=True))
        # 形态回退：显式 k:v 对（允许带标签名但长度不匹配时的尽力解析）
        if all(":" in p for p in parts if p):
            parsed: dict[str, str] = {}
            for p in parts:
                if not p:
                    continue
                k, _, v = p.partition(":")
                if k:
                    parsed[k] = v
            return parsed or None
        return None

    # -- 周期推送 ------------------------------------------------------------- #
    def start_pushing(self, interval: float | None = None, metrics: Any = None) -> None:
        """启动后台 daemon 线程，按 ``interval`` 秒周期调用 :meth:`push_all`。

        重复调用时先停掉旧线程再启动新线程；``interval``（缺省用构造参数）
        ``<= 0`` 时记一条 info 后不启动（禁用周期推送）。禁用状态
        （``port=0``）下同样不启动。
        """
        if self._disabled:
            logger.info("StatsdExporter disabled (port=%s); start_pushing ignored.", self.port)
            return
        itv = self.interval if interval is None else max(float(interval), 0.0)
        if itv <= 0:
            logger.info(
                "StatsdExporter periodic push disabled (interval=0); "
                "use push()/push_all() manually."
            )
            return
        self.stop_pushing()

        def _loop() -> None:
            while not self._push_stop.wait(itv):
                self.push_all(metrics)

        self._push_stop.clear()
        th = threading.Thread(target=_loop, name="tstdx-statsd-push", daemon=True)
        self._push_thread = th
        th.start()

    def stop_pushing(self) -> None:
        """停止后台周期推送线程（幂等、线程安全）。"""
        self._push_stop.set()
        th = self._push_thread
        self._push_thread = None
        if th is not None and th.is_alive() and th is not threading.current_thread():
            th.join(timeout=5.0)

    def flush(self) -> None:
        """保持 API 对称（与 Prometheus / OTel 导出器对齐）；UDP 无缓冲故为 no-op。"""
        return None

    def close(self) -> None:
        """关闭底层 socket 并停止周期推送线程（幂等、线程安全）。"""
        self.stop_pushing()
        with self._lock:
            if self._sock is not None:
                try:
                    self._sock.close()
                except OSError as exc:
                    logger.debug("StatsdExporter close: %s", exc)
                self._sock = None
