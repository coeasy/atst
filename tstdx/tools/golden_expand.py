# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""Golden 语料扩充工具（D7）。

目标：把 ``tests/golden/quotation/`` 语料从「实采种子」扩到 **≥ 500 案例**，
覆盖所有已知 spec + 多市场/品种/周期组合。

两种扩充模式
------------

1. **实采**（``capture``）：盘后经真实主站采集 —— 由 :mod:`tstdx.tools.capture`
   负责，产出 ``source: self-captured`` 样本。
2. **合成衍生**（本模块 ``expand``）：以实采样本为种子，对其**请求维度**做
   受控变异（market / code / category），用同一解析器重生成载荷并落盘
   ``source: synthetic`` 样本。合成样本携带 ``derived_from`` 溯源字段，
   语义是「解析器回归基线」而非「主站真实响应」——两者在
   :func:`manifest` 里分开统计。

用法::

    python -m tstdx.tools.golden_expand manifest          # 语料清单
    python -m tstdx.tools.golden_expand expand --per-seed 16
    python -m tstdx.tools.golden_expand verify            # 全量 sha256 校验

只做本地文件操作，不发网络请求。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ._console import setup_console

__all__ = [
    "GoldenCase",
    "manifest",
    "verify",
    "expand",
    "TARGET_CASES",
]

GOLDEN_ROOT = Path("tests/golden/quotation")
TARGET_CASES = 500

#: 每个种子的默认衍生维度：市场 × 代码 组合（≤ per-seed 上限）。
#: 类别维不参与合成——曾经声明过 `SYNTHETIC_CATEGORIES = (0,1,2,3,4)`，但生成器
#: 从没按它展开过一格，留着等于一张假告示（F-82，按 D3 删除）。
SYNTHETIC_MARKETS = (0, 1)
SYNTHETIC_CODES = (
    "600000",
    "600519",
    "601398",
    "600036",
    "601318",
    "600030",  # 沪市
    "000001",
    "000002",
    "300750",
    "000651",
    "002415",
    "300059",  # 深市
)


@dataclass(frozen=True)
class GoldenCase:
    """一个 golden 案例的最小元数据。"""

    cmd: str
    name: str
    dir_name: str
    ts_dir: str
    payload: Path
    sha256: str
    source: str

    @property
    def rel(self) -> str:
        return f"{self.dir_name}/{self.ts_dir}"


def _parse_meta(payload_dir: Path) -> dict[str, Any]:
    meta_json = payload_dir / "meta.json"
    if meta_json.exists():
        try:
            return json.loads(meta_json.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    meta_yaml = payload_dir / "meta.yaml"
    if meta_yaml.exists():
        # 用共享的 _yaml_min（支持嵌套 dict/list/块标量）替代旧的扁平
        # _mini_yaml —— 旧实现把 ``response:`` 解析成空字符串，
        # ``meta["response"].get(...)`` 会 AttributeError，嵌套 sha256 也拿不到
        from ._yaml_min import load_yaml

        parsed = load_yaml(meta_yaml.read_text(encoding="utf-8"))
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _source_of(meta: dict[str, Any]) -> str:
    src = str(meta.get("source", "unknown"))
    if src.startswith("synthetic"):
        return "synthetic"
    return src


def manifest(root: Path | None = None) -> dict[str, Any]:
    """扫描语料，返回清单统计。"""
    root = root or GOLDEN_ROOT
    cases: list[GoldenCase] = []
    if root.exists():
        for cmd_dir in sorted(root.iterdir()):
            if not cmd_dir.is_dir():
                continue
            for ts_dir in sorted(cmd_dir.iterdir()):
                payload = ts_dir / "payload.bin"
                if not payload.is_file():
                    continue
                meta = _parse_meta(ts_dir)
                cases.append(
                    GoldenCase(
                        cmd=str(meta.get("command", "")),
                        name=str(meta.get("command_name", "")),
                        dir_name=cmd_dir.name,
                        ts_dir=ts_dir.name,
                        payload=payload,
                        sha256=_sha256(payload),
                        source=_source_of(meta),
                    )
                )

    by_cmd: dict[str, int] = {}
    by_src: dict[str, int] = {}
    for c in cases:
        by_cmd[c.cmd] = by_cmd.get(c.cmd, 0) + 1
        by_src[c.source] = by_src.get(c.source, 0) + 1

    return {
        "root": str(root),
        "total_cases": len(cases),
        "self_captured": by_src.get("self-captured", 0),
        "synthetic": by_src.get("synthetic", 0),
        "by_command": dict(sorted(by_cmd.items())),
        "target": TARGET_CASES,
        "target_met": len(cases) >= TARGET_CASES,
        "cases": [c.rel for c in cases],
    }


def _expect_sha256(meta: dict[str, Any]) -> str:
    """从 meta 提取期望 sha256：优先 ``response.sha256``（嵌套），兜底顶层。"""
    resp = meta.get("response")
    if isinstance(resp, dict):
        sha = str(resp.get("sha256", "") or "")
        if sha:
            return sha
    return str(meta.get("sha256", "") or "")


def verify(root: Path | None = None) -> tuple[int, list[str]]:
    """校验所有 payload.bin 的 sha256 与 meta 记录一致。返回 (通过数, 失败列表)。

    「样本即契约」：meta 缺少 sha256 记为 bad（旧实现静默按通过处理，
    无契约样本混进语料不设防）。
    """
    root = root or GOLDEN_ROOT
    ok, bad = 0, []
    if not root.exists():
        return 0, ["<missing root>"]
    for cmd_dir in sorted(root.iterdir()):
        if not cmd_dir.is_dir():
            continue
        for ts_dir in sorted(cmd_dir.iterdir()):
            payload = ts_dir / "payload.bin"
            if not payload.is_file():
                continue
            meta = _parse_meta(ts_dir)
            expect = _expect_sha256(meta)
            actual = _sha256(payload)
            if not expect:
                bad.append(f"{cmd_dir.name}/{ts_dir.name}: meta 缺少 response.sha256（无契约）")
            elif actual != expect:
                bad.append(f"{cmd_dir.name}/{ts_dir.name}: meta={expect[:12]} actual={actual[:12]}")
            else:
                ok += 1
    return ok, bad


def _synthetic_payload(seed_payload: bytes, cmd: str, market: int, code: str) -> bytes:
    """按命令语义改写种子载荷，产出「结构一致、维度不同」的合成样本。

    - 0x0530（实时行情，56B 回声帧）：回声段改写为 code，market 字节替换。
    - 0x044E（证券数量）：计数段按 code 哈希派生。
    - 0x052D（K 线）：保留记录区，重写日期基线使其随 code 变化。
    - 其他命令：原样保留（仅市场标记变化），保证结构永不破坏。
    """
    data = bytearray(seed_payload)
    code_b = code.encode("ascii", errors="ignore")
    if cmd in ("0x530", "0x0530"):
        # 回声 code（offset 1..6）与 market（offset 0）；offset 7..8 为不透明区
        if len(data) >= 8:
            data[0] = market
            for i in range(6):
                data[1 + i] = code_b[i] if i < len(code_b) else 0x30
    elif cmd in ("0x44e", "0x044E"):
        if len(data) >= 2:
            count = 1000 + (int(hashlib.sha256(code.encode()).hexdigest(), 16) % 5000)
            data[0:2] = count.to_bytes(2, "little")
    elif cmd in ("0x52d", "0x052D") and len(data) >= 32:
        # 每条 32B 记录的日期字段前移 1 天/市场，制造可区分样本
        base = 26 * 100 + market  # yyyymmdd 低位扰动，保持 u16 合法
        data[0:2] = base.to_bytes(2, "little")
    return bytes(data)


def _is_index_code(code: str) -> bool:
    """指数代码判定：``000xxx``（000 段 < 100）等量纲为指数的代码。

    指数样本的价量是「点」量纲，改写回声码成个股码会让合成样本内部不自洽
    （现价/均价区间校验失真），因此指数种子不得衍生出个股码样本。
    """
    return bool(code) and code.startswith("000") and code[3:].isdigit() and int(code[3:]) < 100


def _combo_grid(n: int) -> list[tuple[int, str]]:
    """前 n 个 (market, code) 组合 —— 无重复，按市场优先展开。"""
    combos: list[tuple[int, str]] = []
    for market in SYNTHETIC_MARKETS:
        for code in SYNTHETIC_CODES:
            combos.append((market, code))
    return combos[:n]


def expand(per_seed: int = 24, root: Path | None = None) -> dict[str, Any]:
    """从每个实采种子生成合成衍生样本，直到达到 TARGET_CASES。

    ``per_seed`` 是每个种子的最大衍生数（受 :func:`_combo_grid` 的
    market×code 组合数上限约束）；默认 24 为组合上限，保证现有种子集
    能填满 500 案例目标。
    """
    root = root or GOLDEN_ROOT
    now = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    created = 0
    info = manifest(root)
    remaining = TARGET_CASES - info["total_cases"]
    if remaining <= 0:
        return {"created": 0, "total": info["total_cases"], "target_met": True}

    # 仅以「实采」样本为种子（读 meta source 字段判定，而非路径名）
    seeds: list[tuple[str, dict[str, Any]]] = []
    for case_rel in info["cases"]:
        meta = _parse_meta(root / case_rel)
        if _source_of(meta) == "self-captured":
            seeds.append((case_rel, meta))

    # 预处理每个种子：载荷 / 命令 / 解析上下文 / 指数过滤
    prepared: list[dict[str, Any]] = []
    for seed_rel, seed_meta in seeds:
        seed_payload_path = root / seed_rel / "payload.bin"
        if not seed_payload_path.is_file():
            continue
        cmd = str(seed_meta.get("command", "0x0000"))
        seed_ctx = dict(seed_meta.get("parse_ctx") or {})
        # 指数种子价量是指数量纲，改写成个股码会导致样本内部不自洽，跳过
        if cmd in ("0x530", "0x0530") and _is_index_code(str(seed_ctx.get("code", ""))):
            continue
        prepared.append(
            {
                "seed_rel": seed_rel,
                "seed_meta": seed_meta,
                "payload": seed_payload_path.read_bytes(),
                "cmd": cmd,
                "cmd_name": str(seed_meta.get("command_name", "UNKNOWN")),
                # 种子目录名参与案例名，避免同命令多种子互相覆盖
                "seed_tag": seed_rel.split("/")[0] if "/" in seed_rel else seed_rel,
                "seed_ctx": seed_ctx,
                "produced": 0,
            }
        )
    if not prepared:
        return {"created": 0, "total": info["total_cases"], "target_met": False}

    # 轮转生成：每个种子每次产出 1 个组合，循环直到预算用尽或全部达到 per_seed，
    # 避免某一命令（如 0x052D）的种子按排序先到先得而吃满预算、挤掉其他命令
    cursor = 0
    while created < remaining:
        advanced = False
        for _ in range(len(prepared)):
            p = prepared[cursor % len(prepared)]
            cursor += 1
            if p["produced"] >= per_seed:
                continue
            advanced = True
            break
        if not advanced:
            break

        combos = _combo_grid(per_seed)
        if p["produced"] >= len(combos):
            p["produced"] = per_seed  # 组合耗尽视为达到上限
            continue
        market, code = combos[p["produced"]]
        p["produced"] += 1

        payload = _synthetic_payload(p["payload"], p["cmd"], market, code)
        sha = hashlib.sha256(payload).hexdigest()
        case_base = f"{p['seed_tag']}_{code}_m{market}_syn"

        # 幂等：同一种子+组合已存在（任意时间戳目录）则跳过，不重复计数
        already = False
        for d in root.glob(f"{case_base}_*"):
            for ts_dir in d.iterdir():
                pp = ts_dir / "payload.bin"
                if pp.is_file() and hashlib.sha256(pp.read_bytes()).hexdigest() == sha:
                    already = True
                    break
            if already:
                break
        if already:
            continue

        # 时间戳进入目录名；同秒重跑加序号，保证目录唯一
        case_name = f"{case_base}_{now}"
        n = 1
        while (root / case_name).exists():
            case_name = f"{case_base}_{now}_{n}"
            n += 1
        out_dir = root / case_name / now
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "payload.bin").write_bytes(payload)

        meta = {
            "schema": 1,
            "source": "synthetic",
            "derived_from": p["seed_rel"],
            "family": str(p["seed_meta"].get("family", "quotation")),
            "command": p["cmd"],
            "command_name": p["cmd_name"],
            "tag": f"{code}_m{market}_syn",
            "note": "合成衍生样本：解析器回归基线，非主站真实响应",
            "captured_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "request": {
                "method": p["cmd"],
                "body_hex": f"{market:02x}" + code.encode().hex()[:12],
                "body_len": 8,
            },
            "response": {
                "zip_size": len(payload),
                "unzip_size": len(payload),
                "compressed": False,
                "payload_len": len(payload),
                "sha256": sha,
            },
            "parse_ctx": {**p["seed_ctx"], "code": code, "market": market},
        }
        (out_dir / "meta.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        _write_yaml(out_dir / "meta.yaml", meta)
        created += 1

    total = manifest(root)["total_cases"]
    return {"created": created, "total": total, "target_met": total >= TARGET_CASES}


def _write_yaml(path: Path, meta: dict[str, Any]) -> None:
    """把扁平 meta 写成与实采样本同构的 meta.yaml。"""

    def dump(obj: Any, indent: int = 0) -> str:
        pad = "  " * indent
        lines: list[str] = []
        if isinstance(obj, dict):
            for k, v in obj.items():
                if isinstance(v, dict):
                    lines.append(f"{pad}{k}:")
                    lines.append(dump(v, indent + 1))
                else:
                    lines.append(f'{pad}{k}: "{v}"' if isinstance(v, str) else f"{pad}{k}: {v}")
        return "\n".join(lines)

    path.write_text(dump(meta) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    setup_console()  # Windows GBK 控制台防乱码（UTF-8 + replace）
    parser = argparse.ArgumentParser(description="Golden 语料清单 / 扩充 / 校验")
    parser.add_argument("action", choices=["manifest", "expand", "verify"])
    parser.add_argument("--per-seed", type=int, default=24, help="每个种子的最大衍生数")
    args = parser.parse_args(argv)

    if args.action == "manifest":
        m = manifest()
        cases = m.pop("cases")
        print(json.dumps(m, ensure_ascii=False, indent=2))
        print(f"... 共 {len(cases)} 案例")
        return 0
    if args.action == "expand":
        r = expand(per_seed=args.per_seed)
        print(json.dumps(r, ensure_ascii=False, indent=2))
        return 0
    ok, bad = verify()
    print(f"verified: {ok}, bad: {len(bad)}")
    for b in bad[:20]:
        print("  ", b)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
