# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""轻量缓存层（U4 + C5）。

覆盖面（C5：缓存覆盖面与文档一致）
--------------------------------
* :class:`KlineCache` —— K 线（SQLite 持久化，增量合并）；
* :class:`QuoteCache` —— 实时行情快照（进程内 TTL，默认 3s 对齐盘中
  刷新节奏），由 :class:`~tstdx.sources.DataSourceRouter.quotes` 读穿；
* **分时不缓存**：分时数据按交易日单调追加、当前 API 面无 router 级
  minute 降级路径，重复全量拉取场景尚未成立；若后续路由增加 minute
  分支，按 :class:`QuoteCache` 同款模式（TTL 60s）接入，先行在本文档
  明确「不覆盖」，避免能力与文档漂移。

设计目标
--------
* **避免重复全量拉取**：同一标的同一周期的日线被反复请求时，命中缓存直接返回；
* **增量更新**：新数据只在旧数据「末根之后」合并追加（按 ``datetime`` 去重），
  不重复存储历史；
* **零额外依赖**：仅用标准库 ``sqlite3``；失败静默降级（缓存是优化项，绝不
  影响主链路可用性）。

用法::

    from tstdx.cache import KlineCache

    cache = KlineCache("~/.tstdx/kline_cache.sqlite3")
    bars = cache.get("sh600519", "day")        # list[dict] | None
    cache.merge("sh600519", "day", bars)       # 增量合并，返回新增/变更条数
    cache.close()

:class:`KlineCache` 可注入 :class:`~tstdx.sources.DataSourceRouter` 的
``kline_cache`` 参数；配置 ``sources.kline_cache_db`` 后由
:func:`~tstdx.sources.build_router` 自动装配（一键开启）。
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import sqlite3
import threading
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

__all__ = ["KlineCache", "QuoteCache"]

_LOG = logging.getLogger("tstdx.cache")

#: K 线核心字段（与 :class:`tstdx.domain.models.Bar` 对齐）
_BAR_KEYS = ("datetime", "open", "high", "low", "close", "volume", "amount")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS kline (
    symbol     TEXT    NOT NULL,
    period     TEXT    NOT NULL,
    datetime   TEXT    NOT NULL,
    open       REAL    NOT NULL DEFAULT 0,
    high       REAL    NOT NULL DEFAULT 0,
    low        REAL    NOT NULL DEFAULT 0,
    close      REAL    NOT NULL DEFAULT 0,
    volume     INTEGER NOT NULL DEFAULT 0,
    amount     REAL    NOT NULL DEFAULT 0,
    extra      TEXT    NOT NULL DEFAULT '{}',
    updated_at REAL    NOT NULL,
    PRIMARY KEY (symbol, period, datetime)
);
CREATE INDEX IF NOT EXISTS idx_kline_symbol_period ON kline (symbol, period);
"""


def _bar_to_row(b: dict[str, Any]) -> tuple[Any, ...]:
    extra = {k: v for k, v in b.items() if k not in _BAR_KEYS}
    return (
        b.get("datetime", ""),
        float(b.get("open", 0.0) or 0.0),
        float(b.get("high", 0.0) or 0.0),
        float(b.get("low", 0.0) or 0.0),
        float(b.get("close", 0.0) or 0.0),
        int(b.get("volume", 0) or 0),
        float(b.get("amount", 0.0) or 0.0),
        json.dumps(extra, ensure_ascii=False, sort_keys=True),
    )


class KlineCache:
    """SQLite K 线缓存（线程安全，惰性建库，失败静默降级）。"""

    def __init__(self, db_path: str | os.PathLike[str] = "~/.tstdx/kline_cache.sqlite3") -> None:
        self.path = Path(os.path.expanduser(str(db_path)))
        self._conn: sqlite3.Connection | None = None
        self._lock = threading.RLock()

    # -- 连接管理 ----------------------------------------------------------- #
    def _db(self) -> sqlite3.Connection | None:
        """惰性建库；失败（只读目录 / 权限）返回 None 并静默降级。"""
        if self._conn is not None:
            return self._conn
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(str(self.path), timeout=5.0)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(_SCHEMA)
            conn.commit()
            self._conn = conn
            return conn
        except Exception as exc:  # noqa: BLE001 - 缓存失败不致命
            _LOG.warning("KlineCache 初始化失败，本次禁用缓存: %s", exc)
            self._conn = None
            return None

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                with contextlib.suppress(Exception):
                    self._conn.close()
                self._conn = None

    # -- 读 ---------------------------------------------------------------- #
    def get(self, symbol: str, period: str, count: int = 0) -> list[dict[str, Any]] | None:
        """读取缓存 K 线（按 datetime 升序）；``count`` 为 0 或负数时返回全部。

        Returns
        -------
        未命中（无该 symbol/period 记录）返回 ``None``；命中返回最近 ``count`` 根。
        """
        with self._lock:
            conn = self._db()
            if conn is None:
                return None
            try:
                rows = conn.execute(
                    "SELECT open,high,low,close,volume,amount,datetime,extra "
                    "FROM kline WHERE symbol=? AND period=? ORDER BY datetime ASC",
                    (symbol, period),
                ).fetchall()
            except Exception:  # noqa: BLE001
                return None
            if not rows:
                return None
            if count and len(rows) > count:
                rows = rows[-count:]
            bars = []
            for open_, high, low, close, volume, amount, dt, extra_json in rows:
                bar: dict[str, Any] = {
                    "datetime": dt,
                    "open": open_,
                    "high": high,
                    "low": low,
                    "close": close,
                    "volume": volume,
                    "amount": amount,
                }
                try:
                    extra = json.loads(extra_json or "{}")
                except json.JSONDecodeError:
                    extra = {}
                if extra:
                    bar.update(extra)
                bars.append(bar)
            return bars

    def count(self, symbol: str, period: str) -> int:
        with self._lock:
            conn = self._db()
            if conn is None:
                return 0
            try:
                row = conn.execute(
                    "SELECT COUNT(*) FROM kline WHERE symbol=? AND period=?",
                    (symbol, period),
                ).fetchone()
            except Exception:  # noqa: BLE001
                return 0
            return int(row[0]) if row else 0

    # -- 写 ---------------------------------------------------------------- #
    def merge(self, symbol: str, period: str, bars: Sequence[dict[str, Any]]) -> int:
        """把 ``bars`` 增量合并进缓存（按 datetime 去重，新根才落库）。

        Returns
        -------
        实际新增/变更的根数（0 = 无新数据，可据此跳过后续写入）。
        """
        if not bars:
            return 0
        with self._lock:
            conn = self._db()
            if conn is None:
                return 0
            now = time.time()
            changed = 0
            try:
                cur = conn.cursor()
                for b in bars:
                    if not isinstance(b, dict) or not b.get("datetime"):
                        continue
                    values = _bar_to_row(b)
                    cur.execute(
                        "INSERT OR REPLACE INTO kline "
                        "(symbol, period, datetime, open, high, low, close, volume, amount, extra, updated_at) "
                        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                        (symbol, period, *values, now),
                    )
                    if cur.rowcount > 0:
                        changed += 1
                conn.commit()
                return changed
            except Exception as exc:  # noqa: BLE001
                _LOG.warning("KlineCache.merge 失败: %s", exc)
                with contextlib.suppress(Exception):
                    conn.rollback()
                return 0

    def clear(self, symbol: str | None = None, period: str | None = None) -> int:
        """清空缓存；指定 symbol/period 时只清该项。返回删除条数。"""
        with self._lock:
            conn = self._db()
            if conn is None:
                return 0
            try:
                cur = conn.cursor()
                if symbol and period:
                    cur.execute("DELETE FROM kline WHERE symbol=? AND period=?", (symbol, period))
                elif symbol:
                    cur.execute("DELETE FROM kline WHERE symbol=?", (symbol,))
                else:
                    cur.execute("DELETE FROM kline")
                conn.commit()
                return cur.rowcount
            except Exception:  # noqa: BLE001
                return 0

    def __enter__(self) -> KlineCache:
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()


# --------------------------------------------------------------------------- #
# QuoteCache（C5）：进程内实时行情 TTL 缓存
# --------------------------------------------------------------------------- #
class QuoteCache:
    """实时行情快照 TTL 缓存（C5）。

    进程内、线程安全（``threading.Lock``）；默认 TTL 3s（对齐盘中刷新
    节奏），由 :class:`~tstdx.sources.DataSourceRouter.quotes` 读穿——
    命中直接返回，未命中走降级链并回写。缓存是优化项：任何内部错误
    静默当作未命中，绝不影响主链路。

    Parameters
    ----------
    ttl:
        条目有效期（秒）。默认 3.0。
    maxsize:
        容量上限（按 symbols 组合计数）；超限整体清空（行情快照低频
        重取成本低，无需 LRU 精细淘汰）。
    """

    def __init__(self, ttl: float = 3.0, maxsize: int = 4096) -> None:
        self.ttl = float(ttl)
        self.maxsize = int(maxsize)
        self._data: dict[str, tuple[float, list[dict[str, Any]]]] = {}
        self._lock = threading.Lock()
        #: 命中 / 未命中计数（供观测与测试断言）
        self.hits = 0
        self.misses = 0

    @staticmethod
    def _key(symbols: Sequence[str]) -> str:
        return ",".join(str(s) for s in symbols)

    def get(self, symbols: Sequence[str]) -> list[dict[str, Any]] | None:
        """命中返回深拷贝行列表（防调用方原地改写缓存），否则 ``None``。"""
        key = self._key(symbols)
        try:
            with self._lock:
                entry = self._data.get(key)
                if entry is None:
                    self.misses += 1
                    return None
                ts, rows = entry
                if time.monotonic() - ts > self.ttl:
                    del self._data[key]
                    self.misses += 1
                    return None
                self.hits += 1
                return [dict(r) for r in rows]
        except Exception:  # noqa: BLE001 —— 缓存异常不影响主链路
            return None

    def put(self, symbols: Sequence[str], rows: Sequence[dict[str, Any]]) -> None:
        """写入（失败静默）。"""
        key = self._key(symbols)
        try:
            with self._lock:
                if len(self._data) >= self.maxsize:
                    self._data.clear()
                self._data[key] = (time.monotonic(), [dict(r) for r in rows])
        except Exception:  # noqa: BLE001
            pass

    def clear(self) -> None:
        with self._lock:
            self._data.clear()
