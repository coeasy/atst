# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""LocalDaySink 增量落盘测试（P1-3 §4）。

覆盖：
* 全量首写：空文件 → 写入后 DayBarReader 回读一致；
* 断点续传：仅拉取本地末日期之后的新日线；
* 幂等：重跑不产生重复记录；
* prev_close 链：新记录上日收盘 = 前条收盘；
* `sync_daily` capability 批量增量同步 + fake tdx。
"""

from __future__ import annotations

import struct
from collections.abc import Iterator, Sequence
from pathlib import Path

import pytest

from tstdx.client_api import Client
from tstdx.domain.models import Bar
from tstdx.errors import TdxError
from tstdx.reader.formats import DayBarReader
from tstdx.runtime.kernel import UnifiedRuntime
from tstdx.sink.local_day import LocalDaySink

_SCALE = 100


def _bar(date: str, close: float, *, base: float | None = None) -> Bar:
    """构造一条日线（open≈close-1, high=close+2, low=close-3, 全为正价）。"""
    if base is None:
        base = close - 1.0
    return Bar(
        datetime=f"{date} 15:00:00",
        open=round(base + 1, 4),
        high=round(base + 3, 4),
        low=round(base - 1, 4),
        close=close,
        volume=1_000_000,
        amount=float(close * 1_000_000),
    )


def _build_day_file(path: Path, dates: Sequence[str], closes: Sequence[float]) -> None:
    """构造已有 .day 文件（int+scale 布局，升序日期）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    recs = []
    for i, (date, close) in enumerate(zip(dates, closes, strict=False)):
        y, m, d = (int(x) for x in date.split("-"))
        date_raw = y * 10000 + m * 100 + d
        o = int(round((close - 1) * _SCALE))
        h = int(round((close + 2) * _SCALE))
        lo = int(round((close - 3) * _SCALE))
        c = int(round(close * _SCALE))
        prev = int(round((closes[i - 1] if i else close) * _SCALE))
        recs.append(
            struct.pack(
                "<IIIIIfII", date_raw, o, h, lo, c, float(close * 1_000_000), 1_000_000, prev
            )
        )
    path.write_bytes(b"".join(recs))


def _read_back(path: Path) -> list[Bar]:
    return DayBarReader("a_share_day").read(path, output="model")


@pytest.fixture()
def vipdoc(tmp_path: Path) -> Iterator[Path]:
    yield tmp_path


# --------------------------------------------------------------------------- #
# LocalDaySink.sync
# --------------------------------------------------------------------------- #
class TestSync:
    def test_full_first_write_roundtrip(self, vipdoc: Path) -> None:
        """空文件全量首写：新增 3 条，回读一致。"""
        sink = LocalDaySink(vipdoc)
        bars = [_bar("2026-01-02", 10.0), _bar("2026-01-05", 10.5), _bar("2026-01-06", 11.0)]
        res = sink.sync("sh600519", lambda o, c: bars)
        assert res.added == 3
        assert res.existed == 0
        path = vipdoc / "sh" / "lday" / "sh600519.day"
        assert res.path == str(path)
        read = _read_back(path)
        assert [b.datetime[:10] for b in read] == ["2026-01-02", "2026-01-05", "2026-01-06"]
        assert [b.close for b in read] == [10.0, 10.5, 11.0]
        assert [b.volume for b in read] == [1_000_000] * 3
        # prev_close 链：首条取 open，其后取前条 close
        assert read[0].extra["prev_close"] == 10.0
        assert read[1].extra["prev_close"] == 10.0
        assert read[2].extra["prev_close"] == 10.5

    def test_breakpoint_resume_appends_only_new(self, vipdoc: Path) -> None:
        """断点续传：本地末日期之后的新日线才追加。"""
        path = vipdoc / "sh" / "lday" / "sh600519.day"
        _build_day_file(path, ["2026-01-02", "2026-01-05"], [10.0, 10.5])
        sink = LocalDaySink(vipdoc)
        fetched = [_bar("2026-01-05", 10.5), _bar("2026-01-06", 11.0), _bar("2026-01-07", 11.5)]
        res = sink.sync("sh600519", lambda o, c: fetched)
        assert res.added == 2
        assert res.existed == 2
        read = _read_back(path)
        assert [b.datetime[:10] for b in read] == [
            "2026-01-02",
            "2026-01-05",
            "2026-01-06",
            "2026-01-07",
        ]
        assert [b.close for b in read] == [10.0, 10.5, 11.0, 11.5]
        # 跨断点 prev_close：新首条 = 本地末条 close
        assert read[2].extra["prev_close"] == 10.5

    def test_idempotent_rerun_no_duplicates(self, vipdoc: Path) -> None:
        """幂等：重跑同一数据源，不再新增、不产生重复记录。"""
        sink = LocalDaySink(vipdoc)
        bars = [_bar("2026-01-02", 10.0), _bar("2026-01-05", 10.5)]
        r1 = sink.sync("sh600519", lambda o, c: bars)
        assert r1.added == 2
        r2 = sink.sync("sh600519", lambda o, c: bars)
        assert r2.added == 0
        read = _read_back(vipdoc / "sh" / "lday" / "sh600519.day")
        assert len(read) == 2

    def test_fetched_newest_first_is_normalized(self, vipdoc: Path) -> None:
        """fetch 返回最新在前（网络序）也能正确升序落盘。"""
        sink = LocalDaySink(vipdoc)
        bars = [_bar("2026-01-06", 11.0), _bar("2026-01-05", 10.5), _bar("2026-01-02", 10.0)]
        sink.sync("sh600519", lambda o, c: bars)
        read = _read_back(vipdoc / "sh" / "lday" / "sh600519.day")
        assert [b.datetime[:10] for b in read] == ["2026-01-02", "2026-01-05", "2026-01-06"]

    def test_dict_input_coerced(self, vipdoc: Path) -> None:
        """fetch 返回 dict 形态也能写入（容忍客户端 dict 输出）。"""
        sink = LocalDaySink(vipdoc)
        bars = [
            {
                "datetime": "2026-01-02",
                "open": 10.0,
                "high": 11.0,
                "low": 9.0,
                "close": 10.0,
                "volume": 100,
                "amount": 1000.0,
            }
        ]
        res = sink.sync("sh600519", lambda o, c: bars)
        assert res.added == 1
        read = _read_back(vipdoc / "sh" / "lday" / "sh600519.day")
        assert read[0].close == 10.0
        assert read[0].volume == 100

    def test_multi_window_backfill(self, vipdoc: Path) -> None:
        """本地为空 + 数据超过单窗口 → 多窗口回填（offset 递增）。"""
        calls: list[tuple[int, int]] = []

        def fake_fetch(offset: int, count: int) -> list[Bar]:
            calls.append((offset, count))
            if offset >= 8:  # 共 8 根，之后没有更多历史
                return []
            return [
                _bar(f"2026-01-{2 + i:02d}", float(100 + i))
                for i in range(offset, min(offset + count, 8))
            ]

        sink = LocalDaySink(vipdoc)
        res = sink.sync("sh600519", fake_fetch, chunk=2)
        assert res.added == 8
        assert calls[0] == (0, 2)
        assert calls[1] == (2, 2)
        assert calls[2] == (4, 2)
        assert calls[3] == (6, 2)
        read = _read_back(vipdoc / "sh" / "lday" / "sh600519.day")
        assert len(read) == 8

    def test_future_profile_writes_open_interest(self, vipdoc: Path) -> None:
        """期货档案：末字段写持仓量（open_interest）而非上日收盘。"""
        sink = LocalDaySink(vipdoc, profile="future_day")
        bar = _bar("2026-01-02", 4100.0)
        bar.extra["open_interest"] = 12345
        res = sink.sync("sh600519", lambda o, c: [bar])
        assert res.added == 1
        # 回读（future_day 档案）：末字段解析为 open_interest
        path = sink.resolve("sh600519")
        read = DayBarReader("future_day").read(path, output="model")
        assert read[0].close == 4100.0
        assert read[0].extra["open_interest"] == 12345
        assert "prev_close" not in read[0].extra


# --------------------------------------------------------------------------- #
# sync_daily capability（Client → DirectProviderExecutor）
# --------------------------------------------------------------------------- #
class _FakeTdx:
    """最小 fake TDX 客户端：bars() 按 start 返回内存中的日线。"""

    def __init__(self, bars_by_symbol: dict[str, list[Bar]]) -> None:
        self._data = bars_by_symbol

    def bars(
        self,
        symbol: str,
        *,
        period: str = "day",
        count: int = 320,
        start: int = 0,
        as_format: str = "dict",
    ) -> list[dict]:
        bars = self._data.get(symbol, [])
        window = bars[::-1][start : start + count]  # 模拟 start=0 最新在前
        if as_format == "dict":
            return [
                {
                    "datetime": b.datetime,
                    "open": b.open,
                    "high": b.high,
                    "low": b.low,
                    "close": b.close,
                    "volume": b.volume,
                    "amount": b.amount,
                }
                for b in window
            ]
        return window


class _FakeTdxContext:
    """``DirectProviderExecutor._tdx_client`` 替身：返回可 ``with`` 的 fake 客户端。"""

    def __init__(self, client: _FakeTdx) -> None:
        self._client = client

    def __enter__(self) -> _FakeTdx:
        return self._client

    def __exit__(self, *exc: object) -> bool:
        return False


def _client(bars_by_symbol: dict[str, list[Bar]], *, root: Path | None) -> Client:
    runtime = UnifiedRuntime(vipdoc_root=None if root is None else str(root))
    runtime.executor._tdx_client = lambda: _FakeTdxContext(_FakeTdx(bars_by_symbol))
    return Client(runtime)


class TestSyncDailyCapability:
    def test_sync_daily_writes_and_reports(self, vipdoc: Path) -> None:
        bars = [_bar("2026-01-02", 10.0), _bar("2026-01-05", 10.5)]
        client = _client({"sh600519": bars, "sh600000": bars}, root=vipdoc)
        out = client.call("sync_daily", ["600519", "600000"], root=str(vipdoc)).data
        assert set(out) == {"600519", "600000"}
        assert out["600519"]["added"] == 2
        assert out["600519"]["last_date"] == "2026-01-05"
        # 文件可回读
        read = _read_back(vipdoc / "sh" / "lday" / "sh600519.day")
        assert len(read) == 2

    def test_sync_daily_requires_root(self) -> None:
        client = _client({}, root=None)
        with pytest.raises(TdxError, match="root"):
            client.call("sync_daily", ["600519"])

    def test_sync_daily_incremental(self, vipdoc: Path) -> None:
        """已有本地文件时仅补新日线。"""
        path = vipdoc / "sh" / "lday" / "sh600519.day"
        _build_day_file(path, ["2026-01-02"], [10.0])
        new_bar = _bar("2026-01-05", 10.5)
        client = _client({"sh600519": [new_bar]}, root=vipdoc)
        out = client.call("sync_daily", ["600519"], root=str(vipdoc)).data
        assert out["600519"]["added"] == 1
        assert out["600519"]["existed"] == 1
        read = _read_back(path)
        assert [b.datetime[:10] for b in read] == ["2026-01-02", "2026-01-05"]
