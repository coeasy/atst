# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""Golden 语料 origin 分级与 L1 真实样本门禁（工业级信任度基线）。

背景
----
Golden 语料有两种来源，信任级别不同：

* **real**（``source: self-captured``）—— 真实主站响应，是协议语义的
  「事实基准」；
* **synthetic**（``source: synthetic``）—— 以实采样本为种子对请求维度
  做受控变异后由解析器重生成（``golden_expand``），只证明「解析器自洽」，
  **不**证明「协议语义正确」。

本工具做四件事：

1. **ORIGIN 分级**：全量样本按 meta.source 判定 origin，输出分命令统计；
2. **维度覆盖报告**：K 线类别（category）/ 市场（market）在 real 样本上的
   覆盖矩阵，缺口即补录清单；
3. **payload 有效性**：按命令单记录最小布局下限检查 real 样本字节数——
   低于下限（如空响应头）标记 ``suspect_short``，不计入有效 real 统计；
   ``--require-payloads`` 将其纳入硬门禁；
4. **L1 门禁**：账本中 ``tier=L1 且 verified=True`` 的命令**必须**有
   ≥1 个**有效** real 样本 —— ``--gate`` 模式下缺失即非零退出（CI 阻断）。
   「已宣布精确解析却没有真实主站样本」视同回归。

命令行::

    python -m tstdx.tools.golden_audit               # 人类可读报告
    python -m tstdx.tools.golden_audit --json        # JSON 输出
    python -m tstdx.tools.golden_audit --gate        # L1 门禁（硬）
    python -m tstdx.tools.golden_audit --gate --require-markets
                                              # 追加：行情/K线/数量类命令须双市场覆盖
    python -m tstdx.tools.golden_audit --gate --require-kline-categories 0,4,9
                                              # 追加：K 线类别须覆盖指定集合
    python -m tstdx.tools.golden_audit --gate --require-payloads
                                              # 追加：real 样本须过 payload 下限

只读语料与账本，零网络。
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ._console import setup_console

__all__ = [
    "ORIGIN_REAL",
    "ORIGIN_SYNTHETIC",
    "ORIGIN_UNKNOWN",
    "classify_origin",
    "SampleInfo",
    "CommandCoverage",
    "scan_corpus",
    "audit",
    "main",
]

ORIGIN_REAL = "real"
ORIGIN_SYNTHETIC = "synthetic"
ORIGIN_UNKNOWN = "unknown"

#: 默认语料根（与 capture / golden_expand / test_golden 共用的仓库内路径）
DEFAULT_GOLDEN_ROOT = Path("tests/golden")

#: 维度门禁（--require-markets）适用的市场敏感命令
MARKET_SENSITIVE: tuple[int, ...] = (0x044E, 0x052D, 0x0530)

#: real 样本 payload 单记录最小字节数（按命令响应布局推算的**下限启发式**）：
#: 低于下限视为 ``suspect_short``（空响应头 / 无数据布局），不作为有效语义
#: 证据。下限取「响应头 + 1 条记录」的最小布局：
#:
#: ======  ========  ==========================================
#: 命令    下限      依据
#: ======  ========  ==========================================
#: 0x044E  2         uint16 数量值本身即完整响应
#: 0x044D  29        头 + 1 条证券表记录（29B）
#: 0x000F  29        头 + 1 条 GBBQ 记录（29B）
#: 0x0010  64        财务块远大于 64B
#: 0x052D  32        头 + 1 条 K 线记录（32B）
#: 0x0530  42        头 + 1 条 7709 行情记录（42B）
#: 0x0537  12        头 + 1 条分时记录（12B）
#: 0x0FB4  12        头 + 1 条历史分时记录（12B）
#: 0x0FC5  13        头 + 1 条逐笔记录（13B）
#: 0x06B9  1         任意非空文件块（空块=文件尾，属合法）
#: ======  ========  ==========================================
MIN_PAYLOAD_BYTES: dict[int, int] = {
    0x044E: 2,
    0x044D: 29,
    0x000F: 29,
    0x0010: 64,
    0x052D: 32,
    0x0530: 42,
    0x0537: 12,
    0x0FB4: 12,
    0x0FC5: 13,
    0x06B9: 1,
}

#: L1 命令 → capture 补录计划（提示文案）
_CAPTURE_HINTS: dict[int, str] = {
    0x052D: "--plan kline",
    0x000F: "--plan core",
    0x0010: "--plan core",
    0x044E: "--plan core",
    0x0537: "--plan core",
    0x0FC5: "--plan core",
    0x0530: "--plan quotes",
}


def classify_origin(source: str | None) -> str:
    """把 meta.source 归一化为三级 origin。

    兼容存量字段值：``self-captured``（→ real）、``synthetic*``（→ synthetic），
    其他一律 ``unknown``（报告可见，不静默归类）。
    """
    s = str(source or "").strip().lower()
    if s.startswith(("self-captured", "self_captured", "selfcaptured")):
        return ORIGIN_REAL
    if s in ("real", "captured"):
        return ORIGIN_REAL
    if s.startswith("synthetic"):
        return ORIGIN_SYNTHETIC
    return ORIGIN_UNKNOWN


def _cmd_key(raw: Any) -> str | None:
    """meta.command（``"0x52d"`` / ``"0x052D"``）→ 归一化键 ``"0x52d"``。"""
    try:
        return f"0x{int(str(raw), 16):x}"
    except (TypeError, ValueError):
        return None


@dataclass(frozen=True)
class SampleInfo:
    """一个样本的分级信息。"""

    rel: str
    command: str  # 归一化 hex 键，如 "0x52d"；无法解析为 None
    name: str
    family: str
    origin: str
    category: int | None
    market: int | None
    code: str
    payload_size: int = 0


@dataclass
class CommandCoverage:
    """单个命令的语料覆盖。"""

    command: str
    name: str = ""
    tier: str = ""
    verified: bool = False
    real: int = 0
    synthetic: int = 0
    unknown: int = 0
    #: payload 低于 MIN_PAYLOAD_BYTES 的 real 样本数（不作为有效语义证据）
    short_real: int = 0
    categories: set[int] = field(default_factory=set)
    markets: set[int] = field(default_factory=set)
    codes: set[str] = field(default_factory=set)

    @property
    def total(self) -> int:
        return self.real + self.synthetic + self.unknown

    @property
    def has_real(self) -> bool:
        return self.real > 0

    @property
    def has_valid_real(self) -> bool:
        """存在通过 payload 下限的 real 样本（未登记下限的命令视为有效）。"""
        if not self.command.startswith("0x"):
            return self.has_real
        floor = MIN_PAYLOAD_BYTES.get(int(self.command, 16))
        return self.real > self.short_real if floor is not None else self.has_real


def _load_meta(sample_dir: Path) -> dict[str, Any]:
    """读取 meta.json（缺失/损坏返回空 dict，由上层记为 unknown）。"""
    meta_path = sample_dir / "meta.json"
    if not meta_path.exists():
        return {}
    try:
        return json.loads(meta_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _to_int(raw: Any) -> int | None:
    """宽容解析 parse_ctx 维度字段；非整型返回 None。"""
    if isinstance(raw, bool) or raw is None:
        return None
    if isinstance(raw, int):
        return raw
    s = str(raw).strip()
    return int(s) if s.lstrip("-").isdigit() else None


def scan_corpus(root: Path | None = None) -> list[SampleInfo]:
    """扫描语料根下全部样本（``**/meta.json``，要求同级 payload.bin 存在）。"""
    root = Path(root) if root is not None else DEFAULT_GOLDEN_ROOT
    if not root.exists():
        return []
    out: list[SampleInfo] = []
    for meta_path in sorted(root.rglob("meta.json")):
        sample_dir = meta_path.parent
        payload_path = sample_dir / "payload.bin"
        if not payload_path.is_file():
            continue
        try:
            payload_size = payload_path.stat().st_size
        except OSError:  # pragma: no cover
            payload_size = 0
        meta = _load_meta(sample_dir)
        ctx = meta.get("parse_ctx") or {}
        rel = meta_path.relative_to(root).as_posix()
        out.append(
            SampleInfo(
                rel=rel,
                command=_cmd_key(meta.get("command")) or "",
                name=str(meta.get("command_name", "")),
                family=str(meta.get("family", "")),
                origin=classify_origin(meta.get("source")),
                category=_to_int(ctx.get("category")),
                market=_to_int(ctx.get("market")),
                code=str(ctx.get("code", "") or ""),
                payload_size=payload_size,
            )
        )
    return out


def _ledger_commands(override: Sequence[Any] | None) -> list[Any]:
    """L1 命令集合：默认取账本 ``tier=L1 且 verified=True``；测试可注入。"""
    if override is not None:
        return list(override)
    from ..protocol.commands import COMMANDS, TIER_L1

    return [c for c in COMMANDS.values() if c.tier == TIER_L1 and c.verified]


def audit(
    root: Path | None = None,
    *,
    ledger_commands: Sequence[Any] | None = None,
) -> dict[str, Any]:
    """全量审计：origin 分级 + 维度覆盖 + L1 真实样本缺口。

    Returns
    -------
    dict::

        {
          "root": str,
          "summary": {"total": n, "real": n, "synthetic": n, "unknown": n},
          "by_command": {"0x52d": {...}, ...},
          "l1_verified": ["0xf", "0x44e", "0x52d", "0x530"],
          "missing_real": [CommandCoverage dict, ...],   # L1 无 real 样本
          "market_gaps": {"0x530": [0], ...},            # real 样本缺的市场
          "kline_categories_real": [0, 1, ...],
          "hints": {"0x530": "python -m tstdx.tools.capture --plan quotes ..."},
        }
    """
    samples = scan_corpus(root)
    by_cmd: dict[str, CommandCoverage] = {}
    suspect_short: dict[str, list[dict[str, Any]]] = {}

    for s in samples:
        key = s.command or "<unparsed>"
        cov = by_cmd.setdefault(key, CommandCoverage(command=key))
        if s.origin == ORIGIN_REAL:
            cov.real += 1
            cmd_int = int(key, 16) if key.startswith("0x") else None
            floor = MIN_PAYLOAD_BYTES.get(cmd_int) if cmd_int is not None else None
            if floor is not None and s.payload_size < floor:
                cov.short_real += 1
                suspect_short.setdefault(key, []).append(
                    {"sample": s.rel, "size": s.payload_size, "min": floor}
                )
        elif s.origin == ORIGIN_SYNTHETIC:
            cov.synthetic += 1
        else:
            cov.unknown += 1
        if s.origin == ORIGIN_REAL:
            if s.category is not None:
                cov.categories.add(s.category)
            if s.market is not None:
                cov.markets.add(s.market)
            if s.code:
                cov.codes.add(s.code)

    # 回填账本元信息（tier / verified / name）
    from ..protocol.commands import get_command

    for key, cov in by_cmd.items():
        try:
            cmd_int = int(key, 16)
        except ValueError:
            continue
        meta = get_command(cmd_int)
        if meta is not None:
            cov.name = meta.name
            cov.tier = meta.tier
            cov.verified = meta.verified

    ledger = _ledger_commands(ledger_commands)
    l1_keys = [f"0x{c.cmd:x}" for c in ledger]

    def _missing_cov(key: str, c: Any) -> CommandCoverage:
        """命令在语料中完全缺席时，用账本元信息构造占位覆盖。"""
        return by_cmd.get(key) or CommandCoverage(
            command=key,
            name=getattr(c, "name", ""),
            tier=getattr(c, "tier", ""),
            verified=True,
        )

    missing_real = [
        _missing_cov(k, c)
        for k, c in zip(l1_keys, ledger, strict=True)
        if not by_cmd.get(k) or not by_cmd[k].has_real
    ]
    missing_valid_real = [
        _missing_cov(k, c)
        for k, c in zip(l1_keys, ledger, strict=True)
        if k in by_cmd and by_cmd[k].has_real and not by_cmd[k].has_valid_real
    ]

    market_gaps: dict[str, list[int]] = {}
    for c in ledger:
        key = f"0x{c.cmd:x}"
        cov_gap: CommandCoverage | None = by_cmd.get(key)
        if cov_gap is None or not cov_gap.has_real or c.cmd not in MARKET_SENSITIVE:
            continue
        gap = sorted({0, 1} - cov_gap.markets)
        if gap:
            market_gaps[key] = gap

    kline_real = by_cmd.get("0x52d")
    hints: dict[str, str] = {}
    for cov in missing_real:
        if not cov.command.startswith("0x"):
            continue
        plan = _CAPTURE_HINTS.get(int(cov.command, 16), "--plan all")
        hints[cov.command] = (
            f"python -m tstdx.tools.capture {plan} --i-understand-the-legal-boundary"
        )

    return {
        "root": str(root if root is not None else DEFAULT_GOLDEN_ROOT),
        "summary": {
            "total": len(samples),
            "real": sum(1 for s in samples if s.origin == ORIGIN_REAL),
            "synthetic": sum(1 for s in samples if s.origin == ORIGIN_SYNTHETIC),
            "unknown": sum(1 for s in samples if s.origin == ORIGIN_UNKNOWN),
        },
        "by_command": {
            k: {
                "name": v.name,
                "tier": v.tier,
                "verified": v.verified,
                "real": v.real,
                "short_real": v.short_real,
                "synthetic": v.synthetic,
                "unknown": v.unknown,
                "categories": sorted(v.categories),
                "markets": sorted(v.markets),
                "n_codes": len(v.codes),
            }
            for k, v in sorted(by_cmd.items())
        },
        "l1_verified": l1_keys,
        "missing_real": [
            {"command": c.command, "name": c.name, "tier": c.tier, "samples": c.total}
            for c in missing_real
        ],
        "missing_valid_real": [
            {"command": c.command, "name": c.name, "tier": c.tier, "samples": c.total}
            for c in missing_valid_real
        ],
        "suspect_short": suspect_short,
        "market_gaps": market_gaps,
        "kline_categories_real": sorted(kline_real.categories) if kline_real else [],
        "hints": hints,
    }


# --------------------------------------------------------------------------- #
# 报告与 CLI
# --------------------------------------------------------------------------- #
def _print_report(a: dict[str, Any]) -> None:
    s = a["summary"]
    print(f"Golden corpus audit -- {a['root']}")
    print(
        f"  total {s['total']}: real {s['real']} / synthetic {s['synthetic']}"
        f" / unknown {s['unknown']}"
    )
    print(f"  L1 verified: {', '.join(a['l1_verified']) or '(none)'}")
    print()
    print(f"  {'cmd':<8} {'real':>5} {'syn':>5} {'unk':>4}  {'name':<18} {'tier':<6} dims")
    for key, cov in a["by_command"].items():
        dims = []
        if cov["categories"]:
            dims.append(f"cat={cov['categories']}")
        if cov["markets"]:
            dims.append(f"mkt={cov['markets']}")
        print(
            f"  {key:<8} {cov['real']:>5} {cov['synthetic']:>5} {cov['unknown']:>4}"
            f"  {cov['name']:<18} {cov['tier']:<6} {' '.join(dims)}"
        )
    print()
    if a["missing_real"]:
        print("  [GATE] L1 verified commands missing REAL samples:")
        for m in a["missing_real"]:
            hint = a["hints"].get(m["command"], "")
            print(f"    - {m['command']} {m['name']:<16} capture: {hint}")
    else:
        print("  [GATE] all L1 verified commands have real samples (OK)")
    if a["market_gaps"]:
        print(
            "  [INFO] market-sensitive real-sample gaps: "
            + ", ".join(f"{k} missing {v}" for k, v in a["market_gaps"].items())
        )
    if a["suspect_short"]:
        parts = ", ".join(
            f"{k} {v[0]['size']}B<{v[0]['min']}B x{len(v)}" for k, v in a["suspect_short"].items()
        )
        print(f"  [WARN] suspect_short real samples (below per-command floor): {parts}")
    print(f"  [INFO] 0x052D real category coverage: {a['kline_categories_real'] or 'none'}")


def main(argv: Sequence[str] | None = None) -> int:
    setup_console()
    parser = argparse.ArgumentParser(
        prog="python -m tstdx.tools.golden_audit",
        description="Golden 语料 origin 分级 + L1 真实样本门禁",
    )
    parser.add_argument("--root", default=None, help="语料根目录（默认 tests/golden）")
    parser.add_argument("--json", action="store_true", help="JSON 输出")
    parser.add_argument("--gate", action="store_true", help="L1 命令缺 real 样本时退出码 1")
    parser.add_argument(
        "--require-markets",
        action="store_true",
        help="门禁追加：市场敏感 L1 命令的 real 样本须覆盖 market 0 与 1",
    )
    parser.add_argument(
        "--require-kline-categories",
        default=None,
        help="门禁追加：0x052D real 样本须覆盖的类别集合，如 0,4,9",
    )
    parser.add_argument(
        "--require-payloads",
        action="store_true",
        help="门禁追加：L1 命令的 real 样本须过 payload 单记录最小字节下限"
        "（suspect_short 的空响应样本不计为有效证据）",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    root = Path(args.root) if args.root else None
    a = audit(root)

    if args.json:
        print(json.dumps(a, ensure_ascii=False, indent=2))
    else:
        _print_report(a)

    if args.gate:
        if a["missing_real"]:
            print(
                f"[GATE FAIL] 存在缺 real 样本的 L1 verified 命令（{len(a['missing_real'])} 条）",
                file=sys.stderr,
            )
            return 1
        if args.require_markets and a["market_gaps"]:
            print(f"[GATE FAIL] 市场覆盖缺口: {a['market_gaps']}", file=sys.stderr)
            return 1
        if args.require_kline_categories:
            want = {int(x) for x in str(args.require_kline_categories).split(",") if x != ""}
            got = set(a["kline_categories_real"])
            if not want <= got:
                print(f"[GATE FAIL] K 线类别缺口: {sorted(want - got)}", file=sys.stderr)
                return 1
        if args.require_payloads and a["missing_valid_real"]:
            names = ", ".join(m["command"] for m in a["missing_valid_real"])
            print(
                f"[GATE FAIL] 以下 L1 命令的 real 样本全部未过 payload 下限: {names}",
                file=sys.stderr,
            )
            return 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
