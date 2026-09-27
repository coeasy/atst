# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""G4：7709 核心链的真取探针——仓里唯一会在流水线里真发 7709 包的判据。

为什么这一族刻意**不 skip**：第 13 轮之前，``-m network`` 选出的判据全落在 web
适配器上，交易时段内一次 7709 请求都没发过（G4）。探针要回答的问题是"没有人手工
真取时，流水线能不能说出核心链坏了"，而"连不上就 skip"会把它变成一条永不失败的
安慰剂——主站全体下线时它必须红。这与 ``scripts/audit_hosts.py --strict`` 在
STANDARD 族零健康主机时退 1 是同一条口径（同一批 CI runner、同一批主机）。

唯一被允许的 skip 是"这条断言此刻不适用"：盘中分钟 K 线只在交易时段存在，休市时
跳过它不是链路坏了。日期判据从 :mod:`atst.domain.calendar` 现推，所以节假日不会
制造假红。
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any

import pytest

from atst import Client
from atst.domain.calendar import TradingSession, get_calendar, is_trading_day

pytestmark = pytest.mark.network

#: 探针标的：大盘流动性最好的两只。取不到数只可能是链路问题，不是"没人交易"。
PROBE_SYMBOLS: tuple[str, ...] = ("sh600519", "sz000001")

#: 交易所时钟。A 股不分夏令时，固定 UTC+8 就是正确时钟，且不像 ``ZoneInfo`` 那样
#: 在缺 tzdata 的环境里当场炸（Windows 纯 pip 环境默认缺失）。
MARKET_TZ = timezone(timedelta(hours=8), "UTC+8")

#: 开盘时刻——日线"今天这一根"从此刻起才存在。
_OPEN = 9 * 60 + 30


def market_now() -> datetime:
    return datetime.now(MARKET_TZ)


def expected_daily_bar_date(now: datetime) -> date:
    """此刻盘面上**应该**存在的最新一根日期的那一天。

    由交易日历现推，不是抄来的日期常量：休市日回退到上一个交易日，交易日开盘前
    同样回退，开盘后（含盘中与收盘后）就是当天。
    """
    calendar = get_calendar()
    today = now.date()
    if is_trading_day(today) and now.hour * 60 + now.minute >= _OPEN:
        return today
    return calendar.prev_trading_day(today)


def in_trading_session(now: datetime) -> bool:
    return is_trading_day(now.date()) and TradingSession.in_session(now.hour * 60 + now.minute)


def _rows(result: Any) -> list[dict[str, Any]]:
    data = result.data
    if isinstance(data, dict):
        return [data]
    return list(data)


@pytest.fixture(scope="module")
def live_client() -> Any:
    """一个真连接的客户端：模块内共享握手，失败直接抛给判据（不兜底、不换源）。"""
    with Client() as client:
        yield client


def _assert_stayed_on_tdx(result: Any, capability: str) -> None:
    """探针必须量到 tdx 7709 本身，而不是被别的 Provider 救活的结果。"""
    meta = result.meta
    assert meta.provider == "tdx", f"{capability} 探针被 {meta.provider} 接走了"
    assert not meta.provenance.fallback, f"{capability} 探针发生了跨 Provider 回退"


def test_security_count_serves_both_markets(live_client: Client) -> None:
    """0x044E：代码表计数面。核心链最便宜的一格——一次握手、一个定长应答。"""
    for market in ("sh", "sz"):
        result = live_client.security_count(market=market, provider="tdx")
        _assert_stayed_on_tdx(result, f"security_count({market})")
        count = result.data
        assert isinstance(count, int) and count > 1000, f"{market} 计数不成形状：{count!r}"


def test_daily_bars_serve_the_expected_trading_day(live_client: Client) -> None:
    """0x052D：日线主链。``strict=True`` 让任何一页瑕疵（空桩首页、锚点漂移）直接红。

    这一格同时是 G5 的日期判据：最新一根落在哪天由交易日历推出，所以周末/节假日
    的例行运行只会要求"上一个交易日"，不会假红。
    """
    now = market_now()
    result = live_client.bars(
        PROBE_SYMBOLS[0],
        provider="tdx",
        period="day",
        count=10,
        strict=True,
    )
    _assert_stayed_on_tdx(result, "bars(day)")
    rows = _rows(result)
    assert len(rows) == 10, f"日线只回了 {len(rows)} 根"
    for row in rows:
        assert 0 < row["low"] <= row["high"], f"高低次序反了：{row}"
        assert row["close"] > 0 and row["open"] > 0, f"开收盘不成立：{row}"
    assert rows == sorted(rows, key=lambda r: str(r["date"])), "日线不是升序"
    assert rows[-1]["date"] == expected_daily_bar_date(now).isoformat(), (
        f"盘中时钟 {now.isoformat()} 应该看到的最新一根是 "
        f"{expected_daily_bar_date(now)}，实际是 {rows[-1]['date']}"
    )


def test_live_quotes_serve_a_price(live_client: Client) -> None:
    """0x0530：实时行情主链。只要求有价——`datetime`/`bid`/`ask` 恒空是 G8 的口径。"""
    result = live_client.quotes(list(PROBE_SYMBOLS), provider="tdx")
    _assert_stayed_on_tdx(result, "quotes")
    rows = _rows(result)
    assert len(rows) == len(PROBE_SYMBOLS), f"两只标的只回了 {len(rows)} 行"
    for row in rows:
        assert row["price"] > 0, f"{row.get('code')} 无价：{row}"
        assert row["high"] >= row["low"] > 0, f"{row.get('code')} 高低不成立：{row}"


def test_snapshot_composes_quote_and_bar(live_client: Client) -> None:
    """组合面：`quotes` + 当日 `bars(count=1)` 合成，两条主链都得给数才算通。

    形状是 ``{'code', 'quote', 'prev_close'}``——``quote`` 来自 0x0530，
    ``prev_close`` 是当日那根 K 线（名字沿用，它是合成用的那一根而非昨收，见
    ``docs/tdx_status.md`` §一）。
    """
    result = live_client.snapshot(PROBE_SYMBOLS[0], provider="tdx")
    _assert_stayed_on_tdx(result, "snapshot")
    snapshot = result.data
    assert isinstance(snapshot, dict), f"快照不是字典：{type(snapshot)}"
    quote = snapshot.get("quote")
    assert isinstance(quote, dict) and quote.get("price"), f"快照缺 0x0530 那一半：{snapshot}"
    bar = snapshot.get("prev_close")
    assert isinstance(bar, dict) and bar.get("close"), f"快照缺当日 K 线那一半：{snapshot}"


def test_intraday_minute_bar_is_being_written(live_client: Client) -> None:
    """盘中分钟 K 线：只在交易时段内成立，休市时这条不适用（真 skip，不是兜底）。

    这是 G4 存在的理由——02:30 UTC（北京 10:30）的那次运行是仓里唯一有机会看到
    "本分钟 K 线正在被写入"的自动化判据；第 13 轮之前它从未被执行过。
    """
    now = market_now()
    if not in_trading_session(now):
        pytest.skip(f"{now.isoformat()} 不在 A 股交易时段内，盘中分钟线断言不适用")
    result = live_client.bars(
        PROBE_SYMBOLS[0],
        provider="tdx",
        period="1m",
        count=10,
        strict=True,
    )
    _assert_stayed_on_tdx(result, "bars(1m)")
    rows = _rows(result)
    assert rows, "交易时段内分钟线回了空页"
    latest = rows[-1]["datetime"]
    assert latest.startswith(now.date().isoformat()), (
        f"交易时段内最新一根分钟 K 线是 {latest}，不是今天（{now.date()}）新写入的"
    )
    hhmm = latest.split(" ")[1]
    minutes = int(hhmm[:2]) * 60 + int(hhmm[3:5])
    assert minutes <= now.hour * 60 + now.minute + 1, f"分钟线时间戳跑到时钟之后：{latest}"
