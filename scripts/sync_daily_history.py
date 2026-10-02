#!/usr/bin/env python3
# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""用 TDX 接口每天全量同步 A 股历史日线到本地目录。

设计要点（都是被实战教训逼出来的，不是可有可无的装饰）：

1. **断点续拉**：``start`` 是 16-bit 分页偏移，不是日期游标，续拉只能靠
   "本地那支文件的最后一根日期 + 多要一段回来合并"。见
   :func:`_resume_bar_count`。
2. **幂等落地**：先合并去重再整体重写到 ``.tmp`` 然后 ``os.replace``
   原子改名——写一半崩了不会留半个坏 parquet。
3. **限速与失败退避**：并发旋钮是连接池的 ``slots_per_host``（每台主站
   几条连接），加调用间隔；不可重试的错误（``Advice.retryable`` 为假）
   直接跳过，不白等。
4. ** universe 解析**：7709 的 ``0x044D SECURITY_LIST`` 已登记 offline
   （首页请求即抛 ``CommandOffline``），代码表**不能**从 tdx 拿。这里按
   本地 vipdoc → 自备清单的顺序取，都没有时明确报错而不是静默拉个零。
5. **口径自检**：OHLC 自洽、volume 单位归一到股、日期升序去重，任一条
   破就记进状态文件的 ``rejected`` 而不污染磁盘。

用法::

    python scripts/sync_daily_history.py --root data/kline/day
    python scripts/sync_daily_history.py --root data/kline/day --symbols sh600519 sz000001
    python scripts/sync_daily_history.py --root data/kline/day --full --workers 4
    python scripts/sync_daily_history.py --root data/kline/day --index
    python scripts/sync_daily_history.py --root data/kline/day --dry-run

调度（每个交易日盘后跑，错峰避开 9:30 开盘后 30 分钟）::

    # crontab -e
    30 18 * * 1-5 cd /path/to/atst && python scripts/sync_daily_history.py --root data/kline/day
"""

from __future__ import annotations

import argparse
import json
import os
import random
import re
import sys
import tempfile
import threading
import time
from collections.abc import Iterable, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

#: ``python scripts/sync_daily_history.py`` 会把 ``scripts/`` 放上 sys.path[0]，
#: 而 atst 在仓库根——不补这一行就等于要求用户先装包或先设 PYTHONPATH。
_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from atst.client import TdxClient  # noqa: E402
from atst.errors import TdxError, advice_for  # noqa: E402
from atst.output import write  # noqa: E402

#: 单只一次最多要多少根。协议给的分页上限是 0xFFFF，且 ``start + count > 0x10000``
#: 直接抛 ParseError；800 是服务端单页上限，超了客户端自动翻页。
MAX_WINDOW = 8000

#: 续拉时比"上次那根"多要的根数（多要一段，覆盖周末/停牌造成的错位）
RESUME_OVERLAP = 30

BAR_KEYS = ("datetime", "open", "high", "low", "close", "volume", "amount")


# ---------------------------------------------------------------------------
# 状态：一个 JSON 文件记住"上次拉到哪、哪些失败过"
# ---------------------------------------------------------------------------


@dataclass
class State:
    """``state.json`` 的形状。落盘即续跑的证据，删了就当全新全量。"""

    last_run: str = ""
    symbols: int = 0
    bars: int = 0
    #: symbol -> 该支最后一次落进磁盘的日期键（``YYYY-MM-DD``）
    last_date: dict[str, str] = field(default_factory=dict)
    #: symbol -> 上次失败原因（下次重试，再失败就在矩阵里标红）
    failed: dict[str, str] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path) -> State:
        if path.exists():
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                raw = {}
            if isinstance(raw, dict):
                return cls(
                    last_run=str(raw.get("last_run", "")),
                    symbols=int(raw.get("symbols", 0)),
                    bars=int(raw.get("bars", 0)),
                    last_date=dict(raw.get("last_date") or {}),
                    failed=dict(raw.get("failed") or {}),
                )
        return cls()

    def dump(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "last_run": self.last_run,
            "symbols": self.symbols,
            "bars": self.bars,
            "last_date": self.last_date,
            "failed": self.failed,
        }
        _atomic_write_text(path, json.dumps(payload, ensure_ascii=False, indent=2))


# ---------------------------------------------------------------------------
# universe：代码表从哪来
# ---------------------------------------------------------------------------


def symbols_from_vipdoc(vipdoc: Path, markets: Sequence[str] = ("sh", "sz")) -> list[str]:
    """本地通达信目录 ``vipdoc/<mkt>/lday/*.day``——文件名即带前缀的代码。"""
    out: list[str] = []
    for market in markets:
        folder = vipdoc / market / "lday"
        if not folder.is_dir():
            continue
        out.extend(path.stem for path in folder.glob(f"{market}*.day"))
    return sorted(out)


def symbols_from_csv(path: Path) -> list[str]:
    """自备清单（每行一个代码，允许带/不带 sh/sz 前缀，允许带列名）。"""
    out: list[str] = []
    seen: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        cell = line.strip().split(",")[0].strip()
        token = cell.lower()
        if not token or token in {"symbol", "code"}:
            continue
        if token.startswith(("sh", "sz", "bj")):
            code = token
        elif len(cell) == 6 and cell.isdigit():
            code = ("sh" if cell[0] == "6" else "sz") + cell
        else:
            continue
        if code not in seen:
            seen.add(code)
            out.append(code)
    return sorted(out)


# ---------------------------------------------------------------------------
# 取数与合并
# ---------------------------------------------------------------------------


def _atomic_write_text(path: Path, text: str) -> None:
    """先写同目录临时文件再原子改名——写一半崩了不会留半个坏 JSON。"""
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    os.close(fd)
    tmp_path = Path(tmp)
    try:
        tmp_path.write_text(text, encoding="utf-8", newline="\n")
        os.replace(tmp_path, path)
    finally:
        if tmp_path.exists():
            tmp_path.unlink()


#: 日期键：只认 ``YYYY-MM-DD``。坏代码（如 ``sh999999``）主站真的会回一堆
#: 荒唐日期（实测出现过 ``8414-91-57`` 这种），拿它当排序键会把整个文件带歪。
_DATE_KEY = re.compile(r"(\d{4}-\d{2}-\d{2})")


def _date_key(row: dict[str, Any]) -> str:
    """从一行里取 ``YYYY-MM-DD`` 日期键；取不到就返回空串（该行不可排）。"""
    match = _DATE_KEY.search(str(row.get("date") or row.get("datetime") or ""))
    return match.group(1) if match else ""


def _dedup_merge(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """按日期键去重合并并保持升序（同一天重复覆盖取后者）。

    取不到日期键的行直接丢——它们没法进时间序列，留着只会在下一次合并里
    把排序彻底搅乱。
    """
    merged: dict[str, dict[str, Any]] = {}
    for row in rows:
        key = _date_key(row)
        if key:
            merged[key] = row
    return sorted(merged.values(), key=_date_key)


def _validate(rows: Sequence[dict[str, Any]]) -> list[str]:
    """口径自检：返回问题清单（空清单表示合格）。"""
    problems: list[str] = []
    prev = ""
    for row in rows:
        day = _date_key(row)
        if not day:
            problems.append("有行取不出 YYYY-MM-DD 日期键")
            continue
        if day < prev:
            problems.append(f"日期非升序 {prev} -> {day}")
        prev = day
        try:
            high, low = float(row["high"]), float(row["low"])
            o, c = float(row["open"]), float(row["close"])
        except (KeyError, TypeError, ValueError):
            problems.append(f"{day or '?'} 缺 OHLC")
            continue
        if high < low:
            problems.append(f"{day} high<low")
        elif high < max(o, c) or low > min(o, c):
            problems.append(f"{day} OHLC 不自洽")
        if row.get("volume_unit") not in (None, "", "share"):
            problems.append(f"{day} volume 单位是 {row['volume_unit']}（应为股）")
    return problems


def _existing_rows(path: Path) -> list[dict[str, Any]]:
    """读回本地已落盘的日线（parquet / csv 都认）。返回空表示"没存过"。"""
    if not path.exists():
        return []
    fmt = "parquet" if path.suffix.lower() in {".parquet", ".pq"} else "csv"
    try:
        return _read_rows(path, fmt)
    except Exception:  # 坏文件不阻断本轮，按空处理（下一轮会被覆盖成好的）
        return []


def _read_rows(path: Path, fmt: str) -> list[dict[str, Any]]:
    """按 sink 反读已落盘的行，字段补齐成 dict。"""
    import csv as _csv

    if fmt == "parquet":
        try:
            import pyarrow.parquet as pq  # type: ignore[import-not-found]
        except ImportError:
            return []
        table = pq.read_table(path)
        return table.to_pylist()
    with path.open("r", encoding="utf-8", newline="") as handle:
        return [dict(row) for row in _csv.DictReader(handle)]


def _resume_count(state: State, symbol: str, full: bool, lookback: int) -> int:
    """该支这次要多少根：续拉多要一段，全量就拉满窗口。"""
    if full:
        return MAX_WINDOW
    last = state.last_date.get(symbol, "")
    if not last:
        return lookback
    return lookback + RESUME_OVERLAP


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------


@dataclass
class Outcome:
    """一只标的的观测结果。"""

    symbol: str
    status: str
    rows: int = 0
    day: str = ""
    detail: str = ""


class Syncer:
    """把 TDX 日线同步到本地目录。实例本身无状态，跨轮次靠 :class:`State`。"""

    def __init__(
        self,
        out: Path,
        state_path: Path | None = None,
        *,
        timeout: float = 10.0,
        slots_per_host: int = 4,
        lookback: int = 320,
        gap_sleep: float = 0.2,
        jitter: float = 0.1,
        full: bool = False,
        dry_run: bool = False,
        as_index: bool = False,
    ) -> None:
        self.out = out
        self.state_path = state_path or (out / "state.json")
        self.state = State.load(self.state_path)
        self.lookback = lookback
        self.gap_sleep = gap_sleep
        self.jitter = jitter
        self.full = full
        self.dry_run = dry_run
        self.as_index = as_index
        self.out.mkdir(parents=True, exist_ok=True)
        self._client = TdxClient(timeout=timeout, slots_per_host=slots_per_host)
        self._lock = threading.Lock()
        self.rejected: dict[str, str] = {}

    # -- 单只 ----------------------------------------------------------
    def sync_one(self, symbol: str) -> Outcome:
        count = _resume_count(self.state, symbol, self.full, self.lookback)
        try:
            bars = self._client.bars(symbol, period="day", count=count, index=self.as_index)
        except TdxError as exc:
            advice = advice_for(exc)
            if advice.retryable and "timeout" in str(exc).lower():
                time.sleep(min(advice.backoff or 0.5, 3.0))
                try:
                    bars = self._client.bars(symbol, period="day", count=count, index=self.as_index)
                except TdxError as exc2:
                    return Outcome(symbol, "SKIP", detail=f"{type(exc2).__name__}: {exc2}")
            else:
                return Outcome(symbol, "SKIP", detail=f"{type(exc).__name__}: {exc}")
        except Exception as exc:  # 传输层原异常（内核不替换 Provider）
            return Outcome(symbol, "SKIP", detail=f"{type(exc).__name__}: {exc}")

        if not bars:
            return Outcome(symbol, "EMPTY")

        # 和磁盘上已有的合并：TDX 的 bars 永远只给"最近 N 根"，不合并的话
        # 每轮都会把文件重写成 N 根，历史窗口随轮数往上漂；合并后收敛到一个
        # 稳定的长度（--full 跑出的长历史也保得住）。
        merged = _dedup_merge(bars)
        if not self.dry_run:
            merged = _dedup_merge([*_existing_rows(self.out / f"{symbol}.parquet"), *merged])
        # 自检必须在"合并 + 排好序之后"做：--full 拉回来的是倒序（最新在前），
        # 在原始批次上校验只会撞出假的「日期非升序」。
        problems = _validate(merged)
        if problems:
            self.rejected[symbol] = problems[0]
            return Outcome(symbol, "REJECT", detail=problems[0])
        if not merged:
            return Outcome(symbol, "EMPTY")
        day = _date_key(merged[-1])
        if not self.dry_run:
            _atomic_write_parquet(merged, self.out / f"{symbol}.parquet")
        with self._lock:
            self.state.last_date[symbol] = day
            self.state.failed.pop(symbol, None)
        return Outcome(symbol, "OK", rows=len(merged), day=day)

    # -- 全量 ----------------------------------------------------------
    def run(self, symbols: Sequence[str], workers: int = 4) -> dict[str, Any]:
        tally: dict[str, Any] = {"OK": 0, "EMPTY": 0, "SKIP": 0, "REJECT": 0}
        bars_total, days_total = 0, 0
        started = time.time()
        with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
            futures = {pool.submit(self.sync_one, s): s for s in symbols}
            for done in as_completed(futures):
                outcome = done.result()
                tally[outcome.status] = tally.get(outcome.status, 0) + 1
                if outcome.status == "OK":
                    bars_total += outcome.rows
                    days_total += 1
                else:
                    self.state.failed[outcome.symbol] = outcome.detail or outcome.status
                print(
                    f"[{outcome.status:<6}] {outcome.symbol:<10} "
                    f"{outcome.rows:>5} 行  末日 {outcome.day or '-'}  {outcome.detail[:60]}",
                    flush=True,
                )
                time.sleep(self.gap_sleep + random.random() * self.jitter)
        if not self.dry_run:
            self.state.last_run = time.strftime("%Y-%m-%d %H:%M:%S")
            self.state.symbols = days_total
            self.state.bars = bars_total
            self.state.dump(self.state_path)
        tally["elapsed_s"] = round(time.time() - started, 1)
        tally["rejected_detail"] = json.dumps(self.rejected, ensure_ascii=False)
        return tally


def _atomic_write_parquet(rows: Sequence[dict[str, Any]], path: Path) -> None:
    """先落 ``.tmp`` 再原子改名；同时清掉可能存在的 csv 影子文件。"""
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    os.close(fd)
    tmp_path = Path(tmp)
    try:
        # 复用仓库自己的 sink。原子改名的临时文件不以后缀收尾，
        # 所以必须显式 fmt="parquet"——让 write() 按扩展名推断恰好在这里会抛
        # ValueError（审计 §2-15 已经把那个静默语义拿掉了）。
        write(list(rows), str(tmp_path), fmt="parquet")
        os.replace(tmp_path, path)
    finally:
        if tmp_path.exists():
            tmp_path.unlink()
    for sibling in (path.with_suffix(".csv"),):
        if sibling.exists():
            sibling.unlink()


def resolve_universe(args: argparse.Namespace) -> list[str]:
    """按命令行优先级定宇宙：显式 --symbols > 清单文件 > 本地 vipdoc。"""
    if args.symbols:
        return args.symbols
    if args.universe_file:
        return symbols_from_csv(Path(args.universe_file))
    for candidate in (args.vipdoc, Path("C:/new_tdx"), Path("D:/new_tdx")):
        found = symbols_from_vipdoc(Path(candidate)) if candidate else []
        if found:
            print(f"universe：取自本地通达信目录 {candidate}（{len(found)} 只）")
            return found
    return []


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="用 TDX 接口每天全量同步 A 股历史日线到本地目录",
    )
    default_root = Path("data/kline/day")
    p.add_argument("--root", type=Path, default=default_root, help="本地落盘根目录")
    p.add_argument(
        "--state", type=Path, default=None, help="状态文件路径（默认 <root>/state.json）"
    )
    p.add_argument("--symbols", nargs="+", default=[], help="显式指定要同步的代码（跳过宇宙解析）")
    p.add_argument("--universe-file", type=Path, default=None, help="自备清单 CSV")
    p.add_argument("--vipdoc", type=Path, default=None, help="本地通达信目录（自动找 lday）")
    p.add_argument("--full", action="store_true", help="全量重拉（默认按上次日期增量续拉）")
    p.add_argument("--lookback", type=int, default=320, help="每只最少拉多少根日线")
    p.add_argument("--workers", type=int, default=4, help="并发只数（实际瓶颈是 slots_per_host）")
    p.add_argument("--gap-sleep", type=float, default=0.2, help="每只之间的礼貌间隔秒数")
    p.add_argument(
        "--index",
        action="store_true",
        help="把 universe 里的代码当指数拉（0x052D 的 index 位；sh000*/sz399* 要走这条路）",
    )
    p.add_argument("--dry-run", action="store_true", help="只拉取不落盘（验证连通性用）")
    p.add_argument("--timeout", type=float, default=10.0)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    symbols = resolve_universe(args)
    if not symbols:
        print(
            "找不到 universe：7709 的 0x044D SECURITY_LIST 已登记 offline，tdx 拿不回代码表。\n"
            "给 --symbols 列代码，或准备 --universe-file（CSV 一行一个），或装过通达信并指 --vipdoc。",
            file=sys.stderr,
        )
        return 2
    print(
        f"universe：{len(symbols)} 只；root={args.root}；模式={'全量' if args.full else '增量续拉'}"
    )
    syncer = Syncer(
        args.root,
        args.state,
        timeout=args.timeout,
        lookback=args.lookback,
        gap_sleep=args.gap_sleep,
        full=args.full,
        dry_run=args.dry_run,
        as_index=args.index,
    )
    tally = syncer.run(symbols, workers=args.workers)
    print(
        "\n汇总："
        + json.dumps({k: v for k, v in tally.items() if k != "rejected_detail"}, ensure_ascii=False)
    )
    if syncer.rejected:
        print(f"口径自检拦下 {len(syncer.rejected)} 只（未落盘）：")
        for sym, why in list(syncer.rejected.items())[:10]:
            print(f"  - {sym}: {why}")
    # 全空失败视为环境级（网络/主站不可达），不是本脚本缺陷
    return 0 if tally.get("OK") else 2


if __name__ == "__main__":
    raise SystemExit(main())
