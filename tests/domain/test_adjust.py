"""复权引擎行为测试（审计 §3-7）。

覆盖：
* 缺 prev_close 忽略现金红利 → 一次性 UserWarning；
* ``bar.extra=None`` 的 None 防御；
* 成交量取整由截断改为 round；
* 送配比例分母 <=0：价格/成交量两条链统一 raise AdjustError；
* 事件日期解析移出内层循环后行为不变（乱序事件集合同样正确）。
"""

from __future__ import annotations

import warnings

import pytest

from atst.domain.adjust import (
    AdjustEngine,
    AdjustError,
    compute_factors,
    to_adjusted,
)
from atst.domain.models import Bar, CapitalChange

pytestmark = pytest.mark.unit


def _bar(dt: str, close: float = 10.0, volume: int = 100, extra: dict | None = None) -> Bar:
    return Bar(
        datetime=dt,
        open=close,
        high=close,
        low=close,
        close=close,
        volume=volume,
        extra=extra or {},
    )


def _ev(date: str, **kw) -> CapitalChange:
    base = {"code": "600000", "category": 1, "date": date}
    base.update(kw)
    return CapitalChange(**base)


class TestMissingPrevCloseWarning:
    """缺 prev_close：忽略现金红利 + 一次性告警。"""

    def test_warns_once_per_module(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import atst.domain.adjust as adj

        monkeypatch.setattr(adj, "_warned_missing_prev_close", False)
        bars = [_bar("2026-06-02", volume=100)]
        events = [_ev("2026-06-01", dividend=5.0, bonus_ratio=0.0)]
        with pytest.warns(UserWarning, match="现金红利"):
            to_adjusted(bars, events, method="hfq")
        # 第二次调用不再告警（模块级 once 旗标）
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            to_adjusted(bars, events, method="hfq")
        assert [w for w in caught if "现金红利" in str(w.message)] == []

    def test_volume_factor_ignores_dividend(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import atst.domain.adjust as adj

        monkeypatch.setattr(adj, "_warned_missing_prev_close", True)  # 静音告警
        bars = [_bar("2026-06-02")]
        events = [_ev("2026-06-01", dividend=5.0)]
        factors = compute_factors(bars, events)
        assert factors[0].price_factor == pytest.approx(1.0)  # 红利被忽略
        assert factors[0].volume_factor == pytest.approx(1.0)


class TestExtraNoneDefense:
    """bar.extra 为 None 时不再 AttributeError。"""

    def test_extra_none_bar(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import atst.domain.adjust as adj

        monkeypatch.setattr(adj, "_warned_missing_prev_close", True)

        class BareBar:
            datetime = "2026-06-02"
            volume = 100

        ev = _ev("2026-06-01", bonus_ratio=100.0)  # S=10
        # 退化口径：1/(1+S) 方向的放大系数 = 1+S = 11（红利被忽略）
        ratio = adj._price_adjust_ratio(ev, BareBar())  # type: ignore[arg-type]
        assert ratio == pytest.approx(11.0)


class TestVolumeRounding:
    """成交量取整 round 而非截断（15 × 0.1 = 1.5 → 2，旧实现给 1）。"""

    def test_volume_rounded(self) -> None:
        bars = [_bar("2026-06-02", volume=15)]
        events = [_ev("2026-06-01", bonus_ratio=90.0)]  # S=9 → vol 因子 1/10
        result = to_adjusted(bars, events, method="hfq")
        assert result[0].volume == 2  # round(1.5)=2（旧 int() 截断给 1）


class TestDenomNonPositiveRaises:
    """分母 <=0：价格/成交量统一 raise（不再静默返回 1.0）。"""

    def test_price_chain_raises(self) -> None:
        bars = [_bar("2026-06-02")]
        events = [_ev("2026-06-01", bonus_ratio=-10.0)]  # S=-1 → denom=0
        with pytest.raises(AdjustError, match="送配比例"):
            compute_factors(bars, events)

    def test_volume_chain_raises_consistently(self) -> None:
        import atst.domain.adjust as adj

        ev = _ev("2026-06-01", bonus_ratio=-10.0)
        with pytest.raises(AdjustError, match="成交量因子分母非正"):
            adj._volume_adjust_ratio(ev)


class TestEventDateHoisting:
    """事件日期解析移出内层循环：乱序事件集合仍按日期升序正确应用。"""

    def test_unsorted_events_same_result_as_sorted(self) -> None:
        bars = [_bar(f"2026-06-{d:02d}") for d in (1, 2, 3, 4)]
        ev_a = _ev("2026-06-02", bonus_ratio=100.0)  # S=10 → 放大 11
        ev_b = _ev("2026-06-03", bonus_ratio=200.0)  # S=20 → 再放大 21
        shuffled = [ev_b, ev_a]  # 乱序传入
        f1 = compute_factors(bars, shuffled)
        f2 = compute_factors(bars, sorted(shuffled, key=lambda e: e.date))
        assert [x.to_dict() for x in f1] == [x.to_dict() for x in f2]
        # 第 1 根无事件；第 2 根起 ×11；第 3 根起再 ×21
        assert f1[0].price_factor == pytest.approx(1.0)
        assert f1[1].price_factor == pytest.approx(11.0)
        assert f1[2].price_factor == pytest.approx(11.0 * 21.0)
        assert f1[3].price_factor == pytest.approx(11.0 * 21.0)

    def test_non_adjust_category_ignored(self) -> None:
        bars = [_bar("2026-06-02")]
        events = [_ev("2026-06-01", category=9, bonus_ratio=100.0)]
        factors = compute_factors(bars, events)
        assert factors[0].price_factor == pytest.approx(1.0)


class TestPrevCloseIsPerEvent:
    """前收盘价按**事件日**定位，不是"循环当前位置的前一根"。

    两者只在「一个 bar 间隔内恰好只有一个事件」时相等。一旦同一间隔挤了多个事件
    （长停牌后连续除权），除最早的以外全拿到错误的基准价——而除权参考价
    ``(P - D + Pr·R)/(1+S+R)`` 对 P 是敏感的，因子于是系统性偏大。
    """

    def test_two_events_in_one_gap_use_their_own_prev_close(self) -> None:
        #: 三根 bar；两个事件（06-02、06-04）都落在第 2、3 根之间的同一个间隔里，
        #: 于是它们在**同一次循环迭代**中被应用。基准价应分别是 10 与 20。
        bars = [
            _bar("2026-06-01", close=10.0),
            _bar("2026-06-03", close=20.0),
            _bar("2026-06-06", close=30.0),
        ]
        e1 = _ev("2026-06-02", dividend=10.0)  # 每股 1 元；基准价 10
        e2 = _ev("2026-06-04", dividend=10.0)  # 每股 1 元；基准价 20
        factors = compute_factors(bars, [e1, e2])
        expected = (1 / ((10.0 - 1.0) / 10.0)) * (1 / ((20.0 - 1.0) / 20.0))
        assert factors[2].price_factor == pytest.approx(expected)

    def test_second_event_does_not_reuse_first_prev_close(self) -> None:
        #: 旧实现用 ``bars[i-1]``（i = 循环位置）：同一间隔里的两个事件会**共用**
        #: 后一根的收盘价 20，第一条的基准价因此偏高，因子偏小。
        bars = [
            _bar("2026-06-01", close=10.0),
            _bar("2026-06-03", close=20.0),
            _bar("2026-06-06", close=30.0),
        ]
        ev = [_ev("2026-06-02", dividend=10.0), _ev("2026-06-04", dividend=10.0)]
        right = (1 / 0.9) * (1 / 0.95)
        wrong = (1 / 0.95) * (1 / 0.95)  # 两条都错用 P=20
        factors = compute_factors(bars, ev)
        assert factors[2].price_factor == pytest.approx(right)
        assert factors[2].price_factor != pytest.approx(wrong)

    def test_prev_close_before_returns_none_at_series_start(self) -> None:
        import atst.domain.adjust as adj

        assert adj._prev_close_before([], [], _date_of("2026-06-01")) is None

    def test_prev_close_before_ignores_non_positive_close(self) -> None:
        import atst.domain.adjust as adj

        bars = [_bar("2026-06-01", close=0.0), _bar("2026-06-05", close=20.0)]
        dates = [adj._parse_date(b.datetime) for b in bars]
        closes = [adj._as_positive_close(b) for b in bars]
        #: 停牌（close=0）不能当基准价：返回 None 走退化口径并告警，不拿 0 去除。
        assert adj._prev_close_before(dates, closes, _date_of("2026-06-02")) is None

    def test_event_before_window_message_names_the_fix(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        #: "早于窗口"与"窗口内取不到"是两种成因，前者调用方能修（加大 count）。
        #: 混成一句，调用方就不知道该改参数还是该换源。
        import atst.domain.adjust as adj

        monkeypatch.setattr(adj, "_warned_missing_prev_close", False)
        recorded: list[str] = []
        monkeypatch.setattr(
            adj,
            "record_warning",
            lambda code, message, **kw: recorded.append(message),
        )
        bars = [_bar("2026-06-05", close=20.0)]
        events = [_ev("2001-01-01", dividend=1.0)]  # 远早于窗口首根
        compute_factors(bars, events)
        assert recorded, "早于窗口的事件必须留下告警"
        assert "加大 count" in recorded[0]


def _date_of(text: str):
    from atst.domain.adjust import _parse_date

    return _parse_date(text)


class TestAdjusterSmoke:
    """AdjustEngine 三方法冒烟（前复权 / 后复权 / 定点）。"""

    def test_hfq_qfq_fixed_consistency(self) -> None:
        bars = [_bar("2026-06-01", close=10.0), _bar("2026-06-02", close=11.0)]
        events = [_ev("2026-06-02", bonus_ratio=100.0)]  # S=10 → 事件日后价格因子 11
        engine = AdjustEngine(round_price=6)
        hfq = engine.apply(bars, events, "hfq")
        qfq = engine.apply(bars, events, "qfq")
        fixed = engine.apply(bars, events, "fixed", anchor_date="2026-06-02")
        # 前复权最后一根 = 未复权（归一化到最新口径）
        assert qfq[-1].close == pytest.approx(11.0)
        # 定点复权以 anchor 日为基准：anchor 日等于未复权价，且与 qfq 一致
        assert fixed[-1].close == pytest.approx(11.0)
        assert [b.close for b in fixed] == [b.close for b in qfq]
        # hfq：事件日之前不变，事件日起放大 11 倍
        assert hfq[0].close == pytest.approx(10.0)
        assert hfq[1].close == pytest.approx(11.0 * 11.0)
