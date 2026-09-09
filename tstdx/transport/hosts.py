# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""主站候选池与速度排名持久化（§12）。

核心契约：候选 endpoint 可以被多个协议族复用，但 ``HostEntry.family``
必须始终代表当前请求的 canonical family。排名文件只是同 family 内的排序
优化，不能扩大显式 ``servers=`` / ``TSTDX_HOSTS`` 的候选集合，也不能把
其它 family 的历史条目注入当前请求。
"""

from __future__ import annotations

import contextlib
import json
import os
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
        return f"{self.host}:{self.port}"

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
        return cls(**{key: value for key, value in data.items() if key in known})

    def __repr__(self) -> str:  # pragma: no cover - 调试便利
        return (
            f"HostEntry({self.key}, family={self.family}, "
            f"rtt={self.rtt_ms}, failures={self.failures})"
        )


def _h(host: str, port: int, family: str, name: str = "", verified: bool = False) -> HostEntry:
    return HostEntry(host=host, port=port, family=family, name=name, verified=verified)


def _as_family(entries: Iterable[HostEntry], family: str) -> tuple[HostEntry, ...]:
    """Reuse endpoint evidence while rebinding every entry to the target family."""

    return tuple(replace(entry, family=family) for entry in entries)


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
# GOODS 与 EXTENDED 共享 7727 endpoints，但 identity 必须保持 GOODS。
POOL_BY_FAMILY[Family.GOODS] = _as_family(POOL_BY_FAMILY[Family.EXTENDED], Family.GOODS)
# F10 文件下载复用标准 7709 endpoints，但不能伪装成 STANDARD family。
POOL_BY_FAMILY[Family.F10] = _as_family(POOL_BY_FAMILY[Family.STANDARD], Family.F10)


def parse_server(item: Any, *, family: str = Family.STANDARD) -> HostEntry:
    """把配置条目解析为 :class:`HostEntry`."""

    if isinstance(item, str):
        if ":" in item:
            host, _, port_s = item.rpartition(":")
            return HostEntry(host=host, port=int(port_s), family=family)
        return HostEntry(host=item, port=7709, family=family)
    if isinstance(item, HostEntry):
        return item
    if isinstance(item, Mapping):
        return HostEntry(
            host=str(item["host"]),
            port=int(item.get("port", 7709)),
            family=item.get("family", family),
            name=item.get("name", ""),
            verified=bool(item.get("verified", False)),
        )
    if isinstance(item, (list, tuple)) and len(item) == 2:
        host, port = item
        return HostEntry(host=str(host), port=int(port), family=family)
    raise ConfigError(f"无法解析主站条目: {item!r}")


def _env_hosts(family: str = Family.STANDARD) -> list[HostEntry] | None:
    raw = os.environ.get("TSTDX_HOSTS", "").strip()
    if not raw:
        return None
    entries: list[HostEntry] = []
    for part in raw.replace(",", " ").split():
        part = part.strip()
        if not part:
            continue
        try:
            entries.append(parse_server(part, family=family))
        except (ConfigError, ValueError):
            continue
    return entries or None


def _require_family(entries: Iterable[HostEntry], family: str, *, source: str) -> None:
    mismatched = [entry for entry in entries if entry.family != family]
    if mismatched:
        sample = mismatched[0]
        raise ConfigError(
            f"{source} 主站 family 不匹配: requested={family!r}, "
            f"entry={sample.key} family={sample.family!r}"
        )


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
    built-in pool. Explicit/environment selectors own the candidate set; ranking
    may only attach observations to matching endpoints. Only the built-in path may
    retain same-family ranked endpoints that were removed from the current pool.
    """

    if family not in _VALID_FAMILIES:
        raise ConfigError(f"未知主站 family: {family!r}")

    allow_ranked_extras = False
    if servers:
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
        store = ranking or (RankingStore(ranking_file) if ranking_file else None)
        if store is not None:
            known = {
                key: entry
                for key, entry in store.load().items()
                if entry.family == family
            }
            merged = [known.get(entry.key, entry) for entry in entries]
            if allow_ranked_extras:
                in_pool = {entry.key for entry in entries}
                merged.extend(entry for key, entry in known.items() if key not in in_pool)
            entries = merged

    entries.sort(key=lambda entry: entry.score)
    return entries[: max(1, max_hosts)]


class RankingStore:
    """``~/.tstdx/server_ranking.json`` 的读写封装。

    VERSION 1 keeps the historical ``host:port`` storage key. Family isolation is
    therefore enforced before persistence and at every resolve boundary.
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
        if not isinstance(raw, Mapping) or int(raw.get("version", 0)) != self.VERSION:
            return {}
        out: dict[str, HostEntry] = {}
        for item in (raw.get("entries") or {}).values():
            if not isinstance(item, Mapping):
                continue
            try:
                entry = HostEntry.from_dict(item)
            except Exception:
                continue
            out[entry.key] = entry
        return out

    def save(self, entries: Iterable[HostEntry]) -> None:
        by_key: dict[str, HostEntry] = {}
        for entry in entries:
            previous = by_key.get(entry.key)
            if previous is not None and previous.family != entry.family:
                raise ConfigError(
                    f"ranking key family collision: {entry.key} "
                    f"{previous.family!r} != {entry.family!r}"
                )
            by_key[entry.key] = entry

        data = {
            "version": self.VERSION,
            "updated": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "entries": {key: entry.to_dict() for key, entry in by_key.items()},
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    def merge(self, entries: Iterable[HostEntry]) -> dict[str, HostEntry]:
        """Merge observations without mixing metrics across family identities."""

        known = self.load()
        for entry in entries:
            previous = known.get(entry.key)
            if previous is None or previous.family != entry.family:
                known[entry.key] = entry
                continue
            if entry.rtt_ms is not None:
                previous.rtt_ms = entry.rtt_ms
            if entry.connect_ms is not None:
                previous.connect_ms = entry.connect_ms
            if entry.last_ok:
                previous.last_ok = entry.last_ok
                previous.failures = 0
                previous.last_error = ""
            else:
                previous.failures += max(1, entry.failures)
                previous.last_error = entry.last_error or previous.last_error
        return known

    def update(self, entries: Iterable[HostEntry]) -> None:
        self.save(self.merge(entries).values())

    def clear(self) -> None:
        with contextlib.suppress(FileNotFoundError):
            self.path.unlink()

    def top(self, n: int = 5, family: str | None = None) -> list[HostEntry]:
        items = [entry for entry in self.load().values() if family is None or entry.family == family]
        items.sort(key=lambda entry: entry.score)
        return items[:n]
