# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""Installable TDX host-pool audit shared by CLI, source wrapper and CI.

All protocol families are audited and reported, but the historical runtime
``RankingStore`` is a V1 ``host:port`` keyed cache. Therefore only STANDARD
results may be persisted there; F10/GOODS/etc. remain report evidence and can
never overwrite STANDARD ranking identity.
"""

from __future__ import annotations

import argparse
import concurrent.futures as _fut
import json
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..protocol.commands import Family
from ..transport.base import DEFAULT_HEARTBEAT_CMD
from ..transport.hosts import DEFAULT_HOST_POOL, POOL_BY_FAMILY, HostEntry, RankingStore
from ..transport.speedtest import ProbeResult, probe

__all__ = [
    "AuditReport",
    "FamilyAudit",
    "STATUS_DEGRADED",
    "STATUS_HEALTHY",
    "STATUS_OFFLINE",
    "audit_all",
    "audit_family",
    "load_external_hosts",
    "main",
    "write_markdown_summary",
    "write_report",
]

STATUS_HEALTHY = "healthy"
STATUS_DEGRADED = "degraded"
STATUS_OFFLINE = "offline"
_HEALTHY_RTT_MS = 500.0
_FAMILIES = (Family.STANDARD, Family.EXTENDED, Family.MAC, Family.GOODS, Family.F10)
_FAMILY_ALIASES: dict[str, str] = {
    "quotation": Family.STANDARD,
    "standard": Family.STANDARD,
    "std": Family.STANDARD,
    "7709": Family.STANDARD,
    "ex_quotation": Family.EXTENDED,
    "extended": Family.EXTENDED,
    "ex": Family.EXTENDED,
    "7727": Family.EXTENDED,
    "mac_quotation": Family.MAC,
    "mac": Family.MAC,
    "goods": Family.GOODS,
    "f10": Family.F10,
}


@dataclass
class FamilyAudit:
    family: str
    total: int = 0
    healthy: int = 0
    degraded: int = 0
    offline: int = 0
    best_rtt_ms: float | None = None
    worst_ok_rtt_ms: float | None = None
    results: list[dict[str, Any]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def status(self) -> str:
        if self.healthy > 0:
            return STATUS_HEALTHY
        if self.degraded > 0:
            return STATUS_DEGRADED
        return STATUS_OFFLINE

    def summary_line(self) -> str:
        rtt = f"{self.best_rtt_ms:.1f}ms" if self.best_rtt_ms is not None else "-"
        return (
            f"{self.family:<10s} status={self.status:<8s} "
            f"ok={self.healthy + self.degraded}/{self.total} best={rtt}"
        )


@dataclass
class AuditReport:
    generated_at: str
    families: dict[str, FamilyAudit] = field(default_factory=dict)
    ranking_file: str = "~/.tstdx/server_ranking.json"
    ranking_saved: bool = False
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "generated_at": self.generated_at,
            "ranking_file": self.ranking_file,
            "ranking_saved": self.ranking_saved,
            "families": {
                name: {
                    "status": audit.status,
                    "total": audit.total,
                    "healthy": audit.healthy,
                    "degraded": audit.degraded,
                    "offline": audit.offline,
                    "best_rtt_ms": audit.best_rtt_ms,
                    "worst_ok_rtt_ms": audit.worst_ok_rtt_ms,
                    "results": audit.results,
                    "notes": list(audit.notes),
                }
                for name, audit in self.families.items()
            },
            "notes": list(self.notes),
        }


def _norm_family(raw: str | None) -> str:
    if not raw:
        return Family.STANDARD
    key = str(raw).strip().lower()
    if key in _FAMILY_ALIASES:
        return _FAMILY_ALIASES[key]
    for family in _FAMILIES:
        if key == family.lower():
            return family
    raise ValueError(f"未知协议族: {raw!r}; 支持: {', '.join(_FAMILY_ALIASES)}")


def _parse_port(raw: object) -> int:
    try:
        port = int(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"非法端口: {raw!r}") from exc
    if not 1 <= port <= 65535:
        raise ValueError(f"端口超出范围 1..65535: {port}")
    return port


def _positive_float(raw: str) -> float:
    try:
        value = float(raw)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"需要正数，实际 {raw!r}") from exc
    if value <= 0:
        raise argparse.ArgumentTypeError(f"需要 > 0，实际 {value}")
    return value


def _positive_int(raw: str) -> int:
    try:
        value = int(raw)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"需要正整数，实际 {raw!r}") from exc
    if value <= 0:
        raise argparse.ArgumentTypeError(f"需要 > 0，实际 {value}")
    return value


def _require_positive(*, timeout: float, samples: int, max_workers: int) -> None:
    if timeout <= 0:
        raise ValueError(f"timeout 必须 > 0，实际 {timeout}")
    if samples <= 0:
        raise ValueError(f"samples 必须 > 0，实际 {samples}")
    if max_workers <= 0:
        raise ValueError(f"max_workers 必须 > 0，实际 {max_workers}")


def _host_entry(item: dict[str, Any], *, default_family: str) -> HostEntry:
    try:
        host = str(item["host"]).strip()
    except KeyError as exc:
        raise ValueError(f"host 条目缺少 host: {item!r}") from exc
    if not host:
        raise ValueError(f"host 不能为空: {item!r}")
    return HostEntry(
        host=host,
        port=_parse_port(item.get("port", 7709)),
        family=_norm_family(item.get("family", default_family)),
        name=str(item.get("name", "")),
        verified=False,
    )


def load_external_hosts(path: str | Path) -> dict[str, list[HostEntry]]:
    """Load text/JSON candidates and bucket every entry by its canonical family."""

    source = Path(path).expanduser()
    if not source.is_file():
        raise FileNotFoundError(f"hosts 文件不存在: {source}")
    raw = source.read_text(encoding="utf-8")
    stripped = raw.strip()
    if not stripped:
        return {}

    parsed: object | None = None
    if stripped.startswith(("[", "{")):
        try:
            parsed = json.loads(stripped)
        except json.JSONDecodeError as exc:
            raise ValueError(f"JSON 解析失败: {exc}") from exc

    out: dict[str, list[HostEntry]] = {family: [] for family in _FAMILIES}

    def push(entry: HostEntry) -> None:
        family = _norm_family(entry.family)
        entry.family = family
        out[family].append(entry)

    if isinstance(parsed, list):
        for item in parsed:
            if not isinstance(item, dict):
                raise ValueError(f"JSON list 元素必须为 object: {item!r}")
            push(_host_entry(item, default_family=Family.STANDARD))
    elif isinstance(parsed, dict):
        for raw_family, items in parsed.items():
            family = _norm_family(str(raw_family))
            if not isinstance(items, list):
                raise ValueError(f"family {raw_family!r} 对应值必须为 list")
            for item in items:
                if isinstance(item, str):
                    host, sep, port_raw = item.rpartition(":")
                    if not sep or not host.strip():
                        raise ValueError(f"字符串 host 项必须为 host:port: {item!r}")
                    push(
                        HostEntry(
                            host=host.strip(),
                            port=_parse_port(port_raw),
                            family=family,
                        )
                    )
                elif isinstance(item, dict):
                    push(_host_entry(item, default_family=family))
                else:
                    raise ValueError(f"不支持的 host 元素: {type(item).__name__}: {item!r}")
    elif parsed is not None:
        raise ValueError(f"JSON 顶层必须为 list 或 object: {type(parsed).__name__}")
    else:
        for lineno, raw_line in enumerate(raw.splitlines(), 1):
            line = raw_line.split("#", 1)[0].strip()
            if not line:
                continue
            parts = line.split(maxsplit=2)
            host_port = parts[0]
            family = _norm_family(parts[1] if len(parts) > 1 else None)
            name = parts[2] if len(parts) > 2 else ""
            if ":" in host_port:
                host, _, port_raw = host_port.rpartition(":")
                if not host:
                    raise ValueError(f"第 {lineno} 行 host 为空")
                port = _parse_port(port_raw)
            else:
                host, port = host_port, 7709
            if not host.strip():
                raise ValueError(f"第 {lineno} 行 host 为空")
            push(HostEntry(host=host.strip(), port=port, family=family, name=name))

    return {family: entries for family, entries in out.items() if entries}


def _result_row(result: ProbeResult) -> dict[str, Any]:
    return {
        "host": result.host,
        "port": result.port,
        "family": result.family,
        "ok": result.ok,
        "connect_ms": result.connect_ms,
        "rtt_ms": result.rtt_ms,
        "error": result.error,
    }


def audit_family(
    family: str,
    *,
    timeout: float = 2.0,
    samples: int = 1,
    max_workers: int = 16,
    cmd: int = DEFAULT_HEARTBEAT_CMD,
    progress: bool = True,
    additional_hosts: list[HostEntry] | None = None,
) -> FamilyAudit:
    """Probe every unique endpoint for exactly one protocol family."""

    family = _norm_family(family)
    _require_positive(timeout=timeout, samples=samples, max_workers=max_workers)
    candidates = list(POOL_BY_FAMILY.get(family, ()))
    if additional_hosts:
        for entry in additional_hosts:
            if _norm_family(entry.family) != family:
                raise ValueError(
                    f"additional host {entry.key} family={entry.family!r} "
                    f"does not match audit family={family!r}"
                )
            candidates.append(entry)
    if not candidates and family == Family.STANDARD:
        candidates = [entry for entry in DEFAULT_HOST_POOL if entry.family == Family.STANDARD]

    seen_keys: set[str] = set()
    unique: list[HostEntry] = []
    for entry in candidates:
        if entry.key not in seen_keys:
            seen_keys.add(entry.key)
            unique.append(entry)

    audit = FamilyAudit(family=family, total=len(unique))
    if not unique:
        audit.notes.append("no candidate in pool")
        return audit

    def run(entry: HostEntry) -> ProbeResult:
        return probe(
            entry.host,
            entry.port,
            family=family,
            timeout=timeout,
            cmd=cmd,
            samples=samples,
        )

    with _fut.ThreadPoolExecutor(max_workers=min(max_workers, len(unique))) as executor:
        futures = {executor.submit(run, entry): entry for entry in unique}
        for index, future in enumerate(_fut.as_completed(futures), 1):
            entry = futures[future]
            try:
                result = future.result()
            except Exception as exc:  # pragma: no cover - provider/executor defense
                result = ProbeResult(
                    host=entry.host,
                    port=entry.port,
                    family=family,
                    ok=False,
                    error=f"{type(exc).__name__}: {exc}",
                )
            audit.results.append(_result_row(result))
            if progress:
                mark = "OK " if result.ok else "ERR"
                rtt = f"{result.rtt_ms:.1f}ms" if result.rtt_ms is not None else "-"
                print(f"  [{family:<9s} {index}/{len(unique):>2}] {mark} {result.key} {rtt}")

    for row in audit.results:
        if not row["ok"]:
            audit.offline += 1
            continue
        rtt_raw = row["rtt_ms"]
        if rtt_raw is None:
            audit.degraded += 1
            audit.notes.append(f"{row['host']}:{row['port']} reachable without RTT evidence")
            continue
        rtt = float(rtt_raw)
        if rtt <= _HEALTHY_RTT_MS:
            audit.healthy += 1
        else:
            audit.degraded += 1
        if audit.best_rtt_ms is None or rtt < audit.best_rtt_ms:
            audit.best_rtt_ms = rtt
        if audit.worst_ok_rtt_ms is None or rtt > audit.worst_ok_rtt_ms:
            audit.worst_ok_rtt_ms = rtt

    audit.results.sort(
        key=lambda row: (
            not bool(row["ok"]),
            row["rtt_ms"] if row["rtt_ms"] is not None else float("inf"),
        )
    )
    return audit


def _rows_to_entries(rows: list[dict[str, Any]]) -> list[HostEntry]:
    now = time.time()
    return [
        HostEntry(
            host=str(row["host"]),
            port=int(row["port"]),
            family=str(row["family"]),
            connect_ms=row.get("connect_ms"),
            rtt_ms=row.get("rtt_ms"),
            failures=0 if row["ok"] else 1,
            last_ok=now if row["ok"] else None,
            last_error="" if row["ok"] else str(row.get("error") or ""),
        )
        for row in rows
    ]


def audit_all(
    *,
    families: list[str] | None = None,
    timeout: float = 2.0,
    samples: int = 1,
    max_workers: int = 16,
    ranking_file: str = "~/.tstdx/server_ranking.json",
    progress: bool = True,
    hosts_file: str | Path | None = None,
    save_ranking: bool = True,
) -> AuditReport:
    """Audit selected families; persist only STANDARD ranking evidence."""

    _require_positive(timeout=timeout, samples=samples, max_workers=max_workers)
    target = [_norm_family(family) for family in families] if families else list(_FAMILIES)
    target = list(dict.fromkeys(target))
    external = load_external_hosts(hosts_file) if hosts_file else {}
    report = AuditReport(
        generated_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        ranking_file=ranking_file,
    )
    if hosts_file:
        report.notes.append(
            f"外部候选: {hosts_file} -> "
            + ", ".join(f"{family}={len(entries)}" for family, entries in external.items())
        )

    standard_rows: list[dict[str, Any]] = []
    for family in target:
        if progress:
            print(f"== family={family} ==")
        family_audit = audit_family(
            family,
            timeout=timeout,
            samples=samples,
            max_workers=max_workers,
            progress=progress,
            additional_hosts=list(external.get(family, ())) or None,
        )
        report.families[family] = family_audit
        if family == Family.STANDARD:
            standard_rows.extend(family_audit.results)

    if not save_ranking:
        report.notes.append("dry-run: ranking_file 未写入")
    elif standard_rows:
        store = RankingStore(ranking_file)
        store.update(_rows_to_entries(standard_rows))
        report.ranking_saved = True
        report.notes.append(
            f"ranking_file 仅更新 STANDARD: {len(standard_rows)} entries -> {store.path}"
        )
    else:
        report.notes.append("ranking_file 未更新: 本次巡检没有 STANDARD 结果")

    return report


def write_report(report: AuditReport, path: str | Path) -> Path:
    target = Path(path).expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    return target


def write_markdown_summary(report: AuditReport, path: str | Path) -> Path:
    target = Path(path).expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        f"# 主站池巡检报告 — {report.generated_at}",
        "",
        "| family | status | ok | total | best rtt |",
        "|---|---|---:|---:|---:|",
    ]
    for family, audit in report.families.items():
        rtt = f"{audit.best_rtt_ms:.1f}ms" if audit.best_rtt_ms is not None else "-"
        lines.append(
            f"| {family} | {audit.status} | {audit.healthy + audit.degraded} | "
            f"{audit.total} | {rtt} |"
        )
    notes = list(report.notes)
    for family, audit in report.families.items():
        notes.extend(f"{family}: {note}" for note in audit.notes)
    if notes:
        lines.extend(["", "## 备注"])
        lines.extend(f"- {note}" for note in notes)
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return target


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="TDX 主站池巡检（5 族候选探测 + 排名审计）")
    parser.add_argument(
        "--family",
        choices=_FAMILIES,
        action="append",
        help="要巡检的协议族；可多次指定；默认全部 5 族",
    )
    parser.add_argument("--timeout", type=_positive_float, default=2.0, help="单主机超时秒数")
    parser.add_argument("--samples", type=_positive_int, default=1, help="每主机采样次数")
    parser.add_argument("--workers", type=_positive_int, default=16, help="并发度")
    parser.add_argument(
        "--ranking-file",
        default="~/.tstdx/server_ranking.json",
        help="STANDARD 排名文件路径",
    )
    parser.add_argument(
        "--report",
        default="./host_audit_report.json",
        help="巡检 JSON 报告输出路径",
    )
    parser.add_argument("--markdown", default="", help="可选 Markdown 摘要路径")
    parser.add_argument("--strict", action="store_true", help="STANDARD 无 healthy 时退出 1")
    parser.add_argument("--quiet", action="store_true", help="关闭逐主机进度输出")
    parser.add_argument("--no-save-ranking", action="store_true", help="不写 STANDARD 排名文件")
    parser.add_argument("--hosts-file", default=None, help="外部候选文件（文本/JSON）")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        report = audit_all(
            families=args.family or None,
            timeout=args.timeout,
            samples=args.samples,
            max_workers=args.workers,
            ranking_file=args.ranking_file,
            progress=not args.quiet,
            hosts_file=args.hosts_file,
            save_ranking=not args.no_save_ranking,
        )
    except Exception as exc:  # process-control BaseExceptions intentionally propagate
        print(f"巡检异常: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    out_path = write_report(report, args.report)
    markdown_path = write_markdown_summary(report, args.markdown) if args.markdown else None
    print("\n=== 汇总 ===")
    for audit in report.families.values():
        print(f"  {audit.summary_line()}")
    print(f"\n报告已写入: {out_path}")
    if markdown_path is not None:
        print(f"Markdown 摘要: {markdown_path}")

    standard = report.families.get(Family.STANDARD)
    if args.strict and standard is not None and standard.healthy == 0:
        print(
            f"\n[strict] STANDARD 族无 healthy 主站 "
            f"(offline={standard.offline} degraded={standard.degraded})",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
