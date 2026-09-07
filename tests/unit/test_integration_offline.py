"""离线集成测试（§36）：不依赖网络，验证各层在 golden 样本上的贯通。

覆盖：client 行→模型转换、DataSourceRouter 的 cache 级降级、sinks 归一化与
依赖缺失报错、config 校验、符号/周期解析。这些用例全部可在无网环境复现，
是「全量落地」的收口验证。
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from tstdx.client import _row_to_bar, _row_to_quote, period_to_category, split_symbol
from tstdx.config.schema import Config, SourcesConfig
from tstdx.domain.models import Bar, Quote
from tstdx.errors import DependencyMissingError, ValidationError
from tstdx.output import Sink, _normalize
from tstdx.sources import DataSourceRouter, SourceUnavailable

GOLDEN = Path(__file__).resolve().parents[2] / "tests" / "golden"


def _frame(method, meta, payload):
    from tstdx.codec.framing import ResponseFrame

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
    from tstdx.protocol.registry import dispatch

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
    from tstdx.protocol.registry import dispatch

    # 锚定 2026-08-31 实采罐头（9.16）；新补录样本行情值每日变化不可作数值断言
    meta, payload = _pinned_sample("0x052d_security_bars_600000_cat4", "20260831-125353")
    frame = _frame(0x052D, meta, payload)
    result = dispatch(frame, category=4)
    bar = _row_to_bar(result.rows[-1])
    assert isinstance(bar, Bar)
    assert bar.close == 9.16
    assert bar.volume == 64442012


# --------------------------------------------------------------------------- #
# DataSourceRouter —— cache 级离线降级
# --------------------------------------------------------------------------- #
def _router_cache():
    cfg = SourcesConfig(
        order=["cache", "synthetic"],
        enabled={"cache": True, "synthetic": True, "tdx": False, "web": False, "reader": False},
    )
    return DataSourceRouter(config=cfg, golden_root=GOLDEN)


def test_router_kline_cache():
    r = _router_cache()
    bars = r.kline("600000", period="day", count=5, as_format="dict")
    assert r.last_source == "cache"
    assert len(bars) == 5
    # cache 源按设计取「最新」实采样本，收盘价随补录漂移——本测试验证
    # 路由与结构；数值正确性由 test_client_bar_conversion_from_golden
    # 锚定罐头（9.16）承担
    assert isinstance(bars[-1]["close"], float) and bars[-1]["close"] > 0


def test_router_quotes_cache():
    r = _router_cache()
    qs = r.quotes(["600519", "000002"], as_format="dict")
    assert r.last_source == "cache"
    codes = {q["code"] for q in qs}
    assert "600519" in codes and "000002" in codes


def test_router_fallback_to_synthetic():
    # 不存在的样本 → cache 失败 → synthetic 兜底
    cfg = SourcesConfig(
        order=["cache", "synthetic"],
        enabled={"cache": True, "synthetic": True, "tdx": False, "web": False, "reader": False},
    )
    r = DataSourceRouter(config=cfg, golden_root=GOLDEN)
    bars = r.kline("999999", period="day", count=3, as_format="dict")
    assert r.last_source == "synthetic"
    # Bar.to_dict() 把 extra 内容合并进顶层 dict，没有 "extra" 这个 key
    assert bars[0]["synthetic"] is True


def test_router_all_unavailable_raises():
    cfg = SourcesConfig(
        order=["tdx", "web"],
        enabled={"tdx": True, "web": True, "cache": False, "synthetic": False, "reader": False},
    )
    r = DataSourceRouter(config=cfg)  # 无 golden，cache/reader 关闭，tdx/web 在线失败
    from tstdx.errors import AllSourcesExhausted

    # 确定性失败：直接让 tdx / web 两个源都抛 SourceUnavailable，
    # 不依赖真实网络（沙箱网络被阻断时行为不可控）。
    with patch("tstdx.client.TdxClient") as MockTdx, patch("tstdx.web.WebQuoteClient") as MockWeb:
        MockTdx.return_value.__enter__.return_value.bars.side_effect = SourceUnavailable("tdx down")
        MockWeb.return_value.klines.side_effect = SourceUnavailable("web down")
        with pytest.raises(AllSourcesExhausted):
            r.kline("600000", period="day", count=5)
    assert r.last_source is None
    assert {s for s, _ in r.last_errors} == {"tdx", "web"}


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
def test_sources_config_validation():
    cfg = Config()
    cfg.sources.order = ["tdx", "bogus"]
    with pytest.raises(ValidationError):
        cfg.validate()
    # 合法配置应通过
    good = Config()
    good.validate()
