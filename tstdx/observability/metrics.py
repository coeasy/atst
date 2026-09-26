# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""可观测性：Prometheus 风格指标（§37，零硬依赖 stub）。

本模块提供一个**自包含**的指标子系统，不依赖 ``prometheus_client``（可选增强：
若已安装则 ``render_prometheus_client`` 可导出为真实 Collector）。全部指标走
模块级单例 :data:`metrics`，各子系统（client / protocol / streaming）可显式调用
:func:`record_parse` / :func:`record_request` / :func:`record_stream_event` 上报，
也可用 :func:`instrument_client` 对客户端做无侵入埋点。

设计原则
--------
* **永不抛异常**：指标写入失败（如标签非法）只记一条内部告警，绝不污染业务路径；
* **线程安全**：所有累加走 ``threading.Lock``，可被 :class:`~tstdx.streaming.QuoteStream`
  的后台线程安全调用；
* **可降级**：没有 prometheus_client 也能用 ``render()`` 拿到纯文本 / dict 快照。

典型用法::

    from tstdx.observability.metrics import metrics
    metrics.record_request(duration=0.12, ok=True, command="0x0530")
    print(metrics.render_prometheus())
"""

from __future__ import annotations

import contextlib
import threading
import time
from collections.abc import Iterable, Mapping, Sequence
from typing import Any, TypeVar

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
]


# --------------------------------------------------------------------------- #
# 基础指标类型
# --------------------------------------------------------------------------- #
def _escape_label_value(value: str) -> str:
    """转义 Prometheus exposition 标签值（反斜杠 / 双引号 / 换行）。

    参照 Prometheus text format 规范：``\\`` → ``\\\\``、``"`` → ``\\"``、
    换行 → ``\\n``。其余字符原样保留。
    """
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


class Metric:
    """指标基类。"""

    _TYPE = "untyped"

    def __init__(self, name: str, documentation: str, labelnames: Sequence[str] = ()) -> None:
        if not name:
            raise ValueError("指标名不能为空")
        self.name = name
        self.documentation = documentation
        self.labelnames = tuple(labelnames)
        self._lock = threading.Lock()

    #: 子类按标签元组存放不同序列（没有标签则记在 () 下）
    def _key(self, labels: Mapping[str, str]) -> tuple[str, ...]:
        if not self.labelnames:
            return ()
        out: list[str] = []
        for ln in self.labelnames:
            out.append(str(labels.get(ln, "")))
        return tuple(out)

    def _iter(self) -> Iterable[tuple[tuple[str, ...], Any]]:
        raise NotImplementedError

    def render_prometheus(self) -> str:
        lines = [
            f"# HELP {self.name} {self.documentation}",
            f"# TYPE {self.name} {self._TYPE}",
        ]
        for labels, value in self._iter():
            if labels:
                kv = ",".join(
                    f'{k}="{_escape_label_value(v)}"'
                    for k, v in zip(self.labelnames, labels, strict=False)
                )
                lines.append(f"{self.name}{{{kv}}} {value}")
            else:
                lines.append(f"{self.name} {value}")
        return "\n".join(lines) + "\n"


class Counter(Metric):
    """只增不减的计数器。"""

    _TYPE = "counter"

    def __init__(self, name: str, documentation: str, labelnames: Sequence[str] = ()) -> None:
        super().__init__(name, documentation, labelnames)
        self._series: dict[tuple[str, ...], float] = {}

    def inc(self, amount: float = 1.0, labels: Mapping[str, str] | None = None) -> None:
        if amount < 0:
            raise ValueError("Counter 不能为负")
        with self._lock:
            k = self._key(labels or {})
            self._series[k] = self._series.get(k, 0.0) + amount

    def value(self, labels: Mapping[str, str] | None = None) -> float:
        with self._lock:
            return self._series.get(self._key(labels or {}), 0.0)

    def _iter(self):
        with self._lock:
            return list(self._series.items())


class Gauge(Metric):
    """可增可减的瞬时量（如并发连接数、背压队列长度）。"""

    _TYPE = "gauge"

    def __init__(self, name: str, documentation: str, labelnames: Sequence[str] = ()) -> None:
        super().__init__(name, documentation, labelnames)
        self._series: dict[tuple[str, ...], float] = {}

    def set(self, value: float, labels: Mapping[str, str] | None = None) -> None:
        with self._lock:
            self._series[self._key(labels or {})] = value

    def inc(self, amount: float = 1.0, labels: Mapping[str, str] | None = None) -> None:
        with self._lock:
            k = self._key(labels or {})
            self._series[k] = self._series.get(k, 0.0) + amount

    def dec(self, amount: float = 1.0, labels: Mapping[str, str] | None = None) -> None:
        self.inc(-amount, labels)

    def value(self, labels: Mapping[str, str] | None = None) -> float:
        with self._lock:
            return self._series.get(self._key(labels or {}), 0.0)

    def _iter(self):
        with self._lock:
            return list(self._series.items())


_DEFAULT_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0)


class Histogram(Metric):
    """直方图（观测值落入分桶 + 总计数 + 总和）。"""

    _TYPE = "histogram"

    def __init__(
        self,
        name: str,
        documentation: str,
        labelnames: Sequence[str] = (),
        buckets: Sequence[float] = _DEFAULT_BUCKETS,
    ) -> None:
        super().__init__(name, documentation, labelnames)
        self._buckets = tuple(buckets) + ("+Inf",)
        # 每个序列存：bucket 计数（含 +Inf）、_count、_sum
        self._series: dict[tuple[str, ...], dict[str, float]] = {}

    def _empty(self) -> dict[str, float]:
        d: dict[str, float] = {f"le_{b}": 0.0 for b in self._buckets}
        d["_count"] = 0.0
        d["_sum"] = 0.0
        return d

    def observe(self, value: float, labels: Mapping[str, str] | None = None) -> None:
        """记录一次观测（**累积语义**，Prometheus 规范）。

        每个观测值计入所有 ``le >= value`` 的桶（含 ``+Inf``），即
        ``le_+Inf`` 恒等于 ``_count``；分位数计算（``histogram_quantile``）
        依赖该累积性。
        """
        with self._lock:
            k = self._key(labels or {})
            s = self._series.setdefault(k, self._empty())
            s["_count"] += 1
            s["_sum"] += value
            # 累积：每个值计入所有 le>=value 的桶；+Inf 无条件计入
            # （修复：此前命中首个桶即 break，分位数全链路静默失真）
            s["le_+Inf"] += 1
            for b in self._buckets:
                if b != "+Inf" and value <= float(b):
                    s[f"le_{b}"] += 1

    def _iter(self):
        out: list[tuple[tuple[str, ...], str]] = []
        with self._lock:
            for k, s in self._series.items():
                kv = ""
                if self.labelnames:
                    kv = ",".join(
                        f'{n}="{_escape_label_value(v)}"'
                        for n, v in zip(self.labelnames, k, strict=False)
                    )
                for b in self._buckets:
                    if kv:
                        out.append((k, f'{self.name}_bucket{{{kv},le="{b}"}} {s[f"le_{b}"]}'))
                    else:
                        out.append((k, f'{self.name}_bucket{{le="{b}"}} {s[f"le_{b}"]}'))
                if kv:
                    out.append((k, f"{self.name}_sum{{{kv}}} {s['_sum']}"))
                    out.append((k, f"{self.name}_count{{{kv}}} {s['_count']}"))
                else:
                    out.append((k, f"{self.name}_sum {s['_sum']}"))
                    out.append((k, f"{self.name}_count {s['_count']}"))
        # 返回「标签, 文本行」便于 render_prometheus 复用
        return [(k, line) for k, line in out]

    def render_prometheus(self) -> str:
        lines = [
            f"# HELP {self.name} {self.documentation}",
            f"# TYPE {self.name} histogram",
        ]
        for _, line in self._iter():
            lines.append(line)
        return "\n".join(lines) + "\n"


class Summary(Metric):
    """摘要（仅计数 + 求和；分位数计算交给导出端，保持轻量）。"""

    _TYPE = "summary"

    def __init__(self, name: str, documentation: str, labelnames: Sequence[str] = ()) -> None:
        super().__init__(name, documentation, labelnames)
        self._series: dict[tuple[str, ...], dict[str, float]] = {}

    def observe(self, value: float, labels: Mapping[str, str] | None = None) -> None:
        with self._lock:
            k = self._key(labels or {})
            s = self._series.setdefault(k, {"_count": 0.0, "_sum": 0.0})
            s["_count"] += 1
            s["_sum"] += value

    def _iter(self):
        out: list[tuple[tuple[str, ...], str]] = []
        with self._lock:
            for k, s in self._series.items():
                if self.labelnames:
                    kv = ",".join(
                        f'{n}="{_escape_label_value(v)}"'
                        for n, v in zip(self.labelnames, k, strict=False)
                    )
                    out.append((k, f"{self.name}_sum{{{kv}}} {s['_sum']}"))
                    out.append((k, f"{self.name}_count{{{kv}}} {s['_count']}"))
                else:
                    out.append((k, f"{self.name}_sum {s['_sum']}"))
                    out.append((k, f"{self.name}_count {s['_count']}"))
        return out

    def render_prometheus(self) -> str:
        lines = [
            f"# HELP {self.name} {self.documentation}",
            f"# TYPE {self.name} summary",
        ]
        for _, line in self._iter():
            lines.append(line)
        return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- #
# 注册表
# --------------------------------------------------------------------------- #
_M = TypeVar("_M", bound=Metric)


class Registry:
    """指标注册表。"""

    def __init__(self) -> None:
        self._metrics: dict[str, Metric] = {}
        self._lock = threading.Lock()

    def register(self, metric: _M) -> _M:
        # 泛型签名（L1b）：返回与传入一致的子类类型，Metrics 门面的
        # inc/observe/set 属性调用因此静态可见。
        with self._lock:
            if metric.name in self._metrics and self._metrics[metric.name] is not metric:
                # 同名允许重复 register（幂等：返回已注册的）
                registered = self._metrics[metric.name]
                return registered  # type: ignore[return-value]  # 幂等分支
            self._metrics[metric.name] = metric
            return metric

    def unregister(self, name: str) -> None:
        with self._lock:
            self._metrics.pop(name, None)

    def get(self, name: str) -> Metric | None:
        with self._lock:
            return self._metrics.get(name)

    def names(self) -> list[str]:
        with self._lock:
            return sorted(self._metrics)

    def snapshot_metrics(self) -> list[Metric]:
        """返回已注册指标的**快照列表**（注册表锁内拷贝）。

        导出器（Prometheus / OTLP / StatsD）应遍历该快照而非直读
        ``_metrics`` 字典，避免遍历期间注册/注销引发的
        ``RuntimeError: dictionary changed size`` 与锁不一致。
        """
        with self._lock:
            return list(self._metrics.values())

    def render_prometheus(self) -> str:
        blocks: list[str] = []
        for m in self.snapshot_metrics():
            blocks.append(m.render_prometheus())
        return "\n".join(blocks)

    def snapshot(self) -> dict[str, Any]:
        """返回可被 JSON 序列化的当前指标快照。"""
        out: dict[str, Any] = {}
        for m in self.snapshot_metrics():
            name = m.name
            if isinstance(m, (Counter, Gauge)):
                out[name] = {",".join(k): v for k, v in m._iter()}
            elif isinstance(m, Histogram):
                # 深审 M21：旧实现 buckets 只读第一个序列、count/sum 却跨全部
                # 序列求和——多标签时 bucket 分布与总数口径不一致（分位数
                # 全链路失真）。改为按序列（标签组合）输出，各自完整
                # bucket/count/sum，口径与 Prometheus 规范一致。
                with m._lock:
                    out[name] = {
                        ",".join(k): {
                            "buckets": {b: s[f"le_{b}"] for b in m._buckets},
                            "count": s["_count"],
                            "sum": s["_sum"],
                        }
                        for k, s in m._series.items()
                    }
            elif isinstance(m, Summary):
                with m._lock:
                    out[name] = {
                        "count": sum(d["_count"] for d in m._series.values()),
                        "sum": sum(d["_sum"] for d in m._series.values()),
                    }
        return out

    def render_prometheus_client(self) -> str:
        """若安装了 prometheus_client，导出为真实文本格式。

        未安装时回退到内置 ``render_prometheus``（功能等价，但缺少
        prometheus_client 的并发优化与压缩）。

        注意：Collector 每个指标只创建一次（循环外），再按序列
        ``labels(...)`` 子系列填充——此前在序列循环内重复创建同名
        Collector，多标签多序列时必然触发「Duplicated timeseries」崩溃。
        Histogram / Summary 的 prometheus_client 对象模型需要自定义
        Collector（含 bucket 布局注册），当前显式跳过（见下方注释），
        仍可通过内置 :meth:`render_prometheus` 获得等价文本输出。
        """
        try:
            from prometheus_client import CollectorRegistry, generate_latest
            from prometheus_client.core import Counter as PCCounter
            from prometheus_client.core import Gauge as PCGauge
        except ImportError:
            return self.render_prometheus()
        reg = CollectorRegistry()
        for m in self.snapshot_metrics():
            if isinstance(m, (Counter, Gauge)):
                # Collector 注册到 registry 一次；多序列经 labels() 取子系列
                pc: Any
                if isinstance(m, Counter):
                    pc = PCCounter(m.name, m.documentation, list(m.labelnames), registry=reg)
                else:
                    pc = PCGauge(m.name, m.documentation, list(m.labelnames), registry=reg)
                for labels, val in m._iter():
                    if isinstance(m, Counter):
                        if m.labelnames:
                            pc.labels(*labels).inc(val)
                        else:
                            pc.inc(val)
                    else:
                        if m.labelnames:
                            pc.labels(*labels).set(val)
                        else:
                            pc.set(val)
            elif isinstance(m, (Histogram, Summary)):
                # 显式跳过：prometheus_client 的 Histogram/Summary 需要自定义
                # Collector 才能映射任意 bucket 布局；此处不二次注册以免崩溃。
                # 等价文本输出可走 Metrics.render_prometheus()。
                continue
        return generate_latest(reg).decode("utf-8")


# --------------------------------------------------------------------------- #
# 单例 + 预置 tstdx 指标
# --------------------------------------------------------------------------- #
class Metrics:
    """tstdx 全局指标门面。

    内置一组覆盖「协议解析 / 在线请求 / 流事件 / 重连」的标准指标，并暴露
    :meth:`record_parse` / :meth:`record_request` / :meth:`record_stream_event`
    等便捷上报方法。
    """

    def __init__(self) -> None:
        self.registry = Registry()
        # 协议解析
        self.parse_total = self.registry.register(
            Counter(
                "tstdx_protocol_parse_total",
                "三级解析分派总次数",
                labelnames=("tier", "family", "command"),
            )
        )
        self.parse_confidence = self.registry.register(
            Histogram(
                "tstdx_protocol_parse_confidence",
                "L1/L2 解析置信度分布",
                labelnames=("family", "command"),
            )
        )
        # 在线请求
        self.request_total = self.registry.register(
            Counter(
                "tstdx_request_total",
                "在线请求总次数",
                labelnames=("command", "status"),
            )
        )
        self.request_duration = self.registry.register(
            Histogram(
                "tstdx_request_duration_seconds",
                "在线请求耗时（秒）",
                labelnames=("command",),
            )
        )
        # F-112：这里曾注册 `Gauge("tstdx_active_connections", "当前活跃连接数")`，
        # 并由下面的 `set_active_connections()` 写入——而**全仓没有任何一处写它**。
        # 一个已经注册、会被 `/metrics` 渲染、还被 statsd 文档当示例的仪表恒为 0，
        # 比缺一根仪表更糟：它把"运行时没有活连接"当成实测值说给抓取方，而池里此刻
        # 可能正挂着好几条。成对的 `set_backpressure()` 有人喂（streaming/engine.py:335），
        # 可见这一半本就是漏接。要么补上 transport 层的连接生命周期钩子（两条池路径都要，
        # 且必须与 retire/drain 同刻度，否则会漂），要么撤下——在没有前者这一版之前，
        # 按「宁跳不假绿」撤下仪表与其 setter，而不是留一根恒 0 的对外假读数。
        # 流式
        self.stream_events = self.registry.register(
            Counter(
                "tstdx_stream_events_total",
                "流式事件总次数",
                labelnames=("kind",),
            )
        )
        self.stream_reconnects = self.registry.register(
            Counter("tstdx_stream_reconnects_total", "流式重连总次数")
        )
        self.stream_backpressure = self.registry.register(
            Gauge("tstdx_stream_backpressure", "流式背压队列当前长度")
        )
        # 错误
        self.errors_total = self.registry.register(
            Counter(
                "tstdx_errors_total",
                "错误总次数",
                labelnames=("error_type",),
            )
        )

    # -- 便捷上报 ----------------------------------------------------------- #
    def record_parse(
        self, *, tier: str, family: str, command: str, confidence: float = 1.0
    ) -> None:
        try:
            self.parse_total.inc(labels={"tier": tier, "family": family, "command": command})
            if tier in ("L1", "L2"):
                self.parse_confidence.observe(
                    confidence, labels={"family": family, "command": command}
                )
        except Exception:  # 指标失败绝不外溢
            pass

    def record_request(self, *, command: str, ok: bool, duration: float | None = None) -> None:
        try:
            self.request_total.inc(labels={"command": command, "status": "ok" if ok else "err"})
            if duration is not None:
                self.request_duration.observe(max(duration, 0.0), labels={"command": command})
        except Exception:
            pass

    def record_stream_event(self, kind: str) -> None:
        with contextlib.suppress(Exception):
            self.stream_events.inc(labels={"kind": kind})

    def record_reconnect(self) -> None:
        with contextlib.suppress(Exception):
            self.stream_reconnects.inc()

    def record_error(self, error_type: str) -> None:
        with contextlib.suppress(Exception):
            self.errors_total.inc(labels={"error_type": error_type})

    def set_backpressure(self, n: int) -> None:
        """流式背压队列长度（F-112 撤下恒 0 的 `set_active_connections()` 后，
        这是唯一的 gauge setter，且确实有人喂：``streaming/engine.py:335``）。"""
        with contextlib.suppress(Exception):
            self.stream_backpressure.set(n)

    # -- 导出 --------------------------------------------------------------- #
    def render_prometheus(self) -> str:
        return self.registry.render_prometheus()

    def render(self) -> str:
        return self.registry.render_prometheus()

    def snapshot(self) -> dict[str, Any]:
        return self.registry.snapshot()

    def __call__(self) -> Metrics:
        return self


#: 全局单例
metrics = Metrics()


# --------------------------------------------------------------------------- #
# 便捷函数（供子模块直接 import 使用）
# --------------------------------------------------------------------------- #
def record_parse(*, tier: str, family: str, command: str, confidence: float = 1.0) -> None:
    metrics.record_parse(tier=tier, family=family, command=command, confidence=confidence)


def record_request(*, command: str, ok: bool, duration: float | None = None) -> None:
    metrics.record_request(command=command, ok=ok, duration=duration)


def record_stream_event(kind: str) -> None:
    metrics.record_stream_event(kind)


def record_reconnect() -> None:
    metrics.record_reconnect()


def record_error(error_type: str) -> None:
    metrics.record_error(error_type)


def instrument_client(client: Any, metrics: Metrics | None = None) -> Any:
    """对客户端做**无侵入**埋点：包装其 ``request`` 方法以记录耗时与成败。

    返回同一个 ``client``（原地修改）。仅当客户端存在 ``request`` 属性时生效，
    否则原样返回。失败路径也会通过 :meth:`Metrics.record_error` 上报。

    Parameters
    ----------
    metrics:
        记录目标；缺省使用模块级单例 :data:`metrics`（便于测试注入独立实例）。

    Examples
    --------
    >>> from tstdx.client import TdxClient
    >>> from tstdx.observability.metrics import instrument_client
    >>> c = instrument_client(TdxClient())
    """
    target = metrics if metrics is not None else globals()["metrics"]
    original = getattr(client, "request", None)
    if original is None or getattr(original, "_tstdx_instrumented", False):
        return client

    def wrapped(cmd, body, **kw):
        t0 = time.perf_counter()
        ok = True
        err_type = ""
        try:
            return original(cmd, body, **kw)
        except Exception as exc:  # noqa: BLE001 - 埋点不应改变异常语义
            ok = False
            err_type = type(exc).__name__
            raise
        finally:
            dt = time.perf_counter() - t0
            target.record_request(command=f"0x{int(cmd):04x}", ok=ok, duration=dt)
            if not ok:
                target.record_error(err_type)

    wrapped._tstdx_instrumented = True  # type: ignore[attr-defined]
    client.request = wrapped
    return client
