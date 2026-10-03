#!/usr/bin/env python3
# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""用 TDX 接口把 A 股日线同步到本地目录，**零参数即可直接跑**。

::

    python scripts/sync_daily_history.py

上面这一行就是全部用法：默认把日线写进 ``data/day/``、状态写 ``data/state.json``、
代码表写 ``data/universe.csv``，默认同步内置宇宙（20 只龙头 + 8 个宽基指数）。
不是"示例，需自行改路径"——是拷过去就能每天盘后跑。

设计要点（每一条都是被实战教训逼出来的，不是装饰）：

1. **启动即跑**：默认 root 锚定仓库根的 ``data/``，不依赖当前工作目录；
   universe 按 `本地 vipdoc → data/universe.csv → 内置默认宇宙` 三级兜底，
   三级都没有时也不会空手而归——内置宇宙就是给"什么都没准备"这条路径用的。
2. **指数位自动判定**：``0x052D`` 的 index 位搞反了主站会回一堆荒唐日期
   （实测 ``sh000001`` 不走 index 位得到 ``5616-57-83``），所以 ``sh000*``/
   ``sz399*`` 自动判为指数并走 ``bars(index=True)``；``--index/--no-index``
   可整体覆盖。
3. **断点续拉**：``start`` 是 16-bit 分页偏移不是日期游标，续拉只能靠
   "本地那支文件的最后一根日期 + 多要一段回来合并"。见 :func:`_resume_bar_count`。
4. **幂等落地**：先合并去重再整体重写到 ``.tmp`` 然后 ``os.replace`` 原子改名——
   写一半崩了不会留半个坏 parquet。
5. **口径自检**：OHLC 自洽、volume 单位归一到股、日期升序去重，任一条破就记进
   ``state.json`` 的 ``rejected`` 而不污染磁盘。
6. **代码表可自产**：``0x044D SECURITY_LIST`` 已登记 offline，代码表不能从 tdx
   拿回来。``--scan`` 走"枚举代码段 + 探测主站回不回真实数据"，把全市场代码表
   落成本地 ``universe.csv``——不存在的代码主站回空首页，存在的回真实数据，
   这个差别就是判定依据。

用法::

    python scripts/sync_daily_history.py                      # 零参数：增量同步内置宇宙到 data/
    python scripts/sync_daily_history.py --full               # 全量重拉（长历史）
    python scripts/sync_daily_history.py --symbols sh600519 sz000001
    python scripts/sync_daily_history.py --universe-file data/universe.csv
    python scripts/sync_daily_history.py --root data --limit 200
    python scripts/sync_daily_history.py --scan               # 探测全市场代码表
    python scripts/sync_daily_history.py --index              # 强制全部当指数拉
    python scripts/sync_daily_history.py --dry-run            # 只拉不落盘

调度（每个交易日盘后跑，错峰避开开盘后 30 分钟）::

    # crontab -e
    30 18 * * 1-5 cd /path/to/atst && python scripts/sync_daily_history.py >> data/sync.log 2>&1
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
import warnings
from collections.abc import Iterable, Iterator, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import date as _date
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

#: 默认落盘根目录。锚仓库根而不是 cwd：cwd 换一处就换一套数据，这种漂移在
#: 定时任务里最难查——crontab 的 cwd 往往不是仓库根。
DEFAULT_ROOT = _REPO / "data"

#: 内置默认宇宙。存在的判据是"主站真回得来日线"（不是照着代码段猜的），
#: 后面 :func:`resolve_universe` 在三级兜底都落空时用它，保证零参数有数据。
DEFAULT_UNIVERSE: tuple[tuple[str, str], ...] = (
    ("sh600519", "贵州茅台"),
    ("sh600036", "招商银行"),
    ("sh601318", "中国平安"),
    ("sh600276", "恒瑞医药"),
    ("sh600030", "中信证券"),
    ("sh601899", "紫金矿业"),
    ("sh600887", "伊利股份"),
    ("sh601857", "中国石油"),
    ("sh600309", "万华化学"),
    ("sh601012", "隆基绿能"),
    ("sz000001", "平安银行"),
    ("sz000002", "万科A"),
    ("sz300750", "宁德时代"),
    ("sz002594", "比亚迪"),
    ("sz000651", "格力电器"),
    ("sz000333", "美的集团"),
    ("sz300059", "东方财富"),
    ("sz002415", "海康威视"),
    ("sz000725", "京东方A"),
    ("sz000858", "五粮液"),
    ("sh000001", "上证指数"),
    ("sh000300", "沪深300"),
    ("sh000905", "中证500"),
    ("sh000688", "科创50"),
    ("sh000016", "上证50"),
    ("sz399001", "深证成指"),
    ("sz399006", "创业板指"),
    ("sz399905", "中证500"),
)

#: 指数位自动判定的前缀。``sh000*``（上证系列指数）与 ``sz399*``（深证/中证
#: 系列指数）走 ``bars(index=True)``，其余（含 ``sh900*`` B 股、``sz200*`` B 股）
#: 走普通个股路径。写死这张表比"看开头两位是 sh 就当指数"安全得多。
INDEX_PREFIXES: tuple[str, ...] = ("sh000", "sz399")

#: ``--scan`` 默认枚举的代码段（每段的后三位 000-999 逐个探测）。
SCAN_SEGMENTS: tuple[str, ...] = (
    "sh600",
    "sh601",
    "sh603",
    "sh605",
    "sh688",
    "sh689",
    "sz000",
    "sz001",
    "sz002",
    "sz003",
    "sz300",
    "sz301",
)

#: 北交所段要单独开口（代码段是 43/83/87/92，量小但不少账户用得上）。
BJ_SEGMENTS: tuple[str, ...] = ("bj430", "bj830", "bj870", "bj920")

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
    #: symbol -> 口径自检拦下的原因（与 failed 分开记，便于区分"取不到"和"数据坏"）
    rejected: dict[str, str] = field(default_factory=dict)

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
                    rejected=dict(raw.get("rejected") or {}),
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
            "rejected": self.rejected,
        }
        _atomic_write_text(path, json.dumps(payload, ensure_ascii=False, indent=2))


# ---------------------------------------------------------------------------
# universe：代码表从哪来
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Target:
    """一只要同步的标的：代码 + 是否走指数位 + 可选名字（只为打印好看）。"""

    symbol: str
    index: bool = False
    name: str = ""

    @classmethod
    def parse(cls, raw: str, *, force_index: bool | None = None) -> Target:
        """把一个代码串归一成 :class:`Target`。

        允许 ``sh600519`` / ``600519`` / ``SH600519`` 三种写法；后者按国内
        分段习惯补 ``sh``（6 开头）/ ``sz``。``sh000*``、``sz399*`` 自动判为
        指数位，除非调用方显式给定 ``force_index``。
        """
        token = raw.strip().lower()
        if token.startswith(("sh", "sz", "bj")) and token[2:].isdigit():
            symbol = token
        elif token.isdigit() and len(token) == 6:
            symbol = ("sh" if token.startswith("6") else "sz") + token
        else:
            symbol = token
        index = symbol.startswith(INDEX_PREFIXES) if force_index is None else force_index
        return cls(symbol=symbol, index=index)


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
        code = token if token.startswith(("sh", "sz", "bj")) and token[2:].isdigit() else ""
        if not code and len(cell) == 6 and cell.isdigit():
            code = ("sh" if cell[0] == "6" else "sz") + cell
        if not code or code in seen:
            continue
        seen.add(code)
        out.append(code)
    return sorted(out)


def iter_scan_codes(segments: Sequence[str]) -> Iterator[str]:
    """按代码段枚举候选代码（``sh600`` → ``sh600000`` ... ``sh600999``）。"""
    for segment in segments:
        prefix = segment.lower()
        for tail in range(1000):
            yield f"{prefix}{tail:03d}"


def scan_universe(
    client: TdxClient,
    segments: Sequence[str],
    workers: int,
    timeout: float,
) -> list[str]:
    """探测哪些代码主站真认：存在的回真实数据，不存在的回空首页。

    空首页（``bars`` 声明 0 条）不是"历史耗尽"——耗尽只表现为短页，首页即空
    意味着这个代码主站根本不认。所以拿"非空首页"当存在性判据，比拿 OHLC
    合理性去猜干净得多（后者会被 ``sh999999`` 这类回垃圾日期的代码带歪）。
    """
    warnings.filterwarnings("ignore")
    codes = list(iter_scan_codes(segments))
    found: list[str] = []
    lock = threading.Lock()
    done = 0
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = {
            pool.submit(client.bars, code, period="day", count=2, index=False): code
            for code in codes
        }
        for future in as_completed(futures):
            done += 1
            if done % 2000 == 0:
                print(f"  探测 {done}/{len(codes)}…，已确认 {len(found)}", flush=True)
            try:
                rows = future.result()
            except Exception:
                continue  # 传输层异常按"不存在"处理（下一轮还会再探）
            if rows and isinstance(rows[0], dict):
                with lock:
                    found.append(futures[future])
    return sorted(found)


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
#: 荒唐日期（实测 ``sh999999`` 给 ``0080-26-13``、另有一支给 ``8414-91-57``）。
#: 光靠 ``\d{4}-\d{2}-\d{2}`` 的正则筛不掉它们——``8414-91-57`` 恰好也长得像
#: 日期，于是会堂而皇之地变成排序键，把整个文件带歪。所以过了正则还要按真实
#: 日历构造一次，月份 91、日期 57 这类直接判为取不到。
_DATE_KEY = re.compile(r"(\d{4})-(\d{2})-(\d{2})")


def _date_key(row: dict[str, Any]) -> str:
    """从一行里取 ``YYYY-MM-DD`` 日期键；取不到就返回空串（该行不可排）。"""
    match = _DATE_KEY.search(str(row.get("date") or row.get("datetime") or ""))
    if not match:
        return ""
    year, month, day = (int(part) for part in match.groups())
    try:
        _date(year, month, day)
    except ValueError:
        return ""
    return match.group(0)


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


def _last_date_on_disk(path: Path) -> str:
    """从已落盘的 parquet 尾部取最后一根日期。

    断点的第一事实源应该是**磁盘上的数据本身**，``state.json`` 只是它的加速
    副本：state 被误删、或换了 ``--root`` 之后 state 没跟过来，瓶颈不该退化成
    "从头再拉一遍最近 N 根"。这个读法只取 ``date`` 一列，几千行也很轻。
    """
    if not path.exists():
        return ""
    try:
        import pyarrow.parquet as pq  # type: ignore[import-not-found]

        column = pq.read_table(path, columns=["date"])["date"].to_pylist()
    except Exception:
        return ""
    for row in reversed(column):
        key = _date_key(row if isinstance(row, dict) else {"date": str(row)})
        if key:
            return key
    return ""


def _resume_bar_count(
    state: State, symbol: str, full: bool, lookback: int, last_on_disk: str = ""
) -> int:
    """该支这次要多少根：续拉多要一段，全量就拉满窗口。

    ``state.last_date`` 优先，其次用磁盘上那支的最后日期——两条都取不到才按
    ``lookback`` 从头起量。
    """
    if full:
        return MAX_WINDOW
    last = state.last_date.get(symbol) or last_on_disk
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
    ) -> None:
        self.out = out
        self.state_path = state_path or (out / "state.json")
        self.state = State.load(self.state_path)
        self.lookback = lookback
        self.gap_sleep = gap_sleep
        self.jitter = jitter
        self.full = full
        self.dry_run = dry_run
        self.out.mkdir(parents=True, exist_ok=True)
        self._client = TdxClient(timeout=timeout, slots_per_host=slots_per_host)
        self._lock = threading.Lock()
        self.rejected: dict[str, str] = {}

    # -- 单只 ----------------------------------------------------------
    def sync_one(self, target: Target) -> Outcome:
        count = _resume_bar_count(
            self.state,
            target.symbol,
            self.full,
            self.lookback,
            _last_date_on_disk(self.out / f"{target.symbol}.parquet"),
        )
        try:
            bars = self._client.bars(target.symbol, period="day", count=count, index=target.index)
        except TdxError as exc:
            advice = advice_for(exc)
            if advice.retryable and "timeout" in str(exc).lower():
                time.sleep(min(advice.backoff or 0.5, 3.0))
                try:
                    bars = self._client.bars(
                        target.symbol, period="day", count=count, index=target.index
                    )
                except TdxError as exc2:
                    return Outcome(target.symbol, "SKIP", detail=f"{type(exc2).__name__}: {exc2}")
            else:
                return Outcome(target.symbol, "SKIP", detail=f"{type(exc).__name__}: {exc}")
        except Exception as exc:  # 传输层原异常（内核不替换 Provider）
            return Outcome(target.symbol, "SKIP", detail=f"{type(exc).__name__}: {exc}")

        if not bars:
            return Outcome(target.symbol, "EMPTY")

        # 和磁盘上已有的合并：TDX 的 bars 永远只给"最近 N 根"，不合并的话
        # 每轮都会把文件重写成 N 根，历史窗口随轮数往上漂；合并后收敛到一个
        # 稳定的长度（--full 跑出的长历史也保得住）。
        merged = _dedup_merge(bars)
        if not self.dry_run:
            merged = _dedup_merge([*_existing_rows(self.out / f"{target.symbol}.parquet"), *merged])
        # 自检必须在"合并 + 排好序之后"做：--full 拉回来的是倒序（最新在前），
        # 在原始批次上校验只会撞出假的「日期非升序」。
        problems = _validate(merged)
        if problems:
            with self._lock:
                self.rejected[target.symbol] = problems[0]
            return Outcome(target.symbol, "REJECT", detail=problems[0])
        if not merged:
            return Outcome(target.symbol, "EMPTY")
        day = _date_key(merged[-1])
        if not self.dry_run:
            _atomic_write_parquet(merged, self.out / f"{target.symbol}.parquet")
        with self._lock:
            self.state.last_date[target.symbol] = day
            self.state.failed.pop(target.symbol, None)
            self.state.rejected.pop(target.symbol, None)
        return Outcome(target.symbol, "OK", rows=len(merged), day=day)

    # -- 全量 ----------------------------------------------------------
    def run(self, targets: Sequence[Target], workers: int = 4) -> dict[str, Any]:
        tally: dict[str, Any] = {"OK": 0, "EMPTY": 0, "SKIP": 0, "REJECT": 0}
        bars_total, days_total = 0, 0
        started = time.time()
        with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
            futures = {pool.submit(self.sync_one, t): t for t in targets}
            for future in as_completed(futures):
                outcome = future.result()
                tally[outcome.status] = tally.get(outcome.status, 0) + 1
                if outcome.status == "OK":
                    bars_total += outcome.rows
                    days_total += 1
                elif outcome.status == "REJECT":
                    with self._lock:
                        self.state.rejected[outcome.symbol] = outcome.detail
                else:
                    with self._lock:
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


def resolve_universe(args: argparse.Namespace, root: Path) -> tuple[list[Target], str]:
    """按优先级定宇宙，返回 ``(目标列表, 来源说明)``。

    优先级：显式 ``--symbols`` > ``--universe-file`` > 本地 vipdoc > ``root``
    下缓存的 ``universe.csv`` > 内置默认宇宙。最后一级是"零参数可跑"的保证：
    前面几级都落空时（没装通达信、没准备清单）也得有东西可拉。
    """
    force_index = None
    if args.index is True:
        force_index = True
    elif args.index is False:
        force_index = False

    if args.symbols:
        targets = [Target.parse(s, force_index=force_index) for s in args.symbols]
        return targets, f"命令行 --symbols（{len(targets)} 只）"

    if args.universe_file:
        codes = symbols_from_csv(Path(args.universe_file))
        if codes:
            targets = [Target.parse(c, force_index=force_index) for c in codes]
            return targets, f"清单 {args.universe_file}（{len(targets)} 只）"

    for candidate in (args.vipdoc, Path("C:/new_tdx"), Path("D:/new_tdx")):
        if not candidate:
            continue
        found = symbols_from_vipdoc(Path(candidate))
        if found:
            targets = [Target.parse(c, force_index=force_index) for c in found]
            return targets, f"本地通达信目录 {candidate}（{len(targets)} 只）"

    cached = root / "universe.csv"
    if cached.exists():
        codes = symbols_from_csv(cached)
        if codes:
            targets = [Target.parse(c, force_index=force_index) for c in codes]
            return targets, f"缓存代码表 {cached}（{len(targets)} 只）"

    targets = [Target.parse(s) for s, _ in DEFAULT_UNIVERSE]
    return targets, f"内置默认宇宙（{len(targets)} 只）"


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="用 TDX 接口把 A 股日线同步到本地目录（零参数即可直接跑）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "默认落盘：\n"
            "  data/day/<代码>.parquet   每支日线（合并去重后的完整历史）\n"
            "  data/state.json           断点与失败/被拦记录\n"
            "  data/universe.csv         代码表（--scan 生成）\n"
        ),
    )
    p.add_argument("--root", type=Path, default=DEFAULT_ROOT, help="本地落盘根目录（默认 data/）")
    p.add_argument(
        "--state", type=Path, default=None, help="状态文件路径（默认 <root>/state.json）"
    )
    p.add_argument("--symbols", nargs="+", default=[], help="显式指定要同步的代码（跳过宇宙解析）")
    p.add_argument("--universe-file", type=Path, default=None, help="自备清单 CSV")
    p.add_argument("--vipdoc", type=Path, default=None, help="本地通达信目录（自动找 lday）")
    p.add_argument(
        "--index",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="强制/禁止走指数位（默认按 sh000*/sz399* 自动判定）",
    )
    p.add_argument("--full", action="store_true", help="全量重拉（默认按上次日期增量续拉）")
    p.add_argument("--lookback", type=int, default=320, help="每只最少拉多少根日线")
    p.add_argument("--limit", type=int, default=0, help="只同步前 N 只（0 表示全部）")
    p.add_argument("--workers", type=int, default=4, help="并发只数（实际瓶颈是 slots_per_host）")
    p.add_argument("--gap-sleep", type=float, default=0.2, help="每只之间的礼貌间隔秒数")
    p.add_argument("--dry-run", action="store_true", help="只拉取不落盘（验证连通性用）")
    p.add_argument("--timeout", type=float, default=10.0)
    p.add_argument(
        "--scan",
        action="store_true",
        help="只探测全市场代码表并写入 <root>/universe.csv（不落行情）",
    )
    p.add_argument(
        "--scan-segment",
        nargs="+",
        default=list(SCAN_SEGMENTS),
        metavar="SEG",
        help="--scan 要枚举的代码段（默认沪深全部主要段）",
    )
    p.add_argument("--scan-bj", action="store_true", help="--scan 时一并探测北交所段")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root: Path = args.root
    root.mkdir(parents=True, exist_ok=True)
    day_dir = root / "day"
    day_dir.mkdir(parents=True, exist_ok=True)

    if args.scan:
        segments = list(args.scan_segment)
        if args.scan_bj:
            segments = segments + list(BJ_SEGMENTS)
        print(f"探测 {len(segments)} 个代码段（共 {len(segments) * 1000} 个候选代码）…")
        # 探测是"纯探针"型流量，比拉行情轻得多，并发下限给到 8：段内 1000 个
        # 候选按 4 并发要跑十分钟往上，定时任务等不起。
        scan_workers = max(8, args.workers)
        client = TdxClient(timeout=args.timeout, slots_per_host=scan_workers)
        found = scan_universe(client, segments, scan_workers, args.timeout)
        out = root / "universe.csv"
        _atomic_write_text(out, "".join(f"{line}\n" for line in sorted(found)))
        print(f"确认存在 {len(found)} 只 → {out}")
        print(
            f"下一步：python scripts/sync_daily_history.py --root {root}（会读 {out} 做增量同步）"
        )
        return 0 if found else 2

    targets, source = resolve_universe(args, root)
    if not targets:
        print(
            "找不到 universe：给 --symbols 列代码、--universe-file 备清单、"
            "--vipdoc 指通达信目录，或先跑 --scan 探测代码表。",
            file=sys.stderr,
        )
        return 2
    if args.limit:
        targets = targets[: args.limit]
    kinds = sum(1 for t in targets if t.index)
    print(
        f"universe：{source}；root={root}；"
        f"模式={'全量' if args.full else '增量续拉'}；个股 {len(targets) - kinds} 只 / 指数 {kinds} 只"
    )

    syncer = Syncer(
        day_dir,
        args.state if args.state else (root / "state.json"),
        timeout=args.timeout,
        slots_per_host=max(1, args.workers),
        lookback=args.lookback,
        gap_sleep=args.gap_sleep,
        full=args.full,
        dry_run=args.dry_run,
    )
    tally = syncer.run(targets, workers=args.workers)
    print(
        "\n汇总："
        + json.dumps({k: v for k, v in tally.items() if k != "symbols"}, ensure_ascii=False)
    )
    if syncer.rejected:
        print(f"口径自检拦下 {len(syncer.rejected)} 只（未落盘）：")
        for sym, why in list(syncer.rejected.items())[:10]:
            print(f"  - {sym}: {why}")
    if not args.dry_run:
        print(
            f"落盘：{day_dir} 下 {len(list(day_dir.glob('*.parquet')))} 个 parquet"
            f"；状态：{syncer.state_path}"
        )
    # 全空失败视为环境级（网络/主站不可达），不是本脚本缺陷
    return 0 if tally.get("OK") else 2


if __name__ == "__main__":
    raise SystemExit(main())
