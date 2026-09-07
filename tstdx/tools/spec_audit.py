# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""Spec ↔ 实现 审计工具（§4 / §20：协议全覆盖）。

将 PROTOCOL_SPEC/*.yaml 与当前代码实现进行对比审计：

1. **Ledger 检查**：spec_id 是否存在于 ``commands.py`` 账本？
2. **Parser 检查**：是否有注册的解析器？
3. **Golden 样本检查**：引用的 golden 样本文件是否存在于磁盘？
4. **覆盖率统计**：``coverage_summary()`` 返回汇总数据。

命令行::

    python -m tstdx.tools.spec_audit              # 打印表格
    python -m tstdx.tools.spec_audit --json       # JSON 输出
    python -m tstdx.tools.spec_audit --strict     # 覆盖率 < 100% 时返回 1

设计要点
--------
* **零依赖**：YAML 解析由 :mod:`tstdx.tools._yaml_min` 完成。
* **Windows-safe**：使用 ``pathlib``，UTF-8 编码。
* **CI 友好**：``--strict`` 模式在覆盖率不足时返回非零退出码。
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ._console import setup_console
from .codegen import load_all_specs, load_spec

__all__ = [
    "SpecAuditResult",
    "audit_spec",
    "audit_all",
    "coverage_summary",
    "main",
]


# --------------------------------------------------------------------------- #
# 结果结构
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class SpecAuditResult:
    """单个 spec 的审计结果。"""

    spec_id: str
    name: str
    spec_file: str
    #: 命令号是否存在于 ``commands.py`` 账本
    in_ledger: bool = False
    #: 是否有注册的解析器
    has_parser: bool = False
    #: 引用的 golden 样本文件是否全部存在
    golden_samples_ok: bool = True
    #: 审计备注（问题描述）
    notes: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------- #
# 审计
# --------------------------------------------------------------------------- #
def _get_project_root() -> Path:
    """返回项目根目录（tstdx/ 的父目录）。"""
    return Path(__file__).resolve().parents[2]


def _family_to_constant(family: str) -> str:
    """将 spec family 字符串映射为 commands.py 的 Family 常量。"""
    from tstdx.protocol.commands import Family

    mapping = {
        "7709": Family.STANDARD,
        "7727": Family.EXTENDED,
        "MAC": Family.MAC,
        "F10": Family.F10,
        "GOODS": Family.GOODS,
    }
    return mapping.get(family, Family.STANDARD)


def _ensure_parsers_loaded() -> None:
    """确保所有解析器已加载（触发 ``@register_parser`` 注册）。"""
    # 导入 parsers 包会触发所有注册
    import tstdx.protocol.parsers  # noqa: F401


def audit_spec(spec_path: str) -> SpecAuditResult:
    """审计单个 spec 文件与实现的对比。

    Parameters
    ----------
    spec_path : str
        spec 文件路径。

    Returns
    -------
    SpecAuditResult
        审计结果。
    """
    from tstdx.protocol.commands import get_command
    from tstdx.protocol.registry import PARSERS

    spec = load_spec(spec_path)
    spec_id = spec.get("spec_id", "")
    name = spec.get("name", "")
    family = spec.get("family", "7709")

    notes: list[str] = []

    # 解析命令号
    try:
        cmd_int = int(spec_id, 16)
    except (ValueError, TypeError):
        cmd_int = -1
        notes.append(f"Invalid spec_id: {spec_id}")

    # 映射 family
    family_str = _family_to_constant(family)

    # 检查 ledger
    cmd = get_command(cmd_int, family_str)
    in_ledger = cmd is not None
    if not in_ledger:
        notes.append(f"Command 0x{cmd_int:04X} not found in commands.py ledger")

    # 检查 parser
    _ensure_parsers_loaded()
    has_parser = (family_str, cmd_int) in PARSERS
    if not has_parser:
        notes.append(f"No registered parser for 0x{cmd_int:04X}")

    # 检查 golden 样本
    golden_samples = spec.get("golden_samples", [])
    golden_ok = True
    missing_samples: list[str] = []
    project_root = _get_project_root()

    if golden_samples:
        for sample_path in golden_samples:
            full_path = project_root / sample_path
            if not full_path.exists():
                golden_ok = False
                missing_samples.append(sample_path)
    else:
        # 没有 golden 样本不算失败，但记为信息性备注
        notes.append("No golden_samples referenced")

    if not golden_ok:
        notes.append(f"Missing golden samples: {missing_samples}")

    return SpecAuditResult(
        spec_id=spec_id,
        name=name,
        spec_file=spec_path,
        in_ledger=in_ledger,
        has_parser=has_parser,
        golden_samples_ok=golden_ok,
        notes=notes,
    )


def audit_all(spec_dir: str = "PROTOCOL_SPEC") -> list[SpecAuditResult]:
    """审计所有 spec。

    Parameters
    ----------
    spec_dir : str
        spec 根目录（默认 ``PROTOCOL_SPEC``）。

    Returns
    -------
    list[SpecAuditResult]
        每个 spec 一条审计结果。
    """
    specs = load_all_specs(spec_dir)
    results: list[SpecAuditResult] = []
    for spec_id, spec in sorted(specs.items()):
        # 构造 spec 文件路径
        spec_file = spec.get("_file", "")
        if not spec_file:
            # 从 spec_id 和 family 推断路径
            family = spec.get("family", "7709")
            spec_file = f"PROTOCOL_SPEC/{family}/"
            results.append(
                SpecAuditResult(
                    spec_id=spec_id,
                    name=spec.get("name", ""),
                    spec_file=spec_file,
                    notes=["_file metadata missing in spec"],
                )
            )
            continue
        try:
            result = audit_spec(spec_file)
            results.append(result)
        except (FileNotFoundError, ValueError) as exc:
            results.append(
                SpecAuditResult(
                    spec_id=spec_id,
                    name=spec.get("name", ""),
                    spec_file=spec_file,
                    notes=[str(exc)],
                )
            )
    return results


def coverage_summary(
    results: list[SpecAuditResult] | None = None, spec_dir: str = "PROTOCOL_SPEC"
) -> dict[str, Any]:
    """返回覆盖率统计。

    Parameters
    ----------
    results:
        已有的审计结果（传入则复用，避免 ``audit_all`` 重复解析全部
        spec —— CLI 一次运行会统计 3 处，重复解析纯浪费）。
    spec_dir : str
        spec 根目录（默认 ``PROTOCOL_SPEC``；仅 ``results=None`` 时使用）。

    Returns
    -------
    dict
        ``{total_specs, in_ledger, has_parser, has_golden,
        covered, coverage_pct}``。
    """
    if results is None:
        results = audit_all(spec_dir)
    total = len(results)
    in_ledger = sum(1 for r in results if r.in_ledger)
    has_parser = sum(1 for r in results if r.has_parser)
    has_golden = sum(1 for r in results if r.golden_samples_ok)

    # 覆盖 = 同时在 ledger 中有注册 parser
    covered = sum(1 for r in results if r.in_ledger and r.has_parser)

    return {
        "total_specs": total,
        "in_ledger": in_ledger,
        "has_parser": has_parser,
        "has_golden": has_golden,
        "covered": covered,
        "coverage_pct": round(covered / total * 100, 1) if total else 0.0,
    }


# --------------------------------------------------------------------------- #
# CLI 输出
# --------------------------------------------------------------------------- #
def _print_table(results: list[SpecAuditResult], summary: dict[str, Any] | None = None) -> None:
    """以人类可读表格格式输出审计结果。

    ``summary`` 传入则复用既有统计（避免为打印汇总行再跑一遍 audit_all）。
    """
    header = f"{'Spec ID':<12} {'Name':<22} {'Ledger':<8} {'Parser':<8} {'Golden':<8} {'Notes'}"
    print(header)
    print("-" * len(header))

    for r in results:
        ledger = "OK" if r.in_ledger else "MISS"
        parser = "OK" if r.has_parser else "MISS"
        golden = "OK" if r.golden_samples_ok else "MISS"

        note_str = "; ".join(r.notes[:2]) if r.notes else ""
        if len(note_str) > 40:
            note_str = note_str[:37] + "..."

        print(f"{r.spec_id:<12} {r.name:<22} {ledger:<8} {parser:<8} {golden:<8} {note_str}")

    # 汇总（结果缓存：与调用方共享同一份 results）
    if summary is None:
        summary = coverage_summary(results)
    print()
    print(
        f"Total: {summary['total_specs']}  "
        f"In Ledger: {summary['in_ledger']}  "
        f"Has Parser: {summary['has_parser']}  "
        f"Has Golden: {summary['has_golden']}  "
        f"Coverage: {summary['coverage_pct']}%"
    )


def _result_to_dict(r: SpecAuditResult) -> dict[str, Any]:
    """将审计结果转为 JSON 可序列化字典。"""
    return {
        "spec_id": r.spec_id,
        "name": r.name,
        "spec_file": r.spec_file,
        "in_ledger": r.in_ledger,
        "has_parser": r.has_parser,
        "golden_samples_ok": r.golden_samples_ok,
        "notes": r.notes,
    }


# --------------------------------------------------------------------------- #
# CLI 入口
# --------------------------------------------------------------------------- #
def main(argv: list[str] | None = None) -> int:
    """CLI 入口。

    Parameters
    ----------
    argv : list[str] | None
        命令行参数（默认 ``sys.argv[1:]``）。

    Returns
    -------
    int
        退出码：0 = 成功；1 = ``--strict`` 且覆盖率 < 100%；2 = 用法错误。
    """
    setup_console()  # Windows GBK 控制台防乱码（UTF-8 + replace）
    parser = argparse.ArgumentParser(
        prog="python -m tstdx.tools.spec_audit",
        description="审计 PROTOCOL_SPEC 与实现的覆盖率",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="JSON 格式输出",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="CI 模式：覆盖率 < 100% 时返回非零退出码",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    # 结果缓存：一次运行只解析全部 spec 一遍（audit_all 一次，
    # summary / 表格行复用同一份 results —— 原先重复解析 3 遍）
    results = audit_all()
    summary = coverage_summary(results)

    if args.json:
        payload = {
            "results": [_result_to_dict(r) for r in results],
            "summary": summary,
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        _print_table(results, summary)

    # 严格模式：覆盖率 < 100% 时返回 1
    if args.strict and summary["coverage_pct"] < 100.0:
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
