# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""Q1-a：facade/api.py 路由链 vs sources/DataSourceRouter 五级链 口径对拍（离线）。

背景：两套链取数内核同源（TdxClient / WebQuoteClient / DayBarReader），
但存在多处口径差异。本文件用 mock 客户端 + 合成 vipdoc 把已裁定差异
**锁定**为测试（差异裁定记录见 ``docs/adr/ADR-012-路由链合并口径对拍.md``）：

* D1 start 偏移语义 —— 已裁定以 facade 为准，router ``kline``/``_reader_kline``
  增加 ``start`` 参数（本文件验证 router.start == facade.start）；
* D2 空结果语义 —— router ``default_empty_ok=True`` 时空列表返回而非抛
  ``AllSourcesExhausted``（Q1-b 合并时 facade 用 True 单源调用）；
* D3 web adjust 口径 —— 两链 web klines 均传 ``adjust=""``（原始价），锁定；
* D4/D5/D6 tdx / web / reader 通路参数与返回行等价性（mock 离线对拍）。

对拍口径说明：所有输入使用 canonical 符号（``sh600519``）。已知差异
（facade 先 ``normalize_symbol`` 而 router 部分入口透传原始串、facade tdx
传 ``timeout`` 与 ``as_format="obj"``、router web 固定 ``sources=[KLINE]``）
在 ADR-012 记录，不作为等价性断言项。
"""

from __future__ import annotations

import struct
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

import tstdx.client as client_mod
import tstdx.web as web_mod
from tstdx.errors import AllSourcesExhausted
from tstdx.facade import UnifiedQuoteAPI
from tstdx.sources import DataSourceRouter

_CODE = "sh600519"
_SCALE = 100


# --------------------------------------------------------------------------- #
# fake 客户端（记录调用参数，返回固定行）
# --------------------------------------------------------------------------- #
class FakeTdxClient:
    """TdxClient 替身：记录构造与方法调用参数，回放固定行。"""

    ctor_args: list[dict[str, Any]] = []
    quotes_calls: list[dict[str, Any]] = []
    bars_calls: list[dict[str, Any]] = []
    #: quotes 回放行
    quote_rows: list[Any] = []
    #: bars 回放行（空列表 → 测 D2 空结果语义）
    bar_rows: list[Any] = []

    def __init__(self, **kw: Any) -> None:
        self.kw = kw
        FakeTdxClient.ctor_args.append(kw)

    # context manager（facade 惰性 property 不 with，router 侧 with）
    def __enter__(self) -> FakeTdxClient:
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def close(self) -> None:
        pass

    def quotes(self, symbols: Any, *, as_format: str = "dict", **kw: Any) -> list[Any]:
        FakeTdxClient.quotes_calls.append({"symbols": list(symbols), "as_format": as_format, **kw})
        return list(FakeTdxClient.quote_rows)

    def bars(self, symbol: str, **kw: Any) -> list[Any]:
        FakeTdxClient.bars_calls.append({"symbol": symbol, **kw})
        # 模拟真实 TdxClient.bars 的窗口语义：end=len-start, begin=end-count
        rows = list(FakeTdxClient.bar_rows)
        start = int(kw.get("start", 0) or 0)
        count = int(kw.get("count", 320) or 0)
        end = max(0, len(rows) - start)
        begin = max(0, end - count) if count else 0
        return rows[begin:end]


class FakeWebQuoteClient:
    """WebQuoteClient 替身：同上。"""

    ctor_args: list[dict[str, Any]] = []
    quotes_calls: list[dict[str, Any]] = []
    klines_calls: list[dict[str, Any]] = []
    quote_rows: list[Any] = []
    bar_rows: list[Any] = []

    def __init__(self, **kw: Any) -> None:
        self.kw = kw
        FakeWebQuoteClient.ctor_args.append(kw)

    def close(self) -> None:
        pass

    def quotes(self, symbols: Any, **kw: Any) -> list[Any]:
        FakeWebQuoteClient.quotes_calls.append({"symbols": list(symbols), **kw})
        return list(FakeWebQuoteClient.quote_rows)

    def klines(self, symbol: str, **kw: Any) -> list[Any]:
        FakeWebQuoteClient.klines_calls.append({"symbol": symbol, **kw})
        # 模拟真实 WebQuoteClient.klines：末尾 count 根（web 无 start 语义）
        rows = list(FakeWebQuoteClient.bar_rows)
        count = int(kw.get("count", 320) or 0)
        return rows[-count:] if count else rows


@pytest.fixture()
def fake_tdx(monkeypatch: pytest.MonkeyPatch) -> type[FakeTdxClient]:
    monkeypatch.setattr(client_mod, "TdxClient", FakeTdxClient)
    FakeTdxClient.ctor_args = []
    FakeTdxClient.quotes_calls = []
    FakeTdxClient.bars_calls = []
    FakeTdxClient.quote_rows = [
        {
            "code": _CODE,
            "price": 10.0,
            "last_close": 9.9,
            "open": 9.95,
            "high": 10.2,
            "low": 9.9,
            "volume": 100,
            "amount": 1000.0,
            "bid": [],
            "ask": [],
        },
        {
            "code": "sz000001",
            "price": 5.0,
            "last_close": 5.1,
            "open": 5.05,
            "high": 5.2,
            "low": 4.9,
            "volume": 200,
            "amount": 1000.0,
            "bid": [],
            "ask": [],
        },
    ]
    FakeTdxClient.bar_rows = [
        {
            "datetime": f"2024-01-{i + 1:02d} 15:00",
            "open": 10.0 + i,
            "high": 10.5 + i,
            "low": 9.5 + i,
            "close": 10.1 + i,
            "volume": 100 * (i + 1),
            "amount": 1e4 * (i + 1),
        }
        for i in range(8)
    ]
    return FakeTdxClient


@pytest.fixture()
def fake_web(monkeypatch: pytest.MonkeyPatch) -> type[FakeWebQuoteClient]:
    monkeypatch.setattr(web_mod, "WebQuoteClient", FakeWebQuoteClient)
    FakeWebQuoteClient.ctor_args = []
    FakeWebQuoteClient.quotes_calls = []
    FakeWebQuoteClient.klines_calls = []
    FakeWebQuoteClient.quote_rows = list(FakeTdxClient.quote_rows)
    FakeWebQuoteClient.bar_rows = [
        {
            "datetime": f"2024-01-{i + 1:02d} 15:00",
            "open": 10.0 + i,
            "high": 10.5 + i,
            "low": 9.5 + i,
            "close": 10.1 + i,
            "volume": 100 * (i + 1),
            "amount": 1e4 * (i + 1),
        }
        for i in range(8)
    ]
    return FakeWebQuoteClient


def _build_day_file(root: Path, *, n: int = 8, base: int = 100) -> Path:
    """合成 .day 文件（同 tests/facade/test_bars_local_route.py 口径）。"""
    lday = root / "sh" / "lday"
    lday.mkdir(parents=True, exist_ok=True)
    recs = [
        struct.pack(
            "<IIIIIfII",
            20240101 + i,
            (base + i) * _SCALE,
            (base + 10 + i) * _SCALE,
            (base - 1 + i) * _SCALE,
            (base + 5 + i) * _SCALE,
            1_000_000.0 * (i + 1),
            1000 * (i + 1),
            (base + 4 + i) * _SCALE,
        )
        for i in range(n)
    ]
    path = lday / f"{_CODE}.day"
    path.write_bytes(b"".join(recs))
    return path


@pytest.fixture()
def vipdoc(tmp_path: Path) -> Iterator[Path]:
    _build_day_file(tmp_path)
    yield tmp_path


def _api() -> UnifiedQuoteAPI:
    return UnifiedQuoteAPI(hosts=["fake:1"])


def _router(order: list[str], **kw: Any) -> DataSourceRouter:
    return DataSourceRouter(order=order, tdx_hosts=["fake:1"], **kw)


# --------------------------------------------------------------------------- #
# D4：tdx 通路对拍
# --------------------------------------------------------------------------- #
class TestTdxParity:
    def test_quotes_call_args_and_rows_identical(self, fake_tdx: type[FakeTdxClient]) -> None:
        """facade.quotes(route=tdx) 与 router.quotes(order=[tdx]) 参数/行一致。

        Q1-b 合并后 facade tdx 路由即经 router 单源调用——两链调用参数
        逐字相等（历史差异 as_format="obj" vs "dict" 消除：TdxClient 的
        "obj" 实经 _emit 归一为 dict 行，两链返回行一致）。
        """
        fac = _api().quotes([_CODE, "sz000001"], route="tdx")
        router = _router(order=["tdx"]).quotes([_CODE, "sz000001"])
        assert FakeTdxClient.quotes_calls == [
            {"symbols": [_CODE, "sz000001"], "as_format": "dict"},
            {"symbols": [_CODE, "sz000001"], "as_format": "dict"},
        ]
        # 返回行一致（fake 回放 dict 行；facade/router 均原样回放 dict 行）
        assert fac == router

    def test_bars_call_args_and_rows_identical(self, fake_tdx: type[FakeTdxClient]) -> None:
        """facade.bars(route=tdx) 与 router.kline(order=[tdx]) 参数/行一致。

        Q1-b 合并后 facade tdx 路由即经 router 单源调用——D5 输出形态差异
        消除（两链均 as_format="dict"，facade 在壳内把 dict 行转 Bar 模型）；
        其余参数（symbol/period/count/start）必须逐项相等。
        """
        _api().bars(_CODE, period="day", count=5, start=2, route="tdx")
        out = _router(order=["tdx"]).kline(_CODE, period="day", count=5, start=2)
        assert len(FakeTdxClient.bars_calls) == 2
        fac_call, router_call = FakeTdxClient.bars_calls
        assert fac_call["symbol"] == router_call["symbol"] == _CODE
        assert fac_call["period"] == router_call["period"] == "day"
        assert fac_call["count"] == router_call["count"] == 5
        # D1：router 新增 start 透传给 TdxClient.bars（原本无此参数）
        assert fac_call["start"] == router_call["start"] == 2
        # 合并后两链调用参数完全一致（facade 亦经 router 单源）
        assert fac_call["as_format"] == router_call["as_format"] == "dict"
        assert out == FakeTdxClient.bar_rows[-5 - 2 : -2]

    def test_router_kline_start_zero_backcompat(self, fake_tdx: type[FakeTdxClient]) -> None:
        """start=0（默认）与历史行为完全一致：末尾 count 根、不透传偏移差异。"""
        out = _router(order=["tdx"]).kline(_CODE, period="day", count=3)
        assert FakeTdxClient.bars_calls[0]["start"] == 0
        assert out == FakeTdxClient.bar_rows[-3:]

    def test_router_start_semantics_match_facade(self, fake_tdx: type[FakeTdxClient]) -> None:
        """D1 已裁定：router start 语义 = facade（end=len-start, begin=end-count）。

        Q1-b 合并后 facade 返回 Bar 模型（壳内 dict 行 → _row_to_bar），
        与 router dict 行经同一转换逐字段相等。
        """
        fac = _api().bars(_CODE, count=2, start=3, route="tdx")
        router = _router(order=["tdx"]).kline(_CODE, count=2, start=3)
        assert [b.to_dict() for b in fac] == router == FakeTdxClient.bar_rows[3:5]


# --------------------------------------------------------------------------- #
# D5：web 通路对拍
# --------------------------------------------------------------------------- #
class TestWebParity:
    def test_quotes_call_args_and_rows_identical(self, fake_web: type[FakeWebQuoteClient]) -> None:
        """facade.quotes(route=web) 与 router.quotes(order=[web]) 参数/行一致。

        Q1-b 合并后 facade web 路由即经 router 单源调用（调用参数逐字相等）；
        facade 在壳内把 dict 行转回 Quote 模型（保持 list[Quote] 契约），
        故与 router dict 行经同一 ``_row_to_quote`` 转换后比较。
        """
        fac = _api().quotes([_CODE], route="web")
        router = _router(order=["web"]).quotes([_CODE])
        assert FakeWebQuoteClient.quotes_calls == [
            {"symbols": [_CODE]},
            {"symbols": [_CODE]},
        ]
        from tstdx.client_core import _row_to_quote

        assert fac == [_row_to_quote(r) for r in router]

    def test_klines_adjust_both_raw_price(self, fake_web: type[FakeWebQuoteClient]) -> None:
        """D3 锁定：facade adjust=None→\"\" 与 router 固定 \"\" 完全一致（原始价）。"""
        _api().bars(_CODE, count=5, route="web", adjust=None)
        out = _router(order=["web"]).kline(_CODE, period="day", count=5)
        assert len(FakeWebQuoteClient.klines_calls) == 2
        fac_call, router_call = FakeWebQuoteClient.klines_calls
        assert fac_call["symbol"] == router_call["symbol"] == _CODE
        assert fac_call["period"] == router_call["period"] == "day"
        assert fac_call["count"] == router_call["count"] == 5
        # 核心口径：两链 web klines 的 adjust 都是 ""（不复权）
        assert fac_call["adjust"] == router_call["adjust"] == ""
        assert out == FakeWebQuoteClient.bar_rows[-5:]


# --------------------------------------------------------------------------- #
# D6：local / reader 通路对拍
# --------------------------------------------------------------------------- #
class TestReaderParity:
    def test_rows_identical_at_start_zero(self, vipdoc: Path) -> None:
        """start=0：facade route=local 与 router order=[reader] 行集一致。"""
        fac = _api()
        fac.vipdoc_root = str(vipdoc)
        fac_bars = fac.bars(_CODE, period="day", count=5, route="local")
        router = _router(order=["reader"], vipdoc_root=vipdoc).kline(_CODE, period="day", count=5)
        # reader read(output="dict") 即 Bar.to_dict，两链行应逐字段相等
        assert [b.to_dict() for b in fac_bars] == router
        assert [b.close for b in fac_bars] == [108.0, 109.0, 110.0, 111.0, 112.0]

    def test_start_semantics_match_facade(self, vipdoc: Path) -> None:
        """start>0：facade local 语义（跳过最近 start 根再取 count 根）
        = 新 router reader start 语义。"""
        fac = _api()
        fac.vipdoc_root = str(vipdoc)
        fac_bars = fac.bars(_CODE, period="day", count=2, start=3, route="local")
        router = _router(order=["reader"], vipdoc_root=vipdoc).kline(
            _CODE, period="day", count=2, start=3
        )
        assert [b.to_dict() for b in fac_bars] == router
        # 8 根升序（close=105..112）：start=3 跳过 110,111,112 → 108,109
        assert [b.close for b in fac_bars] == [108.0, 109.0]

    def test_start_beyond_len_clamps(self, vipdoc: Path) -> None:
        """start 超过数据长度：两链一致返回从头部开始的窗口。

        边界差异锁定（ADR-012 D2 边缘情形）：start>=len 时窗口为空——
        facade local 视空为成功返回 []；router 默认口径空=不可用会抛
        AllSourcesExhausted，需 default_empty_ok=True 才对齐（Q1-b 合并
        时 facade 用 True 单源调用，届时行为一致）。
        """
        fac = _api()
        fac.vipdoc_root = str(vipdoc)
        fac_bars = fac.bars(_CODE, period="day", count=2, start=100, route="local")
        router = _router(order=["reader"], vipdoc_root=vipdoc).kline(
            _CODE, period="day", count=2, start=100, default_empty_ok=True
        )
        assert [b.to_dict() for b in fac_bars] == router == []
        # 两链窗口算式一致：start>=len → end 钳制为 0 → 空窗口


# --------------------------------------------------------------------------- #
# D2：空结果语义
# --------------------------------------------------------------------------- #
class TestEmptyResultSemantics:
    def test_default_empty_ok_true_returns_empty(self, fake_tdx: type[FakeTdxClient]) -> None:
        """default_empty_ok=True：空列表视为该源成功，直接返回而非抛
        AllSourcesExhausted（Q1-b 合并时 facade 单源调用将用此口径）。"""
        FakeTdxClient.bar_rows = []
        out = _router(order=["tdx"]).kline(_CODE, default_empty_ok=True)
        assert out == []
        assert _router(order=["tdx"]).last_source is None  # 新实例未污染

    def test_default_false_raises_all_sources_exhausted(
        self, fake_tdx: type[FakeTdxClient]
    ) -> None:
        """默认口径保持：空 = 不可用继续降级，全空抛 AllSourcesExhausted。"""
        FakeTdxClient.bar_rows = []
        with pytest.raises(AllSourcesExhausted):
            _router(order=["tdx"]).kline(_CODE)
