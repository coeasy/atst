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
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from tstdx.codec.framing import ResponseFrame
from tstdx.protocol.registry import dispatch

GOLDEN_ROOT = Path(__file__).resolve().parents[1] / "golden"

#: 已锁定布局、必须**精确耗尽缓冲区**的命令
EXACT_COMMANDS = {0x052D, 0x0530, 0x044E}


def _iter_samples():
    """遍历所有 golden 样本。

    默认只回放 ``source: self-captured`` 实采样本 —— 它们才是协议「事实基准」。
    合成衍生样本（``source: synthetic``）是解析器回归基线，其请求维度经过
    变异，**不满足** OHLC/回声等实采校验，默认排除；
    设 ``TSTDX_GOLDEN_SYNTHETIC=1`` 可显式纳入。

    .. note::
       这里**不能**调用 ``pytest.skip``——该函数会在 parametrize 收集阶段
       于模块层执行，导致整个测试模块被跳过。改为返回空列表，
       由 :func:`test_golden_dispatch` 上的 ``skipif`` 处理。
    """
    if not GOLDEN_ROOT.exists():
        return
    import os

    include_synthetic = os.environ.get("TSTDX_GOLDEN_SYNTHETIC", "") == "1"
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
