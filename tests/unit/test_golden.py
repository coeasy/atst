"""Golden 样本回归测试。

样本全部由 ``python -m tstdx.tools.capture`` **自采集**（满足洁净室约束），
存放在 ``tests/golden/<family>/0x<cmd>_<name>/<timestamp>/``，
只保存原始 payload + 元信息，**不保存解析结果**——因此解析器改动不会
污染样本，样本可以长期作为协议的「事实基准」。

断言策略
--------
1. **完整性**：payload 的 sha256 与 meta 一致（样本未被篡改）。
2. **精确性**：标注 ``verified=True`` 的 L1 解析器必须**按字节耗尽缓冲区**
   （``reader_meta == payload_len``），这是字段布局正确的最强证据。
3. **合理性**：数值落在 OHLC × 成交量构成的区间内（防止解码漂移后仍然自洽）。
4. **不丢包**：任何样本都必须解析出结果，绝不静默返回空。
5. **域内合法**（V18 第 9 轮 / F-37②·G3）：账本自报 ``tier=L1 且 verified=True`` 的命令，
   拿它**自己的实采样本**重放后，解出的字段值必须落在 domain SSOT 的取值域内。
   这条判据的适用范围由账本推导，不是第 2 条那份手工名单——见
   :func:`test_l1_verified_commands_replay_to_domain_legal_rows` 的说明。
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from dataclasses import fields as dataclass_fields
from pathlib import Path
from typing import Any

import pytest

from tstdx.codec.framing import ResponseFrame
from tstdx.domain.integrity import FIELD_CHECKERS
from tstdx.domain.integrity import row_violations as _row_violations
from tstdx.domain.symbol import Symbol
from tstdx.protocol.commands import TIER_L1
from tstdx.protocol.registry import dispatch
from tstdx.tools.golden_audit import (
    MIN_PAYLOAD_BYTES,
    ORIGIN_REAL,
    _ledger_commands,
    classify_origin,
)

GOLDEN_ROOT = Path(__file__).resolve().parents[1] / "golden"

#: 已锁定布局、必须**精确耗尽缓冲区**的命令
EXACT_COMMANDS = {0x052D, 0x0530, 0x044E}

#: 账本自报「L1 精确解析 + 已验证」的命令号——与 ``golden_audit`` 那句
#: ``all L1 verified commands have real samples`` 用同一个谓词、同一个来源。
L1_VERIFIED_COMMANDS: dict[int, Any] = {c.cmd: c for c in _ledger_commands(None)}


def _iter_samples():
    """遍历所有 golden 样本。

    默认只回放 ``source: self-captured`` 实采样本 —— 它们才是协议「事实基准」。
    合成衍生样本（``source: synthetic``）是解析器回归基线，其请求维度经过
    变异，**不满足** OHLC/回声等实采校验，默认排除；
    设 ``GOLDEN_INCLUDE_SYNTHETIC=1`` 可显式纳入。刻意不用 ``TSTDX_`` 前缀：
    那是配置 schema 与环境覆盖的保留命名空间，未登记的 ``TSTDX_*`` 一旦出现在
    环境里就会让 ``load_config()`` fail closed（见 ``tstdx/config/loader.py``）。

    .. note::
       这里**不能**调用 ``pytest.skip``——该函数会在 parametrize 收集阶段
       于模块层执行，导致整个测试模块被跳过。改为返回空列表，
       由 :func:`test_golden_dispatch` 上的 ``skipif`` 处理。
    """
    if not GOLDEN_ROOT.exists():
        return
    import os

    include_synthetic = os.environ.get("GOLDEN_INCLUDE_SYNTHETIC", "") == "1"
    # 样本路径: tests/golden/<family>/0x<cmd>_<name>/<timestamp>/meta.json
    # 即 golden 之下 3 层，因此 glob 必须是 */*/*/meta.json
    for meta_path in sorted(GOLDEN_ROOT.glob("*/*/*/meta.json")):
        payload_path = meta_path.parent / "payload.bin"
        if not payload_path.exists():
            continue
        if not include_synthetic:
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                continue
            if str(meta.get("source", "")).startswith("synthetic"):
                continue
        yield meta_path, payload_path


#: 收集阶段一次性固化，供 parametrize 的 ids 与 skipif 复用
SAMPLES: list[tuple[Path, Path]] = list(_iter_samples())


def _load(meta_path: Path, payload_path: Path):
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    payload = payload_path.read_bytes()
    return meta, payload


def _make_frame(cmd: int, meta: dict, payload: bytes) -> ResponseFrame:
    """从样本 meta 重建一个可直接分派的 ResponseFrame。

    ``compressed`` 是派生属性（``zip_size != unzip_size``），``raw`` 不是
    dataclass 字段，二者都不能作为构造参数传入。
    """
    resp = meta["response"]
    return ResponseFrame(
        magic=0x0074CBB1,
        zip_flag=1 if resp.get("compressed") else 0,
        seq=0,
        method=cmd,
        zip_size=resp["zip_size"],
        unzip_size=resp["unzip_size"],
        payload=payload,
    )


@pytest.mark.skipif(not SAMPLES, reason="无 golden 样本")
def test_sample_integrity():
    """每个样本的 sha256 必须与采集时记录的一致。"""
    n = 0
    for meta_path, payload_path in SAMPLES:
        meta, payload = _load(meta_path, payload_path)
        want = meta["response"]["sha256"]
        got = hashlib.sha256(payload).hexdigest()
        assert got == want, f"样本被篡改: {meta_path}"
        assert len(payload) == meta["response"]["payload_len"], f"长度不符: {meta_path}"
        n += 1
    assert n > 0


@pytest.mark.skipif(not SAMPLES, reason="无 golden 样本")
@pytest.mark.parametrize(
    "meta_path,payload_path",
    SAMPLES,
    # 深审 L14：ids 必须含 case 层（命令+标的+市场段）与时间戳层——旧实现
    # 只取时间戳目录名，market/标的维度在回放报告中不可见，且同秒采集的
    # 多个样本 id 相互冲突无法区分。
    ids=lambda p: f"{p.parent.parent.name}/{p.parent.name}" if isinstance(p, Path) else str(p),
)
def test_golden_dispatch(meta_path: Path, payload_path: Path):
    """逐样本回放：必须解析出结果，且已验证命令要精确耗尽缓冲区。"""
    meta, payload = _load(meta_path, payload_path)
    cmd = int(meta["command"], 16)
    ctx = dict(meta.get("parse_ctx") or {})

    frame = _make_frame(cmd, meta, payload)
    result = dispatch(frame, family=meta.get("family", "quotation"), **ctx)

    # 4. 绝不丢包（空载荷样本除外 —— 盘后/停牌时段可能合法采到空响应）
    assert result is not None
    assert result.rows or result.tier == "L3" or not any(payload), (
        f"{meta_path.parent.name}: 解析结果为空且未回落 L3"
    )

    # 2. 精确性：已验证命令必须按字节耗尽
    if cmd in EXACT_COMMANDS and result.tier == "L1":
        consumed = result.meta.get("reader_meta")
        assert consumed == len(payload), (
            f"{meta_path.parent.name}: L1 未耗尽缓冲区 "
            f"(消耗 {consumed} / 总长 {len(payload)})，字段布局可能已漂移"
        )


def test_realtime_quote_cross_check():
    """0x0530：现价必须落在当日 OHLC 区间内，成交额落在 [low,high]×量 之间。

    这是「解码漂移」的兜底防线：即使字段整体错位，缓冲区也可能恰好耗尽，
    但数值不会同时满足所有区间约束。
    """
    checked = 0
    for meta_path, payload_path in SAMPLES:
        meta, payload = _load(meta_path, payload_path)
        if int(meta["command"], 16) != 0x0530:
            continue
        ctx = dict(meta.get("parse_ctx") or {})
        if ctx.get("code", "").startswith("000") and int(ctx["code"][3:]) < 100:
            continue  # 指数量纲不同，跳过区间校验
        frame = _make_frame(0x0530, meta, payload)
        r = dispatch(frame, **ctx)
        assert r.tier == "L1", f"{ctx.get('code')}: 未走 L1（{r.tier}）{r.warnings}"
        q = r.rows[0]
        assert q["code"] == ctx["code"], f"回声代码不符: {q['code']} != {ctx['code']}"

        low, high, price = q["low"], q["high"], q["price"]
        assert low - 1e-6 <= high, f"{q['code']}: low > high ({low} > {high})"
        assert low - 0.02 <= price <= high + 0.02, (
            f"{q['code']}: 现价 {price} 越出 OHLC 区间 [{low}, {high}]"
        )
        assert q["volume"] > 0, f"{q['code']}: 成交量为 0"
        assert q["amount"] > 0, f"{q['code']}: 成交额为 0"
        # 均价 = 额 / 量，必须落在 [low, high]
        vwap = q["amount"] / q["volume"]
        assert low * 0.9 <= vwap <= high * 1.1, f"{q['code']}: 均价 {vwap:.3f} 越出 [{low}, {high}]"
        checked += 1
    assert checked >= 8, f"0x0530 样本过少（{checked}），请补齐采集"


def test_kline_cross_check():
    """0x052D：均价必须落在 OHLC 区间内（成交量单位归一化的主要防线）。"""
    checked = 0
    for meta_path, payload_path in SAMPLES:
        meta, payload = _load(meta_path, payload_path)
        if int(meta["command"], 16) != 0x052D:
            continue
        frame = _make_frame(0x052D, meta, payload)
        r = dispatch(frame, category=meta["parse_ctx"]["category"])
        assert r.tier == "L1", f"{meta_path.parent.name}: 未走 L1"
        assert r.rows, f"{meta_path.parent.name}: 无记录"
        for bar in r.rows:
            assert bar["low"] - 1e-6 <= bar["high"], f"low > high: {bar}"
            if bar["volume"] <= 0 or bar["amount"] <= 0:
                continue
            vwap = bar["amount"] / bar["volume"]
            assert bar["low"] * 0.5 <= vwap <= bar["high"] * 1.5, (
                f"均价 {vwap:.3f} 越出 [{bar['low']}, {bar['high']}]（成交量单位可能未归一）: {bar}"
            )
            checked += 1
    assert checked >= 20, f"K 线交叉校验样本过少（{checked}）"


# ---------------------------------------------------------------------------
# 第 5 条判据：L1 + verified 的声明必须能被自己的实采样本证伪
# ---------------------------------------------------------------------------

#: 字段名的唯一来源：domain SSOT 的 :class:`~tstdx.domain.symbol.Symbol` 有哪几个字段，
#: 那把尺子就管哪几个字段——不另立一份词表（抄一次就过期，F-42）。尺子本身在
#: :mod:`tstdx.domain.integrity`：同一个判断在出口给调用方看（G7），测试不许各养一份。
_SYMBOL_FIELDS = frozenset(f.name for f in dataclass_fields(Symbol))


def test_the_ruler_covers_every_symbol_field() -> None:
    """``Symbol`` 长出新字段而尺子没跟上 ⇒ 立刻红，而不是静默少判一格。"""
    assert set(FIELD_CHECKERS) == _SYMBOL_FIELDS, (
        f"尺子覆盖 {sorted(FIELD_CHECKERS)} 与 domain 字段 {sorted(_SYMBOL_FIELDS)} 不符"
    )


def test_the_field_rule_fires_on_a_planted_fabricated_row() -> None:
    """正控：把 F-37 实测到的那类错位值种进去，四条都要被认出——顺带证明合法行不误伤。"""
    planted = {
        "market": 48,  # ASCII '0' 被当成市场编号读
        "code": "519\x01",  # 2026-09-22 盘中实测：错位记录剩下的半个代码带着控制字节
        "date": "4231-66-07",  # 日期形状却不是真日历日
        "extra": {"code": ""},  # 嵌套层里的空代码
    }
    assert len(_row_violations(planted)) == 4, _row_violations(planted)
    assert _row_violations({"market": 1, "code": "600000", "date": "2026-08-18"}) == []


def _real_replays():
    """重放全部实采样本，产出 ``(命令号, 样本标签, ParseResult, payload)``。

    只认 ``self-captured``：synthetic 目录里的 payload 是解析器自己产出的形状，拿它
    回放等于让被告给原告作证。两条值域判据共用这一份重放，别在这里养第二份循环。
    """
    for meta_path, payload_path in SAMPLES:
        meta, payload = _load(meta_path, payload_path)
        if classify_origin(meta.get("source")) != ORIGIN_REAL:
            continue
        command = int(meta["command"], 16)
        frame = _make_frame(command, meta, payload)
        result = dispatch(
            frame,
            family=meta.get("family", "quotation"),
            **dict(meta.get("parse_ctx") or {}),
        )
        yield command, f"{meta_path.parent.parent.name}/{meta_path.parent.name}", result, payload


def test_l1_verified_commands_replay_to_domain_legal_rows() -> None:
    """账本说「L1 精确解析、已验证」，代价是：它自己的实采样本重放后字段值必须合法。

    F-37②（v17 台账）留着的半句话是"这两条命令的 golden 从不校验解析出的字段值"。
    本轮把这句话变成判据前先量了一遍：``0x000F`` 的 4 份实采样本按它自己的解析器重放，
    910 行里 ``market`` 取 48/52/56…（那是 ASCII 数字的字节值）、``code`` 取
    ``''``/``'001'``、``date`` 取 ``'3110811704'``——没有一行落在域内，而账本当时写的是
    ``tier=L1, verified=True``。那个"L1"根本不是做出来的判断：``register_parser`` 的
    ``tier`` 缺省就是 L1，写解析器的人没填它，账本照着缺省升了级。

    为什么第 2 条判据（按字节耗尽缓冲区）没抓到它：那条要求挂在手工名单 ``EXACT_COMMANDS``
    上，而名单里没有 0x000F；把名单换成账本推导也不行——实测 0x000F 的解析器用
    ``reader.rest()`` 取正文，而 ``rest()`` 不推进 ``pos``（``tstdx/codec/primitive.py:145``），
    ``reader_meta`` 永远停在 2，4/4 样本都会假红。那把尺子对"以 ``rest()`` 取正文的解析器"
    结构性失明，本判据因此走语义（值域）而不是走字节数。

    只认 ``self-captured`` 样本：synthetic 目录里的 payload 是解析器自己产出的形状，
    拿它回放等于让被告给原告作证。
    """
    in_scope: set[int] = set()
    rows_by_command: Counter[int] = Counter()
    problems: list[str] = []
    for command, label, result, payload in _real_replays():
        if command not in L1_VERIFIED_COMMANDS:
            continue
        floor = MIN_PAYLOAD_BYTES.get(command)
        if floor is not None and len(payload) < floor:
            continue  # 低于 payload 下限的空桩不作语义证据（与 golden_audit 同口径）
        in_scope.add(command)
        if result.tier != TIER_L1:
            problems.append(
                f"{label}: 账本声明 L1，实收 {result.tier}（落到兜底去了）warnings={result.warnings}"
            )
        rows_by_command[command] += len(result.rows)
        for index, row in enumerate(result.rows):
            problems.extend(f"{label} 第 {index} {v}" for v in _row_violations(row))

    assert len(in_scope) >= 3, (
        f"L1∧verified 且带实采样本的命令只剩 {sorted(f'0x{c:x}' for c in in_scope)}，"
        "少于三条：要么账本被清空，要么谓词改名了——判据自身失效"
    )
    for command in sorted(in_scope):
        if not rows_by_command[command]:
            problems.append(f"0x{command:04X}: 实采样本一份行都没解出来，L1 声明无证据")
    assert problems == [], "自报 L1 的命令重放实采样本后字段值不合法：\n" + "\n".join(
        problems[:25] + ([f"…另有 {len(problems) - 25} 条"] if len(problems) > 25 else [])
    )


# ---------------------------------------------------------------------------
# 第 6 条判据：尺子上了生产侧，误伤的代价就升级（G7 的反向证据）
# ---------------------------------------------------------------------------

#: 实采样本里今天唯一两条解出越域值的命令——G3 那两处未经真机 golden 锁定的布局
#: (``0x000F`` 股本变迁、``0x0010`` 财务)。这个集合是第 14 轮量出来的（60 份实采样本
#: 重放出 1667 行，越域行全部落在这两条命令上），不是先验名单。
_DOMAIN_SUSPECT_COMMANDS = frozenset({0x000F, 0x0010})


def test_the_ruler_stays_silent_on_every_other_captured_command() -> None:
    """值域尺子现在会从公共 API 的出口对**所有**命令发声，所以它不许对别的命令误伤。

    第 9 轮它只量 ``L1∧verified`` 那三条命令，误伤面天然窄；第 14 轮把它接进
    ``_forward_decode_caveats`` 之后，一次假告警就等于把"这条结果不可信"写在一条好数据
    上——调用方很快会学会无视整条通道（F-13/F-16 那族反过来的形态）。所以这里把
    "除嫌疑两条之外全部安静"钉成判据。
    """
    rows_by_command: Counter[int] = Counter()
    offenders: Counter[int] = Counter()
    replays = list(_real_replays())
    for command, _label, result, _payload in replays:
        rows_by_command[command] += len(result.rows)
        bad = sum(1 for row in result.rows if _row_violations(row))
        if bad:
            offenders[command] += bad

    #: 自检：分母塌了本判据就是空话，先证 scanners 没瞎。
    assert len(replays) >= 40, f"实采样本只重放出 {len(replays)} 份，免检名单没有分母"
    assert len(rows_by_command) >= 6, f"只覆盖到 {len(rows_by_command)} 条命令，负控没有分母"
    clean_rows = sum(n for c, n in rows_by_command.items() if c not in _DOMAIN_SUSPECT_COMMANDS)
    assert clean_rows >= 200, f"非嫌疑命令只重放出 {clean_rows} 行，「不误伤」没有被量过"

    false_positives = sorted(c for c in offenders if c not in _DOMAIN_SUSPECT_COMMANDS)
    assert false_positives == [], "值域尺子对实采样本误伤：" + "；".join(
        f"0x{c:04X} 有 {offenders[c]} 行被点名（共 {rows_by_command[c]} 行）"
        for c in false_positives
    )
    missing = sorted(_DOMAIN_SUSPECT_COMMANDS - set(rows_by_command))
    assert missing == [], f"嫌疑命令的实采样本不见了：{[hex(c) for c in missing]}——G3 失去取证口"
    for command in sorted(_DOMAIN_SUSPECT_COMMANDS):
        assert offenders[command] > 0, (
            f"0x{command:04X} 一行都没被点名，而它的布局仍是推断的：尺子瞎了或样本被换成了合成副本"
        )
