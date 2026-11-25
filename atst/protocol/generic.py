# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""L2 通用启发式解析 + L3 原始透传 + ProtocolSniffer（§5.2 / §5.3）。

当一条命令没有 L1 精确解析器时，本模块保证数据**绝不丢失**：

1. 先做启发式结构化（L2），给出**带置信度**的字段推断结果；
2. 置信度不足则回落原始透传（L3），返回 raw bytes 与诊断信息；
3. 两种情况都会把样本交给 :class:`ProtocolSniffer` 归档，
   供人工分析后生成 spec 草案、升级为 L1。

启发式流程
----------
1. 空 payload → L3
2. 尝试 ``uint16`` 记录数前缀：若 ``(len - 2) % count == 0`` 且 count 合理 → 定长记录
3. 记录长度属候选集（32/16/8/4/2/1）→ 加分
4. 逐字段类型推断：uint32 / int32 / float32 / 日期位域 / varint / 文本
5. 加权打分得 confidence
"""

from __future__ import annotations

import json
import os
import struct
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..codec.framing import ResponseFrame
from ..codec.primitive import decode_gbk, detect_encoding
from .commands import Family, get_command
from .registry import TIER_L2, TIER_L3, ParseResult

__all__ = [
    "CandidateSpec",
    "infer_record_layout",
    "parse_generic",
    "ProtocolSniffer",
    "get_sniffer",
]

#: 常见记录长度（字节）
CANDIDATE_RECORD_SIZES: tuple[int, ...] = (32, 29, 24, 16, 12, 8, 4, 2, 1)
#: 记录数的合理上限（K 线单页最多 800，列表 1000，留冗余）
MAX_PLAUSIBLE_COUNT = 8000


@dataclass
class CandidateSpec:
    """启发式推断出的记录布局候选。"""

    has_count_prefix: bool
    count: int
    record_size: int
    field_types: list[str] = field(default_factory=list)
    score: float = 0.0
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "has_count_prefix": self.has_count_prefix,
            "count": self.count,
            "record_size": self.record_size,
            "field_types": self.field_types,
            "score": round(self.score, 4),
            "notes": self.notes,
        }


# --------------------------------------------------------------------------- #
# 启发式推断
# --------------------------------------------------------------------------- #
def _looks_like_date(value: int) -> bool:
    """uint32 是否像 TDX 的日期位域编码（year@bit20）。"""
    year = (value >> 20) & 0xFFF
    month = (value >> 16) & 0x0F
    day = (value >> 11) & 0x1F
    hour = (value >> 6) & 0x1F
    minute = value & 0x3F
    return (
        1990 <= year <= 2100 and 1 <= month <= 12 and 1 <= day <= 31 and hour <= 23 and minute <= 59
    )


# P3：_looks_like_uint16_date 全库零调用死代码已移除；uint16 日期候选启发
# 如需扩展 L2 探测再按账本流程重新引入。


def _classify_chunk(chunk: bytes) -> str:
    """对 4 字节块做类型打分，返回最可能类型名。"""
    if len(chunk) < 4:
        return "bytes"
    (u32,) = struct.unpack("<I", chunk)
    (i32,) = struct.unpack("<i", chunk)
    (f32,) = struct.unpack("<f", chunk)

    if _looks_like_date(u32):
        return "datetime32"
    if u32 == 0:
        return "zero_u32"
    # float32：指数位合理且量级在金融数据范围内
    if 1e-6 < abs(f32) < 1e12:
        # 与 u32 相比，谁更"像"？用小整数优先判 u32
        if u32 < 100_000_000:
            return "u32?"
        return "f32"
    if 0 < u32 < 1_000_000_000:
        return "u32"
    if -1_000_000_000 < i32 < 0:
        return "i32"
    return "bytes"


def _score_candidate(
    payload: bytes,
    count: int,
    record_size: int,
    has_prefix: bool,
    notes: list[str] | None = None,
) -> float:
    if count <= 0 or record_size <= 0:
        return 0.0
    score = 0.0
    # 1) 记录数合理性
    if 1 <= count <= MAX_PLAUSIBLE_COUNT:
        score += 0.25
        if 1 <= count <= 1000:
            score += 0.10
    # 2) 记录长度整除
    body_len = len(payload) - (2 if has_prefix else 0)
    if body_len % record_size == 0:
        score += 0.25
    else:
        return score * 0.4
    # 3) 长度属常见集
    if record_size in CANDIDATE_RECORD_SIZES:
        score += 0.15
    # 4) 字段自洽性：抽样最多 20 条记录
    samples = min(count, 20)
    good = 0
    sampled_types: list[str] = []
    for i in range(samples):
        off = (2 if has_prefix else 0) + i * record_size
        rec = payload[off : off + record_size]
        if len(rec) < record_size:
            break
        types = [_classify_chunk(rec[j : j + 4]) for j in range(0, min(record_size, 32), 4)]
        sampled_types.extend(types)
        if any(t in ("datetime32", "u32", "u32?", "f32", "zero_u32") for t in types):
            good += 1
    if samples:
        score += 0.25 * (good / samples)
        # 抽样记录中没有任何「金融类型」字段 → 疑似随机字节，重罚降权（回落 L3）
        if good == 0:
            score *= 0.4
        # 退化记录：全零，或仅一种非零字段 + 大片零字段，此前会拿满 0.85
        # 被误判为 L2（2026-11-25 对 sniffer 语料实测：06b9/000f/0200 均命中；
        # 000f 还会把同一段载荷切成两条 16B 记录来骗过「count=1」特判）。
        # 这类载荷没有可验证的表结构，宁可回落 L3 原始透传，不能伪造字段。
        if sampled_types:
            nonzero = {t for t in sampled_types if t not in ("zero_u32", "bytes")}
            zero_ratio = sampled_types.count("zero_u32") / len(sampled_types)
            if not nonzero or (zero_ratio >= 0.5 and len(nonzero) <= 1):
                score *= 0.4
                if notes is not None:
                    notes.append("记录字段近乎全零，按低置信回落 L3")
    return round(min(score, 1.0), 4)


def infer_record_layout(payload: bytes) -> CandidateSpec:
    """推断记录布局，返回得分最高的候选。"""
    if len(payload) < 4:
        return CandidateSpec(False, 0, 0, score=0.0, notes=["payload 过短"])

    best = CandidateSpec(False, 0, 0, score=0.0, notes=["无可行候选"])
    body_len = len(payload)

    # 分支 A：uint16 记录数前缀
    if body_len >= 6:
        count = struct.unpack_from("<H", payload, 0)[0]
        rest = body_len - 2
        if count > 0 and rest % count == 0:
            rs = rest // count
            notes = ["uint16 记录数前缀自洽"]
            s = _score_candidate(payload, count, rs, True, notes=notes)
            if s > best.score:
                types = [_classify_chunk(payload[2 + j : 6 + j]) for j in range(0, min(rs, 32), 4)]
                best = CandidateSpec(True, count, rs, types, s, notes=notes)
    # 分支 B：无前缀，按候选记录长度整除
    for rs in CANDIDATE_RECORD_SIZES:
        if body_len % rs == 0:
            count = body_len // rs
            notes = ["按候选记录长度整除"]
            s = _score_candidate(payload, count, rs, False, notes=notes) * 0.85  # 无前缀略降权
            if s > best.score:
                types = [_classify_chunk(payload[j : j + 4]) for j in range(0, min(rs, 32), 4)]
                best = CandidateSpec(False, count, rs, types, s, notes=notes)
    return best


def _decode_record(rec: bytes, field_types: list[str], index: int) -> dict[str, Any]:
    row: dict[str, Any] = {"_idx": index}
    pos = 0
    fi = 0
    while pos + 4 <= len(rec):
        chunk = rec[pos : pos + 4]
        ftype = field_types[fi] if fi < len(field_types) else _classify_chunk(chunk)
        (u32,) = struct.unpack("<I", chunk)
        (f32,) = struct.unpack("<f", chunk)
        key = f"f{fi}"
        if ftype == "datetime32":
            row[key] = {
                "year": (u32 >> 20) & 0xFFF,
                "month": (u32 >> 16) & 0x0F,
                "day": (u32 >> 11) & 0x1F,
                "hour": (u32 >> 6) & 0x1F,
                "minute": u32 & 0x3F,
            }
            row[f"{key}_ts"] = (
                f"{(u32 >> 20) & 0xFFF:04d}-{(u32 >> 16) & 0x0F:02d}-{(u32 >> 11) & 0x1F:02d}"
                f" {(u32 >> 6) & 0x1F:02d}:{u32 & 0x3F:02d}"
            )
        elif ftype == "f32":
            row[key] = round(f32, 6)
        elif ftype == "i32":
            row[key] = struct.unpack("<i", chunk)[0]
        else:
            row[key] = u32
        pos += 4
        fi += 1
    # 剩余不足 4 字节的尾巴
    if pos < len(rec):
        tail = rec[pos:]
        row[f"tail_{len(tail)}B"] = tail.hex()
        # 尝试按文本解码
        if len(tail) >= 2:
            try:
                text = decode_gbk(tail, detect_encoding(tail))
                if text:
                    row["tail_text"] = text
            except Exception:  # pragma: no cover
                pass
    return row


def parse_generic(frame: ResponseFrame, *, family: str = Family.STANDARD) -> ParseResult:
    """L2 通用解析入口。失败自动回落 L3。"""
    payload = frame.payload
    cmd = frame.method
    info = get_command(cmd, family)
    name = info.name if info else f"UNKNOWN_{cmd:04X}"
    meta: dict[str, Any] = {
        "payload_len": len(payload),
        "family": family,
        "compressed": frame.compressed,
    }
    warnings: list[str] = []

    if not payload:
        return ParseResult(
            cmd,
            name,
            TIER_L3,
            0.0,
            [],
            payload,
            {**meta, "reason": "empty_payload"},
            ["空 payload"],
        )

    cand = infer_record_layout(payload)
    meta["candidate"] = cand.to_dict()

    if cand.score < 0.5 or cand.count <= 0 or cand.record_size <= 0:
        # L3 为原始透传：置信度恒为 0.0（启发式得分保留在 meta.candidate.score）
        return ParseResult(
            cmd,
            name,
            TIER_L3,
            0.0,
            [],
            payload,
            {**meta, "reason": "low_confidence", "heuristic_score": cand.score},
            warnings + [f"L2 置信度 {cand.score:.2f} < 0.5，回落 L3 原始透传"],
        )

    rows: list[dict[str, Any]] = []
    base = 2 if cand.has_count_prefix else 0
    for i in range(cand.count):
        off = base + i * cand.record_size
        rec = payload[off : off + cand.record_size]
        if len(rec) < cand.record_size:
            warnings.append(f"第 {i} 条记录不完整，已跳过")
            break
        rows.append(_decode_record(rec, cand.field_types, i))

    return ParseResult(cmd, name, TIER_L2, cand.score, rows, payload, meta, warnings)


# --------------------------------------------------------------------------- #
# ProtocolSniffer：未知命令自动归档 + spec 草案生成
# --------------------------------------------------------------------------- #
class ProtocolSniffer:
    """未知命令归档器（§5.4）。

    任何落到 L2/L3 的响应都会在此留档，供 A 组（协议分析）人工分析后
    补全 spec，进而升级为 L1。归档目录结构::

        PROTOCOL_SPEC/_sniffer/<family>/<cmd:04x>/
            ├── <timestamp>.bin         原始 payload
            ├── <timestamp>.meta.json   帧头 + 候选布局 + 环境信息
            └── DRAFT.yaml              自动生成的 spec 草案（人工补全）
    """

    def __init__(self, root: str | Path | None = None, enabled: bool | None = None) -> None:
        # P0-8：归档默认**关闭**。过去生产分派热路径上每个 L2/L3 响应都同步落盘
        # （持全局锁 + 相对 cwd 写 PROTOCOL_SPEC/_sniffer），批量未知命令时是每响应
        # 两次文件 IO。现在默认关，``ATST_PROTOCOL_SNIFF=1`` 显式开启；归档根目录用
        # ``ATST_PROTOCOL_SNIFF_DIR`` 注入绝对路径，不再隐式依赖进程 cwd。
        env_on = os.environ.get("ATST_PROTOCOL_SNIFF", "").strip().lower() in (
            "1",
            "true",
            "yes",
            "on",
        )
        self.enabled = env_on if enabled is None else bool(enabled)
        env_root = os.environ.get("ATST_PROTOCOL_SNIFF_DIR", "").strip()
        self.root = Path(root if root is not None else (env_root or "PROTOCOL_SPEC/_sniffer"))

    def archive(self, frame: ResponseFrame, result: ParseResult, family: str) -> Path | None:
        if not self.enabled or not frame.payload:
            return None
        # 已验证命令不必重复归档
        info = get_command(frame.method, family)
        if info is not None and info.verified and result.tier == "L1":
            return None

        # P#8：整程持锁 + 毫秒级时间戳 + 同名序号探测——旧实现秒级时间戳
        # write_bytes 直写，同一命令 1 秒内第二条样本**静默覆盖**第一条。
        with _ARCHIVE_LOCK:
            d = self.root / family / f"{frame.method:04x}"
            d.mkdir(parents=True, exist_ok=True)
            ts = time.strftime("%Y%m%dT%H%M%S") + f"{int(time.time() * 1000) % 1000:03d}"
            n = 0
            while (d / f"{ts}-{n}.bin" if n else d / f"{ts}.bin").exists():
                n += 1
            suffix = f"{ts}-{n}" if n else ts
            bin_path = d / f"{suffix}.bin"
            bin_path.write_bytes(frame.payload)
            meta = {
                "captured_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                "family": family,
                "command": f"0x{frame.method:04x}",
                "command_name": result.name,
                "frame": frame.to_dict(),
                "result": {
                    "tier": result.tier,
                    "confidence": result.confidence,
                    "rows": len(result.rows),
                    "warnings": result.warnings,
                },
                "candidate": result.meta.get("candidate"),
                "source": "self-captured",
            }
            (d / f"{suffix}.meta.json").write_text(
                json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            draft = d / "DRAFT.yaml"
            if not draft.exists():
                try:
                    # "x" 模式原子创建：并发下只有一个写入者成功
                    with open(draft, "x", encoding="utf-8") as f:
                        f.write(self.gen_draft(frame, result, family))
                except FileExistsError:
                    pass
            return bin_path

    @staticmethod
    def gen_draft(frame: ResponseFrame, result: ParseResult, family: str) -> str:
        """生成 spec 草案（人工补全后重命名为正式 spec）。"""
        cand = result.meta.get("candidate") or {}
        fields_yaml = (
            "\n".join(
                f"  - {{name: f{i}, type: {t}, offset: {i * 4}}}"
                for i, t in enumerate(cand.get("field_types", []))
            )
            or "  # TODO: 人工补全字段"
        )
        return f"""\
# AUTO-GENERATED DRAFT —— 请勿直接提交，需 A 组人工补全后转为正式 spec
# 生成时间: {time.strftime("%Y-%m-%dT%H:%M:%S")}
# 数据来源: self-captured（自有环境抓包）

command: 0x{frame.method:04x}
family: {family}
name: {result.name}
tier: DRAFT
verified: false
confidence: {result.confidence}

request:
  fields: []          # TODO: 补全请求字段

response:
  head_bytes: {2 if cand.get("has_count_prefix") else 0}
  has_count_prefix: {cand.get("has_count_prefix", False)}
  record_size: {cand.get("record_size", 0)}
  fields:
{fields_yaml}

notes: |
  由 ProtocolSniffer 自动推断。请对照 .bin 原始样本逐字段校验，
  校验通过后删除本文件并新建 <command>.yaml 正式 spec。
"""


#: 默认实例：enabled 由环境变量决定（默认关，见 :meth:`ProtocolSniffer.__init__`）。
_default_sniffer = ProtocolSniffer()

#: 归档落盘互斥锁（P#8：多线程 dispatch 并发归档时防交叉写）
_ARCHIVE_LOCK = threading.Lock()


def get_sniffer() -> ProtocolSniffer:
    return _default_sniffer
