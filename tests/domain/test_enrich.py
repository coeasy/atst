# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""宽表日线（``daily_enriched`` 的拼装层）口径测试。

这个模块存在的理由：宽表里**四条口径写错了不会报错，只会给出一个看起来正常的
数**。所以每条都在这里被钉住：

* ``pre_close`` 必须取窗口**前一根** K 线，不是"循环里的前一根"，也不是当日 ``open``；
* ``turnover`` 的分母是 ``FREE_SHARES_A``（股），volume 也是股 —— 单位不一致会让
  换手率差 100 倍；
* ``vol_ratio`` 是**日频近似**，且必须自带 ``vol_ratio_basis`` 标注，不冒充盘中量比；
* ``is_st`` 是**名称启发式**，必须自带 ``is_st_source``，不冒充交易所名单；
* 估值按交易日**等值连接**，不做前向填充 —— 前向填充就是把昨天的估值当今天用。

另有若干"不许静默"的边界：``pre_close`` 缺失时 ``pct_chg``/``amplitude`` 必须是
``None`` 而不是拿 0 或 ``open`` 硬算。
"""

from __future__ import annotations

import pytest

from atst.domain.enrich import (
    ENRICHED_FIELDS,
    VOL_RATIO_WINDOW,
    enrich_daily_bars,
)

pytestmark = pytest.mark.unit


def _bar(
    day: str,
    close: float,
    *,
    volume: float = 1000,
    high: float | None = None,
    low: float | None = None,
) -> dict:
    return {
        "datetime": day,
        "open": close,
        "high": high if high is not None else close,
        "low": low if low is not None else close,
        "close": close,
        "volume": volume,
        "amount": close * volume,
    }


def _val(day: str, **kw) -> dict:
    row = {"TRADE_DATE": f"{day} 00:00:00"}
    row.update(kw)
    return row


class TestColumnContract:
    """列顺序是契约：加列必须改 ``ENRICHED_FIELDS``，否则按位置取数的下游会错位。"""

    def test_row_keys_match_declared_fields(self) -> None:
        rows = enrich_daily_bars([_bar("2026-06-01", 10.0)])
        assert tuple(rows[0].keys()) == ENRICHED_FIELDS

    def test_field_count_is_24(self) -> None:
        #: 21 个业务字段 + 日期/代码类 + 两列来源标注（is_st_source / vol_ratio_basis）
        assert len(ENRICHED_FIELDS) == 24


class TestPreClose:
    """``pre_close`` 取窗口前一根 K 线的收盘价。"""

    def test_first_row_has_no_pre_close(self) -> None:
        rows = enrich_daily_bars([_bar("2026-06-01", 10.0)])
        assert rows[0]["pre_close"] is None
        #: 没有前收盘就不许算涨跌幅（拿 open 或 0 硬算会给出一个假的涨跌幅）
        assert rows[0]["pct_chg"] is None
        assert rows[0]["amplitude"] is None

    def test_uses_previous_bar_close(self) -> None:
        rows = enrich_daily_bars([_bar("2026-06-01", 10.0), _bar("2026-06-02", 11.0)])
        assert rows[1]["pre_close"] == pytest.approx(10.0)
        assert rows[1]["pct_chg"] == pytest.approx(10.0)  # 百分数

    def test_pre_close_is_not_today_open(self) -> None:
        #: 当日 open 与昨收不同（跳空）时，pre_close 必须是昨收，不是 open。
        bars = [_bar("2026-06-01", 10.0), {**_bar("2026-06-02", 11.0), "open": 10.5}]
        rows = enrich_daily_bars(bars)
        assert rows[1]["pre_close"] == pytest.approx(10.0)
        assert rows[1]["pct_chg"] == pytest.approx(10.0)  # (11-10)/10，不是 (11-10.5)/10.5

    def test_amplitude_uses_pre_close_denominator(self) -> None:
        bars = [
            _bar("2026-06-01", 10.0),
            _bar("2026-06-02", 11.0, high=11.5, low=10.2),
        ]
        rows = enrich_daily_bars(bars)
        assert rows[1]["amplitude"] == pytest.approx((11.5 - 10.2) / 10.0 * 100.0)

    def test_zero_pre_close_does_not_divide(self) -> None:
        rows = enrich_daily_bars([_bar("2026-06-01", 0.0), _bar("2026-06-02", 11.0)])
        assert rows[1]["pre_close"] == pytest.approx(0.0)
        assert rows[1]["pct_chg"] is None


class TestTurnover:
    """``turnover = volume / FREE_SHARES_A * 100``（两者单位都是股）。"""

    def test_turnover_uses_float_share(self) -> None:
        bars = [_bar("2026-06-01", 10.0, volume=2_000_000)]
        vals = [_val("2026-06-01", FREE_SHARES_A=100_000_000)]
        rows = enrich_daily_bars(bars, vals)
        assert rows[0]["float_share"] == pytest.approx(100_000_000)
        assert rows[0]["turnover"] == pytest.approx(2.0)  # 2000000/100000000*100

    def test_no_float_share_means_no_turnover(self) -> None:
        rows = enrich_daily_bars([_bar("2026-06-01", 10.0)])
        assert rows[0]["float_share"] is None
        assert rows[0]["turnover"] is None  # 不许拿 total_share 顶替

    def test_fallback_float_share_is_opt_in(self) -> None:
        bars = [_bar("2026-06-01", 10.0, volume=1_000_000)]
        rows = enrich_daily_bars(bars, [], float_share_fallback=50_000_000)
        assert rows[0]["turnover"] == pytest.approx(2.0)


class TestVolRatio:
    """量比是**日频近似**，且必须自报口径。"""

    def test_needs_full_window(self) -> None:
        bars = [_bar(f"2026-06-{d:02d}", 10.0) for d in range(1, VOL_RATIO_WINDOW + 1)]
        rows = enrich_daily_bars(bars)
        assert all(r["vol_ratio"] is None for r in rows)

    def test_ratio_is_volume_over_mean(self) -> None:
        bars = [_bar(f"2026-06-{d:02d}", 10.0, volume=1000) for d in range(1, 6)]
        bars.append(_bar("2026-06-06", 10.0, volume=3000))
        rows = enrich_daily_bars(bars)
        assert rows[-1]["vol_ratio"] == pytest.approx(3.0)

    def test_suspension_in_window_disables_ratio(self) -> None:
        #: 前 5 日里有一天 0 成交（停牌）：含 0 的均值会把量比虚高，故不算。
        bars = [_bar(f"2026-06-{d:02d}", 10.0, volume=1000) for d in range(1, 6)]
        bars[1]["volume"] = 0.0
        bars.append(_bar("2026-06-06", 10.0, volume=3000))
        rows = enrich_daily_bars(bars)
        assert rows[-1]["vol_ratio"] is None

    def test_basis_is_always_declared(self) -> None:
        rows = enrich_daily_bars([_bar("2026-06-01", 10.0)])
        assert rows[0]["vol_ratio_basis"] == "daily_approx"

    def test_zero_volume_today_is_null_not_zero(self) -> None:
        #: 当日 0 成交（停牌，或盘中未开盘时上游回的那根占位 bar：实测 TDX ``bars``
        #: 会回 ``volume=0`` 的当日 bar）。量比此时**没有定义**，报 0.0 会被读成
        #: "只有正常水平的 0%"；``turnover`` 一直是这么处理的，两者必须同口径。
        bars = [_bar(f"2026-06-{d:02d}", 10.0, volume=1000) for d in range(1, 6)]
        bars.append(_bar("2026-06-06", 10.0, volume=0))
        rows = enrich_daily_bars(bars)
        assert rows[-1]["vol_ratio"] is None
        assert rows[-1]["turnover"] is None


class TestIsStHeuristic:
    """``is_st`` 是名称启发式，必须自带来源标注。"""

    def test_st_prefix_detected(self) -> None:
        bars = [_bar("2026-06-01", 10.0)]
        vals = [_val("2026-06-01", SECURITY_NAME_ABBR="*ST海航")]
        rows = enrich_daily_bars(bars, vals)
        assert rows[0]["is_st"] is True
        assert rows[0]["is_st_source"] == "name_heuristic"

    def test_normal_name_not_st(self) -> None:
        bars = [_bar("2026-06-01", 10.0)]
        vals = [_val("2026-06-01", SECURITY_NAME_ABBR="贵州茅台")]
        rows = enrich_daily_bars(bars, vals)
        assert rows[0]["is_st"] is False
        assert rows[0]["is_st_source"] == "name_heuristic"

    def test_no_name_yields_unknown_source(self) -> None:
        #: 拿不到名称时不许把 is_st 说成"不是 ST"——那是"不知道"，是 None。
        rows = enrich_daily_bars([_bar("2026-06-01", 10.0)])
        assert rows[0]["is_st"] is None
        assert rows[0]["is_st_source"] == "unknown"

    def test_mid_name_st_is_not_a_prefix(self) -> None:
        #: 只看**前缀**："ST" 出现在中间（英文缩写等）不构成风险警示。
        bars = [_bar("2026-06-01", 10.0)]
        vals = [_val("2026-06-01", SECURITY_NAME_ABBR="STO科技")]
        rows = enrich_daily_bars(bars, vals)
        assert rows[0]["is_st"] is True  # 前缀命中
        vals = [_val("2026-06-01", SECURITY_NAME_ABBR="宏ST科技")]
        rows = enrich_daily_bars(bars, vals)
        assert rows[0]["is_st"] is False  # 中缀不算

    def test_sst_prefix(self) -> None:
        bars = [_bar("2026-06-01", 10.0)]
        for name in ("SST前锋", "S*ST佳纸", "ST中安"):
            vals = [_val("2026-06-01", SECURITY_NAME_ABBR=name)]
            rows = enrich_daily_bars(bars, vals)
            assert rows[0]["is_st"] is True, name


class TestRawVolumeNotAdjusted:
    """复权后的 ``volume`` 不能拿来算换手率/量比（尺度与流通股本不同）。"""

    def test_turnover_prefers_raw_volume(self) -> None:
        bar = _bar("2026-06-01", 10.0, volume=1_000_000)
        #: 复权把 volume 缩到了 1/2，复权前的量留在 extra 里
        bar["extra"] = {"raw_volume": 2_000_000, "adj_volume_factor": 0.5}
        vals = [_val("2026-06-01", FREE_SHARES_A=100_000_000)]
        rows = enrich_daily_bars([bar], vals)
        assert rows[0]["volume"] == pytest.approx(1_000_000)
        assert rows[0]["turnover"] == pytest.approx(2.0)  # 2e6/1e8*100，不是 1.0

    def test_vol_ratio_uses_raw_volume(self) -> None:
        bars = []
        for d in range(1, 6):
            b = _bar(f"2026-06-{d:02d}", 10.0, volume=500)
            b["extra"] = {"raw_volume": 1000}
            bars.append(b)
        last = _bar("2026-06-06", 10.0, volume=1500)
        last["extra"] = {"raw_volume": 3000}
        bars.append(last)
        rows = enrich_daily_bars(bars)
        assert rows[-1]["vol_ratio"] == pytest.approx(3.0)


class TestValuationJoin:
    """估值按交易日**等值**连接。"""

    def test_exact_day_join(self) -> None:
        bars = [_bar("2026-06-01", 10.0), _bar("2026-06-02", 11.0)]
        vals = [
            _val("2026-06-02", PE_TTM=30.0, PB_MRQ=8.0, TOTAL_SHARES=1e9, FREE_SHARES_A=8e8),
            _val("2026-06-01", PE_TTM=28.0),
        ]
        rows = enrich_daily_bars(bars, vals)
        assert rows[0]["pe_ttm"] == pytest.approx(28.0)
        assert rows[1]["pe_ttm"] == pytest.approx(30.0)
        assert rows[1]["pb"] == pytest.approx(8.0)
        assert rows[1]["total_share"] == pytest.approx(1e9)

    def test_missing_day_is_null_not_forward_filled(self) -> None:
        #: 6-02 没有估值行 → 该行估值列必须是 None。前向填充等于把昨天的估值当今天。
        bars = [_bar("2026-06-01", 10.0), _bar("2026-06-02", 11.0)]
        vals = [_val("2026-06-01", PE_TTM=28.0)]
        rows = enrich_daily_bars(bars, vals)
        assert rows[1]["pe_ttm"] is None
        assert rows[1]["name"] == ""

    def test_valuation_outside_window_never_leaks(self) -> None:
        bars = [_bar("2026-06-01", 10.0)]
        vals = [_val("2026-06-02", PE_TTM=30.0)]  # 未来的估值行
        rows = enrich_daily_bars(bars, vals)
        assert rows[0]["pe_ttm"] is None

    def test_timestamp_is_truncated_to_day(self) -> None:
        bars = [_bar("2026-06-01 00:00:00", 10.0)]
        vals = [_val("2026-06-01", PE_TTM=28.0)]
        rows = enrich_daily_bars(bars, vals)
        assert rows[0]["date"] == "2026-06-01"
        assert rows[0]["pe_ttm"] == pytest.approx(28.0)

    def test_case_insensitive_column_lookup(self) -> None:
        bars = [_bar("2026-06-01", 10.0)]
        vals = [{"trade_date": "2026-06-01", "pe_ttm": 28.0}]
        rows = enrich_daily_bars(bars, vals)
        assert rows[0]["pe_ttm"] == pytest.approx(28.0)


class TestNonMappingRows:
    """脏行（非 Mapping）跳过而不是炸掉整段。"""

    def test_non_mapping_valuation_rows_skipped(self) -> None:
        bars = [_bar("2026-06-01", 10.0)]
        rows = enrich_daily_bars(bars, [None, "garbage", 42])  # type: ignore[list-item]
        assert rows[0]["pe_ttm"] is None

    def test_empty_bars(self) -> None:
        assert enrich_daily_bars([]) == []
