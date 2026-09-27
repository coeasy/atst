"""离线集成测试（§36）：不依赖网络，验证各层在 golden 样本上的贯通。

覆盖：client 行→模型转换、output 归一化与
依赖缺失报错、config 校验、符号/周期解析。这些用例全部可在无网环境复现，
是「全量落地」的收口验证。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from atst.client import _row_to_bar, _row_to_quote, period_to_category, split_symbol
from atst.config.schema import Config
from atst.domain.models import Bar, Quote
from atst.errors import DependencyMissingError, ValidationError
from atst.output import Sink, _normalize

GOLDEN = Path(__file__).resolve().parents[2] / "tests" / "golden"


def _frame(method, meta, payload):
    from atst.codec.framing import ResponseFrame

    resp = meta["response"]
    return ResponseFrame(
        magic=0x0074CBB1,
        zip_flag=1,
        seq=0,
        method=method,
        zip_size=resp["zip_size"],
        unzip_size=resp["unzip_size"],
        payload=payload,
    )


def _latest_sample(name: str):
    cand = sorted(GOLDEN.glob(f"quotation/{name}/20*"), reverse=True)
    assert cand, f"缺少 golden 样本: {name}"
    meta = __import__("json").loads((cand[0] / "meta.json").read_text(encoding="utf-8"))
    payload = (cand[0] / "payload.bin").read_bytes()
    return meta, payload


def _pinned_sample(name: str, ts: str):
    """取**固定时间戳**样本：数值断言类测试必须锁定设计时依据的罐头，
    不得随补录漂移到新实采样本（行情值每日变化）。"""
    d = GOLDEN / "quotation" / name / ts
    assert d.is_dir(), f"缺少锚定 golden 样本: {name}/{ts}"
    meta = __import__("json").loads((d / "meta.json").read_text(encoding="utf-8"))
    payload = (d / "payload.bin").read_bytes()
    return meta, payload


# --------------------------------------------------------------------------- #
# client 转换
# --------------------------------------------------------------------------- #
def test_split_symbol():
    assert split_symbol("sh600519") == (1, "600519")
    assert split_symbol("sz000651") == (0, "000651")
    # 裸 000001 → 深市平安银行（符号白名单新契约；上证指数请写 sh000001）
    assert split_symbol("000001") == (0, "000001")
    assert split_symbol("sh000001") == (1, "000001")


def test_period_to_category():
    assert period_to_category("day") == 4
    assert period_to_category("15min") == 1
    assert period_to_category("3") == 3


def test_client_quote_conversion_from_golden():
    from atst.protocol.registry import dispatch

    meta, payload = _latest_sample("0x0530_realtime_quote_600519")
    frame = _frame(0x0530, meta, payload)
    ctx = dict(meta["parse_ctx"])
    result = dispatch(frame, code=ctx["code"], market=ctx["market"], price_scale=100)
    q = _row_to_quote(result.rows[0])
    assert isinstance(q, Quote)
    assert q.code == "600519"
    assert q.price == 1292.1
    assert q.high == 1305.0 and q.low == 1286.0
    assert q.volume == 1520800  # 股（已 ×100 归一）


def test_client_bar_conversion_from_golden():
    from atst.protocol.registry import dispatch

    # 锚定 2026-08-31 实采罐头（9.16）；新补录样本行情值每日变化不可作数值断言
    meta, payload = _pinned_sample("0x052d_security_bars_600000_cat4", "20260831-125353")
    frame = _frame(0x052D, meta, payload)
    result = dispatch(frame, category=4)
    bar = _row_to_bar(result.rows[-1])
    assert isinstance(bar, Bar)
    assert bar.close == 9.16
    assert bar.volume == 64442012


# --------------------------------------------------------------------------- #
# sinks
# --------------------------------------------------------------------------- #
def test_sinks_normalize():
    bars = [
        Bar(datetime="2026-08-31 15:00", open=1, high=2, low=1, close=1.5, volume=10, amount=15)
    ]
    rows = _normalize(bars)
    assert rows[0]["close"] == 1.5


def test_sinks_missing_dependency_errors(monkeypatch):
    """缺依赖必须显式报错 —— 通过屏蔽 import 模拟未安装 extras，与环境无关。"""
    import builtins

    real_import = builtins.__import__

    def _blocked(name, *args, **kwargs):
        if name.split(".")[0] in {"pyarrow", "duckdb"}:
            raise ImportError(f"blocked for test: {name}")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _blocked)

    bars = [
        Bar(datetime="2026-08-31 15:00", open=1, high=2, low=1, close=1.5, volume=10, amount=15)
    ]

    with pytest.raises(DependencyMissingError):
        Sink("parquet", path="x.parquet").write(bars)
    with pytest.raises(DependencyMissingError):
        Sink("duckdb", table="t").write(bars)


def test_sinks_unknown_format():
    bars = [
        Bar(datetime="2026-08-31 15:00", open=1, high=2, low=1, close=1.5, volume=10, amount=15)
    ]
    with pytest.raises(ValueError):
        Sink("xml").write(bars)


# --------------------------------------------------------------------------- #
# config 校验
# --------------------------------------------------------------------------- #
def test_provider_config_validation():
    cfg = Config()
    cfg.core.default_provider = "bogus"
    with pytest.raises(ValidationError, match="未知 provider"):
        cfg.validate()
    # 合法配置应通过
    Config().validate()
