# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""``daily_enriched``（宽表日线）与复权事件取数的执行器级测试。

这一层测的是**拼装与取数**，不是数学（数学在 ``tests/domain/``）：

* ``_adjusted_events`` 必须**翻页到尽**——``dividend_history`` 的 ``size`` 默认只有
  20，长历史标的的早期除权事件会被静默截断（600519 实有 28 条）；
* 配股走**独立报表**（``rights_issue`` → ``RPT_IPO_ALLOTMENT``），与分红按除权日合成；
* ``daily_enriched`` 多取一根 bar 让首行有真实 ``pre_close``，算完裁掉；
* ``count`` 超过估值序列表的单页上限时必须**显式拒绝**，而不是静默产出估值全 None 的行。
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from atst.client.api import Client
from atst.domain.models import Bar, CapitalChange
from atst.errors import ValidationError
from atst.runtime.executor import (
    _ADJUST_EVENT_MAX_PAGES,
    _ADJUST_EVENT_PAGE_SIZE,
    _ENRICH_MAX_COUNT,
)
from atst.runtime.kernel import UnifiedRuntime

pytestmark = pytest.mark.unit


def _bar(day: str, close: float = 10.0, *, volume: int = 1000) -> Bar:
    return Bar(
        datetime=day,
        open=close,
        high=close,
        low=close,
        close=close,
        volume=volume,
        amount=close * volume,
        extra={},
    )


class _FakeTdx:
    """最小 fake TDX 客户端。

    出参顺序遵循公开契约（``atst/client/_mixin.py``）：**升序**，``bars[0]`` 最早、
    ``bars[-1]`` 最新；``start`` 从最新往回数。
    """

    def __init__(self, bars: list[Bar], changes: list[Any] | None = None) -> None:
        self._bars = bars
        self._changes = changes or []
        self.closes = 0
        self.bar_calls: list[int] = []

    def close(self) -> None:
        self.closes += 1

    def capital_changes(self, symbol: str) -> list[Any]:
        """``TdxClient.capital_changes`` 的契约：**直接返回模型**，不是解析行 dict。"""
        return list(self._changes)

    def bars(
        self,
        symbol: str,
        *,
        period: str = "day",
        count: int = 320,
        start: int = 0,
        as_format: str = "dict",
    ) -> list[Bar]:
        end = max(0, len(self._bars) - start)
        begin = max(0, end - count)
        self.bar_calls.append(count)
        return self._bars[begin:end]


class _WebStub:
    """按 capability 应答的 web 取数桩，同时记录每类能力的调用参数。"""

    def __init__(
        self,
        *,
        dividend_pages: list[list[dict[str, Any]]] | None = None,
        rights_pages: list[list[dict[str, Any]]] | None = None,
        valuation: list[dict[str, Any]] | None = None,
    ) -> None:
        self.dividend_pages = dividend_pages or []
        self.rights_pages = rights_pages or []
        self.valuation = valuation or []
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def __call__(self, plan: Any) -> Any:
        cap = plan.spec.capability
        options = getattr(plan.spec, "options", None) or {}
        kwargs = dict(options.get("kwargs") or {})
        self.calls.append((cap, kwargs))
        if cap == "dividend_history":
            page = int(kwargs.get("page", 1))
            idx = page - 1
            return SimpleNamespace(
                data=self.dividend_pages[idx] if idx < len(self.dividend_pages) else []
            )
        if cap == "rights_issue":
            page = int(kwargs.get("page", 1))
            idx = page - 1
            return SimpleNamespace(
                data=self.rights_pages[idx] if idx < len(self.rights_pages) else []
            )
        if cap == "valuation_history":
            count = int(kwargs.get("count", 500))
            return SimpleNamespace(data=self.valuation[:count])
        raise AssertionError(f"未打桩的能力：{cap}")


def _client(bars: list[Bar], web: _WebStub) -> tuple[Client, _FakeTdx]:
    runtime = UnifiedRuntime(vipdoc_root=None)
    fake = _FakeTdx(bars)
    runtime.executor._tdx_client = lambda _timeout: fake  # type: ignore[method-assign]
    #: 只拦 web 取数，其余（含 composed 能力本身）走原 execute——
    #: 把 execute 整个换掉会连 ``_composed_call`` 的入口一起挡掉。
    original = runtime.executor.execute

    def dispatch(plan: Any) -> Any:
        if plan.spec.capability in {"dividend_history", "rights_issue", "valuation_history"}:
            return web(plan)
        return original(plan)

    runtime.executor.execute = dispatch  # type: ignore[method-assign]
    return Client(runtime), fake


def _paged(rows: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """把行按 ``_ADJUST_EVENT_PAGE_SIZE`` 切页，末页短（表示取尽）。"""
    pages = [
        rows[i : i + _ADJUST_EVENT_PAGE_SIZE] for i in range(0, len(rows), _ADJUST_EVENT_PAGE_SIZE)
    ]
    return pages or [[]]


def _row(day: str, **kw: Any) -> dict[str, Any]:
    row = {"EX_DIVIDEND_DATE": day}
    row.update(kw)
    return row


class TestAdjustEventPagination:
    """事件分页：不翻页会把长历史标的的早期事件静默丢掉。"""

    def test_short_page_stops_paging(self) -> None:
        rows = [_row(f"20{i:02d}-07-01", PRETAX_BONUS_RMB=1.0) for i in range(1, 6)]
        web = _WebStub(dividend_pages=_paged(rows))
        client, _ = _client([_bar("2026-06-01")], web)
        events = client.runtime.executor._adjusted_events("sh600519", "eastmoney", timeout=5.0)
        assert len(events) == 5
        #: 只有一页（第二页会是空页，但短页已经终止循环）
        assert [c for c in web.calls if c[0] == "dividend_history"] == [
            ("dividend_history", {"page": 1, "size": _ADJUST_EVENT_PAGE_SIZE})
        ]

    def test_full_page_triggers_second_request(self) -> None:
        #: 恰好一整页 → 不能当成"取完了"，必须再翻一页。
        rows = [
            _row(f"19{i:02d}-07-01", PRETAX_BONUS_RMB=1.0)
            for i in range(1, _ADJUST_EVENT_PAGE_SIZE + 1)
        ]
        pages = [rows, [_row("2001-07-01", PRETAX_BONUS_RMB=1.0)]]
        web = _WebStub(dividend_pages=pages)
        client, _ = _client([_bar("2026-06-01")], web)
        events = client.runtime.executor._adjusted_events("sh600519", "eastmoney", timeout=5.0)
        assert len(events) == _ADJUST_EVENT_PAGE_SIZE + 1
        assert [c[1]["page"] for c in web.calls if c[0] == "dividend_history"] == [1, 2]

    def test_page_limit_emits_warning(self) -> None:
        #: 每页都满 → 触顶。触顶不是"取完了"，必须留下告警。
        #: 日期必须**跨页互不相同**：合成阶段按除权日归并，同一日期重复出现会被合并成一条。
        total = _ADJUST_EVENT_PAGE_SIZE * _ADJUST_EVENT_MAX_PAGES
        rows = [
            _row(f"{1900 + idx // 12 + 1}-{idx % 12 + 1:02d}-01", PRETAX_BONUS_RMB=1.0)
            for idx in range(total)
        ]
        pages = [
            rows[i : i + _ADJUST_EVENT_PAGE_SIZE] for i in range(0, total, _ADJUST_EVENT_PAGE_SIZE)
        ]
        web = _WebStub(dividend_pages=pages)
        client, _ = _client([_bar("2026-06-01")], web)
        with pytest.warns(UserWarning, match="未取尽"):
            events = client.runtime.executor._adjusted_events("sh600519", "eastmoney", timeout=5.0)
        assert len(events) == total
        assert len([c for c in web.calls if c[0] == "dividend_history"]) == _ADJUST_EVENT_MAX_PAGES

    def test_size_is_explicit_not_default_20(self) -> None:
        #: 回归护栏：过去不传 size，落默认 20 条，600519 的早期事件因此全丢。
        web = _WebStub(dividend_pages=[[]])
        client, _ = _client([_bar("2026-06-01")], web)
        client.runtime.executor._adjusted_events("sh600519", "eastmoney", timeout=5.0)
        assert all(c[1]["size"] == _ADJUST_EVENT_PAGE_SIZE for c in web.calls)
        assert _ADJUST_EVENT_PAGE_SIZE >= 100


class TestRightsIssueWiring:
    """配股必须真的被取，且与分红按除权日合成。"""

    def test_rights_issue_is_requested(self) -> None:
        web = _WebStub(
            dividend_pages=[[_row("2010-07-01", BONUS_RATIO=2.0, PRETAX_BONUS_RMB=1.0)]],
            rights_pages=[[_row("2010-07-01", PLACING_RATIO=3.0, ISSUE_PRICE=8.85)]],
        )
        client, _ = _client([_bar("2026-06-01")], web)
        events = client.runtime.executor._adjusted_events("sh600036", "eastmoney", timeout=5.0)
        assert [c[0] for c in web.calls].count("rights_issue") == 1
        assert len(events) == 1
        assert events[0].rights_ratio == pytest.approx(3.0)
        assert events[0].rights_price == pytest.approx(8.85)
        assert events[0].bonus_ratio == pytest.approx(2.0)

    def test_no_rights_rows_is_not_an_error(self) -> None:
        #: 绝大多数标的没配过股：空配股集不许让整次复权失败。
        web = _WebStub(
            dividend_pages=[[_row("2020-07-01", PRETAX_BONUS_RMB=1.0)]], rights_pages=[[]]
        )
        client, _ = _client([_bar("2026-06-01")], web)
        events = client.runtime.executor._adjusted_events("sh600000", "eastmoney", timeout=5.0)
        assert len(events) == 1
        assert events[0].rights_ratio == pytest.approx(0.0)


class TestDailyEnriched:
    """宽表日线端到端（fake TDX + 打桩估值）。"""

    def _valuation(self, days: list[str]) -> list[dict[str, Any]]:
        return [
            {
                "TRADE_DATE": f"{d} 00:00:00",
                "SECURITY_NAME_ABBR": "测试股份",
                "PE_TTM": 30.0,
                "PB_MRQ": 8.0,
                "FREE_SHARES_A": 100_000_000,
                "TOTAL_SHARES": 200_000_000,
                "TOTAL_MARKET_CAP": 2.0e11,
                "NOTLIMITED_MARKETCAP_A": 1.0e11,
            }
            for d in days
        ]

    def test_rows_trimmed_to_count(self) -> None:
        bars = [_bar(f"2026-06-{d:02d}", 10.0 + d) for d in range(1, 9)]
        web = _WebStub(valuation=self._valuation([f"2026-06-{d:02d}" for d in range(1, 9)]))
        client, _ = _client(bars, web)
        rows = client.call("daily_enriched", "600519", count=5).data
        assert len(rows) == 5  # 多取的那一根已裁掉
        assert rows[-1]["date"] == "2026-06-08"

    def test_first_row_has_real_pre_close(self) -> None:
        #: 多取一根的价值：count=3 的三行里，最早那行也有真实 pre_close。
        bars = [_bar(f"2026-06-{d:02d}", 10.0 + d) for d in range(1, 9)]
        web = _WebStub(valuation=self._valuation([f"2026-06-{d:02d}" for d in range(1, 9)]))
        client, _ = _client(bars, web)
        rows = client.call("daily_enriched", "600519", count=3).data
        assert len(rows) == 3
        assert rows[0]["date"] == "2026-06-06"
        assert rows[0]["pre_close"] == pytest.approx(15.0)  # 06-05 的收盘
        assert rows[0]["pct_chg"] == pytest.approx((16.0 - 15.0) / 15.0 * 100.0)

    def test_valuation_columns_joined(self) -> None:
        bars = [_bar("2026-06-01", 10.0, volume=2_000_000)]
        web = _WebStub(valuation=self._valuation(["2026-06-01"]))
        client, _ = _client(bars, web)
        rows = client.call("daily_enriched", "600519", count=1).data
        assert rows[0]["pe_ttm"] == pytest.approx(30.0)
        assert rows[0]["pb"] == pytest.approx(8.0)
        assert rows[0]["name"] == "测试股份"
        assert rows[0]["turnover"] == pytest.approx(2.0)  # 2e6 / 1e8 * 100

    def test_count_over_page_limit_is_rejected(self) -> None:
        #: 静默截断会让老行的估值列变成 None，调用方分不清"没有"和"没取到"。
        web = _WebStub(valuation=[])
        client, _ = _client([_bar("2026-06-01")], web)
        with pytest.raises(ValidationError, match="单页上限"):
            client.call("daily_enriched", "600519", count=_ENRICH_MAX_COUNT + 1)

    def test_unknown_adjust_is_rejected(self) -> None:
        web = _WebStub(valuation=[])
        client, _ = _client([_bar("2026-06-01")], web)
        with pytest.raises(ValidationError, match="adjust"):
            client.call("daily_enriched", "600519", count=1, adjust="fixed")

    def test_adjust_hfq_changes_prices(self) -> None:
        bars = [_bar("2026-06-01", 10.0), _bar("2026-06-02", 10.0)]
        web = _WebStub(
            dividend_pages=[[_row("2026-06-02", PRETAX_BONUS_RMB=10.0)]],
            rights_pages=[[]],
            valuation=[],
        )
        client, _ = _client(bars, web)
        rows = client.call("daily_enriched", "600519", count=1, adjust="hfq").data
        #: 6-02 除权派 1 元/股，前收盘 10 → k=0.9 → 后复权价 = 10/0.9
        assert rows[0]["close"] == pytest.approx(10.0 / 0.9, rel=1e-6)

    def test_count_zero_is_rejected(self) -> None:
        web = _WebStub(valuation=[])
        client, _ = _client([_bar("2026-06-01")], web)
        with pytest.raises(ValidationError, match="count"):
            client.call("daily_enriched", "600519", count=0)


class TestHfqWindowCoverage:
    """后复权因子必须与请求的 ``count`` 无关。

    实测缺陷：window 盖不住早期事件时，那批事件拿不到前收盘价 → 引擎走
    ``1/(1+S+R)`` 降级口径（忽略现金红利）→ **同一根 bar 在不同 count 下复权结果
    不同**（某标的 hfq 因子 1.045 与 1.642 并存）。既不可复现，也是静默错。
    """

    def _bars(self, n: int) -> list[Bar]:
        return [
            _bar(f"2026-{1 + (d - 1) // 28:02d}-{1 + (d - 1) % 28:02d}", 10.0)
            for d in range(1, n + 1)
        ]

    def _events(self) -> _WebStub:
        #: 除权日在第一根 bar 之后（2026-01-02），派 1 元/股，无送转。
        return _WebStub(
            dividend_pages=[[_row("2026-01-02", PRETAX_BONUS_RMB=10.0)]],
            rights_pages=[[]],
            valuation=[],
        )

    def test_short_window_extends_and_matches_long_window(self) -> None:
        bars = self._bars(20)
        short_client, _ = _client(bars, self._events())
        long_client, _ = _client(bars, self._events())
        short = short_client.call("adjusted_bars", "600519", count=2, method="hfq").data
        long = long_client.call("adjusted_bars", "600519", count=20, method="hfq").data
        assert len(short) == 2
        assert [b.close for b in short] == pytest.approx([b.close for b in long[-2:]])

    def test_factor_actually_reflects_dividend(self) -> None:
        #: 降级口径下（忽略现金红利）因子会是 1.0；正确口径是 10/(10-1) = 1.111…
        bars = self._bars(20)
        client, _ = _client(bars, self._events())
        out = client.call("adjusted_bars", "600519", count=2, method="hfq").data
        assert out[-1].close == pytest.approx(10.0 / 0.9, rel=1e-6)

    def test_extension_requests_are_bounded(self) -> None:
        #: 倍数增长（3 → 12 → 48…）：20 根的历史在个位数请求内盖住，不是逐根线性翻。
        bars = self._bars(20)
        web = self._events()
        client, fake = _client(bars, web)
        client.call("adjusted_bars", "600519", count=2, method="hfq")
        assert fake.bar_calls[0] == 3
        assert len(fake.bar_calls) <= 4, fake.bar_calls
        #: 增长必须严格倍数：否则短窗口会退化成"逐段重拉全历史"。
        assert fake.bar_calls[1] == 12
        #: 事件取数只走一轮（分红一页 + 配股一页），延伸不重复拉事件。
        assert [c[0] for c in web.calls] == ["dividend_history", "rights_issue"]

    def test_no_extension_when_event_inside_window(self) -> None:
        #: 事件在窗口内时不该多花任何一次请求。
        bars = self._bars(20)
        web = self._events()
        client, fake = _client(bars, web)
        client.call("adjusted_bars", "600519", count=20, method="hfq")
        assert len(fake.bar_calls) == 1

    def test_daily_enriched_hfq_is_also_window_independent(self) -> None:
        #: 宽表是批量产物：同一根 bar 在不同批次里复权值必须一致，否则下游对不上账。
        bars = self._bars(20)
        days = [b.datetime[:10] for b in bars]
        valuation = [{"TRADE_DATE": f"{d} 00:00:00", "SECURITY_NAME_ABBR": "测试"} for d in days]
        short_web = _WebStub(
            dividend_pages=[[_row("2026-01-02", PRETAX_BONUS_RMB=10.0)]],
            rights_pages=[[]],
            valuation=valuation,
        )
        long_web = _WebStub(
            dividend_pages=[[_row("2026-01-02", PRETAX_BONUS_RMB=10.0)]],
            rights_pages=[[]],
            valuation=valuation,
        )
        short_client, _ = _client(bars, short_web)
        long_client, _ = _client(bars, long_web)
        short = short_client.call("daily_enriched", "600519", count=2, adjust="hfq").data
        long = long_client.call("daily_enriched", "600519", count=20, adjust="hfq").data
        assert [r["close"] for r in short] == pytest.approx([r["close"] for r in long[-2:]])

    def test_qfq_does_not_extend(self) -> None:
        #: 前复权归一到最后一根，窗口前的常数因子在归一化时约掉，无需延伸——
        #: 因此它**照旧**告警（早于窗口的事件被近似），而 hfq 路径会把告警消掉。
        bars = self._bars(20)
        client, fake = _client(bars, self._events())
        with pytest.warns(UserWarning, match="早于本次 K 线窗口起点"):
            out = client.call("adjusted_bars", "600519", count=2, method="qfq").data
        assert len(fake.bar_calls) == 1  # 没有延伸取数
        #: qfq 最新一根 = 未复权价（归一化到最新口径）
        assert out[-1].close == pytest.approx(10.0)

    def test_history_exhausted_does_not_loop_forever(self) -> None:
        #: 事件早于全部历史（构造出来的情形）：取数不再变大即收手，不许无限翻。
        bars = self._bars(8)
        web = _WebStub(
            dividend_pages=[[_row("1990-01-05", PRETAX_BONUS_RMB=10.0)]],
            rights_pages=[[]],
            valuation=[],
        )
        client, _ = _client(bars, web)
        out = client.call("adjusted_bars", "600519", count=2, method="hfq").data
        assert len(out) == 2


class TestCapabilityRegistration:
    """新能力必须双登记：Provider 显式声明 + 全局清单。"""

    def test_capabilities_present(self) -> None:
        caps = Client(UnifiedRuntime(vipdoc_root=None)).capabilities()
        assert "daily_enriched" in caps
        assert "rights_issue" in caps

    def test_discovery_names_are_plain_strings(self) -> None:
        #: 发现面只发纯 str 名字，禁止加 status 等键（F-66(c)）。
        caps = Client(UnifiedRuntime(vipdoc_root=None)).capabilities()
        assert all(isinstance(name, str) for name in caps)


class TestTdxEventSource:
    """``event_source="tdx"`` 是一条**显式点名**的路：它必须走到底，并以自己的
    判据说话，而不是炸成 E9000。

    历史缺陷：``_adjusted_events`` 无条件对 ``client.capital_changes()`` 的返回值套
    ``to_capital_changes()``，而后者吃的是解析行 dict、前者返回的已经是
    :class:`CapitalChange` 模型 ⇒ 每次都在 ``row.get(...)`` 上抛 AttributeError，
    被执行器兜成 ``InternalError [E9000]``。调用方读到的是一句内部黑话，而不是
    "这条路不通，请用 eastmoney"。
    """

    def test_models_are_not_re_wrapped(self) -> None:
        events = [
            CapitalChange(
                code="600519",
                market=1,
                category=1,
                category_name="除权除息",
                date="2024-06-19",
                dividend=308.76,
                bonus_ratio=0.0,
                rights_ratio=0.0,
                rights_price=0.0,
            )
        ]
        runtime = UnifiedRuntime(vipdoc_root=None)
        fake = _FakeTdx([], events)
        runtime.executor._tdx_client = lambda _t: fake  # type: ignore[method-assign]
        out = runtime.executor._adjusted_events("sh600519", "tdx", timeout=5.0)
        assert len(out) == 1
        assert out[0].date == "2024-06-19"
        assert out[0].dividend == pytest.approx(308.76)

    def test_misaligned_records_raise_validation_not_internal(self) -> None:
        #: 真机 0x000F 的错位形状：market 落在 [0,1,2] 之外、code 带控制字符。
        events = [
            CapitalChange(
                code="519\x01",
                market=48,
                category=1,
                category_name="",
                date="",
                dividend=0.0,
                bonus_ratio=0.0,
                rights_ratio=0.0,
                rights_price=0.0,
            )
        ]
        runtime = UnifiedRuntime(vipdoc_root=None)
        fake = _FakeTdx([], events)
        runtime.executor._tdx_client = lambda _t: fake  # type: ignore[method-assign]
        with pytest.raises(ValidationError) as info:
            runtime.executor._adjusted_events("sh600519", "tdx", timeout=5.0)
        #: 判据必须可行动：告诉调用方换哪个源，而不是说"内部未处理异常"。
        assert "eastmoney" in str(info.value)
        assert info.value.code == "E1010"

    def test_no_records_returns_empty(self) -> None:
        runtime = UnifiedRuntime(vipdoc_root=None)
        fake = _FakeTdx([], [])
        runtime.executor._tdx_client = lambda _t: fake  # type: ignore[method-assign]
        assert runtime.executor._adjusted_events("sh600519", "tdx", timeout=5.0) == []


class TestComposedProviderRouting:
    """组合能力上的 ``provider`` 指的是**取原始数据的那条腿**，不是"谁来执行"。

    历史缺陷：``Client.call`` 把它当成路由字段递进 planner，于是
    ``Client.adjusted_bars(..., provider="tdx")`` 在规划期就 E1010
    （"provider 'tdx' 不支持 capability 'adjusted_bars'"）——而执行体里读
    ``kwargs["provider"]`` 的那一行永远读不到东西，是一条谁都用不上的参数。
    """

    def test_named_provider_reaches_the_raw_bar_leg(self) -> None:
        from atst.catalog.capability import COMPOSED_CAPABILITIES

        assert {"adjusted_bars", "daily_enriched", "sync_daily"} <= COMPOSED_CAPABILITIES

        seen: list[str] = []
        runtime = UnifiedRuntime(vipdoc_root=None)

        def spy(symbol, **kw):  # noqa: ANN001, ANN003
            #: 只在"取原始 K 线"这一步截住：这一步的 raw_provider 就是被测对象，
            #: 让它继续往下走会真的发网络请求（而这正是我们要证明传到了的地方）。
            seen.append(str(kw.get("raw_provider")))
            return [_bar("2026-06-01")]

        runtime.executor._adjusted_raw_bars = spy  # type: ignore[method-assign]
        web = _WebStub(dividend_pages=[[]], rights_pages=[[]])
        orig_exec = runtime.executor.execute

        def dispatch(plan: Any) -> Any:
            if plan.spec.capability in {"dividend_history", "rights_issue"}:
                return web(plan)
            return orig_exec(plan)

        runtime.executor.execute = dispatch  # type: ignore[method-assign]
        client = Client(runtime)
        for provider in ("tdx", "baidu"):
            seen.clear()
            client.call("adjusted_bars", "600519", count=1, provider=provider)
            assert seen == [provider], seen

    def test_default_provider_is_untouched(self) -> None:
        #: 不传 provider 时规划用的仍是该能力的默认 Provider（``derived``）。
        client = Client(UnifiedRuntime(vipdoc_root=None))
        spec_providers: list[str] = []

        def spy(spec):  # noqa: ANN001
            spec_providers.append(spec.provider)
            raise RuntimeError("stop-here")

        runtime_provider = client.runtime.executor
        original = runtime_provider.execute
        runtime_provider.execute = spy  # type: ignore[method-assign]
        with pytest.raises(RuntimeError):
            client.call("adjusted_bars", "600519", count=1)
        runtime_provider.execute = original  # type: ignore[method-assign]
        assert spec_providers == ["derived"]


class TestExplicitEventEscapeHatch:
    """``events=`` 是文档承诺的逃生口：给全量事件集，跳过一次在线取数。

    这条路过去**一行测试都没有**——文档写着可以，实际是"没人验证过的承诺"。
    它同时是 ``events`` 三态语义的判据：``None`` 才去取，``[]`` 就是"不调整"。
    """

    def test_supplied_events_skip_the_online_fetch(self) -> None:
        bars = [_bar("2026-06-01", 10.0), _bar("2026-06-02", 10.0)]
        web = _WebStub()  # 任何一次 dividend/rights 调用都会 AssertionError
        client, _ = _client(bars, web)
        events = [
            CapitalChange(
                code="600519",
                market=1,
                category=1,
                category_name="除权除息",
                date="2026-06-02",
                dividend=10.0,
            )
        ]
        #: 走完整 ``Client.call`` ⇒ ``events`` 会先过 ``_json_contract``（dataclass→dict），
        #: 再由执行体 ``to_capital_changes`` 还原成模型——这条往返本身也在被测之列。
        out = client.call("adjusted_bars", "600519", count=1, method="hfq", events=events).data
        assert web.calls == []
        assert out[0].close == pytest.approx(10.0 / 0.9, rel=1e-6)

    def test_empty_events_means_do_not_adjust_not_go_fetch(self) -> None:
        #: 回归护栏：过去写的是 ``if not events:``，把 ``[]`` 当成"没给"，
        #: 于是显式清空事件集会被静默换成去东财拉一份回来。
        bars = [_bar("2026-06-01", 10.0), _bar("2026-06-02", 10.0)]
        web = _WebStub()
        client, _ = _client(bars, web)
        out = client.call("adjusted_bars", "600519", count=2, method="hfq", events=[]).data
        assert web.calls == []
        assert [b.close for b in out] == pytest.approx([10.0, 10.0])


class TestDailyEnrichedEventSource:
    """宽表的 ``event_source`` 必须真的接线，而不是被丢掉后固定走东财。"""

    def _runtime_with_tdx_events(self, events: list[Any]) -> Client:
        runtime = UnifiedRuntime(vipdoc_root=None)
        #: 原始 K 线这一步截住（真跑会发网络请求，而这里测的是事件源）。
        runtime.executor._adjusted_raw_bars = lambda symbol, **kw: [_bar("2026-06-01")]  # type: ignore[method-assign]
        runtime.executor._tdx_client = lambda _t: _FakeTdx([], events)  # type: ignore[method-assign]
        return Client(runtime)

    def test_event_source_tdx_reaches_the_gate(self) -> None:
        #: 错位记录 ⇒ 布局闸的 E1010。能走到闸前，就证明这个旋钮被透传到了
        #: ``_adjusted_events``，而不是被忽略后固定读东财。
        misaligned = [CapitalChange(code="519\x01", market=48, date="")]
        client = self._runtime_with_tdx_events(misaligned)
        with pytest.raises(ValidationError) as info:
            client.call("daily_enriched", "600519", count=1, adjust="hfq", event_source="tdx")
        assert info.value.code == "E1010"
        assert "eastmoney" in str(info.value)

    def test_default_event_source_is_eastmoney(self) -> None:
        #: 不给 event_source ⇒ 走东财（web 桩应答），不碰 TDX 的 0x000F。
        misaligned = [CapitalChange(code="519\x01", market=48, date="")]
        bar = _bar("2026-06-01", 10.0)
        runtime = UnifiedRuntime(vipdoc_root=None)
        runtime.executor._adjusted_raw_bars = lambda symbol, **kw: [bar]  # type: ignore[method-assign]
        runtime.executor._tdx_client = lambda _t: _FakeTdx([bar], misaligned)  # type: ignore[method-assign]
        web = _WebStub(dividend_pages=[[]], rights_pages=[[]], valuation=[])
        orig = runtime.executor.execute

        def dispatch(plan: Any) -> Any:
            if plan.spec.capability in {"dividend_history", "rights_issue", "valuation_history"}:
                return web(plan)
            return orig(plan)

        runtime.executor.execute = dispatch  # type: ignore[method-assign]
        client = Client(runtime)
        rows = client.call("daily_enriched", "600519", count=1, adjust="hfq").data
        assert [c[0] for c in web.calls][:2] == ["dividend_history", "rights_issue"]
        assert rows[0]["close"] == pytest.approx(10.0)  # 空事件集 ⇒ 无调整

    def test_unknown_event_source_is_rejected(self) -> None:
        runtime = UnifiedRuntime(vipdoc_root=None)
        runtime.executor._adjusted_raw_bars = lambda symbol, **kw: [_bar("2026-06-01")]  # type: ignore[method-assign]
        client = Client(runtime)
        with pytest.raises(ValidationError, match="event_source"):
            client.call("daily_enriched", "600519", count=1, adjust="hfq", event_source="akshare")
