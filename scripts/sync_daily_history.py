#!/usr/bin/env python3
# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""用 TDX 接口把全市场日线同步到本地目录，**零参数即可直接跑**。::

    python scripts/sync_daily_history.py

上面这一行就是全部用法：默认把日线写进 ``data/day/<类别>/``、状态写
``data/state.json``、代码表写 ``data/universe.csv``，默认同步内置宇宙。
不是"示例，需自行改路径"——是拷过去就能每天盘后跑。

**标的类别**（不是"只同步 A 股股票"）：代码段决定类别，类别决定落盘目录、
决定 ``0x052D`` 走不走指数位。实测过每个段（见 :data:`CLASS_SEGMENTS`）：

=========  ==========================================  ============
类别        代码段                                      落盘目录
=========  ==========================================  ============
``index``  sh000* / sz399*                             ``index/``
``etf``    沪 51x/56x/58x、深 158x/159x                ``etf/``
``lof``    沪 501/502、深 160-165                      ``lof/``
``bond``   沪 110/111/113/118、深 121/123/127/131      ``bond/``
``bshare`` 沪 900*、深 200*（B 股，按个股拉）           ``bshare/``
``stock``  沪 600/601/603/605/688/689、深 000-003/300/301 ``stock/``
=========  ==========================================  ============

**全量 / 增量怎么选**：TDX 的 ``bars`` 只给"最近 N 根"，没有翻页游标，所以

* ``--full`` = **全历史**：按 ``MAX_WINDOW`` 拉（8000 根上限，服务端单页 800
  自动翻页），并把已有文件整体重写成长历史。铺底、换机器、校准口径用。
* 默认（增量）= **最近 lookback 根 + 与磁盘合并**：盘后每天只需拿新增那几根，
  拉回来的 320 根和本地已有的去重合并，历史不会随轮数漂。首次跑没有断点，
  就按 ``--lookback`` 铺底（默认 320 根 ≈ 1.5 年），想要完整历史跑一次 ``--full``。

设计要点（每一条都是被实战教训逼出来的，不是装饰）：

1. **启动即跑**：默认 root 锚定仓库根的 ``data/``，不依赖当前工作目录；
   universe 按 `本地 vipdoc → data/universe.csv → 内置默认宇宙` 三级兜底。
2. **类别从代码段推出来**：``sh000*``/``sz399*`` 判为指数并走 ``bars(index=True)``；
   走反了主站回的是 ``5616-57-83`` 这种荒唐日期，本地看极像网络抖动。
3. **断点续拉**：``start`` 是 16-bit 分页偏移不是日期游标，续拉只能靠
   "本地那支文件的最后一根日期 + 多要一段回来合并"。见 :func:`_resume_bar_count`。
4. **幂等落地**：先合并去重再整体重写到 ``.tmp`` 然后 ``os.replace`` 原子改名——
   写一半崩了不会留半个坏 parquet。
5. **口径自检**：OHLC 自洽、volume 单位归一到股、日期升序去重，任一条破就记进
   ``state.json`` 的 ``rejected`` 而不污染磁盘。
6. **代码表可自产**：``0x044D SECURITY_LIST`` 已登记 offline，代码表不能从 tdx
   拿回来。``--scan`` 走"枚举代码段 + 探测主站回不回真实数据"，把全市场代码表
   落成本地 ``universe.csv``——不存在的代码主站回空首页，存在的回真实数据，
   这个差别就是判定依据。再往里一步还要验日期键是真日历日期（见
   :func:`scan_universe`），否则"回垃圾数据但不空"的假代码会被认成存在。

用法::

    python scripts/sync_daily_history.py                      # 零参数：增量同步内置宇宙到 data/
    python scripts/sync_daily_history.py --full               # 全历史重拉（铺底用）
    python scripts/sync_daily_history.py --symbols sh600519 sz000001
    python scripts/sync_daily_history.py --universe-file data/universe.csv
    python scripts/sync_daily_history.py --root data --limit 200
    python scripts/sync_daily_history.py --list-class         # 看类别与段表
    python scripts/sync_daily_history.py --scan               # 探测全市场代码表
    python scripts/sync_daily_history.py --scan --exclude-class etf lof bond
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

#: **类别 → 该类别的代码段**（每段后三位 000-999 逐个探测）。这张表是实测的，
#: 不是照着代码规则推的：113 个候选段逐个抽 30 个尾号探测，46 个有货、67 个是
#: 空段（``sh606``-``sh620``、``sh500``/``sh505``、``sz150-157``、``sz370-373``
#: 等等全是空的，留着只会让 ``--scan`` 每次白跑几十分钟）。抽样有盲区——
#: B 股段 ``sh900``/``sz200`` 抽样 0 命中，但实测 ``sh900932``、``sz200011``
#: 都真能拉到日线——所以有货的段一律保留，只删抽样确定为空的段。
#:
#: 顺序有意义：匹配到第一个就停，所以更"特殊"的段放前面。
CLASS_SEGMENTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("index", ("sh000", "sz399")),
    (
        "etf",
        (
            "sh510",
            "sh511",
            "sh512",
            "sh513",
            "sh515",
            "sh516",
            "sh519",
            "sh520",
            "sh530",
            "sh560",
            "sh561",
            "sh562",
            "sh563",
            "sh588",
            "sh589",
            "sz158",
            "sz159",
        ),
    ),
    ("lof", ("sh501", "sh502", "sz160", "sz161", "sz163", "sz164", "sz165")),
    (
        "bond",
        ("sh110", "sh111", "sh113", "sh118", "sz121", "sz123", "sz127", "sz131"),
    ),
    ("bshare", ("sh900", "sz200")),
    (
        "stock",
        (
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
        ),
    ),
)

#: 默认扫哪些段 = 全类别。``--scan`` 用这张表，不必再单独维护一份。
DEFAULT_SCAN_CLASSES: tuple[str, ...] = tuple(kind for kind, _ in CLASS_SEGMENTS)

#: 协议层没打开的类别。北交所 ``bj*`` 实测直接被客户端拒
#: （``[E3040] market=2 尚未验证；当前只允许 SZ/SH``），扫它 1000 个候选
#: 一个都探不出来——与其让它假装是"空段"，不如显式记下来。
UNSUPPORTED_CLASSES: dict[str, str] = {
    "bse": "北交所 bj*：客户端实测 E3040（market=2 未验证，只允许 SZ/SH）",
}

#: 指数位自动判定的前缀。``sh000*``（上证系列指数）与 ``sz399*``（深证/中证
#: 系列指数）走 ``bars(index=True)``；B 股（``sh900*``/``sz200*``）、ETF、LOF、
#: 可转债都走普通个股路径。
INDEX_PREFIXES: tuple[str, ...] = tuple(CLASS_SEGMENTS[0][1])

#: 单只一次最多要多少根。协议给的分页上限是 0xFFFF，且 ``start + count > 0x10000``
#: 直接抛 ParseError；800 是服务端单页上限，超了客户端自动翻页。
MAX_WINDOW = 8000

#: 续拉时比"上次那根"多要的根数（多要一段，覆盖周末/停牌造成的错位）
RESUME_OVERLAP = 30

#: 首次铺底多少根（没有断点时）。320 根 ≈ 1.5 年日线，一天一根的话够用大半年；
#: 要完整历史就跑 ``--full``。
BASELINE_WINDOW = 320

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
# 类别：从代码段推出来，再决定目录与 index 位
# ---------------------------------------------------------------------------


def classify(symbol: str) -> str:
    """代码 → 类别。``sh600519`` → ``"stock"``、``sh510300`` → ``"etf"``。"""
    token = symbol.strip().lower()
    for kind, segments in CLASS_SEGMENTS:
        if any(token.startswith(segment) for segment in segments):
            return kind
    return "stock"


def classify_with_index(symbol: str) -> tuple[str, bool]:
    """代码 → ``(类别, 是否走指数位)``。``bars(index=...)`` 的取数参数由此而来。"""
    kind = classify(symbol)
    return kind, kind == "index"


def class_dir(kind: str) -> str:
    """类别 → 落盘子目录名（与类别同名，见模块 docstring 的那张表）。"""
    return kind


# ---------------------------------------------------------------------------
# universe：代码表从哪来
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Target:
    """一只要同步的标的：代码 + 类别 + 是否走指数位 + 可选名字。"""

    symbol: str
    index: bool = False
    kind: str = "stock"
    name: str = ""

    @classmethod
    def parse(
        cls, raw: str, *, force_index: bool | None = None, force_kind: str | None = None
    ) -> Target:
        """把一个代码串归一成 :class:`Target`。

        允许 ``sh600519`` / ``600519`` / ``SH600519`` 三种写法；后者按国内
        分段习惯补 ``sh``（6 开头）/ ``sz``。类别由代码段自动判定，``index``
        位跟着类别走，除非调用方显式给定 ``force_index``。
        """
        token = raw.strip().lower()
        if token.startswith(("sh", "sz", "bj")) and token[2:].isdigit():
            symbol = token
        elif token.isdigit() and len(token) == 6:
            symbol = ("sh" if token.startswith("6") else "sz") + token
        else:
            symbol = token
        kind = force_kind or classify(symbol)
        index = (kind == "index") if force_index is None else force_index
        return cls(symbol=symbol, index=index, kind=kind)


def symbols_from_vipdoc(vipdoc: Path, markets: Sequence[str] = ("sh", "sz")) -> list[str]:
    """本地通达信目录 ``vipdoc/<mkt>/lday/*.day``——文件名即带前缀的代码。"""
    out: list[str] = []
    for market in markets:
        folder = vipdoc / market / "lday"
        if not folder.is_dir():
            continue
        out.extend(path.stem for path in folder.glob(f"{market}*.day"))
    return sorted(out)


def symbols_from_csv(path: Path) -> list[Target]:
    """自备清单：每行一个代码，允许带/不带 sh/sz 前缀，允许带列名。

    第二列（若存在）是类别；只写一列时按 :func:`classify` 现判，所以 ``--scan``
    产出的两列表和手搓的一列表都能吃。
    """
    out: list[Target] = []
    seen: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        cells = [cell.strip() for cell in line.split(",")]
        cell = cells[0].lower()
        if not cell or cell in {"symbol", "code"}:
            continue
        code = cell if cell.startswith(("sh", "sz", "bj")) and cell[2:].isdigit() else ""
        if not code and len(cells[0]) == 6 and cells[0].isdigit():
            code = ("sh" if cells[0][0] == "6" else "sz") + cells[0]
        if not code or code in seen:
            continue
        seen.add(code)
        kind = cells[1].strip().lower() if len(cells) > 1 and cells[1].strip() else ""
        out.append(Target.parse(code, force_kind=kind or None))
    return sorted(out, key=lambda target: target.symbol)


def iter_scan_codes(segments: Sequence[str]) -> Iterator[str]:
    """按代码段枚举候选代码（``sh600`` → ``sh600000`` ... ``sh600999``）。"""
    for segment in segments:
        prefix = segment.lower()
        for tail in range(1000):
            yield f"{prefix}{tail:03d}"


def iter_scan_plan(
    segments_by_class: Sequence[tuple[str, Sequence[str]]],
) -> list[tuple[str, str, bool]]:
    """``--scan`` 的探测计划：``(代码, 类别, 是否走指数位)``。

    指数段必须传 ``index=True`` 去探：拿个股位探指数，主站回的是 ``5616-57-83``
    这类荒唐日期——**非空**，会被"非空即存在"的判据漏掉真指数，或反过来认进假代码。
    """
    plan: list[tuple[str, str, bool]] = []
    for kind, segments in segments_by_class:
        index_bit = kind == "index"
        for code in iter_scan_codes(segments):
            plan.append((code, kind, index_bit))
    return plan


def scan_universe(
    client: TdxClient,
    plan: Sequence[tuple[str, str, bool]],
    workers: int,
    timeout: float,
    known: Iterable[str] = (),
    attempts: int = 2,
) -> dict[str, str]:
    """探测哪些代码主站真认：存在的回真实数据，不存在的回空首页。

    两层判据，缺一不可：

    1. **非空首页**。空首页（``bars`` 声明 0 条）不是"历史耗尽"——耗尽只表现为
       短页，首页即空意味着这个代码主站根本不认。
    2. **日期键是真日历日期**。光靠"非空"不够：像 ``sh999999`` 这种主站会回
       ``0080-26-13``、另一支回 ``8414-91-57``，**非空且长得像日期**。这东西
       一旦进了代码表，后面每一轮同步都会为它消耗一次请求还注定失败。

    ``attempts`` 是传输层重试次数——探错了不会写进代码表，但会白白漏掉一只。
    """
    warnings.filterwarnings("ignore")
    pending = [item for item in plan if item[0] not in set(known)]
    found: dict[str, str] = {}
    lock = threading.Lock()
    done = 0

    def probe(code: str, index_bit: bool) -> list[dict[str, Any]]:
        last_exc: Exception | None = None
        for _ in range(attempts):
            try:
                return list(client.bars(code, period="day", count=1, index=index_bit))
            except Exception as exc:  # noqa: BLE001 - 传输层异常按"再试一次"处理
                last_exc = exc
                time.sleep(0.05 * attempts)
        raise RuntimeError(str(last_exc) or type(last_exc).__name__)

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = {
            pool.submit(probe, code, index_bit): (code, kind) for code, kind, index_bit in pending
        }
        for future in as_completed(futures):
            done += 1
            if done % 2000 == 0:
                print(f"  探测 {done}/{len(pending)}…，已确认 {len(found)}", flush=True)
            code, kind = futures[future]
            try:
                rows = future.result()
            except Exception:  # noqa: BLE001 - 重试耗尽仍按"不存在"处理
                continue
            if rows and isinstance(rows[0], dict) and _date_key(rows[0]):
                with lock:
                    found[code] = kind
    return found


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
    ``lookback`` 起量（那是"首次铺底"的窗口，不是每轮的窗口）。
    """
    if full:
        return MAX_WINDOW
    last = state.last_date.get(symbol) or last_on_disk
    if not last:
        return max(lookback, BASELINE_WINDOW)
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
        lookback: int = BASELINE_WINDOW,
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
        _ensure_class_dirs(self.out)
        self._client = TdxClient(timeout=timeout, slots_per_host=slots_per_host)
        self._lock = threading.Lock()
        self.rejected: dict[str, str] = {}

    # -- 单只 ----------------------------------------------------------
    def _path_of(self, target: Target) -> Path:
        """已落盘那支的路径：类别子目录优先，兼容未分类前写的扁平文件。"""
        nested = self.out / class_dir(target.kind) / f"{target.symbol}.parquet"
        if nested.exists():
            return nested
        flat = self.out / f"{target.symbol}.parquet"
        if flat.exists():
            return flat
        return nested

    def sync_one(self, target: Target) -> Outcome:
        path = self._path_of(target)
        count = _resume_bar_count(
            self.state, target.symbol, self.full, self.lookback, _last_date_on_disk(path)
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
            merged = _dedup_merge([*_existing_rows(path), *merged])
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
            try:
                _atomic_write_parquet(
                    merged, self.out / class_dir(target.kind) / f"{target.symbol}.parquet"
                )
            except OSError as exc:
                # 不更新断点：下一轮原样重试这一只。
                return Outcome(
                    target.symbol, "SKIP", detail=f"落盘失败 {type(exc).__name__}: {exc}"
                )
        with self._lock:
            self.state.last_date[target.symbol] = day
            self.state.failed.pop(target.symbol, None)
            self.state.rejected.pop(target.symbol, None)
        return Outcome(target.symbol, "OK", rows=len(merged), day=day)

    # -- 全量 ----------------------------------------------------------
    def run(self, targets: Sequence[Target], workers: int = 4) -> dict[str, Any]:
        tally: dict[str, Any] = {"OK": 0, "EMPTY": 0, "SKIP": 0, "REJECT": 0, "by_class": {}}
        bars_total, days_total, latest_day = 0, 0, ""
        started = time.time()
        with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
            futures = {pool.submit(self.sync_one, t): t for t in targets}
            for future in as_completed(futures):
                outcome = future.result()
                tally[outcome.status] = tally.get(outcome.status, 0) + 1
                if outcome.status == "OK":
                    bars_total += outcome.rows
                    days_total += 1
                    #: 数据停在哪天，是盘后第一眼要看的东西——拿"结果里的最大日期"
                    #: 报（不去调 ``datetime.now()``：那玩意儿一沾就是时区坑）。
                    if outcome.day and outcome.day > latest_day:
                        latest_day = outcome.day
                    kind = futures[future].kind
                    tally["by_class"][kind] = tally["by_class"].get(kind, 0) + 1
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
        tally["latest_day"] = latest_day
        return tally


def _atomic_write_parquet(rows: Sequence[dict[str, Any]], path: Path) -> None:
    """先落 ``.tmp`` 再原子改名；同时清掉可能存在的 csv 影子文件。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    os.close(fd)
    tmp_path = Path(tmp)
    try:
        # 复用仓库自己的 sink。原子改名的临时文件不以后缀收尾，
        # 所以必须显式 fmt="parquet"——让 write() 按扩展名推断恰好在这里会抛
        # ValueError（审计 §2-15 已经把那个静默语义拿掉了）。
        write(list(rows), str(tmp_path), fmt="parquet")
        _replace_with_retry(tmp_path, path)
    finally:
        if tmp_path.exists():
            tmp_path.unlink()
        # csv 影子必须在"无论成败"这条路上清：docstring 承诺过，但它原先挂在
        # ``_replace_with_retry`` 的那个死分支下，一次也没执行过。
        shadow = path.with_suffix(".csv")
        if shadow.exists():
            shadow.unlink()


def _replace_with_retry(tmp_path: Path, path: Path, attempts: int = 3) -> None:
    """覆盖同名文件时，Windows 上会间歇性 `PermissionError (WinError 5)`。

    实测全市场重跑最后一只就吃了这个：目标文件刚被上一轮写过，这一轮替换它
    时系统正锁着。重试两下基本都能过去——不是要掩盖问题，是**一只标的的落盘
    失败不该让整轮全市场同步崩掉**，重试是它该有的降级路径。

    只在 ``return``/``raise`` 两条路上收尾，末尾不再挂"清理 csv 影子"之类的
    收尾块：那类块在这条控制流里永远等不到（曾经写错过一次，静默死代码）。
    """
    for attempt in range(attempts):
        try:
            os.replace(tmp_path, path)
            return
        except OSError:
            if attempt == attempts - 1:
                raise
            time.sleep(0.2 * (attempt + 1))


def _all_targets(force_index: bool | None = None) -> list[Target]:
    return [Target.parse(symbol, force_index=force_index) for symbol, _ in DEFAULT_UNIVERSE]


def _select_classes(
    include: Sequence[str], exclude: Sequence[str]
) -> list[tuple[str, tuple[str, ...]]]:
    """按 ``--include-class``/``--exclude-class`` 裁 ``CLASS_SEGMENTS``。"""
    picked = [
        (kind, segments)
        for kind, segments in CLASS_SEGMENTS
        if (not include or kind in include) and kind not in exclude
    ]
    return picked


def print_class_table(picked: Sequence[tuple[str, tuple[str, ...]]]) -> None:
    """``--list-class``：把段表按类别摊开，附候选规模与落盘目录。"""
    print(f"{'类别':<8} {'段数':>4} {'候选代码':>8}  目录 / 段")
    for kind, segments in picked:
        print(
            f"{kind:<8} {len(segments):>4} {len(segments) * 1000:>8}  "
            f"day/{class_dir(kind)}/  {' '.join(segments)}"
        )
    total = sum(len(segments) for _, segments in picked)
    print(f"\n合计 {len(picked)} 个类别 / {total} 段 / {total * 1000} 个候选代码")

    for kind, reason in UNSUPPORTED_CLASSES.items():
        print(f"\n未纳入：{kind} —— {reason}")


def resolve_universe(
    args: argparse.Namespace, root: Path, picked: Sequence[tuple[str, tuple[str, ...]]]
) -> tuple[list[Target], str]:
    """按优先级定宇宙，返回 ``(目标列表, 来源说明)``。

    优先级：显式 ``--symbols`` > ``--universe-file`` > 本地 vipdoc > ``root``
    下缓存的 ``universe.csv`` > 内置默认宇宙。最后一级是"零参数可跑"的保证。
    """
    force_index = args.index

    if args.symbols:
        targets = [Target.parse(s, force_index=force_index) for s in args.symbols]
        return targets, f"命令行 --symbols（{len(targets)} 只）"

    if args.universe_file:
        codes = symbols_from_csv(Path(args.universe_file))
        if codes:
            return codes, f"清单 {args.universe_file}（{len(codes)} 只）"

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
            kept = [t for t in codes if t.kind in {kind for kind, _ in picked}]
            if kept:
                return kept, f"缓存代码表 {cached}（{len(kept)} 只）"

    # 内置宇宙也要过一遍类别裁剪——否则 `make sync --exclude-class stock`
    # 会 sync 出一堆 --symbols 之外根本没被选中的股票。
    allowed = {kind for kind, _ in picked} or {t.kind for t in _all_targets(force_index)}
    targets = [t for t in _all_targets(force_index) if t.kind in allowed]
    return targets, f"内置默认宇宙（{len(targets)} 只）"


def _dump_universe(root: Path, found: dict[str, str]) -> Path:
    path = root / "universe.csv"
    lines = [f"{symbol},{kind}" for symbol, kind in sorted(found.items())]
    _atomic_write_text(path, "".join(f"{line}\n" for line in lines))
    return path


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="用 TDX 接口把全市场日线同步到本地目录（零参数即可直接跑）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "默认落盘：\n"
            "  data/day/<类别>/<代码>.parquet   每支日线（合并去重后的完整历史）\n"
            "  data/state.json                  断点与失败/被拦记录\n"
            "  data/universe.csv                代码表（--scan 生成，两列 代码,类别）\n"
            "类别：stock / index / etf / lof / bond / bshare（见 --list-class）\n"
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
        help="强制/禁止走指数位（默认按类别自动判定）",
    )
    p.add_argument("--full", action="store_true", help="全历史重拉（铺底 / 校准口径用）")
    p.add_argument("--lookback", type=int, default=BASELINE_WINDOW, help="每只每轮拉多少根日线")
    p.add_argument("--limit", type=int, default=0, help="只同步前 N 只（0 表示全部）")
    p.add_argument("--workers", type=int, default=4, help="并发只数（实际瓶颈是 slots_per_host）")
    p.add_argument("--gap-sleep", type=float, default=0.2, help="每只之间的礼貌间隔秒数")
    p.add_argument("--dry-run", action="store_true", help="只拉取不落盘（验证连通性用）")
    p.add_argument("--timeout", type=float, default=10.0)
    p.add_argument(
        "--include-class",
        nargs="+",
        default=[],
        metavar="KIND",
        help="只同步这些类别（默认全类别）",
    )
    p.add_argument(
        "--exclude-class",
        nargs="+",
        default=[],
        metavar="KIND",
        help="不同步这些类别（例如 etf lof bond）",
    )
    p.add_argument(
        "--scan",
        action="store_true",
        help="只探测全市场代码表并写入 <root>/universe.csv（不落行情）",
    )
    p.add_argument(
        "--scan-class",
        nargs="+",
        default=list(DEFAULT_SCAN_CLASSES),
        metavar="KIND",
        help="--scan 要探测的类别（默认全类别）",
    )
    p.add_argument(
        "--scan-refresh",
        action="store_true",
        help="--scan 时连已知代码也重探一遍（默认跳过，已入库的不重复探）",
    )
    p.add_argument(
        "--list-class",
        action="store_true",
        help="打印类别 / 代码段 / 落盘目录 / 候选规模后退出",
    )
    return p


def _ensure_class_dirs(out: Path) -> None:
    """预建六个类别目录，让 ``data/day/<类别>/`` 这个结构第一次跑之前就在。

    为什么是这六个、而不是"边落盘边建"：断点续拉要读磁盘上已有的尾部日期，父目录
    晚于第一支标的出现就得多一次目录判断；更实际的是文档里写了 ``data/day/stock/``
    这种路径，目录提前存在才对得上写法。

    类别目录名**不参与**架构门的"运行期目录"推导（推导只认 ``mkdir`` + 字面量路径
    分量，这里是循环变量）——它们靠 ``DEFAULT_ROOT`` 声明被整棵 ``data/`` 树认下来。
    别把这两条豁免机制混着读。
    """
    for name in ("stock", "index", "etf", "lof", "bond", "bshare"):
        (out / name).mkdir(parents=True, exist_ok=True)


def _dir_size(path: Path) -> int:
    total = 0
    for file in path.rglob("*.parquet"):
        try:
            total += file.stat().st_size
        except OSError:
            continue
    return total


def _count_day_files(day_dir: Path) -> int:
    """只数类别子目录里的 parquet——扁平层的那份是迁移前的孤儿，不该重复计数。"""
    return sum(1 for child in day_dir.iterdir() if child.is_dir() for _ in child.rglob("*.parquet"))


def _effective_gap(requested: float, targets: int) -> float:
    """按规模折算礼貌间隔，别让"每只之间睡一下"变成全市场的瓶颈。

    全市场 5200 只 × `(gap-sleep 0.2 + jitter 0.1)` ≈ **21 分钟纯在 sleep**——
    比拉数据本身还久。200 只以内保持原值（小清单该慢慢来），规模上去了按比例
    折算并压到 0.01s 下限；真要更礼貌就自己显式给 `--gap-sleep`。
    """
    if targets <= 200:
        return requested
    return max(0.01, requested * 200 / targets)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root: Path = args.root
    root.mkdir(parents=True, exist_ok=True)
    day_dir = root / "day"

    picked = _select_classes(args.include_class, args.exclude_class)
    if args.list_class:
        print_class_table(picked)
        return 0

    if args.scan:
        segments_by_class = [
            (kind, segments) for kind, segments in picked if kind in set(args.scan_class)
        ]
        if not segments_by_class:
            print("没有可扫的类别（--include-class 与 --exclude-class 冲突）", file=sys.stderr)
            return 2
        plan = iter_scan_plan(segments_by_class)
        known: set[str] = set()
        if not args.scan_refresh and (root / "universe.csv").exists():
            known = {target.symbol for target in symbols_from_csv(root / "universe.csv")}
        todo = [item for item in plan if item[0] not in known]
        print(
            f"探测 {len(segments_by_class)} 个类别 / {len(plan)} 个候选代码"
            f"（其中未入库 {len(todo)} 个）"
        )
        # 探测是"纯探针"型流量，比拉行情轻得多，并发下限给到 8：段内 1000 个
        # 候选按 4 并发要跑十分钟往上，定时任务等不起。
        scan_workers = max(8, args.workers)
        client = TdxClient(timeout=args.timeout, slots_per_host=scan_workers)
        found = scan_universe(client, plan, scan_workers, args.timeout, known=known)
        found.update({symbol: kind for symbol, kind, _ in plan if symbol in known})
        out = _dump_universe(root, found)
        print(f"确认存在 {len(found)} 只 → {out}")
        print(
            f"下一步：python scripts/sync_daily_history.py --root {root}（会读 {out} 做增量同步）"
        )
        return 0 if found else 2

    targets, source = resolve_universe(args, root, picked)
    if not targets:
        print(
            "找不到 universe：给 --symbols 列代码、--universe-file 备清单、"
            "--vipdoc 指通达信目录，或先跑 --scan 探测代码表。",
            file=sys.stderr,
        )
        return 2
    if args.limit:
        targets = targets[: args.limit]
    by_kind: dict[str, int] = {}
    for target in targets:
        by_kind[target.kind] = by_kind.get(target.kind, 0) + 1
    kinds = sum(1 for t in targets if t.index)
    mode = "全历史" if args.full else f"最近 {args.lookback} 根 + 合并"
    breakdown = " / ".join(f"{k} {v}" for k, v in sorted(by_kind.items()))
    print(f"universe：{source}；root={root}；模式={mode}；{breakdown}；指数位 {kinds} 只")

    gap = _effective_gap(args.gap_sleep, len(targets))
    if gap != args.gap_sleep:
        print(f"礼貌间隔按规模折算：{args.gap_sleep}s → {gap:.3f}s（{len(targets)} 只）")
    syncer = Syncer(
        day_dir,
        args.state if args.state else (root / "state.json"),
        timeout=args.timeout,
        slots_per_host=max(1, args.workers),
        lookback=args.lookback,
        gap_sleep=gap,
        full=args.full,
        dry_run=args.dry_run,
    )
    tally = syncer.run(targets, workers=args.workers)
    if tally.get("latest_day"):
        print(f"全市场最新交易日：{tally['latest_day']}")
    print(
        "\n汇总："
        + json.dumps({k: v for k, v in tally.items() if k != "by_class"}, ensure_ascii=False)
    )
    if tally.get("by_class"):
        print("按类别：" + json.dumps(tally["by_class"], ensure_ascii=False))
    if syncer.rejected:
        print(f"口径自检拦下 {len(syncer.rejected)} 只（未落盘）：")
        for sym, why in list(syncer.rejected.items())[:10]:
            print(f"  - {sym}: {why}")
    if not args.dry_run:
        files = _count_day_files(day_dir)
        size = _dir_size(day_dir)
        print(
            f"落盘：{day_dir} 下 {files} 个 parquet（{size / 1024 / 1024:.1f} MiB）；"
            f"状态：{syncer.state_path}"
        )
    # 全空失败视为环境级（网络/主站不可达），不是本脚本缺陷
    return 0 if tally.get("OK") else 2


if __name__ == "__main__":
    raise SystemExit(main())
