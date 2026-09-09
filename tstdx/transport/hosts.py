# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""主站候选池与 STANDARD 速度排名持久化（§12）。

候选 endpoint 可以被多个协议族复用，但 ``HostEntry.family`` 必须始终代表
当前请求的 canonical family。历史 V1 ranking 以 ``host:port`` 为 key，因此
只允许持久化 STANDARD；它只能提供运行观测/排序，不能扩大显式
``servers=`` / ``TSTDX_HOSTS`` 候选集合，也不能覆盖 selector 静态身份。
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


@dataclass
class HostEntry:
    """一个主站候选。"""

    host: str
    port: int = 7709
    family: str = Family.STANDARD
    name: str = ""
    verified: bool = False
    connect_ms: float | None = None
    rtt_ms: float | None = None
    failures: int = 0
    biz_failures: int = 0
    last_ok: float | None = None
    last_error: str = ""
    circuit: str = "healthy"
    consec_weighted: float = 0.0
    circuit_opened_at: float = 0.0

    @property
    def key(self) -> str:
        rendered_host = f"[{self.host}]" if ":" in self.host else self.host
        return f"{rendered_host}:{self.port}"

    @property
    def addr(self) -> tuple[str, int]:
        return (self.host, self.port)

    @property
    def score(self) -> float:
        """越小越好；未测速条目排在已测速条目之后、持续失败条目之前。"""

        base = 1000000.0 if self.rtt_ms is None else float(self.rtt_ms)
        penalty = min(1e9, base * (4 ** min(self.failures, 8)))
        if self.biz_failures:
            penalty *= 1.0 + 0.25 * self.biz_failures
        return penalty

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> HostEntry:
        known = set(cls.__dataclass_fields__)
        entry = cls(**{key: value for key, value in data.items() if key in known})
        return _validated_entry(entry, source="ranking entry")

    def __repr__(self) -> str:  # pragma: no cover - 调试便利
        return (
            f"HostEntry({self.key}, family={self.family}, "
            f"rtt={self.rtt_ms}, failures={self.failures})"
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
    if entry.circuit not in _VALID_CIRCUITS:
        raise ConfigError(
            f"{source} circuit 非法: {entry.circuit!r}",
            context={"source": source, "circuit": entry.circuit},
        )

    connect_ms = _require_non_negative_number(entry.connect_ms, field="connect_ms", source=source)
    rtt_ms = _require_non_negative_number(entry.rtt_ms, field="rtt_ms", source=source)
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
    biz_failures = _require_non_negative_int(entry.biz_failures, field="biz_failures", source=source)

    return replace(
        entry,
        host=host,
        port=port,
        family=family,
        connect_ms=connect_ms,
        rtt_ms=rtt_ms,
        failures=failures,
        biz_failures=biz_failures,
        last_ok=last_ok,
        consec_weighted=consec_weighted or 0.0,
        circuit_opened_at=circuit_opened_at or 0.0,
    )


def _h(host: str, port: int, family: str, name: str = "", verified: bool = False) -> HostEntry:
    return HostEntry(host=host, port=port, family=family, name=name, verified=verified)


def _as_family(entries: Iterable[HostEntry], family: str) -> tuple[HostEntry, ...]:
    """Reuse endpoints while resetting family-specific verification provenance."""

    return tuple(replace(entry, family=family, verified=False) for entry in entries)


def _apply_ranked_observation(base: HostEntry, ranked: HostEntry) -> HostEntry:
    """Overlay runtime observations while preserving selector-owned identity fields."""

    if ranked.family != base.family or ranked.key != base.key:
        raise ConfigError(
            "ranking observation identity mismatch: "
            f"base={base.key}/{base.family!r}, ranked={ranked.key}/{ranked.family!r}"
        )
    return replace(
        base,
        connect_ms=ranked.connect_ms,
        rtt_ms=ranked.rtt_ms,
        failures=ranked.failures,
        biz_failures=ranked.biz_failures,
        last_ok=ranked.last_ok,
        last_error=ranked.last_error,
        circuit=ranked.circuit,
        consec_weighted=ranked.consec_weighted,
        circuit_opened_at=ranked.circuit_opened_at,
    )


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
# GOODS 与 EXTENDED 共享 7727 endpoints，但 identity/verified 证据不能继承。
POOL_BY_FAMILY[Family.GOODS] = _as_family(POOL_BY_FAMILY[Family.EXTENDED], Family.GOODS)
# F10 文件下载复用标准 7709 endpoints，但 STANDARD 验证证据不能冒充 F10。
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
        # Bare IPv6 uses the default port. To specify an IPv6 port, require
        # bracket notation so address bytes are never confused with a TCP port.
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
    """把配置条目解析为 canonical :class:`HostEntry`，所有错误统一为 ConfigError。"""

    _require_family_value(family, source="parse_server")
    if isinstance(item, str):
        return _parse_string_server(item, family=family)
    if isinstance(item, HostEntry):
        return _validated_entry(item, source="HostEntry")
    if isinstance(item, Mapping):
        if "host" not in item:
            raise ConfigError("mapping 主站条目缺少 host", context={"source": "mapping"})
        mapped_family = item.get("family", family)
        return _validated_entry(
            HostEntry(
                host=_clean_host(item["host"], source="mapping"),
                port=_parse_port(item.get("port", 7709), source="mapping"),
                family=mapped_family,
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
    raw_value = os.environ.get("TSTDX_HOSTS")
    if raw_value is None or not raw_value.strip():
        return None

    tokens = raw_value.replace(",", " ").split()
    if not tokens:
        raise ConfigError("TSTDX_HOSTS 不能为空")

    entries: list[HostEntry] = []
    for token in tokens:
        try:
            entries.append(parse_server(token, family=family))
        except ConfigError as exc:
            raise ConfigError(
                f"TSTDX_HOSTS 条目无效 {token!r}: {exc.message}",
                context={"source": "TSTDX_HOSTS", "token": token},
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


def resolve_hosts(
    servers: Sequence[Any] | None = None,
    *,
    family: str = Family.STANDARD,
    ranking: RankingStore | None = None,
    ranking_file: str | None = None,
    use_ranking: bool = True,
    max_hosts: int = 8,
) -> list[HostEntry]:
    """Resolve one family without allowing ranking provenance to widen selectors.

    Priority is explicit ``servers`` > ``TSTDX_HOSTS`` > same-family ranking >
    built-in pool. Explicit/environment selectors own the candidate set; default
    persistent ranking is consumed only for the implicit STANDARD built-in path.
    ``HostsConfig.servers=[]`` is represented internally together with its
    ``ranking_file`` and remains equivalent to no explicit selector. A direct
    empty selector without that config context fails closed.
    """

    _require_family_value(family, source="resolve_hosts")
    limit = _require_max_hosts(max_hosts)
    if ranking is not None and ranking_file is not None:
        raise ConfigError("ranking 与 ranking_file 不能同时指定")

    # HostsConfig uses [] to mean "no manual override" and always carries a
    # ranking_file. Preserve that public config contract while making a direct
    # resolve_hosts([]) call fail closed instead of silently restoring defaults.
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
            _require_family(entries, family, source="TSTDX_HOSTS")
        else:
            entries = list(POOL_BY_FAMILY[family])
            allow_ranked_extras = True

    if use_ranking:
        store = ranking
        if store is None and ranking_file is not None:
            store = RankingStore(ranking_file)
        elif store is None and allow_ranked_extras and family == Family.STANDARD:
            # This closes the persistence loop: `hosts scan` / bestip STANDARD
            # results become the next default cold-start ordering automatically.
            store = RankingStore()

        if store is not None:
            known = {
                key: entry
                for key, entry in store.load().items()
                if entry.family == family
            }
            merged = [
                _apply_ranked_observation(entry, known[entry.key])
                if entry.key in known
                else entry
                for entry in entries
            ]
            if allow_ranked_extras:
                in_pool = {entry.key for entry in entries}
                merged.extend(entry for key, entry in known.items() if key not in in_pool)
            entries = merged

    entries.sort(key=lambda entry: entry.score)
    return entries[:limit]


class RankingStore:
    """STANDARD-only ``~/.tstdx/server_ranking.json`` V1 storage.

    V1 uses ``host:port`` keys and therefore cannot safely represent the same
    endpoint in multiple families. Legacy non-STANDARD rows may still be read and
    ignored by family-safe resolvers, but all new writes fail closed unless the
    incoming family is STANDARD.
    """

    VERSION = _RANKING_VERSION

    def __init__(self, path: str | os.PathLike[str] = "~/.tstdx/server_ranking.json") -> None:
        self.path = Path(os.path.expanduser(str(path)))

    def load(self) -> dict[str, HostEntry]:
        if not self.path.exists():
            return {}
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return {}
        if not isinstance(raw, Mapping) or raw.get("version") != self.VERSION:
            return {}
        raw_entries = raw.get("entries")
        if not isinstance(raw_entries, Mapping):
            return {}

        out: dict[str, HostEntry] = {}
        for item in raw_entries.values():
            if not isinstance(item, Mapping):
                continue
            try:
                entry = HostEntry.from_dict(item)
            except ConfigError:
                continue
            out[entry.key] = entry
        return out

    @staticmethod
    def _require_standard(entries: Iterable[HostEntry]) -> list[HostEntry]:
        items = [_validated_entry(entry, source="RankingStore") for entry in entries]
        invalid = [entry for entry in items if entry.family != Family.STANDARD]
        if invalid:
            sample = invalid[0]
            raise ConfigError(
                "RankingStore V1 仅支持 STANDARD: "
                f"entry={sample.key} family={sample.family!r}"
            )
        seen: set[str] = set()
        for entry in items:
            if entry.key in seen:
                raise ConfigError(f"RankingStore 存在重复 endpoint: {entry.key}")
            seen.add(entry.key)
        return items

    def save(self, entries: Iterable[HostEntry]) -> None:
        items = self._require_standard(entries)
        data = {
            "version": self.VERSION,
            "updated": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "entries": {entry.key: entry.to_dict() for entry in items},
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
        """Merge STANDARD runtime observations while preserving stored identity."""

        items = self._require_standard(entries)
        known = self.load()
        # A legacy non-STANDARD row at the same host:port cannot be safely merged
        # into STANDARD. Replace it with the incoming canonical STANDARD identity.
        for entry in items:
            previous = known.get(entry.key)
            if previous is None or previous.family != Family.STANDARD:
                known[entry.key] = entry
                continue
            if entry.rtt_ms is not None:
                previous.rtt_ms = entry.rtt_ms
            if entry.connect_ms is not None:
                previous.connect_ms = entry.connect_ms
            previous.biz_failures = entry.biz_failures
            previous.circuit = entry.circuit
            previous.consec_weighted = entry.consec_weighted
            previous.circuit_opened_at = entry.circuit_opened_at
            if entry.last_ok:
                previous.last_ok = entry.last_ok
                previous.failures = 0
                previous.last_error = ""
            else:
                previous.failures += max(1, entry.failures)
                previous.last_error = entry.last_error or previous.last_error
        return known

    def update(self, entries: Iterable[HostEntry]) -> None:
        merged = self.merge(entries)
        # Drop legacy non-STANDARD rows during the next successful STANDARD write;
        # otherwise `save()` would correctly reject them and persistence could not heal.
        self.save(entry for entry in merged.values() if entry.family == Family.STANDARD)

    def clear(self) -> None:
        with contextlib.suppress(FileNotFoundError):
            self.path.unlink()

    def top(self, n: int = 5, family: str | None = None) -> list[HostEntry]:
        if isinstance(n, bool) or not isinstance(n, int) or n < 1:
            raise ConfigError(f"RankingStore.top n 必须是正整数，收到 {n!r}")
        if family is not None:
            _require_family_value(family, source="RankingStore.top")
        items = [entry for entry in self.load().values() if family is None or entry.family == family]
        items.sort(key=lambda entry: entry.score)
        return items[:n]
