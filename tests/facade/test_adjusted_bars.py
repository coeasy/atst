# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""F1：``adjusted_bars`` 复权生产入口测试。

覆盖 :meth:`tstdx.facade.api.UnifiedQuoteAPI.adjusted_bars`：
* 本地 vipdoc 日线 + 在线 0x000F 事件（fake tdx 注入）→ 复权 K 线；
* ``events`` 参数支持 :class:`CapitalChange` 与 0x000F dict 行两种输入；
* ``method="hfq"`` 事件日后价格按除权系数放大（对齐 adjust 引擎语义）。
"""

from __future__ import annotations

import struct
from collections.abc import Iterator
from pathlib import Path

import pytest

from tstdx.domain.finance import CapitalChangeCache
from tstdx.domain.models import CapitalChange
from tstdx.facade import UnifiedQuoteAPI

_CODE = "sh600519"
_SCALE = 100


def _api(vipdoc: Path) -> UnifiedQuoteAPI:
    """测试门面实例：注入独立除权缓存（N4，避免污染进程级单例/落盘）。"""
    return UnifiedQuoteAPI(vipdoc_root=str(vipdoc), factor_cache=CapitalChangeCache())


def _build_day_file(root: Path, *, n: int = 8, base: int = 100) -> Path:
    """构造合成 .day 文件（close 递增 + prev_close 字段，升序日期 20240101+）。"""
    lday = root / "sh" / "lday"
    lday.mkdir(parents=True, exist_ok=True)
    recs = [
        struct.pack(
            "<IIIIIfII",
            20240101 + i,
            (base + i) * _SCALE,
            (base + 10 + i) * _SCALE,
            (base - 1 + i) * _SCALE,
            (base + 5 + i) * _SCALE,  # close = 105+i（i=0 → 105 … i=7 → 112）
            1_000_000.0 * (i + 1),
            1000 * (i + 1),
            (base + 4 + i) * _SCALE,  # 上日收盘 = 104+i
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


class _FakeTdx:
    """替身 tdx 客户端：仅提供 capital_changes（0x000F）。"""

    def __init__(self, rows: list[dict]) -> None:
        self._rows = rows

    def capital_changes(self, symbol: str) -> list[CapitalChange]:
        from tstdx.domain.finance import to_capital_changes

        return to_capital_changes(self._rows)


#: 事件日 2024-01-03（第 3 根 bar），每 10 股送 10（S=10 → 价格因子 11）
_EVENT_ROWS = [
    {
        "code": "600519",
        "market": 1,
        "category": 1,
        "category_name": "除权除息",
        "date": "2024-01-03",
        "dividend": 0.0,
        "rights_price": 0.0,
        "bonus_ratio": 100.0,
        "rights_ratio": 0.0,
    }
]


def test_adjusted_bars_hfq_amplifies_after_event(vipdoc: Path) -> None:
    """hfq：事件日（含）之后价格 ×(1+S)=11，之前不变。"""
    api = _api(vipdoc)
    api._tdx = _FakeTdx(_EVENT_ROWS)
    bars = api.adjusted_bars(_CODE, method="hfq")
    assert len(bars) == 8
    # 前两根（2024-01-01 / 01-02）不变：close=105 / 106
    assert bars[0].close == pytest.approx(105.0)
    assert bars[1].close == pytest.approx(106.0)
    # 事件日（2024-01-03, close=107）起放大 11 倍
    assert bars[2].close == pytest.approx(107.0 * 11.0)
    assert bars[7].close == pytest.approx(112.0 * 11.0)


def test_adjusted_bars_events_param_accepts_capital_change(vipdoc: Path) -> None:
    """events 传 :class:`CapitalChange`：不依赖网络即可复权。"""
    from tstdx.domain.finance import to_capital_changes

    api = _api(vipdoc)
    events = to_capital_changes(_EVENT_ROWS)
    bars = api.adjusted_bars(_CODE, method="hfq", events=events)
    assert bars[2].close == pytest.approx(107.0 * 11.0)
    # 未注入 fake tdx 也不触发网络（events 已提供）
    assert api._tdx is None


def test_adjusted_bars_events_param_accepts_dict_rows(vipdoc: Path) -> None:
    """events 传 0x000F 等价 dict 行：内部统一构造 CapitalChange。"""
    api = _api(vipdoc)
    bars = api.adjusted_bars(_CODE, method="hfq", events=_EVENT_ROWS)
    assert bars[2].close == pytest.approx(107.0 * 11.0)


def test_adjusted_bars_qfq_normalizes_to_latest(vipdoc: Path) -> None:
    """qfq：最后一根与不复权价一致（归一化到最新口径）。"""
    api = _api(vipdoc)
    api._tdx = _FakeTdx(_EVENT_ROWS)
    bars = api.adjusted_bars(_CODE, method="qfq")
    # 原始最后一根 close=112；qfq 归一化后仍为 112
    assert bars[-1].close == pytest.approx(112.0)
    # 事件前价格被等比缩小：105/11（引擎 round_price=4 → 9.5455）
    assert bars[0].close == pytest.approx(105.0 / 11.0, abs=1e-3)


def test_adjusted_bars_count_window_respected(vipdoc: Path) -> None:
    """count 窗口仅作用于最近 N 根（原始 K 线取数语义不变）。"""
    api = _api(vipdoc)
    api._tdx = _FakeTdx(_EVENT_ROWS)
    bars = api.adjusted_bars(_CODE, method="hfq", count=3)
    # 最近 3 根 = 日期 01-06..01-08，全部在事件日后 → ×11
    assert len(bars) == 3
    assert [b.close for b in bars] == pytest.approx([110.0 * 11.0, 111.0 * 11.0, 112.0 * 11.0])


def test_adjusted_bars_cache_hit_skips_network(vipdoc: Path) -> None:
    """N4：同一 symbol 连续两次 adjusted_bars——第二次命中缓存不打 tdx。"""
    api = _api(vipdoc)
    tdx = _FakeTdx(_EVENT_ROWS)
    tdx.calls = 0  # type: ignore[attr-defined]
    orig = tdx.capital_changes

    def counting(symbol: str) -> list[CapitalChange]:
        tdx.calls += 1  # type: ignore[attr-defined]
        return orig(symbol)

    tdx.capital_changes = counting  # type: ignore[method-assign]
    api._tdx = tdx
    api.adjusted_bars(_CODE, method="hfq")
    assert tdx.calls == 1  # type: ignore[attr-defined]  # 首次 miss → 在线
    api.adjusted_bars(_CODE, method="hfq")
    assert tdx.calls == 1  # type: ignore[attr-defined]  # 第二次命中缓存，不打网络
