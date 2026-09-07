# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""主站候选池与速度排名持久化（§12）。

设计要点
--------
1. **候选池只是候选**。内置列表来自公开文档整理，未逐条实测，
   因此 ``HostEntry.verified`` 默认 ``False``，真实可用性由
   :func:`~tstdx.transport.speedtest.speedtest` 现场测定。
2. **用户配置优先**。``hosts.servers`` 一旦非空即完全接管候选池。
3. **排名可持久化**。测速结果写入 ``hosts.ranking_file``（默认
   ``~/.tstdx/server_ranking.json``），下次启动自动按分数排序复用。
4. **失败惩罚**。连续失败的条目分数衰减，落到池尾，但**不删除**
   （网络是波动的，删了就永久失去一个候选）。
"""

from __future__ import annotations

import contextlib
import json
import os
import time
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass
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


# --------------------------------------------------------------------------- #
# 主站条目
# --------------------------------------------------------------------------- #
@dataclass
class HostEntry:
    """一个主站候选。"""

    host: str
    port: int = 7709
    family: str = Family.STANDARD
    name: str = ""
    #: 是否经过自采集 golden 验证
    verified: bool = False
    #: 测速指标（未测速时为 None）
    connect_ms: float | None = None
    rtt_ms: float | None = None
    #: 连续失败次数（连接失败 + 业务失败共用一个计数，驱动指数惩罚）
    failures: int = 0
    #: 业务帧失败次数（R2：连接成功但请求/响应交换失败的次数，
    #: 单独记数并给轻微 score 惩罚——连接失败用指数惩罚更激进）
    biz_failures: int = 0
    last_ok: float | None = None
    last_error: str = ""
    #: B4 熔断状态机：healthy → degraded（连续失败加权 ≥3）→
    #: open（≥8，请求直接跳过）→ half_open（冷却 30s 后单次探测）
    circuit: str = "healthy"
    #: 连续失败加权值：连接失败 1.0 / 业务失败 0.5（成功即清零）
    consec_weighted: float = 0.0
    #: open 进入时间戳（驱动冷却）
    circuit_opened_at: float = 0.0

    @property
    def key(self) -> str:
        return f"{self.host}:{self.port}"

    @property
    def addr(self) -> tuple[str, int]:
        return (self.host, self.port)

    @property
    def score(self) -> float:
        """越小越好。未测速条目给中性分 1e6（排在已测速之后、失败条目之前）。"""
        base = 1000000.0 if self.rtt_ms is None else float(self.rtt_ms)
        # 连续失败 → 指数惩罚，但封顶 1e9，保证仍排在"未测速"之后
        penalty = min(1e9, base * (4 ** min(self.failures, 8)))
        # R2 业务失败率降权：连接正常但业务帧失败的主机轻微降权
        if self.biz_failures:
            penalty *= 1.0 + 0.25 * self.biz_failures
        return penalty

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> HostEntry:
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in data.items() if k in known})

    def __repr__(self) -> str:  # pragma: no cover - 调试便利
        return (
            f"HostEntry({self.key}, family={self.family}, "
            f"rtt={self.rtt_ms}, failures={self.failures})"
        )


def _h(host: str, port: int, family: str, name: str = "", verified: bool = False) -> HostEntry:
    return HostEntry(host=host, port=port, family=family, name=name, verified=verified)


# --------------------------------------------------------------------------- #
# 内置候选池
# --------------------------------------------------------------------------- #
# .. note::
#    下列条目排序已按 2026-08-31 实测连通性调整：可达的 7 台主站前置并
#    标记 ``verified=True``，不可达的保留在后（网络是波动的，不删除）。
#    真实可用性仍建议用 ``tstdx hosts scan`` 自测并生成排名文件。
DEFAULT_HOST_POOL: tuple[HostEntry, ...] = (
    # --- 7709 标准行情（实测可达，2026-08-31 验证）---
    _h("180.153.18.170", 7709, Family.STANDARD, "上海双线-3", verified=True),
    _h("60.191.117.167", 7709, Family.STANDARD, "杭州-1", verified=True),
    _h("115.238.90.165", 7709, Family.STANDARD, "杭州-2", verified=True),
    _h("115.238.56.198", 7709, Family.STANDARD, "杭州-3", verified=True),
    _h("218.75.126.9", 7709, Family.STANDARD, "长沙-1", verified=True),
    _h("218.6.170.47", 7709, Family.STANDARD, "四川-1", verified=True),
    _h("123.125.108.14", 7709, Family.STANDARD, "北京-2", verified=True),
    # --- 以下为公开文档整理，实测暂不可达（保留为候选）---
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
    # --- 7727 扩展行情（期货 / 期权 / 外汇 / 港股通等）---
    # ⚠️ 2026-09-06 实测：4 台候选全部连接超时，且 13 台 7709 存活主机均未
    # 双开 7727 端口——扩展行情服务疑似整体迁移/下线。候选保留供后续
    # 更新主站池时复测；GOODS（商品语义）与 EXTENDED 共池，同样受影响。
    _h("119.147.212.81", 7727, Family.EXTENDED, "扩展-深圳"),
    _h("218.108.98.244", 7727, Family.EXTENDED, "扩展-深圳2"),
    _h("114.80.63.12", 7727, Family.EXTENDED, "扩展-上海"),
    _h("124.71.187.122", 7727, Family.EXTENDED, "扩展-通用"),
    # --- 7709 MAC 专用（mac_quotation，命令号 0x120F+）---
    # ⚠️ 2026-09-06 实测：两台均连接超时，MAC 命令发往标准 7709 主站无响应。
    _h("120.53.120.235", 7709, Family.MAC, "MAC-1"),
    _h("119.147.212.81", 7709, Family.MAC, "MAC-2"),
)

POOL_BY_FAMILY: dict[str, tuple[HostEntry, ...]] = {
    fam: tuple(e for e in DEFAULT_HOST_POOL if e.family == fam)
    for fam in (Family.STANDARD, Family.EXTENDED, Family.MAC, Family.GOODS, Family.F10)
}
# GOODS 与 EXTENDED 共用 7727 端口
POOL_BY_FAMILY[Family.GOODS] = POOL_BY_FAMILY[Family.EXTENDED]
# F10 资料网关（2026-09-06 实测）：常见 7615 端口主机不可达，但 F10 文件下载
# 命令（0x1004 download）在标准 7709 主站真实可用（catalog 0x0001 除外——
# 该命令已无响应）。F10 池指向 STANDARD 存活池。
POOL_BY_FAMILY[Family.F10] = POOL_BY_FAMILY[Family.STANDARD]


# --------------------------------------------------------------------------- #
# 解析用户配置
# --------------------------------------------------------------------------- #
def parse_server(item: Any, *, family: str = Family.STANDARD) -> HostEntry:
    """把配置里的 ``[host, port]`` 解析为 :class:`HostEntry`。

    支持 ``["1.2.3.4", 7709]`` / ``("1.2.3.4", 7709)`` / ``"1.2.3.4:7709"``。
    """
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
    """U3：环境变量 ``TSTDX_HOSTS`` 快速注入主站列表。

    格式：逗号或空格分隔的 ``host:port``（缺省端口 7709），例如
    ``TSTDX_HOSTS="180.153.18.170:7709,60.191.117.167"``。
    未设置或解析为空时返回 ``None``（走内置候选池）。
    """
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


def resolve_hosts(
    servers: Sequence[Any] | None = None,
    *,
    family: str = Family.STANDARD,
    ranking: RankingStore | None = None,
    ranking_file: str | None = None,
    use_ranking: bool = True,
    max_hosts: int = 8,
) -> list[HostEntry]:
    """按优先级解析主站列表：用户配置 > 环境变量 TSTDX_HOSTS > 排名文件 > 内置候选池。

    ``max_hosts`` 默认 8：内置候选池来自公开文档、未逐条实测，网络波动下
    只有少数主站可达；候选太少会导致「池里明明还有可用主站却提前放弃」。
    故障转移会按序尝试池内全部候选，因此多带几个候选能显著提高一次调用成功率。

    Returns
    -------
    按 :attr:`HostEntry.score` 升序排列的条目列表（长度 ≤ ``max_hosts``）。
    """
    if servers:
        entries = [parse_server(s, family=family) for s in servers]
    else:
        env_entries = _env_hosts(family)
        if env_entries is not None:
            entries = env_entries
        else:
            entries = list(POOL_BY_FAMILY.get(family, DEFAULT_HOST_POOL))

    if use_ranking:
        # ranking 参数优先；无注入实例且配置了文件路径时才落盘加载
        store = ranking or (RankingStore(ranking_file) if ranking_file else None)
        if store is not None:
            known = store.load()
            merged: list[HostEntry] = []
            for e in entries:
                prev = known.get(e.key)
                merged.append(prev if prev is not None else e)
            # 排名文件里出现过、但内置池已移除的条目也保留下来（用户自采集过的）
            in_pool = {e.key for e in entries}
            merged.extend(v for k, v in known.items() if k not in in_pool)
            entries = merged

    entries.sort(key=lambda e: e.score)
    return entries[: max(1, max_hosts)]


# --------------------------------------------------------------------------- #
# 排名持久化
# --------------------------------------------------------------------------- #
class RankingStore:
    """``~/.tstdx/server_ranking.json`` 的读写封装。

    文件损坏时**静默降级**为空排名（不阻塞启动），因为排名只是优化项。
    """

    VERSION = _RANKING_VERSION

    def __init__(self, path: str | os.PathLike[str] = "~/.tstdx/server_ranking.json") -> None:
        self.path = Path(os.path.expanduser(str(path)))

    # -- IO ---------------------------------------------------------------- #
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
        for _key, item in (raw.get("entries") or {}).items():
            if not isinstance(item, Mapping):
                continue
            try:
                entry = HostEntry.from_dict(item)
            except Exception:
                continue
            out[entry.key] = entry
        return out

    def save(self, entries: Iterable[HostEntry]) -> None:
        data = {
            "version": self.VERSION,
            "updated": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "entries": {e.key: e.to_dict() for e in entries},
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    def merge(self, entries: Iterable[HostEntry]) -> dict[str, HostEntry]:
        """把一批新结果并入既有排名（同 key 覆盖指标、累加失败计数）。"""
        known = self.load()
        for e in entries:
            prev = known.get(e.key)
            if prev is None:
                known[e.key] = e
                continue
            if e.rtt_ms is not None:
                prev.rtt_ms = e.rtt_ms
            if e.connect_ms is not None:
                prev.connect_ms = e.connect_ms
            if e.last_ok:
                prev.last_ok = e.last_ok
                prev.failures = 0
                prev.last_error = ""
            else:
                prev.failures = prev.failures + max(1, e.failures)
                prev.last_error = e.last_error or prev.last_error
        return known

    def update(self, entries: Iterable[HostEntry]) -> None:
        self.save(self.merge(entries).values())

    def clear(self) -> None:
        with contextlib.suppress(FileNotFoundError):
            self.path.unlink()

    def top(self, n: int = 5, family: str | None = None) -> list[HostEntry]:
        items = [e for e in self.load().values() if family is None or e.family == family]
        items.sort(key=lambda e: e.score)
        return items[:n]
