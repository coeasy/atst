# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""本地日线增量落盘（P1-3 §4）：把网络日 K 线增量写回 vipdoc ``.day`` 文件。

与 :class:`~atst.reader.formats.DayBarReader` 共用同一份记录布局
（32 字节 Little-Endian，见 :mod:`atst.reader.formats` 文件头注释），
保证「写入后回读一致」。

增量语义
--------
* **断点续传**：``sync()`` 从本地末日期之后开始补，已存在的日期一律跳过
  （幂等——重跑不重复、不产生重复记录）。
* **prev_close 链**：新记录的上日收盘 = 前一条记录的收盘价（本地无前值时
  取该条 open），与 reader 的 ``extra.prev_close`` 语义一致。
* **记录编码是 reader 解码的精确逆变换**（见 :meth:`LocalDaySink._encode_record`），
  因此写入的字节可直接由 :class:`DayBarReader` 回读。

用法::

    from atst import Client
    from atst.sink.local_day import LocalDaySink

    client = Client()
    sink = LocalDaySink(r"D:/tdx/vipdoc")
    # fetch(offset, count) 拉取从 start=offset 起的最近 count 根日线
    result = sink.sync(
        "sh600519",
        lambda offset, count: client.bars(
            "sh600519", period="day", count=count, start=offset
        ).data,
    )
    print(result.added)  # 本次新增条数
"""

from __future__ import annotations

import struct
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..domain.models import Bar
from ..domain.symbol import normalize_symbol
from ..reader.formats import DayBarReader, resolve_vipdoc_path
from ..reader.profile import (
    Period,
    PriceEncoding,
    TimeEncoding,
    VolumeUnit,
    get_profile,
)

__all__ = [
    "LocalDaySink",
    "SyncResult",
    "sync_daily",
]

#: 单条日线记录字节数（与 reader 的 ``RECORD_SIZE`` 同构）
RECORD_SIZE = 32

#: 单窗口拉取根数上限（对齐 0x052D 服务端单次限制）
MAX_CHUNK = 800

#: 断点续传最大回退窗口数（防御性上限：64 × 800 = 51 200 根 ≈ 200+ 年日线）
MAX_WINDOWS = 64


@dataclass(slots=True)
class SyncResult:
    """一次 ``sync`` 的结果。"""

    symbol: str  # 规范化符号，如 ``sh600519``
    path: str  # 写入的 .day 文件路径
    added: int  # 本次新增条数
    existed: int  # 本地已有（被跳过）条数
    windows: int  # 实际回退拉取窗口数


def _coerce_bar(item: Bar | dict[str, Any]) -> Bar:
    """把 ``Bar`` / ``dict`` 统一成 :class:`Bar`（容忍 fetch 返回两种形态）。"""
    if isinstance(item, Bar):
        return item
    return Bar(
        datetime=str(item.get("datetime", "")),
        open=float(item.get("open", 0.0)),
        high=float(item.get("high", 0.0)),
        low=float(item.get("low", 0.0)),
        close=float(item.get("close", 0.0)),
        volume=int(item.get("volume", 0) or 0),
        amount=float(item.get("amount", 0.0)),
        extra=dict(item.get("extra", {}) or {}),
    )


class LocalDaySink:
    """日线增量写回器：把网络日 K 线增量写入本地 ``.day`` 文件。

    Parameters
    ----------
    root:
        本地 vipdoc 根目录（与 :func:`resolve_vipdoc_path` 语义一致，
        即 ``.../vipdoc`` 本身）。
    profile:
        本地档案名（如 ``"a_share_day"`` / ``"a_share_day_sz"`` /
        ``"index_day"`` / ``"future_day"``）或 :class:`DataProfile` 实例。
    """

    def __init__(self, root: str | Path, *, profile: str | Any = "a_share_day") -> None:
        self.root = Path(root)
        self.profile = get_profile(profile) if isinstance(profile, str) else profile

    # -- 路径 / 状态 -------------------------------------------------------- #
    def resolve(self, symbol: str) -> Path:
        """返回该符号对应的 ``.day`` 文件路径。"""
        return resolve_vipdoc_path(self.root, normalize_symbol(symbol), Period.DAY)

    def last_date(self, symbol: str) -> str | None:
        """本地末日期（``YYYY-MM-DD``）；无本地数据返回 ``None``。"""
        bars = self._read_existing(self.resolve(symbol))
        return bars[-1].datetime[:10] if bars else None

    def _read_existing(self, path: Path) -> list[Bar]:
        if not path.exists() or path.stat().st_size < RECORD_SIZE:
            return []
        return DayBarReader(self.profile).read(path, output="model")

    # -- 增量同步 ----------------------------------------------------------- #
    def sync(
        self,
        symbol: str,
        fetch: Callable[[int, int], Sequence[Bar | dict[str, Any]]],
        *,
        chunk: int = MAX_CHUNK,
        max_windows: int = MAX_WINDOWS,
    ) -> SyncResult:
        """断点续传式增量同步，返回本次新增条数。

        Parameters
        ----------
        symbol:
            任意书写变种（``sh600519`` / ``600519`` / ``600519.SH``）。
        fetch:
            拉取回调 ``fetch(offset, count) -> Sequence[Bar|dict]``——
            返回从 ``start=offset`` 起的最近 ``count`` 根日线（offset 0 =
            最新）。典型取值 ``client.bars`` 或
            ``lambda o, c: client.bars(sym, period="day", count=c, start=o, as_format="obj")``。
        chunk:
            单窗口拉取根数（≤ 服务端上限 800）。
        max_windows:
            断点续传最大回退窗口数（防御性上限）。

        Returns
        -------
        :class:`SyncResult`
        """
        sym = normalize_symbol(symbol)
        path = self.resolve(sym)
        existing = self._read_existing(path)
        existing_dates = {b.datetime[:10] for b in existing}
        last = existing[-1].datetime[:10] if existing else None

        new: list[Bar] = []
        seen: set[str] = set(existing_dates)
        offset = 0
        windows = 0
        for _ in range(max_windows):
            fetched = [_coerce_bar(b) for b in fetch(offset, chunk)]
            windows += 1
            if not fetched:
                break
            asc = sorted((b for b in fetched if b.datetime), key=lambda b: b.datetime)
            if not asc:
                break
            for b in asc:
                d = b.datetime[:10]
                if d not in seen:
                    seen.add(d)
                    new.append(b)
            # 断点判断：本窗口最旧日期 ≤ 本地末日期 → 后续更早的窗口已被覆盖
            if last is not None and asc[0].datetime[:10] <= last:
                break
            if len(fetched) < chunk:  # 已拿完全部历史
                break
            offset += chunk

        if new:
            self._append_bars(path, existing, new)
        return SyncResult(
            symbol=sym,
            path=str(path),
            added=len(new),
            existed=len(existing),
            windows=windows,
        )

    # -- 写入 --------------------------------------------------------------- #
    def _append_bars(self, path: Path, existing: Sequence[Bar], new: Sequence[Bar]) -> None:
        """把新增日线按升序追加到 ``.day`` 文件（保持 prev_close 链连续）。"""
        merged = sorted(new, key=lambda b: b.datetime)
        prev_close: float | None = existing[-1].close if existing else None
        payload = bytearray()
        for bar in merged:
            pc = prev_close if prev_close is not None else bar.open
            payload += self._encode_record(bar, prev_close=pc)
            prev_close = bar.close
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("ab") as f:
            f.write(payload)

    # -- 编码（reader 解码的精确逆变换）------------------------------------- #
    def _encode_record(self, bar: Bar, *, prev_close: float) -> bytes:
        p = self.profile
        if p.time_encoding != TimeEncoding.YYYYMMDD:
            raise NotImplementedError(
                f"本地日线写回暂不支持 time_encoding={p.time_encoding!r}（仅 YYYYMMDD）"
            )
        date_raw = self._encode_date(bar.datetime)
        scale = p.price_scale or 1
        if p.price_encoding == PriceEncoding.FLOAT32:
            o, h, lo, c = bar.open, bar.high, bar.low, bar.close
        elif p.price_encoding == PriceEncoding.UINT32:
            o = int(round(bar.open * scale))
            h = int(round(bar.high * scale))
            lo = int(round(bar.low * scale))
            c = int(round(bar.close * scale))
        else:
            raise NotImplementedError(
                f"本地日线写回暂不支持 price_encoding={p.price_encoding!r}（仅 uint32/float32）"
            )
        amount_raw = self._to_raw_amount(bar.amount)
        volume_raw = self._to_raw_volume(bar.volume)
        last = self._encode_last(bar, p, prev_close)
        return struct.pack("<IIIIIfII", date_raw, o, h, lo, c, amount_raw, volume_raw, last)

    @staticmethod
    def _encode_date(datetime_str: str) -> int:
        """``"2026-01-22"`` / ``"2026-01-22 15:00:00"`` → ``20260122``。"""
        y, m, d = (int(x) for x in datetime_str[:10].split("-"))
        return y * 10000 + m * 100 + d

    def _to_raw_amount(self, amount: float) -> float:
        """成交额（元）→ 文件内 float32 原值（reader 的 to_amount 逆变换）。"""
        unit = self.profile.amount_unit
        if unit == "wan":
            return amount / 10_000.0
        if unit == "yi":
            return amount / 100_000_000.0
        return amount

    def _to_raw_volume(self, volume: int) -> int:
        """成交量（股）→ 文件内 uint32 原值（reader 的 to_volume 逆变换）。"""
        if self.profile.volume_unit == VolumeUnit.LOT:
            return int(round(volume / 100.0))
        return int(volume)

    def _encode_last(self, bar: Bar, p: Any, prev_close: float) -> int:
        """末字段：期货为持仓量，其余为上日收盘（× scale）。"""
        scale = p.price_scale or 1
        if "open_interest" in p.extra_fields:
            return int(bar.extra.get("open_interest", 0) or 0)
        if p.price_encoding == PriceEncoding.FLOAT32:
            return int(round(prev_close))
        return int(round(prev_close * scale))


def sync_daily(
    symbols: Sequence[str],
    fetch: Callable[[str, int, int], Sequence[Bar | dict[str, Any]]],
    root: str | Path,
    *,
    profile: str | Any = "a_share_day",
    chunk: int = MAX_CHUNK,
    max_windows: int = MAX_WINDOWS,
) -> dict[str, SyncResult]:
    """批量增量同步日线（模块级便捷入口）。

    Parameters
    ----------
    symbols:
        证券代码列表（任意书写变种）。
    fetch:
        ``fetch(symbol, offset, count) -> Sequence[Bar|dict]``——按符号拉取。
        典型取值 ``lambda sym, o, c: client.bars(sym, period="day", count=c, start=o)``。
    root:
        本地 vipdoc 根目录。
    profile:
        本地档案名（每只统一使用同一档案）。

    Returns
    -------
    ``dict[symbol, SyncResult]``
    """
    sink = LocalDaySink(root, profile=profile)
    out: dict[str, SyncResult] = {}

    def _make_fetch(symbol: str) -> Callable[[int, int], Sequence[Bar | dict[str, Any]]]:
        def _fetch(offset: int, count: int) -> Sequence[Bar | dict[str, Any]]:
            return fetch(symbol, offset, count)

        return _fetch

    for sym in symbols:
        norm = normalize_symbol(sym)
        out[sym] = sink.sync(norm, _make_fetch(norm), chunk=chunk, max_windows=max_windows)
    return out
