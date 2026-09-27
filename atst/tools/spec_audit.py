# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""Spec ↔ 实现 审计工具（§4 / §20：协议全覆盖）。

将 PROTOCOL_SPEC/**/*.yaml 与当前代码实现进行对比审计。**逐个 YAML 遍历**
（不按 spec_id 去重，跨族同号命令不会被互相吃掉），自动探测的 draft 除外。

1. **Ledger 检查**：命令号是否登记在该族自己的账本里？
   行情族查 ``commands.py``；交易族查 ``atst/trade/constants.py``（常量值与
   ``CMD_NAMES`` 都要对得上）。
2. **Parser / 编解码检查**：行情数据命令须在 ``PARSERS`` 注册；**无载荷控制帧**
   须同时显式声明 ``response.parse: false`` 与空响应结构才判为免注册（判定源自
   spec 内容，不是硬编码白名单）；交易族须在 ``atst/trade/frames.py`` 有该命令
   的编解码锚点。
3. **Golden 样本检查**：引用的 golden 样本文件是否存在于磁盘？
4. **覆盖率统计**：``coverage_summary()`` 返回汇总数据，含 ``uncovered`` 明细。

命令行::

    python -m atst.tools.spec_audit              # 打印表格
    python -m atst.tools.spec_audit --json       # JSON 输出
    python -m atst.tools.spec_audit --strict     # 覆盖率 < 100% 时返回 1

设计要点
--------
* **零依赖**：YAML 解析由 :mod:`atst.tools._yaml_min` 完成。
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
from .codegen import load_spec

__all__ = [
    "SpecAuditResult",
    "audit_spec",
    "audit_all",
    "spec_files",
    "draft_spec_files",
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
    #: 命令号是否存在于该族自己的账本（行情族 = commands.py，交易族 = trade.constants）
    in_ledger: bool = False
    #: 是否有注册的解析器（无载荷控制帧与交易族帧走各自证据，见 ``notes``）
    has_parser: bool = False
    #: 引用的 golden 样本文件是否全部存在
    golden_samples_ok: bool = True
    #: 实现所在族："quotation"（commands.py 账本 + PARSERS）或 "trade"
    plane: str = "quotation"
    #: spec 显式声明"不解析该响应"（``response.parse: false``）且响应无记录结构，
    #: 因此不要求 PARSERS 注册
    control_frame: bool = False
    #: 审计备注（问题描述）
    notes: list[str] = field(default_factory=list)

    @property
    def covered(self) -> bool:
        """账本已登记，且实现证据齐备（解析器 / 控制帧 / 交易族编解码）。"""
        return self.in_ledger and (self.has_parser or self.control_frame)


# --------------------------------------------------------------------------- #
# 审计
# --------------------------------------------------------------------------- #
def _get_project_root() -> Path:
    """返回项目根目录（atst/ 的父目录）。"""
    return Path(__file__).resolve().parents[2]


def _family_to_constant(family: str) -> str | None:
    """将 spec family 字符串映射为 commands.py 的 Family 常量。

    ``None`` 表示该族不在行情账本里（TRADE 是一例）——由调用方改走该族自己的
    实现锚点。这里**不做静默回落**：早先未知 family 回落到 STANDARD，于是拿
    7709 账本去查交易命令，把"查错了账本"报告成"命令没登记"。
    """
    from atst.protocol.commands import Family

    mapping = {
        "7709": Family.STANDARD,
        "7727": Family.EXTENDED,
        "MAC": Family.MAC,
        "F10": Family.F10,
        "GOODS": Family.GOODS,
    }
    return mapping.get(family)


#: 非行情族的实现锚点。交易命令的账本与帧编解码都在 ``atst.trade`` 自己的
#: 模块里（模拟器回路验证，``status: draft``），不走 ``commands.py`` / PARSERS。
TRADE_FAMILY = "TRADE"
_TRADE_LEDGER_MODULE = "atst.trade.constants"
_TRADE_CODEC_MODULE = "atst.trade.frames"
#: 命令号 → (账本常量, 帧编解码入口)
_TRADE_IMPLEMENTATION: dict[int, tuple[str, str]] = {
    0x0001: ("CMD_LOGIN", "build_login_body"),
    0x0002: ("CMD_HEARTBEAT", "parse_heartbeat_response"),
    0x0003: ("CMD_LOGOUT", "build_request"),
    0x0100: ("CMD_QUERY", "build_query_body"),
    0x1000: ("CMD_SEND_ORDER", "build_order_body"),
    0x1001: ("CMD_CANCEL_ORDER", "build_cancel_body"),
}


def _import_module(module: str) -> Any:
    """导入模块，失败返回 ``None``（审计工具不得因缺失实现而崩溃）。"""
    import importlib

    try:
        return importlib.import_module(module)
    except ImportError:
        return None


def _attr_exists(module: str, attr: str) -> bool:
    """模块属性是否真实存在——证据缺失即门禁缺失。"""
    mod = _import_module(module)
    return mod is not None and hasattr(mod, attr)


def _trade_ledger_ok(cmd_int: int, name: str) -> str | None:
    """交易族账本校验：常量存在且取值与命令号、名字都对上。"""
    entry = _TRADE_IMPLEMENTATION.get(cmd_int)
    if entry is None:
        return f"TRADE 族无命令号 0x{cmd_int:04X} 的账本锚点"
    const, _codec = entry
    mod = _import_module(_TRADE_LEDGER_MODULE)
    if mod is None or not hasattr(mod, const):
        return f"{_TRADE_LEDGER_MODULE}.{const} 不存在"
    value = getattr(mod, const)
    if value != cmd_int:
        return f"{_TRADE_LEDGER_MODULE}.{const}={value!r} 与 spec 0x{cmd_int:04X} 不符"
    if getattr(mod, "CMD_NAMES", {}).get(cmd_int) != name:
        return f"CMD_NAMES[0x{cmd_int:04X}] 与 spec name {name!r} 不符"
    return None


def _trade_codec_ok(cmd_int: int) -> str | None:
    """交易族帧编解码校验：``atst.trade.frames`` 里确有该命令的入口符号。"""
    entry = _TRADE_IMPLEMENTATION.get(cmd_int)
    if entry is None:  # pragma: no cover - 账本校验已先报错
        return f"TRADE 族无命令号 0x{cmd_int:04X} 的编解码锚点"
    _const, codec = entry
    if not _attr_exists(_TRADE_CODEC_MODULE, codec):
        return f"{_TRADE_CODEC_MODULE}.{codec} 不存在"
    return None


def is_payloadless(spec: dict[str, Any]) -> bool:
    """spec 自声明"本包不解析该响应"，且声明的响应确实没有记录结构（控制帧）。

    判定取自 spec 自己的 ``response`` 段，而不是硬编码命令号清单。两条依据缺一
    不免：**未声明** ``fields`` / ``record_size`` 不等于声明为空，**没有写下**
    ``parse: false`` 也不等于免解析。

    ``parse: false`` 不是装饰：2026-09-26 真机探针实测 0x0004 的响应**有字节**
    （7/7 可达主站各回 10 字节 ``0000000000003c283501``），只是没有记录结构。
    把依据从"响应看起来为空"换成"spec 主动声明不解析"之后，"响应体为空"这类
    从未被核对过的主张就再也换不到免注册（台账 F-20）。
    """
    response = spec.get("response")
    if not isinstance(response, dict):
        return False
    if response.get("parse") is not False:
        return False
    if "fields" not in response or "record_size" not in response:
        return False
    return not response["fields"] and not response.get("header") and not response["record_size"]


def _ensure_parsers_loaded() -> None:
    """确保所有解析器已加载（触发 ``@register_parser`` 注册）。"""
    # 导入 parsers 包会触发所有注册
    import atst.protocol.parsers  # noqa: F401


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
    from atst.protocol.commands import get_command
    from atst.protocol.registry import PARSERS

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

    if family_str is None and family.upper() != TRADE_FAMILY:
        notes.append(f"未知 family {family!r}：既不在行情账本映射里，也不是交易族")

    control_frame = family_str is not None and is_payloadless(spec)

    if family_str is None and family.upper() == TRADE_FAMILY:
        # 交易族：账本与帧编解码都在 atst.trade 自己那一层
        plane = "trade"
        ledger_err = _trade_ledger_ok(cmd_int, name)
        in_ledger = ledger_err is None
        if ledger_err:
            notes.append(ledger_err)
        codec_err = _trade_codec_ok(cmd_int)
        has_parser = codec_err is None
        if codec_err:
            notes.append(codec_err)
        else:
            notes.append(f"编解码锚点：{_TRADE_CODEC_MODULE}.{_TRADE_IMPLEMENTATION[cmd_int][1]}")
    else:
        plane = "quotation"
        # 检查 ledger
        cmd = get_command(cmd_int, family_str) if family_str is not None else None
        in_ledger = cmd is not None
        if not in_ledger:
            notes.append(f"Command 0x{cmd_int:04X} not found in commands.py ledger")

        # 检查 parser
        _ensure_parsers_loaded()
        has_parser = family_str is not None and (family_str, cmd_int) in PARSERS
        if not has_parser:
            if control_frame:
                notes.append(
                    "无载荷控制帧：spec 显式声明 parse:false，且响应 fields/header/record_size 皆空"
                )
            else:
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
        plane=plane,
        control_frame=control_frame,
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
    root = _spec_root(spec_dir)
    results: list[SpecAuditResult] = []
    for spec_file in _spec_files(root):
        try:
            results.append(audit_spec(spec_file))
        except (FileNotFoundError, ValueError) as exc:
            results.append(
                SpecAuditResult(
                    spec_id="",
                    name="",
                    spec_file=spec_file,
                    notes=[f"spec 无法审计: {exc}"],
                )
            )
    return results


def _spec_root(spec_dir: str) -> Path:
    root = Path(spec_dir)
    if not root.is_absolute():
        root = _get_project_root() / spec_dir
    return root


def _rel_posix(path: Path) -> str:
    try:
        return Path(path.resolve().relative_to(_get_project_root())).as_posix()
    except ValueError:
        return path.as_posix()


def _is_draft_probe(spec_file: Path) -> bool:
    """自动探测产物（``_sniffer`` / ``UNKNOWN`` 下的 draft）不参与账本分母。"""
    parts = {p.lower() for p in spec_file.parts}
    return "_sniffer" in parts or "unknown" in parts


def spec_files(spec_dir: str = "PROTOCOL_SPEC") -> list[str]:
    """纳入审计分母的 spec 文件（以仓库根为基准的相对 POSIX 路径）。

    这里逐个 YAML 遍历，而不是复用 :func:`atst.tools.codegen.load_all_specs`：
    后者以 ``spec_id`` 为键，跨族同号会互相覆盖（实测 ``TRADE/0x0001`` 吃掉
    ``F10/0x0001``、``TRADE/0x0100`` 吃掉 ``7727/0x0100``），分母静默变小比
    红灯更危险——被吃掉的那两条命令再也不会被审计。
    """
    root = _spec_root(spec_dir)
    if not root.is_dir():
        return []
    return [
        _rel_posix(path) for path in sorted(root.glob("**/*.yaml")) if not _is_draft_probe(path)
    ]


def draft_spec_files(spec_dir: str = "PROTOCOL_SPEC") -> list[str]:
    """被排除的自动探测 draft（供报告透明展示，不影响严格判定）。"""
    root = _spec_root(spec_dir)
    if not root.is_dir():
        return []
    return [_rel_posix(path) for path in sorted(root.glob("**/*.yaml")) if _is_draft_probe(path)]


def _spec_files(root: Path) -> list[str]:
    """spec 文件清单，剔除无 ``spec_id`` 的自动探测 draft。"""
    out: list[str] = []
    for spec_file in spec_files(str(root)):
        try:
            spec = load_spec(spec_file)
        except (FileNotFoundError, ValueError):
            out.append(spec_file)  # 解析失败也要进分母：让 audit_spec 报出来
            continue
        if spec.get("spec_id"):
            out.append(spec_file)
    return out


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
        ``{total_specs, in_ledger, has_parser, has_golden, control_frames,
        trade_plane, covered, uncovered, coverage_pct}``。
    """
    if results is None:
        results = audit_all(spec_dir)
    total = len(results)
    in_ledger = sum(1 for r in results if r.in_ledger)
    has_parser = sum(1 for r in results if r.has_parser)
    has_golden = sum(1 for r in results if r.golden_samples_ok)
    control_frames = sum(1 for r in results if r.control_frame)
    trade_plane = sum(1 for r in results if r.plane == "trade")

    # 覆盖 = 命令号在其族的账本里登记，且实现证据齐备：
    # 行情数据命令 → PARSERS 注册；无载荷控制帧 → spec 显式声明 parse:false 且
    # 响应无记录结构；
    # 交易族 → atst.trade.frames 的编解码锚点。
    covered_specs = [r for r in results if r.covered]
    covered = len(covered_specs)

    return {
        "total_specs": total,
        "in_ledger": in_ledger,
        "has_parser": has_parser,
        "has_golden": has_golden,
        "control_frames": control_frames,
        "trade_plane": trade_plane,
        "covered": covered,
        "uncovered": [f"{r.spec_id} {r.name}".strip() for r in results if not r.covered],
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
        if r.plane == "trade":
            parser = "T-codec" if r.has_parser else "MISS"
        elif r.control_frame:
            parser = "n/a" if r.in_ledger else "MISS"
        else:
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
        f"Control frames: {summary['control_frames']}  "
        f"Trade plane: {summary['trade_plane']}  "
        f"Coverage: {summary['coverage_pct']}%"
    )
    if summary["uncovered"]:
        print("Uncovered: " + ", ".join(summary["uncovered"]))


def _result_to_dict(r: SpecAuditResult) -> dict[str, Any]:
    """将审计结果转为 JSON 可序列化字典。"""
    return {
        "spec_id": r.spec_id,
        "name": r.name,
        "spec_file": r.spec_file,
        "in_ledger": r.in_ledger,
        "has_parser": r.has_parser,
        "golden_samples_ok": r.golden_samples_ok,
        "plane": r.plane,
        "control_frame": r.control_frame,
        "covered": r.covered,
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
        prog="python -m atst.tools.spec_audit",
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
