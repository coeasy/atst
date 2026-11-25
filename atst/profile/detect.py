# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""detect_profile 六步自动探测管线（Tier D item D3）。

**定位（与 reader/profile 的关系，两套探测器互相指认）**：
:mod:`atst.reader.profile` 是**生产文件档案**（本地 vipdoc 二进制，
ProfileDetector 六步探测直接接 ``DayBarReader/MinBarReader`` 主链路）；
本模块是**网络帧档案**（对网络 payload / 未知归档字节做证据化探测，
供协议发现与人工评审）。两者共享 :class:`DataProfile` 与打分内核
:mod:`atst.profile.scoring`（候选长度表/抽样/字段探针/日期投票的
SSOT，P1-7 探测合一），但互不调用——不要把本模块的输出直接喂给 reader 主链路。

本模块提供一个**证据化**的探测入口 :func:`detect`，与
:class:`atst.reader.profile.ProfileDetector` 的差别：

* **分步输出**：每步给出 :data:`StepEvidence`，调用方可以审计推理链；
* **候选排名**：不只是返回单一 :class:`DataProfile`，还返回排名靠前的
  候选列表，便于人工判断边界情况；
* **可注入先验**：``hint_market`` / ``hint_period`` 通过
  :mod:`atst.profile.presets` 把市场/品种先验映射成评分加权；
* **绝不静默猜测**：置信度低于阈值时抛
  :class:`~atst.errors.ProfileUndetectable`。

六步流程
--------

1. **Magic/Length 启发**：文件大小是否为常见记录长度（32/29/24/16/8/4）
   的整数倍？若是，给候选长度加基线分。
2. **GCD 记录长度候选**：对每个候选长度，验证整除并把命中长度排到前面。
3. **字段类型探针**：在每个候选长度下，抽样记录，在每个 4 字节偏移处
   并行测试 ``uint32`` / ``float32`` / ``tdx_float``，用
   :func:`atst.codec.primitive.decode_tdx_float` 判定成交量/成交额是否
   落在合理区间。（探针机械收敛于 :mod:`atst.profile.scoring`，P1-7）
4. **日期字段探测**：在偏移 0 / 2 / 4 尝试 ``yyyymmdd`` / ``lc16`` /
   ``datetime32`` 三种时间编码，选合法样本比例最高者。（投票机械同上）
5. **候选评分与排序**：把步骤 2–4 的分数按模块常量 ``WEIGHT_MAGIC_LENGTH``
   等四项加权（数值以代码常量为 SSOT，文档不手抄），取最高者为最终
   :class:`DataProfile`。
6. **置信度决策**：总置信度 ≥ 0.5 → 返回；< 0.5 →
   抛 :class:`~atst.errors.ProfileUndetectable`。

依赖
----
* :mod:`atst.reader.profile` —— 复用 :class:`DataProfile` 与其枚举；
* :mod:`atst.codec.primitive` —— :func:`decode_tdx_float`（tdx_float 探针）；
* :mod:`atst.profile.scoring` —— 共享打分内核（P1-7 探测合一）；
* :mod:`atst.profile.presets` —— 先验市场/品种映射。
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field, replace
from typing import Any

from ..codec.primitive import decode_tdx_float_at
from ..errors import ProfileUndetectable
from ..profile.scoring import (
    FIELD_PROBE_OFFSETS,
    RECORD_SIZES,
    plausible_float32_price,
    plausible_tdx_float,
    plausible_u32_price,
    probe_field_types,
    sample_records,
    vote_date_encodings,
)
from ..reader.profile import (
    BUILTIN_PROFILES,
    AssetClass,
    DataProfile,
    Market,
    Period,
    PriceEncoding,
    TimeEncoding,
    VolumeUnit,
)

__all__ = [
    "DetectionResult",
    "StepEvidence",
    "detect",
    "CONFIDENCE_THRESHOLD",
]

#: 置信度阈值；低于此值抛 :class:`ProfileUndetectable`。
CONFIDENCE_THRESHOLD: float = 0.5

#: 步骤加权（P1-7：文档不再手抄数字，一律引用本组常量）。
WEIGHT_MAGIC_LENGTH: float = 0.20
WEIGHT_RECORD_SIZE: float = 0.20
WEIGHT_FIELD_TYPES: float = 0.25
WEIGHT_DATE_DETECTION: float = 0.35


# --------------------------------------------------------------------------- #
# 结果类型
# --------------------------------------------------------------------------- #
@dataclass
class StepEvidence:
    """单步探测的证据。

    Attributes
    ----------
    step:
        步骤名（如 ``"magic_length"`` / ``"record_size"`` /
        ``"field_types"`` / ``"date_detection"`` / ``"scoring"`` /
        ``"decision"``）。
    description:
        人类可读的简述。
    score:
        本步贡献的 0–1 分数。
    details:
        自由结构的细节 dict。
    """

    step: str
    description: str
    score: float
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "step": self.step,
            "description": self.description,
            "score": round(self.score, 4),
            "details": self.details,
        }


@dataclass
class DetectionResult:
    """探测结果。

    Attributes
    ----------
    profile:
        最终 :class:`DataProfile`。
    confidence:
        总置信度（0–1）。
    candidates:
        所有候选（按分数降序），每项为 dict。
    evidence:
        每一步的证据（按执行顺序）。
    preset_name:
        若命中 :mod:`atst.profile.presets` 中的预设则为其名称，否则空串。
    """

    profile: DataProfile
    confidence: float
    candidates: list[dict[str, Any]] = field(default_factory=list)
    evidence: list[StepEvidence] = field(default_factory=list)
    preset_name: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "profile": self.profile.to_dict(),
            "confidence": self.confidence,
            "candidates": list(self.candidates),
            "evidence": [e.to_dict() for e in self.evidence],
            "preset_name": self.preset_name,
        }


# --------------------------------------------------------------------------- #
# 辅助
# --------------------------------------------------------------------------- #
# P1-7：合理性谓词（plausible_*）、日期三编码判定（looks_like_*）与等距
# 抽样（sample_records）收敛到 :mod:`atst.profile.scoring`，见顶部导入。


# --------------------------------------------------------------------------- #
# 步骤实现
# --------------------------------------------------------------------------- #
def _step1_magic_length(data: bytes) -> StepEvidence:
    """步骤 1：文件长度是否为常见记录长度的整数倍。"""
    n = len(data)
    if n < 4:
        return StepEvidence(
            "magic_length",
            "数据过短，跳过 magic 启发",
            0.2,
            details={"len": n, "too_short": True},
        )
    hits: list[int] = []
    for rs in RECORD_SIZES:
        if n % rs == 0 and n // rs >= 1:
            hits.append(rs)
    score = min(1.0, len(hits) / 3.0) if hits else 0.1
    return StepEvidence(
        "magic_length",
        f"数据长度 {n} 命中 {len(hits)} 个常见记录长度",
        score,
        details={"len": n, "hits": hits},
    )


def _step2_record_size_candidates(data: bytes) -> StepEvidence:
    """步骤 2：GCD 分析，返回最可能的记录长度候选。"""
    n = len(data)
    if n < 4:
        return StepEvidence(
            "record_size",
            "数据过短，无法推断记录长度",
            0.1,
            details={"len": n, "candidates": [], "top": 0},
        )
    # 优先按常见长度命中
    scored: list[tuple[int, float]] = []
    for rs in RECORD_SIZES:
        if n % rs == 0:
            # 32 是日线/分钟线最常见的记录长度
            weight = 1.0 if rs == 32 else 0.7 if rs in (29, 16, 8) else 0.5
            scored.append((rs, weight))
    if not scored:
        return StepEvidence(
            "record_size",
            "无常见记录长度整除",
            0.1,
            details={"len": n, "candidates": [], "top": 0},
        )
    scored.sort(key=lambda x: -x[1])
    top = scored[0][0]
    return StepEvidence(
        "record_size",
        f"GCD 分析命中 {len(scored)} 个候选，最可能 {top}B",
        min(1.0, scored[0][1]),
        details={"len": n, "candidates": [rs for rs, _ in scored], "top": top},
    )


def _step3_field_types(data: bytes, record_size: int) -> tuple[StepEvidence, str]:
    """步骤 3：字段类型探针。

    在每个候选记录长度下，对每个记录的 4 字节偏移并行测试
    ``uint32`` / ``float32`` / ``tdx_float``。返回 (evidence, encoding)。
    """
    if record_size < 4:
        return StepEvidence(
            "field_types",
            "记录长度 < 4，跳过字段探针",
            0.3,
            details={"encoding": PriceEncoding.UINT32, "reason": "record_too_small"},
        ), PriceEncoding.UINT32

    samples = sample_records(data, record_size)
    if not samples:
        return StepEvidence(
            "field_types",
            "无可用样本",
            0.3,
            details={"encoding": PriceEncoding.UINT32, "reason": "no_samples"},
        ), PriceEncoding.UINT32

    # 常见价格字段偏移（相对记录起点）
    offsets = tuple(off for off in FIELD_PROBE_OFFSETS if off + 4 <= record_size)
    # P1-7：uint32/float32 的解包与计数循环收敛到 scoring.probe_field_types
    float_like, int_like, total = probe_field_types(
        samples,
        plausible_float32=plausible_float32_price,
        plausible_uint32=plausible_u32_price,
        offsets=offsets,
    )
    # tdx_float 探针是本入口独有的字段类型（reader 探针不含），仍在本模块计数
    tdx_float_like = 0
    for rec in samples:
        for off in offsets:
            try:
                tf, _ = decode_tdx_float_at(rec, off)
            except Exception:
                tf = 0.0
            if plausible_tdx_float(tf):
                tdx_float_like += 1

    if total == 0:
        return StepEvidence(
            "field_types",
            "无有效偏移",
            0.3,
            details={"encoding": PriceEncoding.UINT32},
        ), PriceEncoding.UINT32

    # 决策：float32 概率 > 60% → FLOAT32；否则 UINT32
    if float_like / total > 0.6:
        encoding = PriceEncoding.FLOAT32
        score = float_like / total
    else:
        encoding = PriceEncoding.UINT32
        score = int_like / total if int_like else 0.3

    return StepEvidence(
        "field_types",
        f"字段类型探针：float32={float_like}/{total}, "
        f"uint32={int_like}/{total}, tdx_float={tdx_float_like}/{total}",
        round(min(score, 1.0), 4),
        details={
            "encoding": encoding,
            "float32": float_like,
            "uint32": int_like,
            "tdx_float": tdx_float_like,
            "total": total,
        },
    ), encoding


def _step4_date_detection(data: bytes, record_size: int) -> tuple[StepEvidence, str]:
    """步骤 4：日期字段探测（偏移 0 / 2 / 4 三种时间编码）。"""
    if record_size < 2:
        return StepEvidence(
            "date_detection",
            "记录长度 < 2，跳过日期探测",
            0.2,
            details={"encoding": TimeEncoding.YYYYMMDD},
        ), TimeEncoding.YYYYMMDD

    samples = sample_records(data, record_size)
    if not samples:
        return StepEvidence(
            "date_detection",
            "无可用样本",
            0.2,
            details={"encoding": TimeEncoding.YYYYMMDD},
        ), TimeEncoding.YYYYMMDD

    # P1-7：三编码投票机械收敛到 scoring.vote_date_encodings
    ymd_votes, dt32_votes, lc16_votes, total = vote_date_encodings(samples, record_size)

    if total == 0:
        return StepEvidence(
            "date_detection",
            "无有效样本",
            0.2,
            details={"encoding": TimeEncoding.YYYYMMDD},
        ), TimeEncoding.YYYYMMDD

    scores = {
        TimeEncoding.YYYYMMDD: ymd_votes / total,
        TimeEncoding.DATETIME32: dt32_votes / total,
        TimeEncoding.LC16: lc16_votes / total,
    }
    best = max(scores, key=lambda k: scores[k])
    return StepEvidence(
        "date_detection",
        f"日期探测：yyyymmdd={ymd_votes}/{total}, datetime32={dt32_votes}/{total}, "
        f"lc16={lc16_votes}/{total}",
        round(scores[best], 4),
        details={
            "encoding": best,
            "yyyymmdd": ymd_votes,
            "datetime32": dt32_votes,
            "lc16": lc16_votes,
            "total": total,
        },
    ), best


# --------------------------------------------------------------------------- #
# 候选构造
# --------------------------------------------------------------------------- #
def _build_candidate_profile(
    record_size: int,
    encoding: str,
    time_encoding: str,
    *,
    hint_market: int | None,
    hint_period: str | None,
) -> DataProfile:
    """根据探测结果构造一个候选 :class:`DataProfile`。"""
    # 先按 period 推断 volume_unit 与 asset_class
    period = hint_period or Period.DAY
    # P-9 死分支修复：原 if/else 两分支同赋 ``AssetClass.STOCK``，是假分支。
    # 探测层目前只能产出 STOCK（见 reader/profile.py ``AssetClass`` 的词表
    # 说明）；要按周期改判品种，先得有品种探针，再恢复分支。
    asset_class = AssetClass.STOCK

    # 尝试匹配 preset（若有 hint_market）：加权在 detect() 主循环内做
    # （对 presets 匹配成功者的首要记录长度候选 +0.1 置信度），
    # 此处只负责按 period/encoding 选基础档案，不再有静默 pass 死分支。

    # 若 period=day 且 record_size=32 且 encoding=uint32 且 time=yyyymmdd
    # → 极可能是 A 股日线
    if (
        record_size == 32
        and encoding == PriceEncoding.UINT32
        and time_encoding == TimeEncoding.YYYYMMDD
        and period in (Period.DAY, Period.WEEK, Period.MONTH)
    ):
        # 用 A 股日线默认
        base = BUILTIN_PROFILES.get("a_share_day", DataProfile())
        return replace(
            base,
            name="auto-detected-a-day",
            record_size=record_size,
            price_encoding=encoding,
            time_encoding=time_encoding,
            period=period,
            confidence=0.0,  # 让 detect() 重新算
            notes="auto-detected",
        )

    if (
        record_size == 32
        and encoding == PriceEncoding.FLOAT32
        and time_encoding == TimeEncoding.LC16
        and period in (Period.M1, Period.M5)
    ):
        base = BUILTIN_PROFILES.get("a_share_min", DataProfile())
        return replace(
            base,
            name="auto-detected-a-min",
            record_size=record_size,
            price_encoding=encoding,
            time_encoding=time_encoding,
            period=period,
            confidence=0.0,
            notes="auto-detected",
        )

    # 兜底：构造通用 profile
    market = Market.SH
    if hint_market is not None:
        # 反查 Market.CODES
        for name, mid in Market.CODES.items():
            if mid == hint_market:
                market = name
                break

    return DataProfile(
        name="auto-detected-generic",
        market=market,
        asset_class=asset_class,
        period=period,
        record_size=record_size,
        price_scale=100 if encoding == PriceEncoding.UINT32 else 1,
        price_encoding=encoding,
        volume_unit=VolumeUnit.SHARE,
        time_encoding=time_encoding,
        confidence=0.0,
        notes="auto-detected generic fallback",
    )


# --------------------------------------------------------------------------- #
# 辅助：presets 粗匹配 / price_scale 合理性
# --------------------------------------------------------------------------- #
def _match_preset_name(hint_market: int) -> str:
    """按市场编号粗匹配预设名（presets 先验；未命中返回空串）。

    代码前缀无法从字节流推断，因此只按 ``market_id`` 粗匹配。
    """
    for preset in _iter_presets():
        if preset.market_id == hint_market:
            return preset.name
    return ""


def _price_scale_sanity(
    data: bytes, record_size: int, profile: DataProfile
) -> tuple[int | None, float, str]:
    """步骤 5'：price_scale 合理性检查（借鉴 reader.profile 价量一致性思路）。

    最小规则：uint32 档案且 ``price_scale == 100`` 时，抽样记录的价格字段
    （偏移 4/8/12/16 的 u32）中位数 ÷ 100 若 > 5000 元（A 股股价量级之上，
    典型误判：真实 scale=1000 的网络帧档案被按 ×100 解析）——
    优先**改判 ×1000**（改判后落在 (0, 100000] 元区间才改），
    否则**降置信 ×0.5** 并写 notes。两分支都会在 evidence 里留痕。

    Returns
    -------
    ``(new_scale, confidence_factor, note)``：``new_scale`` 非空表示改判；
    ``confidence_factor`` < 1 表示降置信；空 note 表示无需处理。
    """
    if (
        record_size < 8
        or profile.price_scale != 100
        or profile.price_encoding != PriceEncoding.UINT32
    ):
        return None, 1.0, ""
    samples = sample_records(data, record_size, limit=32)
    if not samples:
        return None, 1.0, ""
    values: list[int] = []
    offsets = tuple(off for off in (4, 8, 12, 16) if off + 4 <= record_size)
    for rec in samples:
        for off in offsets:
            if off + 4 > len(rec):
                continue
            (u32,) = struct.unpack_from("<I", rec, off)
            if u32:
                values.append(u32)
    if not values:
        return None, 1.0, ""
    values.sort()
    median = values[len(values) // 2]
    price_at_100 = median / 100
    if price_at_100 <= 5000:
        return None, 1.0, ""
    price_at_1000 = median / 1000
    if 0.0 < price_at_1000 <= 100_000:
        note = (
            f"price_scale 合理性：抽样价格中位数 {median} 按 ×100 解析为 "
            f"{price_at_100:.0f} 元，超出 A 股量级；改判 ×1000（约 {price_at_1000:.2f} 元）"
        )
        return 1000, 1.0, note
    note = (
        f"price_scale 合理性：抽样价格中位数 {median} 按 ×100 解析为 "
        f"{price_at_100:.0f} 元，超出 A 股量级且 ×1000 亦不合理；降置信 50%"
    )
    return None, 0.5, note


# --------------------------------------------------------------------------- #
# 主入口
# --------------------------------------------------------------------------- #
def detect(
    data: bytes,
    *,
    hint_market: int | None = None,
    hint_period: str | None = None,
) -> DetectionResult:
    """六步自动探测数据规格。

    Parameters
    ----------
    data:
        原始字节（本地 vipdoc 文件内容或网络 payload）。
    hint_market:
        可选市场编号（如 1=沪 0=深 2=北），用于优先匹配
        :mod:`atst.profile.presets` 中的预设。
    hint_period:
        可选周期字符串（如 ``"day"`` / ``"1min"``），用于缩小候选空间。

    Returns
    -------
    :class:`DetectionResult`

    Raises
    ------
    ProfileUndetectable
        置信度低于 :data:`CONFIDENCE_THRESHOLD`。
    """
    if not data:
        raise ProfileUndetectable("空数据无法探测", context={"len": 0})

    evidence: list[StepEvidence] = []
    candidates: list[dict[str, Any]] = []

    # ---- 步骤 1：Magic/Length 启发 ---------------------------------------
    ev1 = _step1_magic_length(data)
    evidence.append(ev1)

    # ---- 步骤 2：记录长度候选 ---------------------------------------------
    ev2 = _step2_record_size_candidates(data)
    evidence.append(ev2)

    top_rs = ev2.details.get("top", 0)
    candidate_rs_list: list[int] = list(ev2.details.get("candidates", []))
    if top_rs > 0:
        candidate_rs_list.insert(0, top_rs)
    # 去重保序：top_rs 通常已居于 candidates 首位，不修正会把同一记录长度
    # 重复评估两遍（步骤 3/4 各算两次，分数互相污染）
    candidate_rs_list = list(dict.fromkeys(candidate_rs_list))
    if not candidate_rs_list:
        candidate_rs_list = list(RECORD_SIZES)

    # presets 先验（hint_market 命中预设 → 首要记录长度候选加权）
    preset_matched = _match_preset_name(hint_market) if hint_market is not None else ""

    # ---- 步骤 3 + 4：对 top-2 候选记录长度做字段类型与日期探测 -------------
    best_candidate: dict[str, Any] | None = None
    best_profile: DataProfile | None = None
    best_confidence = 0.0

    for rs in candidate_rs_list[:2]:
        ev3, encoding = _step3_field_types(data, rs)
        ev4, time_enc = _step4_date_detection(data, rs)
        # 归一化分数（步骤 2 已有分数）
        rs_score = 1.0 if rs == top_rs else 0.6
        conf = round(
            WEIGHT_MAGIC_LENGTH * ev1.score
            + WEIGHT_RECORD_SIZE * rs_score
            + WEIGHT_FIELD_TYPES * ev3.score
            + WEIGHT_DATE_DETECTION * ev4.score,
            4,
        )
        if preset_matched and rs == top_rs and conf >= CONFIDENCE_THRESHOLD * 0.6:
            # presets 先验加权：hint_market 命中预设时，给首要记录长度候选
            # +0.1 置信度（封顶 1.0）——接通旧的 `if hint_market: pass` 死分支。
            # 深审 M24 防御：原始数据证据严重不足（conf < 阈值 60%）时加权
            # 不生效——hint 只是市场先验，不能把数据不匹配的候选推过
            # CONFIDENCE_THRESHOLD 探测门禁。
            conf = round(min(1.0, conf + 0.1), 4)
        cand = {
            "record_size": rs,
            "price_encoding": encoding,
            "time_encoding": time_enc,
            "confidence": conf,
            "step_scores": {
                "magic_length": ev1.score,
                "record_size": rs_score,
                "field_types": ev3.score,
                "date_detection": ev4.score,
            },
        }
        candidates.append(cand)
        # P1-7/P-9：每个候选的探针证据无条件入链——此前 ev3/ev4 只在候选
        # 刷新最优时 extend，落选候选的字段/日期探针证据丢失，审计链不完整。
        evidence.extend([ev3, ev4])
        evidence.append(
            StepEvidence("scoring", f"候选 rs={rs} 综合置信度 {conf:.2f}", conf, details=cand)
        )
        if conf > best_confidence:
            best_confidence = conf
            best_candidate = cand
            best_profile = _build_candidate_profile(
                rs,
                encoding,
                time_enc,
                hint_market=hint_market,
                hint_period=hint_period,
            )

    if best_profile is None:
        # 极端兜底：构造一个空 profile
        best_profile = DataProfile(
            name="auto-detected-empty",
            record_size=top_rs or 32,
            confidence=0.0,
            notes="no viable candidate",
        )
        best_confidence = 0.0

    # ---- 步骤 5'：price_scale 合理性 ------------------------------------
    if best_candidate is not None:
        new_scale, factor, sanity_note = _price_scale_sanity(
            data, int(best_candidate.get("record_size", 0)), best_profile
        )
        if new_scale is not None or factor < 1.0:
            if new_scale is not None:
                best_profile = replace(best_profile, price_scale=new_scale)
            else:
                best_confidence = round(best_confidence * factor, 4)
                best_candidate["confidence"] = best_confidence
            if sanity_note:
                best_profile = replace(
                    best_profile,
                    notes=(best_profile.notes or "") + "; " + sanity_note,
                )
                best_candidate["price_scale_sanity"] = sanity_note
        evidence.append(
            StepEvidence(
                "price_scale_sanity",
                sanity_note or "抽样价格量级在档案口径内，无需修正",
                round(factor, 4),
                details={"note": sanity_note, "scale_after": best_profile.price_scale},
            )
        )

    # ---- 步骤 5：候选排序 ------------------------------------------------
    candidates.sort(key=lambda c: -c["confidence"])
    evidence.append(
        StepEvidence(
            "scoring",
            f"共 {len(candidates)} 个候选，最高置信度 {best_confidence:.2f}",
            round(best_confidence, 4),
            details={"ranked": candidates[:5]},
        )
    )

    # ---- 步骤 6：置信度决策 ----------------------------------------------
    preset_name = preset_matched

    if best_confidence < CONFIDENCE_THRESHOLD:
        evidence.append(
            StepEvidence(
                "decision",
                f"置信度 {best_confidence:.2f} < 阈值 {CONFIDENCE_THRESHOLD}，判定为无法探测",
                0.0,
                details={
                    "confidence": best_confidence,
                    "threshold": CONFIDENCE_THRESHOLD,
                    "raised": "ProfileUndetectable",
                },
            )
        )
        raise ProfileUndetectable(
            f"数据规格探测置信度过低: {best_confidence:.2f}",
            context={
                "steps": [e.to_dict() for e in evidence],
                "candidates": candidates,
                "hint_market": hint_market,
                "hint_period": hint_period,
            },
        )

    evidence.append(
        StepEvidence(
            "decision",
            f"最终置信度 {best_confidence:.2f}，返回 profile {best_profile.name}",
            round(best_confidence, 4),
            details={
                "confidence": best_confidence,
                "preset_name": preset_name,
                "chosen": best_candidate,
            },
        )
    )

    # 把最终置信度与预设名写回 profile
    best_profile = replace(
        best_profile,
        confidence=best_confidence,
        notes=(best_profile.notes or "") + (f"; preset={preset_name}" if preset_name else ""),
    )

    return DetectionResult(
        profile=best_profile,
        confidence=best_confidence,
        candidates=candidates,
        evidence=evidence,
        preset_name=preset_name,
    )


# --------------------------------------------------------------------------- #
# 预设迭代辅助
# --------------------------------------------------------------------------- #
def _iter_presets():
    """延迟导入预设（避免循环引用）。"""
    from .presets import PRESETS

    yield from PRESETS.values()


__all__.append("_iter_presets")
