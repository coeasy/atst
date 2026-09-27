# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""主站候选、selector 校验与 STANDARD V1 排名持久化。

物理 endpoint 可以被多个协议族复用，但 ``HostEntry.family`` 始终表示当前
canonical family。V1 ranking 以 ``host:port`` 为 key，因此只允许持久化
STANDARD。持久化排名只能携带 probe/ranking 观测，不能扩大显式 selector、
覆盖静态 identity，或注入当前进程的 live-health / half-open 探测状态。
"""

from __future__ import annotations

import contextlib
import ipaddress
import json
import math
import os
import tempfile
import time
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any

from ..errors import ConfigError
from ..protocol.commands import Family

__all__ = [
    "HostEntry",
    "DEFAULT_HOST_POOL",
    "POOL_BY_FAMILY",
    "RankingStore",
    "resolve_hosts",
    "parse_server",
]

_RANKING_VERSION = 1
_VALID_FAMILIES = (Family.STANDARD, Family.EXTENDED, Family.MAC, Family.GOODS, Family.F10)
_VALID_CIRCUITS = {"healthy", "degraded", "open", "half_open"}
_MAX_RESOLVED_HOSTS = 64
_EPHEMERAL_FIELDS = {"live_rtt_ms", "live_ok_at", "circuit_probe_inflight"}


@dataclass
class HostEntry:
    """一个主站候选及其 probe/live 观测。"""

    host: str
    port: int = 7709
    family: str = Family.STANDARD
    name: str = ""
    verified: bool = False
    # Background/probe ranking observations; safe to persist in V1.
    connect_ms: float | None = None
    rtt_ms: float | None = None
    # Current-process request/heartbeat health; never persisted.
    live_rtt_ms: float | None = None
    live_ok_at: float | None = None
    failures: int = 0
    biz_failures: int = 0
    last_ok: float | None = None
    last_error: str = ""
    circuit: str = "healthy"
    consec_weighted: float = 0.0
    circuit_opened_at: float = 0.0
    # Runtime-only half-open single-probe token.
    circuit_probe_inflight: bool = False

    @property
    def key(self) -> str:
        rendered_host = f"[{self.host}]" if ":" in self.host else self.host
        return f"{rendered_host}:{self.port}"

    @property
    def addr(self) -> tuple[str, int]:
        return (self.host, self.port)

    @property
    def score(self) -> float:
        """真实请求 RTT 优先于后台 probe RTT；越小越优。"""

        measured = self.live_rtt_ms if self.live_rtt_ms is not None else self.rtt_ms
        base = 1_000_000.0 if measured is None else float(measured)
        penalty = min(1e9, base * (4 ** min(self.failures, 8)))
        if self.biz_failures:
            penalty *= 1.0 + 0.25 * self.biz_failures
        return penalty

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        for field in _EPHEMERAL_FIELDS:
            data.pop(field, None)
        return data

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> HostEntry:
        # Persisted V1 data must not restore process-local health/probe locks.
        known = set(cls.__dataclass_fields__) - _EPHEMERAL_FIELDS
        entry = cls(**{key: value for key, value in data.items() if key in known})
        return _validated_entry(entry, source="ranking entry")

    def __repr__(self) -> str:  # pragma: no cover - 调试便利
        return (
            f"HostEntry({self.key}, family={self.family}, "
            f"rtt={self.rtt_ms}, live_rtt={self.live_rtt_ms}, failures={self.failures})"
        )


def _require_family_value(family: str, *, source: str) -> str:
    if family not in _VALID_FAMILIES:
        raise ConfigError(
            f"{source} family 非法: {family!r}",
            context={"family": family, "source": source},
        )
    return family


def _parse_port(value: Any, *, source: str) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise ConfigError(
            f"{source} port 必须是整数 1..65535，收到 {value!r}",
            context={"source": source},
        )
    try:
        port = int(value)
    except (TypeError, ValueError) as exc:
        raise ConfigError(
            f"{source} port 无效: {value!r}",
            context={"source": source},
            cause=exc,
        ) from exc
    if not 1 <= port <= 65535:
        raise ConfigError(
            f"{source} port 超出范围 1..65535: {port}",
            context={"source": source, "port": port},
        )
    return port


def _clean_host(value: Any, *, source: str) -> str:
    if not isinstance(value, str):
        raise ConfigError(
            f"{source} host 必须是字符串，收到 {type(value).__name__}",
            context={"source": source},
        )
    host = value.strip()
    if host.startswith("[") and host.endswith("]"):
        host = host[1:-1].strip()
    if not host:
        raise ConfigError(f"{source} host 为空", context={"source": source})
    if any(ch.isspace() for ch in host):
        raise ConfigError(
            f"{source} host 含空白字符: {host!r}",
            context={"source": source},
        )
    if any(ch in host for ch in "/?#@[]\x00"):
        raise ConfigError(
            f"{source} host 含非法字符: {host!r}",
            context={"source": source},
        )
    if ":" in host:
        address = host.split("%", 1)[0]
        try:
            parsed = ipaddress.ip_address(address)
        except ValueError as exc:
            raise ConfigError(
                f"{source} IPv6 host 无效: {host!r}",
                context={"source": source},
                cause=exc,
            ) from exc
        if parsed.version != 6:
            raise ConfigError(f"{source} host 无效: {host!r}", context={"source": source})
    return host


def _require_non_negative_number(
    value: Any,
    *,
    field: str,
    source: str,
    allow_none: bool = True,
) -> float | None:
    if value is None:
        if allow_none:
            return None
        raise ConfigError(
            f"{source} {field} 不能为 None",
            context={"source": source, "field": field},
        )
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigError(
            f"{source} {field} 必须是非负有限数值，收到 {value!r}",
            context={"source": source, "field": field},
        )
    normalized = float(value)
    if not math.isfinite(normalized) or normalized < 0:
        raise ConfigError(
            f"{source} {field} 必须是非负有限数值，收到 {value!r}",
            context={"source": source, "field": field},
        )
    return normalized


def _require_non_negative_int(value: Any, *, field: str, source: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ConfigError(
            f"{source} {field} 必须是非负整数，收到 {value!r}",
            context={"source": source, "field": field},
        )
    return value


def _validated_entry(entry: HostEntry, *, source: str) -> HostEntry:
    family = _require_family_value(entry.family, source=source)
    host = _clean_host(entry.host, source=source)
    port = _parse_port(entry.port, source=source)
    if not isinstance(entry.name, str):
        raise ConfigError(f"{source} name 必须是字符串", context={"source": source})
    if not isinstance(entry.verified, bool):
        raise ConfigError(f"{source} verified 必须是 bool", context={"source": source})
    if not isinstance(entry.last_error, str):
        raise ConfigError(f"{source} last_error 必须是字符串", context={"source": source})
    if not isinstance(entry.circuit_probe_inflight, bool):
        raise ConfigError(
            f"{source} circuit_probe_inflight 必须是 bool",
            context={"source": source},
        )
    if entry.circuit not in _VALID_CIRCUITS:
        raise ConfigError(
            f"{source} circuit 非法: {entry.circuit!r}",
            context={"source": source, "circuit": entry.circuit},
        )

    connect_ms = _require_non_negative_number(entry.connect_ms, field="connect_ms", source=source)
    rtt_ms = _require_non_negative_number(entry.rtt_ms, field="rtt_ms", source=source)
    live_rtt_ms = _require_non_negative_number(
        entry.live_rtt_ms, field="live_rtt_ms", source=source
    )
    live_ok_at = _require_non_negative_number(entry.live_ok_at, field="live_ok_at", source=source)
    last_ok = _require_non_negative_number(entry.last_ok, field="last_ok", source=source)
    consec_weighted = _require_non_negative_number(
        entry.consec_weighted,
        field="consec_weighted",
        source=source,
        allow_none=False,
    )
    circuit_opened_at = _require_non_negative_number(
        entry.circuit_opened_at,
        field="circuit_opened_at",
        source=source,
        allow_none=False,
    )
    failures = _require_non_negative_int(entry.failures, field="failures", source=source)
    biz_failures = _require_non_negative_int(
        entry.biz_failures, field="biz_failures", source=source
    )

    return replace(
        entry,
        host=host,
        port=port,
        family=family,
        connect_ms=connect_ms,
        rtt_ms=rtt_ms,
        live_rtt_ms=live_rtt_ms,
        live_ok_at=live_ok_at,
        failures=failures,
        biz_failures=biz_failures,
        last_ok=last_ok,
        consec_weighted=consec_weighted or 0.0,
        circuit_opened_at=circuit_opened_at or 0.0,
    )


def _h(host: str, port: int, family: str, name: str = "", verified: bool = False) -> HostEntry:
    return HostEntry(host=host, port=port, family=family, name=name, verified=verified)


def _as_family(entries: Iterable[HostEntry], family: str) -> tuple[HostEntry, ...]:
    """复用物理 endpoint，但重绑定 family 并清除跨族 verified provenance。"""

    return tuple(
        replace(
            entry,
            family=family,
            verified=False,
            live_rtt_ms=None,
            live_ok_at=None,
            circuit_probe_inflight=False,
        )
        for entry in entries
    )


def _apply_ranked_observation(base: HostEntry, ranked: HostEntry) -> HostEntry:
    """仅叠加持久化的 probe 观测（connect_ms / rtt_ms），保留 selector-owned
    identity 与运行态健康字段。

    merged from _ranking_hardening：进程间不共享 circuit/failures 等 runtime 状态。
    """

    if ranked.family != base.family or ranked.key != base.key:
        raise ConfigError(
            "ranking observation identity mismatch: "
            f"base={base.key}/{base.family!r}, ranked={ranked.key}/{ranked.family!r}"
        )
    return replace(base, connect_ms=ranked.connect_ms, rtt_ms=ranked.rtt_ms)


DEFAULT_HOST_POOL: tuple[HostEntry, ...] = (
    _h("180.153.18.170", 7709, Family.STANDARD, "上海双线-3", verified=True),
    _h("60.191.117.167", 7709, Family.STANDARD, "杭州-1", verified=True),
    _h("115.238.90.165", 7709, Family.STANDARD, "杭州-2", verified=True),
    _h("115.238.56.198", 7709, Family.STANDARD, "杭州-3", verified=True),
    _h("218.75.126.9", 7709, Family.STANDARD, "长沙-1", verified=True),
    _h("218.6.170.47", 7709, Family.STANDARD, "四川-1", verified=True),
    _h("123.125.108.14", 7709, Family.STANDARD, "北京-2", verified=True),
    _h("119.147.212.81", 7709, Family.STANDARD, "深圳双线-1"),
    _h("218.108.98.244", 7709, Family.STANDARD, "深圳双线-2"),
    _h("218.108.47.69", 7709, Family.STANDARD, "深圳双线-3"),
    _h("114.80.63.12", 7709, Family.STANDARD, "上海双线-1"),
    _h("114.80.63.35", 7709, Family.STANDARD, "上海双线-2"),
    _h("180.153.18.171", 7709, Family.STANDARD, "上海双线-4"),
    _h("180.153.39.51", 7709, Family.STANDARD, "上海双线-5"),
    _h("202.108.253.130", 7709, Family.STANDARD, "北京-1"),
    _h("124.160.88.183", 7709, Family.STANDARD, "成都-1"),
    _h("221.231.141.60", 7709, Family.STANDARD, "南京-1"),
    _h("101.227.73.20", 7709, Family.STANDARD, "扬州-1"),
    _h("14.215.128.18", 7709, Family.STANDARD, "广州-1"),
    _h("59.173.18.140", 7709, Family.STANDARD, "西安-1"),
    _h("119.147.212.81", 7727, Family.EXTENDED, "扩展-深圳"),
    _h("218.108.98.244", 7727, Family.EXTENDED, "扩展-深圳2"),
    _h("114.80.63.12", 7727, Family.EXTENDED, "扩展-上海"),
    _h("124.71.187.122", 7727, Family.EXTENDED, "扩展-通用"),
    _h("120.53.120.235", 7709, Family.MAC, "MAC-1"),
    _h("119.147.212.81", 7709, Family.MAC, "MAC-2"),
)

POOL_BY_FAMILY: dict[str, tuple[HostEntry, ...]] = {
    family: tuple(entry for entry in DEFAULT_HOST_POOL if entry.family == family)
    for family in _VALID_FAMILIES
}
POOL_BY_FAMILY[Family.GOODS] = _as_family(POOL_BY_FAMILY[Family.EXTENDED], Family.GOODS)
POOL_BY_FAMILY[Family.F10] = _as_family(POOL_BY_FAMILY[Family.STANDARD], Family.F10)


def _parse_string_server(item: str, *, family: str) -> HostEntry:
    raw = item.strip()
    if not raw:
        raise ConfigError("主站条目为空", context={"source": "server string"})

    port: Any = 7709
    host: Any = raw
    if raw.startswith("["):
        closing = raw.find("]")
        if closing < 0:
            raise ConfigError(f"IPv6 主站缺少 ]: {item!r}")
        host = raw[1:closing]
        suffix = raw[closing + 1 :]
        if suffix:
            if not suffix.startswith(":"):
                raise ConfigError(f"IPv6 主站格式无效: {item!r}")
            port = suffix[1:]
            if not port:
                raise ConfigError(f"IPv6 主站 port 为空: {item!r}")
    elif raw.count(":") == 1:
        host, port = raw.rsplit(":", 1)
    elif ":" in raw:
        # Bare IPv6 uses default port. Explicit IPv6 port requires brackets.
        host = raw

    return _validated_entry(
        HostEntry(
            host=_clean_host(host, source="server string"),
            port=_parse_port(port, source="server string"),
            family=family,
        ),
        source="server string",
    )


def parse_server(item: Any, *, family: str = Family.STANDARD) -> HostEntry:
    """把配置条目解析为 canonical HostEntry；错误统一为 ConfigError。"""

    _require_family_value(family, source="parse_server")
    if isinstance(item, str):
        return _parse_string_server(item, family=family)
    if isinstance(item, HostEntry):
        return _validated_entry(item, source="HostEntry")
    if isinstance(item, Mapping):
        if "host" not in item:
            raise ConfigError("mapping 主站条目缺少 host", context={"source": "mapping"})
        return _validated_entry(
            HostEntry(
                host=_clean_host(item["host"], source="mapping"),
                port=_parse_port(item.get("port", 7709), source="mapping"),
                family=item.get("family", family),
                name=item.get("name", ""),
                verified=item.get("verified", False),
            ),
            source="mapping",
        )
    if isinstance(item, (list, tuple)) and len(item) == 2:
        host, port = item
        return _validated_entry(
            HostEntry(
                host=_clean_host(host, source="sequence"),
                port=_parse_port(port, source="sequence"),
                family=family,
            ),
            source="sequence",
        )
    raise ConfigError(f"无法解析主站条目: {item!r}")


def _env_hosts(family: str = Family.STANDARD) -> list[HostEntry] | None:
    raw_value = os.environ.get("ATST_HOSTS")
    if raw_value is None or not raw_value.strip():
        return None
    tokens = raw_value.replace(",", " ").split()
    if not tokens:
        raise ConfigError("ATST_HOSTS 不能为空")

    entries: list[HostEntry] = []
    for token in tokens:
        try:
            entries.append(parse_server(token, family=family))
        except ConfigError as exc:
            raise ConfigError(
                f"ATST_HOSTS 条目无效 {token!r}: {exc.message}",
                context={"source": "ATST_HOSTS", "token": token},
                cause=exc,
            ) from exc
    return entries


def _require_family(entries: Iterable[HostEntry], family: str, *, source: str) -> None:
    mismatched = [entry for entry in entries if entry.family != family]
    if mismatched:
        sample = mismatched[0]
        raise ConfigError(
            f"{source} 主站 family 不匹配: requested={family!r}, "
            f"entry={sample.key} family={sample.family!r}"
        )


def _require_max_hosts(max_hosts: Any) -> int:
    if isinstance(max_hosts, bool) or not isinstance(max_hosts, int):
        raise ConfigError(f"max_hosts 必须是整数 1..{_MAX_RESOLVED_HOSTS}，收到 {max_hosts!r}")
    if not 1 <= max_hosts <= _MAX_RESOLVED_HOSTS:
        raise ConfigError(f"max_hosts 必须在 1..{_MAX_RESOLVED_HOSTS}，收到 {max_hosts}")
    return max_hosts


# --- Probe-only ranking persistence (merged from _ranking_hardening) ----- #
# 持久化只允许 5 个字段：身份 (host/port/family) + probe 观测 (connect_ms/rtt_ms)。
# runtime health（failures / circuit / biz_failures / last_ok / last_error /
# consec_weighted / circuit_opened_at / verified / name / live_*）永不落盘——
# 进程间不共享熔断状态；新进程应该从头开始健康观测。

_PERSISTED_FIELDS = frozenset({"host", "port", "family", "connect_ms", "rtt_ms"})
_LEGACY_RUNTIME_FIELDS = frozenset(
    {
        "name",
        "verified",
        "failures",
        "biz_failures",
        "last_ok",
        "last_error",
        "circuit",
        "consec_weighted",
        "circuit_opened_at",
    }
)
_REQUIRED_IDENTITY_FIELDS = frozenset({"host", "port", "family"})
_LEGACY_ALLOWED_FIELDS = _PERSISTED_FIELDS | _LEGACY_RUNTIME_FIELDS | _EPHEMERAL_FIELDS


def _probe_only_entry(entry: HostEntry) -> HostEntry:
    """剥掉所有 process-local runtime 字段，只保留持久化 probe 观测。"""

    validated = _validated_entry(entry, source="RankingStore")
    return HostEntry(
        host=validated.host,
        port=validated.port,
        family=validated.family,
        connect_ms=validated.connect_ms,
        rtt_ms=validated.rtt_ms,
    )


def _probe_payload(entry: HostEntry) -> dict[str, Any]:
    probe = _probe_only_entry(entry)
    return {
        "host": probe.host,
        "port": probe.port,
        "family": probe.family,
        "connect_ms": probe.connect_ms,
        "rtt_ms": probe.rtt_ms,
    }


def _entry_from_legacy_payload(item: Mapping[str, Any]) -> HostEntry | None:
    """白名单反序列化：只接受 probe 可持久化字段。"""

    keys = set(item)
    if _EPHEMERAL_FIELDS.intersection(keys):
        return None
    if not _REQUIRED_IDENTITY_FIELDS.issubset(keys):
        return None
    if keys - _LEGACY_ALLOWED_FIELDS:
        return None
    try:
        entry = HostEntry(
            host=item["host"],
            port=item["port"],
            family=item["family"],
            connect_ms=item.get("connect_ms"),
            rtt_ms=item.get("rtt_ms"),
        )
        entry = _validated_entry(entry, source="ranking entry")
    except (KeyError, TypeError, ValueError, ConfigError):
        return None
    if entry.family != Family.STANDARD or entry.rtt_ms is None:
        return None
    return entry


# --- Host selector isolation (merged from _host_selector_hardening) ----- #


def isolated_host_entry_snapshot(resolved: Sequence[HostEntry], *, limit: int) -> list[HostEntry]:
    """Ensure every caller gets fresh, unique HostEntry snapshots."""

    seen: set[str] = set()
    for entry in resolved:
        if entry.key in seen:
            raise ConfigError(
                f"resolve_hosts 存在重复 canonical endpoint: {entry.key}",
                context={"host": entry.key},
            )
        seen.add(entry.key)
    return [replace(entry) for entry in resolved[:limit]]


class RankingStore:
    """STANDARD-only ``~/.atst/server_ranking.json`` V1 storage。"""

    VERSION = _RANKING_VERSION

    def __init__(self, path: str | os.PathLike[str] = "~/.atst/server_ranking.json") -> None:
        self.path = Path(os.path.expanduser(str(path)))

    def load(self) -> dict[str, HostEntry]:
        # --- merged from _ranking_hardening: probe-only provenance --- #
        if not self.path.exists():
            return {}
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return {}
        if not isinstance(raw, Mapping):
            return {}
        version = raw.get("version")
        if isinstance(version, bool) or not isinstance(version, int) or version != self.VERSION:
            return {}
        raw_entries = raw.get("entries")
        if not isinstance(raw_entries, Mapping):
            return {}

        out: dict[str, HostEntry] = {}
        seen_embedded: set[str] = set()
        for persisted_key, item in raw_entries.items():
            if not isinstance(persisted_key, str) or not isinstance(item, Mapping):
                continue
            entry = _entry_from_legacy_payload(item)
            if entry is None:
                continue
            if persisted_key != entry.key or entry.key in seen_embedded:
                return {}
            seen_embedded.add(entry.key)
            out[entry.key] = entry
        return out

    @staticmethod
    def _require_standard(entries: Iterable[HostEntry]) -> list[HostEntry]:
        items = [_validated_entry(entry, source="RankingStore") for entry in entries]
        invalid = [entry for entry in items if entry.family != Family.STANDARD]
        if invalid:
            sample = invalid[0]
            raise ConfigError(
                f"RankingStore V1 仅支持 STANDARD: entry={sample.key} family={sample.family!r}"
            )
        seen: set[str] = set()
        for entry in items:
            if entry.key in seen:
                raise ConfigError(f"RankingStore 存在重复 endpoint: {entry.key}")
            seen.add(entry.key)
        return items

    def save(self, entries: Iterable[HostEntry]) -> None:
        # --- merged from _ranking_hardening: probe-only serialization --- #
        items = [entry for entry in self._require_standard(entries) if entry.rtt_ms is not None]
        data = {
            "version": self.VERSION,
            "updated": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "entries": {entry.key: _probe_payload(entry) for entry in items},
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=self.path.parent,
                prefix=f"{self.path.name}.",
                suffix=".tmp",
                delete=False,
            ) as stream:
                json.dump(data, stream, ensure_ascii=False, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
                temp_path = Path(stream.name)
            temp_path.replace(self.path)
        finally:
            if temp_path is not None:
                with contextlib.suppress(FileNotFoundError):
                    temp_path.unlink()

    def merge(self, entries: Iterable[HostEntry]) -> dict[str, HostEntry]:
        """合并 probe-only ranking 观测；process-local runtime health 永不落盘。

        merged from _ranking_hardening：
        - 失败 probe（rtt_ms=None 但有 failures/last_error）→ 清除 stale positive latency
        - 成功 probe → 只更新 rtt_ms / connect_ms，不覆盖 circuit/failures 等 runtime 字段
        """

        items = self._require_standard(entries)
        known = self.load()
        for entry in items:
            if entry.rtt_ms is None:
                # 失败 probe 清除该主机的 stale positive latency 条目
                if entry.failures > 0 or bool(entry.last_error):
                    known.pop(entry.key, None)
                continue

            previous = known.get(entry.key)
            if previous is None:
                known[entry.key] = _probe_only_entry(entry)
                continue
            previous.rtt_ms = entry.rtt_ms
            if entry.connect_ms is not None:
                previous.connect_ms = entry.connect_ms
        return known

    def update(self, entries: Iterable[HostEntry]) -> None:
        merged = self.merge(entries)
        self.save(entry for entry in merged.values() if entry.family == Family.STANDARD)

    def clear(self) -> None:
        with contextlib.suppress(FileNotFoundError):
            self.path.unlink()

    def top(self, n: int = 5, family: str | None = None) -> list[HostEntry]:
        if isinstance(n, bool) or not isinstance(n, int) or n < 1:
            raise ConfigError(f"RankingStore.top n 必须是正整数，收到 {n!r}")
        if family is not None:
            _require_family_value(family, source="RankingStore.top")
        items = [
            entry for entry in self.load().values() if family is None or entry.family == family
        ]
        items.sort(key=lambda entry: entry.score)
        return items[:n]


def resolve_hosts(
    servers: Sequence[Any] | None = None,
    *,
    family: str = Family.STANDARD,
    ranking: RankingStore | None = None,
    ranking_file: str | None = None,
    use_ranking: bool = True,
    max_hosts: int = 8,
) -> list[HostEntry]:
    """按 selector provenance 解析一个 family 的主站集合。"""

    _require_family_value(family, source="resolve_hosts")
    limit = _require_max_hosts(max_hosts)
    if ranking is not None and ranking_file is not None:
        raise ConfigError("ranking 与 ranking_file 不能同时指定")

    # HostsConfig historically uses [] + ranking_file to mean "unset". Keep that
    # shape, while a direct empty selector fails closed.
    if servers is not None and len(servers) == 0:
        if ranking_file is None:
            raise ConfigError("explicit servers 不能为空；使用 None 表示采用默认主站池")
        servers = None

    allow_ranked_extras = False
    if servers is not None:
        entries = [parse_server(server, family=family) for server in servers]
        _require_family(entries, family, source="explicit servers")
    else:
        env_entries = _env_hosts(family)
        if env_entries is not None:
            entries = env_entries
            _require_family(entries, family, source="ATST_HOSTS")
        else:
            entries = list(POOL_BY_FAMILY[family])
            allow_ranked_extras = True

    if use_ranking:
        store = ranking
        if store is None and ranking_file is not None:
            store = RankingStore(ranking_file)
        elif store is None and allow_ranked_extras and family == Family.STANDARD:
            store = RankingStore()

        if store is not None:
            known = {key: entry for key, entry in store.load().items() if entry.family == family}
            entries = [
                _apply_ranked_observation(entry, known[entry.key]) if entry.key in known else entry
                for entry in entries
            ]
            if allow_ranked_extras:
                in_pool = {entry.key for entry in entries}
                entries.extend(entry for key, entry in known.items() if key not in in_pool)

    entries.sort(key=lambda entry: entry.score)
    # --- merged from _host_selector_hardening: 去重 + replace 快照隔离 --- #
    return isolated_host_entry_snapshot(entries, limit=limit)


# --------------------------------------------------------------------------- #
# 连接池主站代际更新共享原语（同步池 / 异步池同一套发布规则）
# --------------------------------------------------------------------------- #
def validate_host_updates(hosts: Sequence[HostEntry], *, family: str) -> list[HostEntry]:
    """校验并返回 generation 匹配用的 canonical 快照（不改调用方对象）。"""

    items: list[HostEntry] = []
    seen: set[str] = set()
    for entry in hosts:
        if not isinstance(entry, HostEntry):
            raise ConfigError(f"update_hosts 只接受 HostEntry，收到 {type(entry).__name__}")
        validated = parse_server(entry, family=family)
        if validated.family != family:
            raise ConfigError(
                "update_hosts family 不匹配: "
                f"requested={family!r}, entry={validated.key} family={validated.family!r}"
            )
        if validated.key in seen:
            raise ConfigError(f"update_hosts 存在重复 endpoint: {validated.key}")
        seen.add(validated.key)
        items.append(validated)
    return items


def new_endpoint_entry(entry: HostEntry, *, family: str) -> HostEntry:
    """新主站进入池时的自有副本：只带身份与探测证据，不带运行期健康。"""

    validated = parse_server(entry, family=family)
    return HostEntry(
        host=validated.host,
        port=validated.port,
        family=validated.family,
        name=validated.name,
        verified=validated.verified,
        connect_ms=validated.connect_ms,
        rtt_ms=validated.rtt_ms,
    )


def next_generation_host(old: HostEntry, observed: HostEntry) -> HostEntry:
    """沿用上一代的身份与运行期健康，仅采纳探测层证据。"""

    fresh = replace(old)
    if observed.rtt_ms is not None:
        fresh.rtt_ms = observed.rtt_ms
        fresh.connect_ms = observed.connect_ms
    elif observed.failures > 0 or bool(observed.last_error):
        fresh.rtt_ms = None
        fresh.connect_ms = None
    elif observed.connect_ms is not None:
        fresh.connect_ms = observed.connect_ms

    if old.circuit_probe_inflight:
        fresh.circuit = "open"
        fresh.circuit_opened_at = time.time()
    fresh.circuit_probe_inflight = False
    return fresh
