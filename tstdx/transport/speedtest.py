# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""主站测速与 STANDARD 运行时排名（§12.5）。

测速口径
--------
一次探测包含两段耗时：

* ``connect_ms`` —— TCP 三次握手耗时
* ``rtt_ms``     —— 一个完整请求/响应往返耗时（含解析前读取）

排序使用 **rtt_ms**。所有 family 都可测速并在调用方提供 ``HostEntry`` 时
原位刷新当前进程的 RTT 排序证据；历史 V1 ``RankingStore`` 仅持久化
STANDARD，因为它仍以 ``host:port`` 为 key，无法安全表达同一 endpoint 的
多 family 身份。测速绝不覆盖真实请求维护的 failures/circuit/name/verified。
"""

from __future__ import annotations

import concurrent.futures as _fut
import time
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from ..errors import ConfigError
from ..protocol.commands import Family
from .base import DEFAULT_HEARTBEAT_CMD, TcpConnection
from .hosts import POOL_BY_FAMILY, HostEntry, RankingStore

__all__ = [
    "ProbeResult",
    "probe",
    "speedtest",
    "rank_hosts",
    "speedtest_and_save",
]

_VALID_FAMILIES = (Family.STANDARD, Family.EXTENDED, Family.MAC, Family.GOODS, Family.F10)


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


def _require_family(family: str) -> None:
    if family not in _VALID_FAMILIES:
        raise ConfigError(f"未知测速 family: {family!r}")


def _require_limits(*, timeout: float, samples: int, max_workers: int) -> None:
    if timeout <= 0:
        raise ValueError(f"timeout 必须 > 0，实际 {timeout}")
    if samples <= 0:
        raise ValueError(f"samples 必须 > 0，实际 {samples}")
    if max_workers <= 0:
        raise ValueError(f"max_workers 必须 > 0，实际 {max_workers}")


def _apply_probe_observations(
    hosts: Sequence[HostEntry],
    results: Sequence[ProbeResult],
    *,
    family: str,
) -> None:
    """Refresh only latency observations on caller-owned host objects.

    ConnectionPool slots retain references to these HostEntry objects, so a
    successful background probe changes subsequent score-based ordering in the
    current process. Failed/incomplete probes leave previous latency evidence
    untouched, and request-health/static identity fields remain authoritative.
    """

    _require_family(family)
    by_key: dict[str, ProbeResult] = {}
    for result in results:
        if result.family != family:
            raise ConfigError(
                "测速结果 family 不匹配: "
                f"requested={family!r}, result={result.key} family={result.family!r}"
            )
        if result.ok:
            by_key[result.key] = result

    for host in hosts:
        if host.family != family:
            raise ConfigError(
                f"测速回灌 host family 不匹配: requested={family!r}, "
                f"entry={host.key} family={host.family!r}"
            )
        result = by_key.get(host.key)
        if result is None:
            continue
        if result.connect_ms is not None:
            host.connect_ms = result.connect_ms
        if result.rtt_ms is not None:
            host.rtt_ms = result.rtt_ms


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
    """探测单个主站。取 ``samples`` 次里最好的一次。"""

    _require_family(family)
    if timeout <= 0:
        raise ValueError(f"timeout 必须 > 0，实际 {timeout}")
    if samples <= 0:
        raise ValueError(f"samples 必须 > 0，实际 {samples}")

    best: ProbeResult | None = None
    for _ in range(samples):
        conn = TcpConnection(host, port, timeout=timeout, connect_timeout=timeout)
        started = time.perf_counter()
        try:
            conn.connect()
            connect_ms = (time.perf_counter() - started) * 1000.0
            rtt = conn.ping(cmd, body)
            result = ProbeResult(
                host=host,
                port=port,
                family=family,
                ok=True,
                connect_ms=connect_ms,
                rtt_ms=rtt,
            )
        except Exception as exc:
            result = ProbeResult(
                host=host,
                port=port,
                family=family,
                ok=False,
                error=f"{type(exc).__name__}: {exc}",
            )
        finally:
            conn.close()
        if best is None or result.score < best.score:
            best = result
    return best or ProbeResult(host=host, port=port, family=family, ok=False, error="no sample")


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
    """并发测速 exactly-one family，失败项排在最后。"""

    _require_family(family)
    _require_limits(timeout=timeout, samples=samples, max_workers=max_workers)
    entries = list(hosts) if hosts is not None else list(POOL_BY_FAMILY[family])
    mismatched = [entry for entry in entries if entry.family != family]
    if mismatched:
        sample = mismatched[0]
        raise ConfigError(
            f"测速 host family 不匹配: requested={family!r}, "
            f"entry={sample.key} family={sample.family!r}"
        )
    if not entries:
        return []

    results: list[ProbeResult] = []
    with _fut.ThreadPoolExecutor(max_workers=min(max_workers, len(entries))) as pool:
        futures = {
            pool.submit(
                probe,
                entry.host,
                entry.port,
                family=family,
                timeout=timeout,
                cmd=cmd,
                samples=samples,
            ): entry
            for entry in entries
        }
        for index, future in enumerate(_fut.as_completed(futures), 1):
            try:
                result = future.result()
            except Exception as exc:  # pragma: no cover - executor defense
                entry = futures[future]
                result = ProbeResult(
                    host=entry.host,
                    port=entry.port,
                    family=family,
                    ok=False,
                    error=f"{type(exc).__name__}: {exc}",
                )
            results.append(result)
            if progress:
                mark = "OK " if result.ok else "ERR"
                rtt = f"{result.rtt_ms:.1f}ms" if result.rtt_ms is not None else "-"
                print(
                    f"[{index}/{len(entries)}] {mark} "
                    f"{result.host}:{result.port} {rtt} {result.error}"
                )

    results.sort(key=lambda result: result.score)
    return results


def rank_hosts(results: Iterable[ProbeResult]) -> list[HostEntry]:
    """把同 family 测速结果转成可直接热更新连接池的条目。"""

    entries = [result.to_entry() for result in results]
    families = {entry.family for entry in entries}
    if len(families) > 1:
        raise ConfigError(f"rank_hosts 不接受跨 family 结果: {sorted(families)!r}")
    entries.sort(key=lambda entry: entry.score)
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
    """测速、刷新当前调用方排序证据，并在安全时持久化 STANDARD 排名。

    调用方显式传入 ``hosts`` 时，成功探测的 connect/rtt 会原位回灌到这些
    HostEntry；其它字段不变。V1 RankingStore 只持久化 STANDARD，非 STANDARD
    仍可让当前 ConnectionPool 立即受益而不会污染跨进程排名缓存。
    """

    results = speedtest(
        hosts,
        family=family,
        timeout=timeout,
        samples=samples,
        max_workers=max_workers,
        progress=progress,
    )
    if hosts is not None:
        _apply_probe_observations(hosts, results, family=family)
    if family == Family.STANDARD:
        selected = results if keep_failures else [result for result in results if result.ok]
        RankingStore(ranking_file).update(rank_hosts(selected))
    return results
