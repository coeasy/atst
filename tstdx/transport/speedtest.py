# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""主站测速与排名（§12.5）。

测速口径
--------
一次探测包含两段耗时：

* ``connect_ms`` —— TCP 三次握手耗时
* ``rtt_ms``     —— 一个完整请求/响应往返耗时（含解析前读取）

排序使用 **rtt_ms**（更能反映真实查询体验）；连接都失败的条目
按 ``failures`` 指数惩罚沉底。

.. note::
   探测命令默认 :data:`~tstdx.transport.base.DEFAULT_HEARTBEAT_CMD`，
   只测传输、不解析内容，因此对任何主站都安全。
"""

from __future__ import annotations

import concurrent.futures as _fut
import time
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from ..protocol.commands import Family
from .base import DEFAULT_HEARTBEAT_CMD, TcpConnection
from .hosts import DEFAULT_HOST_POOL, HostEntry, RankingStore

__all__ = [
    "ProbeResult",
    "probe",
    "speedtest",
    "rank_hosts",
    "speedtest_and_save",
]


# --------------------------------------------------------------------------- #
# 结果
# --------------------------------------------------------------------------- #
@dataclass
class ProbeResult:
    host: str
    port: int
    family: str = Family.STANDARD
    ok: bool = False
    connect_ms: float | None = None
    rtt_ms: float | None = None
    error: str = ""

    @property
    def key(self) -> str:
        return f"{self.host}:{self.port}"

    @property
    def score(self) -> float:
        if not self.ok or self.rtt_ms is None:
            return float("inf")
        return self.rtt_ms

    def to_entry(self) -> HostEntry:
        return HostEntry(
            host=self.host,
            port=self.port,
            family=self.family,
            connect_ms=self.connect_ms,
            rtt_ms=self.rtt_ms,
            failures=0 if self.ok else 1,
            last_ok=time.time() if self.ok else None,
            last_error="" if self.ok else self.error,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "host": self.host,
            "port": self.port,
            "family": self.family,
            "ok": self.ok,
            "connect_ms": None if self.connect_ms is None else round(self.connect_ms, 2),
            "rtt_ms": None if self.rtt_ms is None else round(self.rtt_ms, 2),
            "error": self.error,
        }


# --------------------------------------------------------------------------- #
# 单点探测
# --------------------------------------------------------------------------- #
def probe(
    host: str,
    port: int = 7709,
    *,
    family: str = Family.STANDARD,
    timeout: float = 1.0,
    cmd: int = DEFAULT_HEARTBEAT_CMD,
    body: bytes = b"",
    samples: int = 1,
) -> ProbeResult:
    """探测单个主站。取 ``samples`` 次里 **最好的一次**（网络噪声取最小值更稳）。"""
    best: ProbeResult | None = None
    for _ in range(max(1, samples)):
        conn = TcpConnection(host, port, timeout=timeout, connect_timeout=timeout)
        started = time.perf_counter()
        try:
            conn.connect()
            connect_ms = (time.perf_counter() - started) * 1000.0
            rtt = conn.ping(cmd, body)
            res = ProbeResult(
                host=host,
                port=port,
                family=family,
                ok=True,
                connect_ms=connect_ms,
                rtt_ms=rtt,
            )
        except Exception as exc:
            res = ProbeResult(
                host=host,
                port=port,
                family=family,
                ok=False,
                error=f"{type(exc).__name__}: {exc}",
            )
        finally:
            conn.close()
        if best is None or res.score < best.score:
            best = res
        if best.ok:
            # 已有成功样本时仍继续采样，取最优
            continue
    return best or ProbeResult(host=host, port=port, family=family, ok=False, error="no sample")


# --------------------------------------------------------------------------- #
# 批量测速
# --------------------------------------------------------------------------- #
def speedtest(
    hosts: Sequence[HostEntry] | None = None,
    *,
    family: str = Family.STANDARD,
    timeout: float = 1.0,
    samples: int = 1,
    max_workers: int = 16,
    cmd: int = DEFAULT_HEARTBEAT_CMD,
    progress: bool = False,
) -> list[ProbeResult]:
    """并发测速，返回按耗时升序的结果列表（失败的排在最后）。"""
    entries = list(hosts) if hosts else [e for e in DEFAULT_HOST_POOL if e.family == family]
    if not entries:
        return []

    results: list[ProbeResult] = []
    with _fut.ThreadPoolExecutor(max_workers=min(max_workers, len(entries))) as pool:
        futures = {
            pool.submit(
                probe,
                e.host,
                e.port,
                family=e.family,
                timeout=timeout,
                cmd=cmd,
                samples=samples,
            ): e
            for e in entries
        }
        for n, fut in enumerate(_fut.as_completed(futures), 1):
            try:
                res = fut.result()
            except Exception as exc:  # pragma: no cover
                e = futures[fut]
                res = ProbeResult(
                    host=e.host,
                    port=e.port,
                    family=e.family,
                    ok=False,
                    error=f"{type(exc).__name__}: {exc}",
                )
            results.append(res)
            if progress:
                mark = "OK " if res.ok else "ERR"
                rtt = f"{res.rtt_ms:.1f}ms" if res.rtt_ms is not None else "-"
                print(f"[{n}/{len(entries)}] {mark} {res.host}:{res.port} {rtt} {res.error}")

    results.sort(key=lambda r: r.score)
    return results


def rank_hosts(results: Iterable[ProbeResult]) -> list[HostEntry]:
    """把测速结果转成可直接喂给 :class:`ConnectionPool` 的条目列表。"""
    entries = [r.to_entry() for r in results]
    entries.sort(key=lambda e: e.score)
    return entries


def speedtest_and_save(
    hosts: Sequence[HostEntry] | None = None,
    *,
    family: str = Family.STANDARD,
    timeout: float = 1.0,
    samples: int = 1,
    max_workers: int = 16,
    ranking_file: str = "~/.tstdx/server_ranking.json",
    progress: bool = False,
    keep_failures: bool = True,
) -> list[ProbeResult]:
    """测速并写入排名文件。返回全部结果（含失败项）。"""
    results = speedtest(
        hosts,
        family=family,
        timeout=timeout,
        samples=samples,
        max_workers=max_workers,
        progress=progress,
    )
    entries = rank_hosts(results if keep_failures else [r for r in results if r.ok])
    RankingStore(ranking_file).update(entries)
    return results
