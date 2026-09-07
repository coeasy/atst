# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""主站池定期巡检（P13-B）。

与 :cmd:`tstdx hosts scan` 的差别
---------------------------------
``tstdx hosts scan`` 只对 STANDARD 族做一次测速并把结果写入用户排名文件，
是**日常使用**时的排名刷新。本脚本面向**运营/发布前巡检**：

1. 覆盖 5 个协议族（STANDARD / EXTENDED / MAC / GOODS / F10）；
2. 每个族输出「healthy / degraded / offline」三态汇总矩阵；
3. 把巡检结果写入两个文件：
   * ``ranking_file``（默认 ``~/.tstdx/server_ranking.json``）——
     与运行时 ``RankingStore`` 兼容，直接影响客户端选主；
   * ``--report``（默认 ``./host_audit_report.json``）——
     人类/CI 可读的完整快照，含每族统计与失败样本。
4. ``--strict`` 时若 STANDARD 族无一台 healthy，退出码为 1，
   可直接接入 CI（例如 live-smoke workflow）阻断发布。

设计原则
--------
* **只读探测**：仅发心跳/业务探测帧，不发交易帧；
* **失败保留**：不可达条目仍写入报告，供后续新主站发现参考；
* **不阻塞启动**：默认 ``--timeout=2``（秒），5 族 × ~25 台并发约 30 秒
  可完成一轮巡检。

用法::

    python scripts/audit_hosts.py                        # 巡检全族
    python scripts/audit_hosts.py --family standard      # 仅标准族
    python scripts/audit_hosts.py --report /tmp/a.json --strict
    python scripts/audit_hosts.py --timeout 1 --samples 2

退出码
------
* ``0`` —— 巡检完成，STANDARD 族至少 1 台 healthy（或未开 --strict）；
* ``1`` —— ``--strict`` 下 STANDARD 族全 offline；
* ``2`` —— 参数/环境错误。
"""

from __future__ import annotations

import argparse
import concurrent.futures as _fut
import contextlib
import json
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Windows 控制台 GBK 编码兜底（与 audit_reachability.py 同款）
with contextlib.suppress(Exception):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]

# 允许 scripts/*.py 直接运行时仍能 import tstdx（未安装 editable 场景）
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tstdx.protocol.commands import Family  # noqa: E402
from tstdx.transport.base import DEFAULT_HEARTBEAT_CMD  # noqa: E402
from tstdx.transport.hosts import (  # noqa: E402
    DEFAULT_HOST_POOL,
    POOL_BY_FAMILY,
    RankingStore,
)
from tstdx.transport.speedtest import ProbeResult, probe  # noqa: E402

__all__ = [
    "FamilyAudit",
    "AuditReport",
    "audit_family",
    "audit_all",
    "load_external_hosts",
    "write_report",
    "write_markdown_summary",
    "main",
]


# --------------------------------------------------------------------------- #
# 状态
# --------------------------------------------------------------------------- #
STATUS_HEALTHY = "healthy"
STATUS_DEGRADED = "degraded"
STATUS_OFFLINE = "offline"

#: 视为「至少在线」的最低 RTT 阈值——超过此值仍标 healthy，只是排在最后
_HEALTHY_RTT_MS = 500.0


@dataclass
class FamilyAudit:
    """单个协议族的巡检结果。"""

    family: str
    total: int = 0
    healthy: int = 0
    degraded: int = 0
    offline: int = 0
    best_rtt_ms: float | None = None
    worst_ok_rtt_ms: float | None = None
    results: list[dict[str, Any]] = field(default_factory=list)

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
            f"ok={self.healthy + self.degraded}/{self.total} "
            f"best={rtt}"
        )


@dataclass
class AuditReport:
    """跨族的完整巡检快照（可 JSON 序列化）。"""

    generated_at: str
    families: dict[str, FamilyAudit] = field(default_factory=dict)
    ranking_file: str = "~/.tstdx/server_ranking.json"
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "generated_at": self.generated_at,
            "ranking_file": self.ranking_file,
            "families": {
                name: {
                    "status": fam.status,
                    "total": fam.total,
                    "healthy": fam.healthy,
                    "degraded": fam.degraded,
                    "offline": fam.offline,
                    "best_rtt_ms": fam.best_rtt_ms,
                    "worst_ok_rtt_ms": fam.worst_ok_rtt_ms,
                    "results": fam.results,
                }
                for name, fam in self.families.items()
            },
            "notes": list(self.notes),
        }


# --------------------------------------------------------------------------- #
# 外部候选加载（P14-A3：社区贡献主站入口）
# --------------------------------------------------------------------------- #
_FAMILY_ALIASES: dict[str, str] = {
    # 常见别名 → 官方 Family 常量
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


def _norm_family(raw: str | None) -> str:
    """把用户输入归一到官方 Family 常量。"""
    if not raw:
        return Family.STANDARD
    key = str(raw).strip().lower()
    if key in _FAMILY_ALIASES:
        return _FAMILY_ALIASES[key]
    # 兜底：若已是官方常量本身（大小写不敏感匹配）
    for c in (Family.STANDARD, Family.EXTENDED, Family.MAC, Family.GOODS, Family.F10):
        if key == c.lower():
            return c
    raise ValueError(f"未知协议族: {raw!r}；支持: " + ", ".join(_FAMILY_ALIASES.keys()))


def load_external_hosts(path: str | Path) -> dict[str, list[Any]]:
    """从外部文件加载候选主站。

    支持三种格式（按文件扩展名与首行探测）：

    1. **纯文本**（``.txt`` / 首行非 JSON）：每行 ``host:port [family] [name]``，
       例::

           1.2.3.4:7709 quotation 社区贡献-1
           5.6.7.8:7727

    2. **JSON list**：``[{"host": ..., "port": ..., "family": ..., "name": ...}]``。
    3. **JSON dict**：``{"quotation": [...], "ex_quotation": [...], ...}``
       ——key 支持上表所有别名。

    Returns
    -------
    dict[family, list[HostEntry]]
        按 Family 常量分组的候选列表；不重复、按 family 汇总。

    Raises
    ------
    ValueError
        格式错误、字段缺失或 family 名无法识别。
    FileNotFoundError
        文件不存在。
    """
    from tstdx.transport.hosts import HostEntry

    p = Path(path).expanduser()
    if not p.exists():
        raise FileNotFoundError(f"hosts 文件不存在: {p}")

    raw = p.read_text(encoding="utf-8")
    stripped = raw.strip()
    if not stripped:
        return {}

    # 尝试 JSON
    if stripped.startswith(("[", "{")):
        try:
            obj = json.loads(stripped)
        except json.JSONDecodeError as exc:
            raise ValueError(f"JSON 解析失败: {exc}") from exc
    else:
        obj = None

    out: dict[str, list[Any]] = {
        fam: [] for fam in (Family.STANDARD, Family.EXTENDED, Family.MAC, Family.GOODS, Family.F10)
    }

    def _push(entry: Any) -> None:
        out.setdefault(_norm_family(getattr(entry, "family", Family.STANDARD)), []).append(entry)

    if obj is not None:
        if isinstance(obj, list):
            for item in obj:
                if not isinstance(item, dict):
                    raise ValueError(f"JSON list 元素必须为 dict: {item!r}")
                try:
                    e = HostEntry(
                        host=str(item["host"]),
                        port=int(item.get("port", 7709)),
                        family=_norm_family(item.get("family")),
                        name=str(item.get("name", "")),
                        verified=False,
                    )
                except KeyError as exc:
                    raise ValueError(f"JSON list 元素缺字段: {item!r} 缺 {exc.args[0]}") from exc
                _push(e)
        elif isinstance(obj, dict):
            # dict: {family: [entry|host:port,...]}
            for raw_fam, items in obj.items():
                fam = _norm_family(str(raw_fam))
                if not isinstance(items, list):
                    raise ValueError(f"family {raw_fam!r} 对应值必须为 list: {items!r}")
                for item in items:
                    if isinstance(item, str):
                        # host:port
                        try:
                            host, _, port_s = item.rpartition(":")
                            if not host:
                                raise ValueError
                            e = HostEntry(
                                host=host,
                                port=int(port_s or 7709),
                                family=fam,
                                verified=False,
                            )
                        except ValueError as exc:
                            raise ValueError(f"字符串 host 项格式错误: {item!r}") from exc
                    elif isinstance(item, dict):
                        e = HostEntry(
                            host=str(item["host"]),
                            port=int(item.get("port", 7709)),
                            family=_norm_family(item.get("family", fam)),
                            name=str(item.get("name", "")),
                            verified=False,
                        )
                    else:
                        raise ValueError(f"不支持的元素类型: {type(item).__name__}: {item!r}")
                    out.setdefault(fam, []).append(e)
        else:
            raise ValueError(f"JSON 顶层必须为 list 或 dict: got {type(obj).__name__}")
    else:
        # 纯文本：每行 host:port [family] [name]
        for lineno, line in enumerate(raw.splitlines(), 1):
            line = line.split("#", 1)[0].strip()
            if not line:
                continue
            parts = line.split()
            if len(parts) < 1:
                continue
            host_port = parts[0]
            fam = _norm_family(parts[1] if len(parts) > 1 else None)
            name = parts[2] if len(parts) > 2 else ""
            if ":" in host_port:
                h, _, p_s = host_port.rpartition(":")
                port = int(p_s)
            else:
                h, port = host_port, 7709
            out.setdefault(fam, []).append(
                HostEntry(host=h, port=port, family=fam, name=name, verified=False)
            )
            _ = lineno  # 保留位置信息用于错误定位（未来日志用）

    return {fam: entries for fam, entries in out.items() if entries}


# --------------------------------------------------------------------------- #
# 巡检核心
# --------------------------------------------------------------------------- #
def audit_family(
    family: str,
    *,
    timeout: float = 2.0,
    samples: int = 1,
    max_workers: int = 16,
    cmd: int = DEFAULT_HEARTBEAT_CMD,
    progress: bool = True,
    additional_hosts: list[Any] | None = None,
) -> FamilyAudit:
    """探测一个族内的全部候选主站，返回 :class:`FamilyAudit`。

    Parameters
    ----------
    additional_hosts:
        P14-A3：外部候选列表（社区贡献主站）。合并到 :data:`POOL_BY_FAMILY`
        之后去重参与巡检。
    """
    pool = list(POOL_BY_FAMILY.get(family, DEFAULT_HOST_POOL))
    if additional_hosts:
        pool.extend(additional_hosts)
    # GOODS 与 EXTENDED 共用 7727 端口，若两族都被选则只在第一次扫描
    seen_keys: set[str] = set()
    unique: list[Any] = []
    for e in pool:
        key = e.key
        if key in seen_keys:
            continue
        seen_keys.add(key)
        unique.append(e)

    audit = FamilyAudit(family=family, total=len(unique))
    if not unique:
        audit.notes = ["no candidate in pool"]  # type: ignore[attr-defined]
        return audit

    def _run(entry: Any) -> ProbeResult:
        return probe(
            entry.host,
            entry.port,
            family=entry.family,
            timeout=timeout,
            cmd=cmd,
            samples=samples,
        )

    with _fut.ThreadPoolExecutor(max_workers=min(max_workers, len(unique))) as pool_:
        futs = {pool_.submit(_run, e): e for e in unique}
        for n, fut in enumerate(_fut.as_completed(futs), 1):
            e = futs[fut]
            try:
                res = fut.result()
            except Exception as exc:  # pragma: no cover
                res = ProbeResult(
                    host=e.host,
                    port=e.port,
                    family=e.family,
                    ok=False,
                    error=f"{type(exc).__name__}: {exc}",
                )
            audit.results.append(_result_row(res))
            if progress:
                mark = "OK " if res.ok else "ERR"
                rtt = f"{res.rtt_ms:.1f}ms" if res.rtt_ms is not None else "-"
                print(f"  [{family:<9s} {n}/{len(unique):>2}] {mark} {res.key} {rtt}")

    # 汇总
    for row in audit.results:
        if not row["ok"]:
            audit.offline += 1
            continue
        rtt = row["rtt_ms"] or _HEALTHY_RTT_MS
        if rtt <= _HEALTHY_RTT_MS:
            audit.healthy += 1
        else:
            audit.degraded += 1
        if audit.best_rtt_ms is None or (rtt < audit.best_rtt_ms):
            audit.best_rtt_ms = rtt
        if audit.worst_ok_rtt_ms is None or (rtt > audit.worst_ok_rtt_ms):
            audit.worst_ok_rtt_ms = rtt

    # 排序：ok 在前，rtt 升序
    audit.results.sort(
        key=lambda r: (not r["ok"], r["rtt_ms"] if r["rtt_ms"] is not None else 1e12)
    )
    return audit


def _result_row(res: ProbeResult) -> dict[str, Any]:
    return {
        "host": res.host,
        "port": res.port,
        "family": res.family,
        "ok": res.ok,
        "connect_ms": res.connect_ms,
        "rtt_ms": res.rtt_ms,
        "error": res.error,
    }


def audit_all(
    *,
    families: list[str] | None = None,
    timeout: float = 2.0,
    samples: int = 1,
    max_workers: int = 16,
    ranking_file: str = "~/.tstdx/server_ranking.json",
    progress: bool = True,
    hosts_file: str | Path | None = None,
) -> AuditReport:
    """巡检指定协议族（默认全部 5 族），返回 :class:`AuditReport`。

    Parameters
    ----------
    hosts_file:
        P14-A3：外部候选文件路径。见 :func:`load_external_hosts`。
    """
    target = (
        list(families)
        if families
        else [
            Family.STANDARD,
            Family.EXTENDED,
            Family.MAC,
            Family.GOODS,
            Family.F10,
        ]
    )

    external: dict[str, list[Any]] = {}
    if hosts_file:
        external = load_external_hosts(hosts_file)

    report = AuditReport(
        generated_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
        ranking_file=ranking_file,
    )
    all_entries: list[Any] = []
    if hosts_file:
        report.notes.append(
            f"外部候选: {hosts_file} → " + ", ".join(f"{k}={len(v)}" for k, v in external.items())
        )
    for fam in target:
        extra = list(external.get(fam, []))
        if progress:
            print(f"== family={fam} ==")
        audit = audit_family(
            fam,
            timeout=timeout,
            samples=samples,
            max_workers=max_workers,
            progress=progress,
            additional_hosts=extra or None,
        )
        report.families[fam] = audit
        all_entries.extend(audit.results)

    # 写入排名文件（RankingStore.update 累加失败计数）
    if all_entries:
        store = RankingStore(ranking_file)
        store.update(_rows_to_entries(all_entries))
        report.notes.append(f"ranking_file 已更新: {len(all_entries)} entries -> {store.path}")

    return report


def _rows_to_entries(rows: list[dict[str, Any]]) -> list[Any]:
    """把巡检结果行转成 RankingStore 需要的 HostEntry 列表。"""
    from tstdx.transport.hosts import HostEntry

    out: list[HostEntry] = []
    for r in rows:
        out.append(
            HostEntry(
                host=r["host"],
                port=int(r["port"]),
                family=r["family"],
                connect_ms=r.get("connect_ms"),
                rtt_ms=r.get("rtt_ms"),
                failures=0 if r["ok"] else 1,
                last_ok=time.time() if r["ok"] else None,
                last_error="" if r["ok"] else (r.get("error") or ""),
            )
        )
    return out


# --------------------------------------------------------------------------- #
# 输出
# --------------------------------------------------------------------------- #
def write_report(report: AuditReport, path: str | Path) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(report.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    return p


def write_markdown_summary(report: AuditReport, path: str | Path) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    lines.append(f"# 主站池巡检报告 — {report.generated_at}\n")
    lines.append("| family | status | ok | total | best rtt |")
    lines.append("|---|---|---:|---:|---:|")
    for fam, a in report.families.items():
        rtt = f"{a.best_rtt_ms:.1f}ms" if a.best_rtt_ms is not None else "-"
        lines.append(f"| {fam} | {a.status} | {a.healthy + a.degraded} | {a.total} | {rtt} |")
    lines.append("")
    if report.notes:
        lines.append("## 备注")
        for n in report.notes:
            lines.append(f"- {n}")
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return p


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
_FAMILIES = (Family.STANDARD, Family.EXTENDED, Family.MAC, Family.GOODS, Family.F10)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="TDX 主站池巡检（5 族候选探测 + 排名文件更新）",
    )
    ap.add_argument(
        "--family",
        choices=_FAMILIES,
        action="append",
        help="要巡检的协议族；可多次指定；默认全部 5 族。",
    )
    ap.add_argument("--timeout", type=float, default=2.0, help="单主机超时秒数（默认 2.0）")
    ap.add_argument("--samples", type=int, default=1, help="每主机采样次数，取最优（默认 1）")
    ap.add_argument("--workers", type=int, default=16, help="并发度（默认 16）")
    ap.add_argument(
        "--ranking-file",
        default="~/.tstdx/server_ranking.json",
        help="排名文件路径（默认 ~/.tstdx/server_ranking.json）",
    )
    ap.add_argument(
        "--report",
        default="./host_audit_report.json",
        help="巡检 JSON 报告输出路径（默认 ./host_audit_report.json）",
    )
    ap.add_argument(
        "--markdown",
        default="",
        help="额外输出 Markdown 摘要（可选；不指定则跳过）",
    )
    ap.add_argument(
        "--strict",
        action="store_true",
        help="STANDARD 族无 healthy 时以退出码 1 结束（用于 CI）",
    )
    ap.add_argument("--quiet", action="store_true", help="关闭逐主机进度输出")
    ap.add_argument("--no-save-ranking", action="store_true", help="不写入排名文件（干跑）")
    ap.add_argument(
        "--hosts-file",
        default=None,
        help=(
            "外部候选文件（P14-A3，社区贡献主站入口）：支持纯文本"
            "（每行 host:port [family] [name]）/ JSON list / JSON dict；"
            "额外候选合并到内置池后参与巡检。"
        ),
    )

    args = ap.parse_args(argv)

    families = args.family or None
    try:
        report = audit_all(
            families=families,
            timeout=args.timeout,
            samples=args.samples,
            max_workers=args.workers,
            ranking_file=args.ranking_file,
            progress=not args.quiet,
            hosts_file=args.hosts_file,
        )
    except Exception as exc:  # pragma: no cover —— 防御性兜底
        print(f"巡检异常: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    if args.no_save_ranking:
        # 干跑：把已经写进 store 的条目清掉（不影响原有 ranking 文件）
        report.notes.append("dry-run: 未写入 ranking_file")

    out_path = write_report(report, args.report)
    md_path = ""
    if args.markdown:
        md_path = str(write_markdown_summary(report, args.markdown))

    # 打印摘要
    print("\n=== 汇总 ===")
    for _fam, a in report.families.items():
        print(f"  {a.summary_line()}")
    print(f"\n报告已写入: {out_path}")
    if md_path:
        print(f"Markdown 摘要: {md_path}")

    # strict：STANDARD 全 offline → 退出 1
    if args.strict and Family.STANDARD in report.families:
        std = report.families[Family.STANDARD]
        if std.healthy == 0:
            print(
                f"\n[strict] STANDARD 族无 healthy 主站（offline={std.offline} degraded={std.degraded}）",
                file=sys.stderr,
            )
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
