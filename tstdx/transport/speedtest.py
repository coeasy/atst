# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""主站测速与 STANDARD 运行时排名（§12.5）。

测速只提供网络观测证据；它不得通过 Python 类型强制转换或异常测速结果覆盖
selector identity / request-health。所有 family 可用于当前进程排序，V1 ranking
只持久化 STANDARD。
"""

from __future__ import annotations

import concurrent.futures as _fut
import math
import time
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from ..errors import ConfigError
from ..protocol.commands import Family
from .base import DEFAULT_HEARTBEAT_CMD, TcpConnection
from .hosts import POOL_BY_FAMILY, HostEntry, RankingStore, parse_server

__all__ = [
    "ProbeResult",
    "probe",
    "speedtest",
    "rank_hosts",
    "speedtest_and_save",
]

_VALID_FAMILIES = (Family.STANDARD, Family.EXTENDED, Family.MAC, Family.GOODS, Family.F10)
_MAX_SPEEDTEST_WORKERS = 64


def _require_family(family: str) -> None:
    if family not in _VALID_FAMILIES:
        raise ConfigError(f"未知测速 family: {family!r}")


def _require_bool(name: str, value: Any) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{name} 必须是 bool，实际 {value!r}")
    return value


def _require_timeout(timeout: Any) -> float:
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
        raise ValueError(f"timeout 必须是正有限数值，实际 {timeout!r}")
    normalized = float(timeout)
    if not math.isfinite(normalized) or normalized <= 0:
        raise ValueError(f"timeout 必须是正有限数值，实际 {timeout!r}")
    return normalized


def _require_positive_int(name: str, value: Any, *, maximum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} 必须是正整数，实际 {value!r}")
    if value <= 0:
        raise ValueError(f"{name} 必须是正整数，实际 {value}")
    if maximum is not None and value > maximum:
        raise ValueError(f"{name} 不能超过 {maximum}，实际 {value}")
    return value


def _require_probe_payload(cmd: Any, body: Any) -> tuple[int, bytes]:
    if isinstance(cmd, bool) or not isinstance(cmd, int) or not 0 <= cmd <= 0xFFFF:
        raise ValueError(f"cmd 必须是 uint16 整数，实际 {cmd!r}")
    if not isinstance(body, bytes):
        raise ValueError(f"body 必须是 bytes，实际 {type(body).__name__}")
    return cmd, body


def _require_nonnegative_observation(name: str, value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigError(f"测速结果 {name} 必须是非负有限数值或 None，收到 {value!r}")
    normalized = float(value)
    if not math.isfinite(normalized) or normalized < 0:
        raise ConfigError(f"测速结果 {name} 必须是非负有限数值或 None，收到 {value!r}")
    return normalized


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
        rendered_host = f"[{self.host}]" if ":" in self.host else self.host
        return f"{rendered_host}:{self.port}"

    @property
    def score(self) -> float:
        if not self.ok or self.rtt_ms is None:
            return float("inf")
        return self.rtt_ms

    def to_entry(self) -> HostEntry:
        _validate_probe_result(self)
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
        _validate_probe_result(self)
        return {
            "host": self.host,
            "port": self.port,
            "family": self.family,
            "ok": self.ok,
            "connect_ms": None if self.connect_ms is None else round(self.connect_ms, 2),
            "rtt_ms": None if self.rtt_ms is None else round(self.rtt_ms, 2),
            "error": self.error,
        }


def _validate_probe_result(result: ProbeResult, *, family: str | None = None) -> ProbeResult:
    if not isinstance(result, ProbeResult):
        raise ConfigError(f"测速结果必须是 ProbeResult，收到 {type(result).__name__}")
    _require_family(result.family)
    if family is not None and result.family != family:
        raise ConfigError(
            "测速结果 family 不匹配: "
            f"requested={family!r}, result={result.key} family={result.family!r}"
        )
    identity = parse_server((result.host, result.port), family=result.family)
    if not isinstance(result.ok, bool):
        raise ConfigError(f"测速结果 ok 必须是 bool，收到 {result.ok!r}")
    if not isinstance(result.error, str):
        raise ConfigError(f"测速结果 error 必须是字符串，收到 {type(result.error).__name__}")
    result.host = identity.host
    result.port = identity.port
    result.connect_ms = _require_nonnegative_observation("connect_ms", result.connect_ms)
    result.rtt_ms = _require_nonnegative_observation("rtt_ms", result.rtt_ms)
    if result.ok and result.rtt_ms is None:
        raise ConfigError(f"测速结果 ok=True 但缺少 rtt_ms: {result.key}")
    return result


def _require_limits(*, timeout: Any, samples: Any, max_workers: Any) -> tuple[float, int, int]:
    return (
        _require_timeout(timeout),
        _require_positive_int("samples", samples),
        _require_positive_int("max_workers", max_workers, maximum=_MAX_SPEEDTEST_WORKERS),
    )


def _require_unique_keys(items: Iterable[Any], *, source: str) -> None:
    """Reject duplicate canonical endpoints before network or persistence work."""

    seen: set[str] = set()
    for index, item in enumerate(items):
        key = item.key
        if key in seen:
            raise ConfigError(
                f"{source} 存在重复 canonical endpoint: {key}",
                context={"source": source, "host": key, "index": index},
            )
        seen.add(key)


def _apply_probe_observations(
    hosts: Sequence[HostEntry],
    results: Sequence[ProbeResult],
    *,
    family: str,
) -> None:
    """Refresh only validated latency observations on caller-owned host objects."""

    _require_family(family)
    by_key: dict[str, ProbeResult] = {}
    for result in results:
        validated = _validate_probe_result(result, family=family)
        if validated.ok:
            if validated.key in by_key:
                raise ConfigError(
                    f"测速回灌存在重复 canonical endpoint: {validated.key}",
                    context={"family": family, "host": validated.key},
                )
            by_key[validated.key] = validated

    for host in hosts:
        validated_host = parse_server(host, family=family)
        if validated_host.family != family:
            raise ConfigError(
                f"测速回灌 host family 不匹配: requested={family!r}, "
                f"entry={validated_host.key} family={validated_host.family!r}"
            )
        observed = by_key.get(validated_host.key)
        if observed is None:
            continue
        if observed.connect_ms is not None:
            host.connect_ms = observed.connect_ms
        if observed.rtt_ms is not None:
            host.rtt_ms = observed.rtt_ms


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
    """探测单个主站。取 ``samples`` 次里 RTT 最好的一次。"""

    _require_family(family)
    probe_timeout = _require_timeout(timeout)
    sample_count = _require_positive_int("samples", samples)
    command, payload = _require_probe_payload(cmd, body)
    entry = parse_server((host, port), family=family)

    best: ProbeResult | None = None
    for _ in range(sample_count):
        conn = TcpConnection(
            entry.host,
            entry.port,
            timeout=probe_timeout,
            connect_timeout=probe_timeout,
        )
        started = time.perf_counter()
        try:
            conn.connect()
            connect_ms = (time.perf_counter() - started) * 1000.0
            rtt = conn.ping(command, payload)
            result = ProbeResult(
                host=entry.host,
                port=entry.port,
                family=family,
                ok=True,
                connect_ms=connect_ms,
                rtt_ms=rtt,
            )
        except Exception as exc:
            result = ProbeResult(
                host=entry.host,
                port=entry.port,
                family=family,
                ok=False,
                error=f"{type(exc).__name__}: {exc}",
            )
        finally:
            conn.close()
        _validate_probe_result(result, family=family)
        if best is None or result.score < best.score:
            best = result
    assert best is not None
    return best


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
    probe_timeout, sample_count, worker_count = _require_limits(
        timeout=timeout,
        samples=samples,
        max_workers=max_workers,
    )
    command, payload = _require_probe_payload(cmd, b"")
    show_progress = _require_bool("progress", progress)
    entries = list(hosts) if hosts is not None else list(POOL_BY_FAMILY[family])
    validated_entries: list[HostEntry] = []
    for entry in entries:
        if not isinstance(entry, HostEntry):
            raise ConfigError(f"测速 hosts 必须包含 HostEntry，收到 {type(entry).__name__}")
        validated = parse_server(entry, family=family)
        if validated.family != family:
            raise ConfigError(
                f"测速 host family 不匹配: requested={family!r}, "
                f"entry={validated.key} family={validated.family!r}"
            )
        validated_entries.append(validated)
    _require_unique_keys(validated_entries, source="speedtest hosts")
    if not validated_entries:
        return []

    results: list[ProbeResult] = []
    with _fut.ThreadPoolExecutor(max_workers=min(worker_count, len(validated_entries))) as pool:
        futures = {
            pool.submit(
                probe,
                entry.host,
                entry.port,
                family=family,
                timeout=probe_timeout,
                cmd=command,
                samples=sample_count,
            ): entry
            for entry in validated_entries
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
            _validate_probe_result(result, family=family)
            results.append(result)
            if show_progress:
                mark = "OK " if result.ok else "ERR"
                rtt = f"{result.rtt_ms:.1f}ms" if result.rtt_ms is not None else "-"
                print(
                    f"[{index}/{len(validated_entries)}] {mark} "
                    f"{result.host}:{result.port} {rtt} {result.error}"
                )

    results.sort(key=lambda result: result.score)
    return results


def rank_hosts(results: Iterable[ProbeResult]) -> list[HostEntry]:
    """把同 family、已验证且 endpoint 唯一的测速结果转成可热更新条目。"""

    items = list(results)
    validated = [_validate_probe_result(result) for result in items]
    families = {result.family for result in validated}
    if len(families) > 1:
        raise ConfigError(f"rank_hosts 不接受跨 family 结果: {sorted(families)!r}")
    _require_unique_keys(validated, source="rank_hosts results")
    entries = [result.to_entry() for result in validated]
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
    """测速、刷新当前调用方排序证据，并在安全时持久化 STANDARD 排名。"""

    keep_failed = _require_bool("keep_failures", keep_failures)
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
        selected = results if keep_failed else [result for result in results if result.ok]
        RankingStore(ranking_file).update(rank_hosts(selected))
    return results
