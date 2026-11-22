# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""主站发现：从本机通达信安装或开源清单取候选，实测通过后才写排名。

为什么需要这一层：:func:`atst.transport.hosts.resolve_hosts` 只会重排**已知**候选
（内置池 + 排名文件里的 extras），它没有"发现"能力；内置池又编译进 wheel，只随版本
变化。于是候选池的成员实际上被锁死在发布时刻。本模块把"发现"拆成三步，每步都能单独
验证：

1. **取候选**（纯解析、不联网）：本机 ``connect.cfg``（GBK）、pytdx/mootdx 的 Python
   元组表、gotdx 的 Go 结构体表、通用 JSON。
2. **实测**：复用 :func:`atst.transport.speedtest.speedtest`，只认"握手 + 心跳回包"。
   TCP 可连但协议不应答的主机（7727 扩展族全族就是例子）不算通过——端口探活会把死
   主机写进池子。
3. **采纳**：只把实测通过项写进 :class:`~atst.transport.hosts.RankingStore`，因此仍然
   受"排名只记 STANDARD"的既有边界约束；默认池模式下这些条目会作为 extras 自动参与
   排序。

边界：
- 不改 ``HostEntry`` 的字段语义——来源只出现在发现报告里，不伪造探测字段。
- 发现不等于静默换源：选谁仍由 ``score`` 与调用方的显式策略决定。
- 不打包任何第三方清单：``gotdx`` / ``mootdx`` / ``pytdx`` 的地址是**运行时**默认值，
  可用 ``--url`` 覆盖，也可完全离线只用本机 ``connect.cfg``。
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import time
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

from ..errors import ConfigError
from .hosts import Family, HostEntry, RankingStore, parse_server
from .speedtest import ProbeResult, rank_hosts, speedtest

__all__ = [
    "OSS_SOURCES",
    "Candidate",
    "DiscoveryResult",
    "adopt_results",
    "build_report",
    "candidate_key",
    "discover",
    "fetch_text",
    "find_tdx_installs",
    "load_source",
    "merge_candidates",
    "parse_connect_cfg_text",
    "parse_go_host_table",
    "parse_json_hosts",
    "parse_python_host_table",
    "read_connect_cfg",
    "to_entries",
]

#: 三个开源仓库的清单地址。**运行时**默认值，不随 wheel 分发任何清单内容。
OSS_SOURCES: dict[str, str] = {
    "gotdx": "https://raw.githubusercontent.com/yzsmzc/gotdx/main/hosts.go",
    "mootdx": "https://raw.githubusercontent.com/mootdx/mootdx/master/mootdx/consts.py",
    "pytdx": "https://raw.githubusercontent.com/rainx/pytdx/master/pytdx/config/hosts.py",
}

#: 单份候选表的大小上限：清单是文本表，超过这个体积只可能是拿错 URL。
_MAX_TABLE_BYTES = 2 * 1024 * 1024

#: 目录扫描的硬上界：防止在超大目录树里把"发现"变成一次全盘遍历。
_MAX_SCAN_DIRS = 4000

#: 只对这些名字的目录做二层下探（``P:\stock\new_tdx64`` 这类布局）。
_TDX_DIR_HINT = re.compile(r"tdx|tongdaxin|stock|通达信", re.IGNORECASE)

# 注意两点：① 必须用 ``[ \t]`` 而不是 ``\s``——``\s`` 会把换行吃掉，于是
# ``IPAddress05=``（空值）会把下一行 ``Port05=7709`` 当成地址读进来；
# ② 官方文件是 CRLF，``$`` 只能匹配到 ``\n`` 之前，所以行尾要显式吃掉 ``\r``。
_IP_LINE = re.compile(r"^[ \t]*IPAddress(\d+)[ \t]*=[ \t]*([^\r\n]*?)[ \t]*\r?$", re.MULTILINE)
_PORT_LINE = re.compile(r"^[ \t]*Port(\d+)[ \t]*=[ \t]*(\d+)[ \t]*\r?$", re.MULTILINE)
_NAME_LINE = re.compile(r"^[ \t]*HostName(\d+)[ \t]*=[ \t]*([^\r\n]*?)[ \t]*\r?$", re.MULTILINE)

_PY_TUPLE = re.compile(r"\(\s*['\"]([^'\"]*)['\"]\s*,\s*['\"]([^'\"]+)['\"]\s*,\s*(\d{1,5})\s*\)")
_GO_TUPLE = re.compile(r'Name:\s*"([^"]*)"\s*,\s*IP:\s*"([^"]+)"\s*,\s*Port:\s*(\d{1,5})')
_GO_TUPLE_NO_NAME = re.compile(r'IP:\s*"([^"]+)"\s*,\s*Port:\s*(\d{1,5})')


@dataclass(frozen=True, slots=True)
class Candidate:
    """一个待验证的候选端点，带来源标签（只用于报告，不落排名）。

    ``family`` 为空表示"来源没有声明族"（如通用 JSON），由调用方按 ``--family`` 解释；
    非空时只在请求同一族时参与实测——``connect.cfg`` 同时带着 7709 行情与 7727 扩展，
    混着测会把 18 台扩展主机全记成"行情不可达"。
    """

    host: str
    port: int = 7709
    name: str = ""
    source: str = ""
    family: str = ""


#: 端口 → 协议族。只收本协议栈真的会说的两个族：7709（行情）、7727（扩展）。
#: 7615 是扩展市场的另一入口（同一批 IP 在 gotdx 表里以 7727 出现），7719 是同族
#: 备用端口；80/443 的 "Z80" 端点走的是另一套传输，不在本栈射程内，解析时直接丢弃。
_PORT_FAMILY: dict[int, str] = {
    7709: Family.STANDARD,
    7719: Family.STANDARD,
    7727: Family.EXTENDED,
    7615: Family.EXTENDED,
}


def _family_for_port(port: int) -> str | None:
    return _PORT_FAMILY.get(port)


@dataclass(frozen=True, slots=True)
class DiscoveryResult:
    """一次发现的完整结果：候选、实测、采纳数与来源。"""

    family: str
    candidates: tuple[Candidate, ...]
    results: tuple[ProbeResult, ...]
    adopted: int
    origins: tuple[str, ...]

    @property
    def reachable(self) -> int:
        return sum(1 for result in self.results if result.ok)


# --------------------------------------------------------------------------- #
# 解析：connect.cfg
# --------------------------------------------------------------------------- #
def _coerce_port(value: Any) -> int | None:
    """端口必须在 1..65535；``connect.cfg`` 里真实存在 ``Port=0`` 的坏条目。"""

    try:
        port = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return port if 1 <= port <= 65535 else None


def _clean_candidate_host(value: str) -> str:
    host = value.strip().strip('"').strip("'")
    if not host or any(ch.isspace() for ch in host):
        return ""
    return host


def parse_connect_cfg_text(text: str, *, source: str = "tdx-install") -> list[Candidate]:
    """解析通达信 ``connect.cfg`` 的 ``[HQHOST]`` 段（容错：坏条目逐条跳过）。

    官方客户端的这份文件里真实存在 ``Port=0``、空 ``IPAddress``、重复条目；任何一条
    坏数据都不该让整份候选作废——发现是"尽量多拿可用端点"，不是"校验配置文件"。
    """

    hosts = {match.group(1): match.group(2) for match in _IP_LINE.finditer(text)}
    ports = {match.group(1): match.group(2) for match in _PORT_LINE.finditer(text)}
    names = {match.group(1): match.group(2) for match in _NAME_LINE.finditer(text)}

    out: list[Candidate] = []
    seen: set[tuple[str, int]] = set()
    for index, raw_host in hosts.items():
        host = _clean_candidate_host(raw_host)
        if not host:
            continue
        port = _coerce_port(ports.get(index, "7709"))
        if port is None:
            continue
        family = _family_for_port(port)
        if family is None:
            continue
        key = (host, port)
        if key in seen:
            continue
        seen.add(key)
        out.append(
            Candidate(
                host=host,
                port=port,
                name=names.get(index, "").strip(),
                source=source,
                family=family,
            )
        )
    return out


def read_connect_cfg(path: str | os.PathLike[str]) -> list[Candidate]:
    """按 GBK 读 ``connect.cfg``（官方文件是 GBK，按 UTF-8 读会把主机名读成乱码）。"""

    source_path = Path(path).expanduser()
    raw = source_path.read_bytes()
    text = raw.decode("gbk", errors="replace")
    return parse_connect_cfg_text(text)


def find_tdx_installs(
    roots: Sequence[str | os.PathLike[str]] | None = None,
    *,
    max_depth: int = 2,
    max_dirs: int = _MAX_SCAN_DIRS,
) -> list[Path]:
    """找本机通达信安装目录里的 ``connect.cfg``。

    只做**有界**扫描：盘符根与 ``Program Files`` 的直接子目录逐个看有没有
    ``connect.cfg``；只有名字像通达信/股票软件的目录才继续下探一层
    （``P:\\stock\\new_tdx64`` 这种布局）。不递归全盘。
    """

    queue: list[tuple[Path, int]] = [(Path(root), 0) for root in (roots or _default_roots())]
    found: list[Path] = []
    seen_dirs: set[str] = set()
    visited = 0
    while queue and visited < max_dirs:
        directory, depth = queue.pop(0)
        try:
            children = list(directory.iterdir())
        except OSError:
            continue
        for child in children:
            visited += 1
            if visited > max_dirs:
                break
            try:
                if not child.is_dir():
                    continue
            except OSError:
                continue
            config = child / "connect.cfg"
            try:
                if config.is_file():
                    resolved = str(config.resolve())
                    if resolved not in seen_dirs:
                        seen_dirs.add(resolved)
                        found.append(config)
                    continue
            except OSError:
                continue
            if depth + 1 < max_depth and _TDX_DIR_HINT.search(child.name):
                queue.append((child, depth + 1))
    return found


def _default_roots() -> list[Path]:
    roots: list[Path] = []
    env_home = os.environ.get("ATST_TDX_HOME")
    if env_home:
        roots.append(Path(env_home))
    for drive in ("C:", "D:", "E:", "F:", "G:", "P:"):
        roots.append(Path(f"{drive}/"))
    for var in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA"):
        value = os.environ.get(var)
        if value:
            roots.append(Path(value))
    return roots


# --------------------------------------------------------------------------- #
# 解析：开源清单（Python 元组表 / Go 结构体表 / JSON）
# --------------------------------------------------------------------------- #
def parse_python_host_table(text: str, *, source: str) -> list[Candidate]:
    """解析 pytdx / mootdx 风格的 ``("名字", "IP", 7709)`` 元组表。"""

    rows = (
        _candidate(host, int(port), name=name, source=source)
        for name, host, port in _PY_TUPLE.findall(text)
    )
    return _dedupe(candidate for candidate in rows if candidate is not None)


def parse_go_host_table(text: str, *, source: str) -> list[Candidate]:
    """解析 gotdx 风格的 ``{Name: "…", IP: "…", Port: 7709}`` 结构体表。"""

    rows = _GO_TUPLE.findall(text)
    if rows:
        parsed = (
            _candidate(host, int(port), name=name, source=source) for name, host, port in rows
        )
        return _dedupe(candidate for candidate in parsed if candidate is not None)
    fallback = (
        _candidate(host, int(port), source=source) for host, port in _GO_TUPLE_NO_NAME.findall(text)
    )
    return _dedupe(candidate for candidate in fallback if candidate is not None)


def _candidate(
    host: str,
    port: int,
    *,
    name: str = "",
    source: str = "",
    family: str = "",
    allow_unknown_port: bool = False,
) -> Candidate | None:
    """构造候选；端口不在本协议栈射程内（0/80/443 等）时返回 ``None``。

    ``allow_unknown_port`` 只给 ``--source file`` 用：调用方自己写的端点清单可能用非标准
    端口，此时族由 ``--family`` 决定，不能因为端口不在映射表里就丢掉。
    """

    cleaned = _clean_candidate_host(host)
    if not cleaned or not 1 <= port <= 65535:
        return None
    declared = family or _family_for_port(port)
    if declared is None:
        if not allow_unknown_port:
            return None
        declared = ""
    return Candidate(host=cleaned, port=port, name=name.strip(), source=source, family=declared)


def parse_json_hosts(text: str, *, source: str) -> list[Candidate]:
    """解析通用 JSON 候选表：``["1.2.3.4:7709"]`` 或 ``[{"host":…,"port":…}]``。"""

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ConfigError(f"候选 JSON 解析失败: {exc}", context={"source": source}) from exc

    items: Iterable[Any]
    if isinstance(parsed, dict):
        flat: list[Any] = []
        for value in parsed.values():
            if isinstance(value, list):
                flat.extend(value)
        items = flat
    elif isinstance(parsed, list):
        items = parsed
    else:
        raise ConfigError(f"候选 JSON 顶层必须是 list 或 dict: {type(parsed).__name__}")

    out: list[Candidate] = []
    for item in items:
        if isinstance(item, str):
            host, _, port_text = item.rpartition(":")
            if not host:
                continue
            port = _coerce_port(port_text or "7709")
            if port is None:
                continue
            candidate = _candidate(host, port, source=source, allow_unknown_port=True)
        elif isinstance(item, dict):
            port = _coerce_port(item.get("port", 7709))
            if port is None:
                continue
            candidate = _candidate(
                str(item.get("host", "")),
                port,
                name=str(item.get("name", "")),
                source=source,
                family=str(item.get("family", "")),
                allow_unknown_port=True,
            )
        else:
            continue
        if candidate is not None:
            out.append(candidate)
    return _dedupe(out)


def _dedupe(candidates: Iterable[Candidate]) -> list[Candidate]:
    out: list[Candidate] = []
    seen: set[tuple[str, int]] = set()
    for candidate in candidates:
        key = (candidate.host, candidate.port)
        if not candidate.host or key in seen:
            continue
        seen.add(key)
        out.append(candidate)
    return out


def merge_candidates(*groups: Iterable[Candidate]) -> list[Candidate]:
    """按传入顺序合并去重：同一 endpoint 保留**先出现**的那份来源标签。"""

    return _dedupe(candidate for group in groups for candidate in group)


# --------------------------------------------------------------------------- #
# 取候选
# --------------------------------------------------------------------------- #
def fetch_text(url: str, *, timeout: float = 15.0, max_bytes: int = _MAX_TABLE_BYTES) -> str:
    """拉取一份清单文本（有体积上限，避免拿错 URL 时把内存打满）。"""

    if not url.lower().startswith(("https://", "http://")):
        raise ConfigError(f"候选地址必须是 http(s) URL: {url!r}")
    request = Request(url, headers={"User-Agent": "atst-host-discover"})
    with urlopen(request, timeout=timeout) as response:  # noqa: S310 - 显式 scheme 白名单
        raw = response.read(max_bytes + 1)
    if len(raw) > max_bytes:
        raise ConfigError(f"候选清单超过 {max_bytes} 字节上限: {url}", context={"url": url})
    return raw.decode("utf-8", errors="replace")


def load_source(
    source: str,
    *,
    path: str | os.PathLike[str] | None = None,
    url: str | None = None,
    timeout: float = 15.0,
) -> tuple[list[Candidate], str]:
    """按来源取候选，返回 ``(候选, 来源描述)``。

    ``source`` 取值：``tdx-install`` / ``gotdx`` / ``mootdx`` / ``pytdx`` / ``file``。
    """

    if source == "tdx-install":
        paths = [Path(path).expanduser()] if path else find_tdx_installs()
        if not paths:
            return [], "tdx-install: 未找到 connect.cfg"
        candidates: list[Candidate] = []
        origins: list[str] = []
        for config in paths:
            try:
                parsed = read_connect_cfg(config)
            except OSError as exc:
                origins.append(f"{config}: {type(exc).__name__}")
                continue
            candidates.extend(parsed)
            origins.append(f"{config} ({len(parsed)} 台)")
        return merge_candidates(candidates), "tdx-install: " + "; ".join(origins)

    if source == "file":
        if url:
            text = fetch_text(url, timeout=timeout)
            return _parse_by_shape(text, source="file"), f"file: {url}"
        if not path:
            raise ConfigError("--source file 需要 --path 或 --url")
        file_path = Path(path).expanduser()
        if not file_path.is_file():
            raise ConfigError(f"候选文件不存在: {file_path}", context={"path": str(file_path)})
        suffix = file_path.suffix.lower()
        if suffix == ".cfg":
            return read_connect_cfg(file_path), f"file: {file_path}"
        text = file_path.read_text(encoding="utf-8", errors="replace")
        return _parse_by_shape(text, source="file"), f"file: {file_path}"

    if source in OSS_SOURCES:
        target = url or OSS_SOURCES[source]
        text = fetch_text(target, timeout=timeout)
        parser = parse_go_host_table if source == "gotdx" else parse_python_host_table
        return parser(text, source=source), f"{source}: {target}"

    raise ConfigError(
        f"未知候选来源: {source!r}（可选 tdx-install/gotdx/mootdx/pytdx/file）",
        context={"source": source},
    )


def _parse_by_shape(text: str, *, source: str) -> list[Candidate]:
    """按内容形状猜格式：JSON → Go 结构体 → Python 元组表。"""

    stripped = text.lstrip()
    if stripped.startswith(("[", "{")):
        with contextlib.suppress(ConfigError):
            return parse_json_hosts(text, source=source)
    for parser in (parse_go_host_table, parse_python_host_table):
        parsed = parser(text, source=source)
        if parsed:
            return parsed
    return []


# --------------------------------------------------------------------------- #
# 实测与采纳
# --------------------------------------------------------------------------- #
def to_entries(candidates: Iterable[Candidate], *, family: str) -> list[HostEntry]:
    """把候选转成 ``HostEntry``；非法条目跳过（与解析层同一套容错口径）。"""

    entries: list[HostEntry] = []
    seen: set[str] = set()
    for candidate in candidates:
        try:
            entry = parse_server(
                (candidate.host, candidate.port), family=candidate.family or family
            )
        except ConfigError:
            continue
        if entry.key in seen:
            continue
        seen.add(entry.key)
        entries.append(entry)
    return entries


def adopt_results(
    results: Sequence[ProbeResult],
    *,
    family: str = Family.STANDARD,
    ranking_file: str = "~/.atst/server_ranking.json",
) -> int:
    """把实测通过项写进排名；非 STANDARD 族返回 0（排名文件只记这一族）。"""

    if family != Family.STANDARD:
        return 0
    ok = [result for result in results if result.ok and result.rtt_ms is not None]
    if not ok:
        return 0
    RankingStore(ranking_file).update(rank_hosts(ok))
    return len(ok)


def discover(
    sources: Sequence[str] = ("tdx-install",),
    *,
    family: str = Family.STANDARD,
    path: str | os.PathLike[str] | None = None,
    url: str | None = None,
    timeout: float = 2.0,
    samples: int = 1,
    workers: int = 16,
    ranking_file: str = "~/.atst/server_ranking.json",
    verify: bool = True,
    adopt: bool = True,
) -> DiscoveryResult:
    """完整流程：取候选 → （可选）实测 → （可选）采纳。"""

    groups: list[list[Candidate]] = []
    origins: list[str] = []
    for source in sources:
        candidates, origin = load_source(source, path=path, url=url)
        groups.append(candidates)
        origins.append(origin)

    merged = merge_candidates(*groups)
    # 来源里已声明族的候选只在请求同一族时参与实测；未声明族的交给 --family 解释。
    matched = [candidate for candidate in merged if candidate.family in ("", family)]
    results: tuple[ProbeResult, ...] = ()
    if verify and matched:
        entries = to_entries(matched, family=family)
        results = tuple(
            speedtest(
                entries,
                family=family,
                timeout=timeout,
                samples=samples,
                max_workers=workers,
            )
        )
    adopted = adopt_results(results, family=family, ranking_file=ranking_file) if adopt else 0
    return DiscoveryResult(
        family=family,
        candidates=tuple(matched),
        results=results,
        adopted=adopted,
        origins=tuple(origins),
    )


def build_report(result: DiscoveryResult, *, in_pool: set[str] | None = None) -> dict[str, Any]:
    """把发现结果整理成可落盘的报告（含每台主机的来源与是否已在池中）。"""

    by_key = {candidate_key(candidate): candidate for candidate in result.candidates}
    rows: list[dict[str, Any]] = []
    for probe in result.results:
        candidate = by_key.get(probe.key)
        rows.append(
            {
                "host": probe.host,
                "port": probe.port,
                "name": candidate.name if candidate else "",
                "source": candidate.source if candidate else "",
                "ok": probe.ok,
                "rtt_ms": probe.rtt_ms,
                "connect_ms": probe.connect_ms,
                "in_pool": bool(in_pool and probe.key in in_pool),
                "error": probe.error,
            }
        )
    return {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "family": result.family,
        "origins": list(result.origins),
        "total": len(result.candidates),
        "probed": len(result.results),
        "reachable": result.reachable,
        "adopted": result.adopted,
        "hosts": rows,
    }


def candidate_key(candidate: Candidate) -> str:
    """与 ``HostEntry.key`` 同口径，供报告层按 endpoint 对齐。"""

    rendered = f"[{candidate.host}]" if ":" in candidate.host else candidate.host
    return f"{rendered}:{candidate.port}"
