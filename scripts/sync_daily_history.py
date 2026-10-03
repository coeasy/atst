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

* ``--full`` = **全历史**：按 ``MAX_WINDOW`` 拉（10000 根上限，服务端单页 800
  自动翻页），并把已有文件整体重写成长历史。铺底、换机器、校准口径用。
  （上限取自"最早上市日"的实测天花板 8602 根 / ``sh600601`` 1990-12-19，留足余量；
  详见 ``MAX_WINDOW`` 的注释。）
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
    python scripts/sync_daily_history.py --fetch-list         # 秒级拿全市场代码表（七类）
    python scripts/sync_daily_history.py --doctor             # 不联网体检：清单 vs 落盘
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
import contextlib
import json
import math
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
from atst.errors import TdxError  # noqa: E402
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
#:
#: 类别 → 可探测的代码段。**段表单一事实源在 ``atst.universe._classes``**：
#: 原先脚本自己抄一份，结果真实世界开出 ``sz302`` 段而这份没有，``--scan``
#: 永远扫不到那一段里的新股。现在段内容从 atst 侧取，脚本只决定探测顺序。
from atst.universe._classes import ASSET_CLASSES  # noqa: E402
from atst.universe._classes import class_of_directory as _class_of_directory  # noqa: E402
from atst.universe._classes import classify as _universe_classify  # noqa: E402
from atst.universe._classes import (  # noqa: E402
    classify_with_index as _universe_classify_with_index,
)

#: 首轮探测顺序（指数/ETF 那些段小、先探完有反馈；stock 段最大放最后）
_SCAN_ORDER: tuple[str, ...] = ("index", "etf", "lof", "bond", "bshare", "stock")

_SEGMENTS_BY_CLASS: dict[str, tuple[str, ...]] = {
    item.name: item.tdx_segments for item in ASSET_CLASSES
}

CLASS_SEGMENTS: tuple[tuple[str, tuple[str, ...]], ...] = tuple(
    (name, _SEGMENTS_BY_CLASS[name]) for name in _SCAN_ORDER
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
#:
#: 按**类别名**取段（``_SEGMENTS_BY_CLASS["index"]``），不写 ``CLASS_SEGMENTS[0]``：
#: 后者把"index 恰好排在扫描顺序第一个"当成事实，哪天为了先探大段而调
#: ``_SCAN_ORDER``，这里会静默换成别人的段。类别名是稳定的。
INDEX_PREFIXES: tuple[str, ...] = tuple(_SEGMENTS_BY_CLASS["index"])

#: 单只一次最多要多少根。协议给的分页上限是 0xFFFF，且 ``start + count > 0x10000``
#: 直接抛 ParseError；800 是服务端单页上限，超了客户端自动翻页。
#:
#: **为什么是 10000 而不是 8000（旧值会静默丢历史）**：日线全历史上限来自
#: "最早上市日"，不是拍脑袋。2026-09-30 线路实测：
#: ``sh600601`` 8602 根（1990-12-19，上交所首个交易日，已证最老）/
#: ``sz000001`` 8464 根（1991-04-03）。A 股日线每年约增 244 根，故今日真实天花板
#: ≈8600；留 5.7 年余量取 10000，可安心用到 2032 年前后。
#:
#: **超量请求不花钱**：换页遇到"短页"就停（历史耗尽），所以年轻标的（几千根）
#: 请求数与旧值**完全一样**，只有真正超过 8000 根的老股本才多打那几页——而这
#: 正是它们缺失的那段历史。控制在 ``0x10000`` 分页夹以下，不会触发地址越界。
MAX_WINDOW = 10000

#: 续拉时比"上次那根"多要的根数（多要一段，覆盖周末/停牌造成的错位）
RESUME_OVERLAP = 30

#: 首次铺底多少根（没有断点时）。320 根 ≈ 1.5 年日线，一天一根的话够用大半年；
#: 要完整历史就跑 ``--full``。
BASELINE_WINDOW = 320

#: 服务端单页返回多少根。``count`` 超过它客户端自动翻页，所以"这一轮要打多少
#: 次请求"是按页数算的，不是按根数——全市场 ``--full`` 的代价全藏在页数里。
PAGE_BARS = 800

#: 超过这个只数还上 ``--full``，就在开跑前把代价摊开讲一句。铺底通常没必要
#: 全历史：先按增量铺一层、再对缺历史的个别标的补，比一次莽 8 万次请求温和。
FULL_WIDE_TARGETS = 500

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
    """代码 → 类别。``sh600519`` → ``"stock"``、``sh510300`` → ``"etf"``。

    判定本身在 :func:`atst.universe.classify`（单一事实源），这里只做转发：
    脚本早先自己遍历 ``CLASS_SEGMENTS``，而那张表按 ``_SCAN_ORDER`` 建、里面
    **没有 bse**，于是 ``bj920000`` 一路落到 ``return "stock"``——同一个事实
    两处实现，``day/stock/`` 里混进北交所就是这么来的。
    """
    return _universe_classify(symbol)


def classify_with_index(symbol: str) -> tuple[str, bool]:
    """代码 → ``(类别, 是否走指数位)``。``bars(index=...)`` 的取数参数由此而来。"""
    return _universe_classify_with_index(symbol)


def class_dir(kind: str) -> str:
    """类别 → 落盘子目录名（与类别同名，见模块 docstring 的那张表）。"""
    return _class_of_directory(kind)


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

    with warnings.catch_warnings():
        # 探测走的是 TDX 协议 + pyarrow 解码，会刷一堆第三方内部告警；只在这一段收住，
        # 不再用全局 filterwarnings（那会把整个进程的告警等级都改掉，泄漏到别处）。
        warnings.simplefilter("ignore")
        with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
            futures = {
                pool.submit(probe, code, index_bit): (code, kind)
                for code, kind, index_bit in pending
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
            import pyarrow.parquet as pq
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
        import pyarrow.parquet as pq

        column = pq.read_table(path, columns=["date"])["date"].to_pylist()
    except Exception:
        return ""
    for row in reversed(column):
        key = _date_key(row if isinstance(row, dict) else {"date": str(row)})
        if key:
            return key
    return ""


def _count_rows(path: Path) -> int:
    """已落盘文件有多少行（读元数据，不把整张表拉进内存）。

    体检要区分"文件在"和"文件里有数据"——空 parquet 是最像成功的那种失败。
    读不出来（坏文件、没装 pyarrow）就返回 0，让它在体检里跟空文件同样显眼。
    """
    if not path.exists():
        return 0
    try:
        import pyarrow.parquet as pq

        return int(pq.read_metadata(path).num_rows)
    except Exception:  # noqa: BLE001 - 坏文件按"读不出内容"处理
        return 0


def _resume_bar_count(full: bool, lookback: int, last_on_disk: str) -> int:
    """该支这次要多少根：续拉多要一段，全量就拉满窗口。

    只信磁盘：``last_on_disk`` 有日期 → 续拉窗口；没有（文件缺失 / 0 行 /
    坏文件）→ 铺底窗口。``state.last_date`` **不参与**这个决策——它是磁盘的
    对照副本（供 ``--doctor`` 摊开比对），不是第二事实源。若让它兜底，文件被
    清成 0 行（体检里的"空文件"）而 state 还记着旧日期时，修复只拉
    ``lookback + RESUME_OVERLAP`` 的小窗口，历史窗口静默缩水。写盘失败时
    断点不更新，state 只会比磁盘旧，砍掉它不丢任何真证据。

    铺底窗口补的是"最近一段"，不是完整历史——要补全历史仍要 ``--full``。
    """
    if full:
        return MAX_WINDOW
    if not last_on_disk:
        return max(lookback, BASELINE_WINDOW)
    return lookback + RESUME_OVERLAP


def _bar_budget(n_targets: int, full: bool, lookback: int) -> tuple[int, int]:
    """这一轮要拿多少根、打多少次主站请求，返回 ``(根数, 请求数)``。

    请求数才是成本项：``bars`` 没有翻页游标，``count`` 超过 :data:`PAGE_BARS`
    客户端就自己翻页。28 只 ``--full`` 是 280 次请求（几秒）；全市场 ``--full``
    约 **8385 只（六类，``--scan`` 口径）/ 8733 只（含北交所，``--fetch-list`` 口径）**
    是 8 万+ 次请求——量级差三个数，光看"拉多少根"是感觉不出来的。
    """
    per_symbol = MAX_WINDOW if full else max(lookback, BASELINE_WINDOW)
    pages = math.ceil(per_symbol / PAGE_BARS)
    return n_targets * per_symbol, n_targets * pages


def _plan_lines(
    targets: Sequence[Target],
    source: str,
    root: Path,
    full: bool,
    lookback: int,
    universe_size: int | None = None,
) -> list[str]:
    """开跑前要打印的开场白：规模、预算、以及"你现在其实只跑了一点点"的提醒。

    把提示摊成几行而不是塞进一行摘要里，是因为它回答的正是最容易踩的那一个问题：
    用户传了 ``--full`` 就以为"全量"了，结果跑出来还是内置那 28 只样例。
    """
    by_kind: dict[str, int] = {}
    for target in targets:
        by_kind[target.kind] = by_kind.get(target.kind, 0) + 1
    kinds = sum(1 for t in targets if t.index)
    mode = "全历史" if full else f"最近 {lookback} 根 + 合并"
    breakdown = " / ".join(f"{k} {v}" for k, v in sorted(by_kind.items()))
    bars, requests = _bar_budget(len(targets), full, lookback)
    lines = [
        f"universe：{source}；root={root}；模式={mode}；{breakdown}；指数位 {kinds} 只",
        f"预算：{bars} 根 ≈ {requests} 次主站请求（单页 {PAGE_BARS} 根，"
        f"每只 {math.ceil((MAX_WINDOW if full else max(lookback, BASELINE_WINDOW)) / PAGE_BARS)} 页）",
    ]
    where = root.as_posix()
    #: 提示里的只数要用**宇宙原始规模**：``--limit N`` 是在这一步之后才截的，
    #: 拿截断后的数字说"内置样例宇宙（3 只）"等于把用户骗进同一个坑。
    size = len(targets) if universe_size is None else universe_size
    if source.startswith("内置默认宇宙"):
        lines += [
            "",
            f"注意：这一轮是内置样例宇宙（{size} 只），不是全市场——"
            "``--full`` 只管每只拉多深，不管拉多少只。全市场要先落一张代码表：",
            "  # 1) 探测全市场代码表（首轮约 18 分钟）→ <root>/universe.csv",
            f"  python scripts/sync_daily_history.py --root {where} --scan",
            "  # 2) 用它增量同步（之后每轮只要几秒）",
            f"  python scripts/sync_daily_history.py --root {where}",
        ]
    elif full and len(targets) > FULL_WIDE_TARGETS:
        lines += [
            "",
            f"提醒：{len(targets)} 只 × 全历史 ≈ {requests} 次主站请求，"
            "铺底通常不必一上来就 ``--full``；先按默认增量铺一层，"
            "再对缺历史的标的单独补，比一次莽这么多次更不容易撞主站的软限流。",
        ]
    return lines


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
        count = _resume_bar_count(self.full, self.lookback, _last_date_on_disk(path))
        # 重试完全交给 ``TdxClient``（底层 ConnectionPool 按 RetryAdvice 自动重试 +
        # 换主站 + 退避，max_retries 默认 3）。脚本层早年那套"只对 timeout 重试一次"
        # 既和连接池重复，又一遇非超时错误就直接 SKIP，是不一致的逻辑；砍掉它，
        # 这里只做"取不到就记 SKIP"这一件事，断链清晰、没有孤儿分支。
        try:
            bars = self._client.bars(target.symbol, period="day", count=count, index=target.index)
        except TdxError as exc:
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
            nested = self.out / class_dir(target.kind) / f"{target.symbol}.parquet"
            try:
                _atomic_write_parquet(merged, nested)
            except OSError as exc:
                # 不更新断点：下一轮原样重试这一只。
                return Outcome(
                    target.symbol, "SKIP", detail=f"落盘失败 {type(exc).__name__}: {exc}"
                )
            if path != nested and path.exists():
                # 读旧扁平文件、写类别目录是一次迁移：数据已经合并进 nested，
                # 留在原地的只是幽灵副本（``_path_of`` nested 优先，它永远不再
                # 被读）——迁移到此收尾。删不掉也只是外观问题，不碍同步。
                with contextlib.suppress(OSError):
                    path.unlink()
        with self._lock:
            self.state.last_date[target.symbol] = day
            self.state.failed.pop(target.symbol, None)
            self.state.rejected.pop(target.symbol, None)
        return Outcome(target.symbol, "OK", rows=len(merged), day=day)

    # -- 全量 ----------------------------------------------------------
    def run(
        self, targets: Sequence[Target], workers: int = 4, verbose: bool = False
    ) -> dict[str, Any]:
        """把 ``targets`` 并发同步完，返回统计。

        观测输出做了深度优化（之前每只都 ``print + flush`` 再 ``sleep`` 一次，
        全市场 8733 只就是 8733 行刷屏 + 几十秒空转）：

        * **进度行节流**：默认只在终端上用回车覆盖刷一行（``OK n / SKIP m …``），
          非终端（cron 重定向到日志）则按阈值打平铺行，不污染日志；
        * **明细只在 ``--verbose`` 时逐只打印**，让诊断信息不再淹没在刷屏里；
        * **问题清单在结尾统一汇总**（SKIP / REJECT 预览），该修的一目了然；
        * **礼貌间隔移到提交侧**：``--gap-sleep`` 夹在每次 ``submit`` 之间才有意义，
          收集侧 sleep 只是空转——既保留"小清单慢慢来"、又不再拖全市场后腿；
        * **收尾关掉连接池**（``finally``），不留孤儿连接 / 心跳线程。
        """
        tally: dict[str, Any] = {"OK": 0, "EMPTY": 0, "SKIP": 0, "REJECT": 0, "by_class": {}}
        bars_total, days_total, latest_day = 0, 0, ""
        started = time.time()
        problems: list[tuple[str, str, str]] = []
        total = len(targets)
        progress_step = max(1, total // 50)
        last_tick = time.monotonic()
        completed = 0
        try:
            with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
                futures: dict[Any, Target] = {}
                for target in targets:
                    futures[pool.submit(self.sync_one, target)] = target
                    # 礼貌间隔只在大到"真能限速"时才夹在提交之间。``self.gap_sleep`` 已是
                    # ``main()`` 里按规模折算过的值（见 :func:`_effective_gap`：全市场被
                    # 压到 0.01s 下限），所以默认全市场跑这里根本不进、毫无限速收益——
                    # 真限速在连接池的 ``slots_per_host`` / ``SessionRateLimiter`` 那一层
                    # （见 :mod:`atst.transport.pool`）。小清单或用户显式给大间隔时，
                    # 这里才真正起作用。
                    if self.gap_sleep >= 0.05:
                        # 提交侧礼貌间隔 + 抖动：只在"大得真能限速"时用（``main()`` 已把
                        # 全市场折算到 0.01，这一支默认不进），避免平白空转。抖动打散提交
                        # 节奏，避免小清单下多线程同时撞主站（真实限速仍在连接池那一层）。
                        time.sleep(self.gap_sleep + random.uniform(0, self.jitter))
                for future in as_completed(futures):
                    outcome = future.result()
                    target = futures[future]
                    completed += 1
                    tally[outcome.status] = tally.get(outcome.status, 0) + 1
                    if outcome.status == "OK":
                        bars_total += outcome.rows
                        days_total += 1
                        #: 数据停在哪天，是盘后第一眼要看的东西——拿"结果里的最大日期"
                        #: 报（不去调 ``datetime.now()``：那玩意儿一沾就是时区坑）。
                        if outcome.day and outcome.day > latest_day:
                            latest_day = outcome.day
                        tally["by_class"][target.kind] = tally["by_class"].get(target.kind, 0) + 1
                    else:
                        problems.append((outcome.status, outcome.symbol, outcome.detail))
                        if outcome.status == "REJECT":
                            with self._lock:
                                self.state.rejected[outcome.symbol] = outcome.detail
                        elif target.kind not in UNSUPPORTED_CLASSES:
                            # 协议层不支持（北交所 bj*，E3040）不是真失败——不写进
                            # state.failed，否则它们会永远累积、还污染 doctor 的 failed 读数。
                            with self._lock:
                                self.state.failed[outcome.symbol] = outcome.detail or outcome.status
                    if verbose:
                        print(
                            f"[{outcome.status:<6}] {outcome.symbol:<10} "
                            f"{outcome.rows:>5} 行  末日 {outcome.day or '-'}  {outcome.detail[:60]}",
                            flush=True,
                        )
                    else:
                        now = time.monotonic()
                        if completed % progress_step == 0 or now - last_tick >= 2.0:
                            last_tick = now
                            _print_progress(completed, total, tally, final=False)
                # 当轮二次重投：首轮 SKIP 多半是主站单节点瞬时抖动 / 换主站延迟，
                # 立刻再投一次能压低单次运行的 SKIP 率，不必等下一轮 cron。
                # REJECT（数据坏）与 EMPTY（主站确无数据）不重投——前者必败、后者无需；
                # 协议层不支持的 bj* 也不重投（重投必 SKIP，纯浪费请求）。
                skip_syms = {s for st, s, _ in problems if st == "SKIP"}
                re_targets = [
                    t
                    for t in targets
                    if t.symbol in skip_syms and t.kind not in UNSUPPORTED_CLASSES
                ]
                if re_targets:
                    # 先撤掉首轮的 SKIP 记账，再按二次结果重新记：避免同一只在
                    # 结尾汇总里重复出现，且让计数回到真实终态。
                    tally["SKIP"] = max(0, tally.get("SKIP", 0) - len(re_targets))
                    problems = [p for p in problems if p[0] != "SKIP"]
                    retry_futures = {pool.submit(self.sync_one, t): t for t in re_targets}
                    for future in as_completed(retry_futures):
                        outcome = future.result()
                        target = retry_futures[future]
                        completed += 1
                        tally[outcome.status] = tally.get(outcome.status, 0) + 1
                        if outcome.status == "OK":
                            bars_total += outcome.rows
                            days_total += 1
                            if outcome.day and outcome.day > latest_day:
                                latest_day = outcome.day
                            tally["by_class"][target.kind] = (
                                tally["by_class"].get(target.kind, 0) + 1
                            )
                        else:
                            problems.append((outcome.status, outcome.symbol, outcome.detail))
                            if outcome.status == "REJECT":
                                with self._lock:
                                    self.state.rejected[outcome.symbol] = outcome.detail
                            elif target.kind not in UNSUPPORTED_CLASSES:
                                with self._lock:
                                    self.state.failed[outcome.symbol] = (
                                        outcome.detail or outcome.status
                                    )
                        if verbose:
                            print(
                                f"[R][{outcome.status:<6}] {outcome.symbol:<10} "
                                f"{outcome.rows:>5} 行  末日 {outcome.day or '-'}  {outcome.detail[:60]}",
                                flush=True,
                            )
        finally:
            # 收尾关连接池：否则池里的连接与后台心跳线程会一直挂着（即便 daemon
            # 不会阻塞退出，也是该释放的孤儿资源）。测试里 client 是桩，未必有 close。
            closer = getattr(self._client, "close", None)
            if closer is not None:
                closer()
        if not verbose:
            _print_progress(completed, total, tally, final=True)
        if not self.dry_run:
            self.state.last_run = time.strftime("%Y-%m-%d %H:%M:%S")
            self.state.symbols = days_total
            self.state.bars = bars_total
            self.state.dump(self.state_path)
        tally["elapsed_s"] = round(time.time() - started, 1)
        tally["latest_day"] = latest_day
        self._problems = problems
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


def _default_vipdoc_candidates() -> tuple[Path, ...]:
    """跨平台的通达信目录候选（可由 ``--vipdoc`` 显式覆盖）。

    旧实现硬编码 ``C:/new_tdx`` / ``D:/new_tdx``，在非 Windows 上会生成一个
    毫无意义的相对路径对象（``Path("C:/new_tdx")`` 在 POSIX 下被当普通相对路径）。
    现在按 ``os.name`` 给候选：Windows 仍是那两块盘，非 Windows 走 Wine / 常见
    挂载点约定。拿不到（绝大多数 CI / 服务器）就安静跳过，不影响三级兜底。
    """
    if os.name == "nt":
        return (Path("C:/new_tdx"), Path("D:/new_tdx"))
    return (
        Path.home() / "new_tdx",
        Path("/opt/new_tdx"),
        Path.home() / ".wine" / "drive_c" / "new_tdx",
    )


def resolve_universe(
    args: argparse.Namespace, root: Path, picked: Sequence[tuple[str, tuple[str, ...]]]
) -> tuple[list[Target], str]:
    """按优先级定宇宙，返回 ``(目标列表, 来源说明)``。

    优先级：显式 ``--symbols`` > ``--universe-file`` > 本地 vipdoc > ``root``
    下落的 ``universe.csv``（代码表）> 内置默认宇宙。最后一级是"零参数可跑"的保证。
    """
    force_index = args.index

    if args.symbols:
        targets = [Target.parse(s, force_index=force_index) for s in args.symbols]
        return targets, f"命令行 --symbols（{len(targets)} 只）"

    if args.universe_file:
        codes = symbols_from_csv(Path(args.universe_file))
        if codes:
            return codes, f"清单 {args.universe_file}（{len(codes)} 只）"

    for candidate in (args.vipdoc, *_default_vipdoc_candidates()):
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
                return kept, f"代码表 {cached}（{len(kept)} 只）"

    # 内置宇宙也要过一遍类别裁剪——否则 `make sync --exclude-class stock`
    # 会 sync 出一堆 --symbols 之外根本没被选中的股票。
    allowed = {kind for kind, _ in picked} or {t.kind for t in _all_targets(force_index)}
    targets = [t for t in _all_targets(force_index) if t.kind in allowed]
    return targets, f"内置默认宇宙（{len(targets)} 只）"


def _dump_universe(root: Path, found: dict[str, str]) -> Path:
    """``--scan`` 的产出。

    走**与 ``--fetch-list`` 同一个写手**（``write_universe_csv``）：两种拿表方式
    落出来的文件形状必须一样（三列 + 表头），否则"下游只认一种形状"的分支迟早
    冒出来。段表探测拿不到名称，第三列空着——空名称是允许的（见
    :class:`Security` 的 docstring），不是"写漏了"。
    """
    path = root / "universe.csv"
    write_universe_csv([(symbol, kind, "") for symbol, kind in sorted(found.items())], path)
    return path


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="用 TDX 接口把全市场日线同步到本地目录（零参数即可直接跑）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "默认落盘：\n"
            "  data/day/<类别>/<代码>.parquet   每支日线（合并去重后的完整历史）\n"
            "  data/state.json                  断点与失败/被拦记录\n"
            "  data/universe.csv                代码表（--fetch-list / --scan 生成，三列 代码,类别,名称）\n"
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
    p.add_argument(
        "--verbose",
        action="store_true",
        help="逐只打印每只的同步结果（默认只在结尾汇总 SKIP/REJECT，并用单行滚动进度）",
    )
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
        "--fetch-list",
        action="store_true",
        help="只拉全市场代码表并写入 <root>/universe.csv（不落行情）——走 atst.universe 的"
        "现成清单端点，秒级；拿不到再降回段表探测",
    )
    p.add_argument(
        "--fetch-class",
        default="all",
        metavar="KIND[,KIND...]",
        help="--fetch-list 要取的类别（默认 all）",
    )
    p.add_argument(
        "--fetch-source",
        default="auto",
        metavar="auto|table|sina|tdx",
        help="--fetch-list 的取数源（默认 auto：代码表 → 新浪节点 → 段表探测）",
    )
    p.add_argument(
        "--fetch-dry-run",
        action="store_true",
        help="--fetch-list 只看各源代价，不真拉",
    )
    p.add_argument(
        "--scan",
        action="store_true",
        help="只探测全市场代码表并写入 <root>/universe.csv（不落行情）——走段表逐只探，"
        "首轮约 18 分钟；要快就用 --fetch-list",
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
        "--doctor",
        action="store_true",
        help="不联网体检：代码表 vs day/ 的缺口 / 空文件 / 落伍 / 断点漂移（有缺口退出码 2）",
    )
    p.add_argument(
        "--list-class",
        action="store_true",
        help="打印类别 / 代码段 / 落盘目录 / 候选规模后退出",
    )
    p.add_argument(
        "--json",
        action="store_true",
        help="只影响 --doctor 的输出格式（机器可读）",
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


def _print_progress(done: int, total: int, tally: dict[str, Any], *, final: bool = False) -> None:
    """节流进度行：终端用回车覆盖，非终端（cron 日志）打平铺行。

    全市场 8733 只若逐只 ``print`` 是 8733 行刷屏 + 几十秒空转——改成一行滚动。
    非终端（``>> data/sync.log``）不写回车，否则日志里全是重叠的控制字符。
    """
    ok = tally.get("OK", 0)
    empty = tally.get("EMPTY", 0)
    skip = tally.get("SKIP", 0)
    reject = tally.get("REJECT", 0)
    line = f"进度 {done}/{total}  OK {ok}  空 {empty}  SKIP {skip}  REJECT {reject}"
    if final:
        print(line, flush=True)
        return
    if sys.stdout.isatty():
        print("\r" + line, end="", flush=True)
    elif done % max(1, total // 10) == 0 or done == total:
        print(line, flush=True)


#: 代码表列头（两列也能读——名称是可选的第三列，段表探测出来的标的没名字）
UNIVERSE_HEADER = "代码,类别,名称"


def write_universe_csv(rows: Sequence[tuple[str, str, str]], path: Path) -> None:
    """写代码表（``代码,类别,名称``）。写满就换，不攒在内存里拖尾。"""
    body = "".join(f"{code},{kind},{name}\n" for code, kind, name in rows)
    _atomic_write_text(path, f"{UNIVERSE_HEADER}\n{body}")


def run_fetch_list(args: Any, root: Path) -> int:
    """``--fetch-list``：用 :mod:`atst.universe` 拉代码表，秒级落 ``<root>/universe.csv``。

    与 ``--scan`` 的区别不是"快一点"：

    * ``--scan`` 是**自己枚举段 + 逐只 ``bars`` 探**——北交所被协议层拒
      （E3040）、ETF/LOF/可转债又没有权威清单接口，只能拿"非空 + 日期键是真
      日历日期"两道判据去筛，所以首轮 18 分钟起步；
    * ``--fetch-list`` 是**让 atst 按类别去问现成的清单端点**（新浪节点最优先，
      秒级），拿不到再降回段表探测。北交所也顺带有了——新浪 ``hs_a`` 里带
      ``bj*``。

    所以推荐顺序是：``--fetch-list`` 打底 → 日常增量同步；``--scan`` 留给
    ``--fetch-list`` 漏掉的东西（例如只在新浪侧没有、只有 tdx 才认的代码）。
    """
    from atst.universe import ASSET_CLASSES, universe_report

    wanted = set(args.fetch_class.split(","))
    # 候选取 atst 侧的全部类别（含 ``bse``），再由 --fetch-class 过滤：
    # 这里刻意**不**拿脚本的探测顺序表（``picked``）当候选——那个表没有 bse
    # （客户端 E3040 探不出来），但新浪 ``hs_a`` 里有 ``bj*``，清单端点比探测
    # 拿到的更全；用 atst 的表当唯一候选面，北交所才不会被静默漏掉。
    # ``all`` 是"全部类别"的哨兵，不是一只叫 all 的标的。
    selected = [item.name for item in ASSET_CLASSES]
    kinds = [name for name in selected if "all" in wanted or name in wanted]
    if not kinds:
        print(
            "没有可取的代码类别（--include-class / --exclude-class 与 --fetch-class 冲突）",
            file=sys.stderr,
        )
        return 2

    if args.fetch_dry_run:
        for row in universe_report(
            tuple(kinds),
            source=args.fetch_source,
            root=root,
            workers=args.workers,
            probe=False,  # 纯估算：不真跑 tdx 探测（那正是 dry-run 要避开的代价）
        ):
            estimate = row.get("estimate")
            if row.get("error"):
                tail = row["error"]  # 例如"ETF 没有新浪入口"：得直说，别只丢个 0
            elif estimate:
                tail = f"约 {estimate} 候选待探测"
            else:
                tail = f"{row['seconds']}s"
            print(
                f"  {row['kind']:7s} 源={row['source']:6s} 条数={row['count'] or '未知'}（{tail}）"
                f"｜新浪节点={row['sina_node']}｜段={row['segments']}"
            )
        return 0

    from atst.universe import list_universe

    collected: list[tuple[str, str, str]] = []
    for index, kind in enumerate(kinds):
        if index:
            print()
        try:
            rows = list_universe(
                kind,
                source=args.fetch_source,
                root=root,
                workers=args.workers,
                timeout=args.timeout,
            )
        except LookupError as exc:
            # 显式源拿不到某一类（例如只读代码表却要 ETF）：告一句，别让整张代码表
            # 废在最后一类上——前面已经拿到的类别照样落盘。
            print(f"{kind:7s} ! {exc}")
            continue
        collected += [(row.code, row.kind, row.name) for row in rows]
        print(f"{kind:7s} ← {len(rows)} 条")
    path = root / "universe.csv"
    write_universe_csv(collected, path)
    print(f"\n已写入 {path.as_posix()}（{len(collected)} 条）")
    print("接下来自用同步读这张表：直接跑不带参数的同步命令即可。")
    return 0


def run_doctor(args: Any, root: Path) -> int:
    """``--doctor``：不联网，只回答"落的盘和代码表对不对得上"。

    这是同步链路的**验收读数**，也是"哪些逻辑断了"的第一现场：

    * **缺口**：代码表里有、``day/<类别>/`` 里没有对应 parquet 的标的——同步
      从来没成功过，或者被 ``--include-class`` 裁掉了却忘了；
    * **空文件**：parquet 在但 0 行——最像"成功了"的失败；
    * **落伍**：最后日期落后于全市场最新交易日——单只停牌是正常，成片的落伍
      说明某一类根本没被同步；
    * **断点漂移**：``state.json`` 记的最后日期与磁盘实际不符——换过 ``--root``、
      或磁盘被手工改过。脚本自己的断点第一事实源是磁盘，这里把两者摊开对比。

    退出码：有缺口 / 空文件 → ``2``；只有落伍或断点漂移 → ``0``（它们是"要知道"
    而不是"要修"）。不联网，所以它在断网、盘前、无主站时都能跑。
    """
    table = root / "universe.csv"
    if table.exists():
        targets = symbols_from_csv(table)
        source = f"代码表 {table}（{len(targets)} 只）"
    else:
        targets = _all_targets(args.index)
        source = f"内置默认宇宙（{len(targets)} 只，没有 {table}）"
    if not targets:
        # 表在、却一只都读不出来（空文件 / 只有表头 / 列名不对）：这不是"体检通过"，
        # 是"这次体检什么都没检"。报 2 而不是静默给一个全 0 的报告。
        print(f"代码表 {table} 里一只标的都没读出来——先跑 --fetch-list 或 --scan 生成它")
        return 2

    day_dir = root / "day"
    state = State.load(args.state if args.state else (root / "state.json"))

    rows: list[dict[str, Any]] = []
    latest = ""
    for target in targets:
        path = day_dir / class_dir(target.kind) / f"{target.symbol}.parquet"
        flat = day_dir / f"{target.symbol}.parquet"
        actual = path if path.exists() else (flat if flat.exists() else path)
        last = _last_date_on_disk(actual) if actual.exists() else ""
        bars = _count_rows(actual) if actual.exists() else 0
        rows.append(
            {
                "symbol": target.symbol,
                "kind": target.kind,
                "file": actual.exists(),
                "rows": bars,
                "last_date": last,
                "state_date": state.last_date.get(target.symbol, ""),
            }
        )
        if last > latest:
            latest = last

    missing = [r for r in rows if not r["file"]]
    # 协议层不支持的类别（北交所 bj*）主站根本取不到——它们的"缺文件"是预期内，
    # 不能算进缺口：否则 ``--fetch-list`` 带来的 bj 会让 doctor 假报几百只缺口。
    real_missing = [r for r in missing if r["kind"] not in UNSUPPORTED_CLASSES]
    unsupported = [r for r in missing if r["kind"] in UNSUPPORTED_CLASSES]
    empty = [r for r in rows if r["file"] and not r["rows"]]
    stale = [r for r in rows if r["last_date"] and latest and r["last_date"] < latest]
    drift = [
        r for r in rows if r["state_date"] and r["last_date"] and r["state_date"] != r["last_date"]
    ]
    by_kind: dict[str, int] = {}
    for row in rows:
        if row["file"]:
            by_kind[row["kind"]] = by_kind.get(row["kind"], 0) + 1

    report: dict[str, Any] = {
        "root": root.as_posix(),
        "universe": source,
        "expected": len(targets),
        "on_disk": len(targets) - len(missing),
        "by_kind": by_kind,
        "latest_day": latest,
        "missing": [r["symbol"] for r in real_missing],
        "unsupported": [r["symbol"] for r in unsupported],
        "empty": [r["symbol"] for r in empty],
        "stale": [r["symbol"] for r in stale],
        "state_drift": [r["symbol"] for r in drift],
        "rejected": dict(state.rejected),
        "failed": dict(state.failed),
    }

    if getattr(args, "json", False):
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"体检 {root.as_posix()}")
        print(f"  清单：{source}")
        print(
            f"  落盘：{report['on_disk']}/{report['expected']}（"
            + (" / ".join(f"{k} {v}" for k, v in sorted(by_kind.items())) or "空")
            + "）"
        )
        print(f"  最新交易日：{latest or '（没有任何一根日线）'}")
        for label, key in (("缺口", "missing"), ("空文件", "empty"), ("落伍", "stale")):
            items = report[key]
            print(f"  {label}：{len(items)}" + (f"  例：{' '.join(items[:8])}" if items else ""))
        if report["unsupported"]:
            print(
                f"  协议不支持：{len(report['unsupported'])}"
                f"  例：{' '.join(report['unsupported'][:8])}"
                "（bj*，TDX E3040 取不到，非缺口，不计入退出码）"
            )
        if report["state_drift"]:
            print(
                f"  断点与磁盘不符：{len(report['state_drift'])}"
                f"  例：{' '.join(report['state_drift'][:8])}"
            )
        if state.rejected:
            print(f"  口径自检拦下：{len(state.rejected)}（见 state.json 的 rejected）")
        if real_missing:
            print("  下一步：python scripts/sync_daily_history.py --root " + root.as_posix())
            print("          （缺口会按代码表逐只重试；只补某一类加 --include-class）")
        if empty:
            # 空文件的修复走同一条增量命令：断点以磁盘为准，0 行会让这一只从
            # 铺底窗口重拉；但增量窗口不等于完整历史，要补回去得 --full。
            print("  下一步（空文件）：先重跑上面的增量命令；要补回完整历史加 --full")

    return 2 if (real_missing or empty) else 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root: Path = args.root
    root.mkdir(parents=True, exist_ok=True)
    day_dir = root / "day"

    picked = _select_classes(args.include_class, args.exclude_class)
    if args.list_class:
        print_class_table(picked)
        return 0

    if args.doctor:
        return run_doctor(args, root)

    if args.fetch_list:
        return run_fetch_list(args, root)

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
    universe_size = len(targets)
    if args.limit:
        targets = targets[: args.limit]
        # 截断必须自己说一句，否则开场白会自相矛盾：首行报"清单 600 只"、
        # 预算行却按剩下那 1 只算。用户会以为脚本把清单数算错了。
        print(
            f"--limit：宇宙共 {universe_size} 只，这一轮只跑前 {len(targets)} 只"
            "（下面的预算按这个数算）"
        )
    # 协议层不支持的类别（北交所 bj*，TDX 报 E3040）进得了宇宙、取不到数据：
    # 本轮会全部 SKIP，且不计入缺口、不写进 state.failed。显式告警，避免用户误以为
    # "加了 --include-class bse 就能同步北交所"。它们只能由非 TDX 源获取，脚本暂不支持。
    unsupported = [t for t in targets if t.kind in UNSUPPORTED_CLASSES]
    if unsupported:
        print(
            f"⚠ {len(unsupported)} 只协议层不支持（北交所 bj*，TDX E3040）：本轮会全部 SKIP，"
            "且不算缺口、不写进 state.failed。它们只能由非 TDX 源获取，脚本暂不支持；"
            "建议加 --exclude-class bse 省去这些必败请求。"
        )
    for line in _plan_lines(targets, source, root, args.full, args.lookback, universe_size):
        print(line)

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
    tally = syncer.run(targets, workers=args.workers, verbose=args.verbose)
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
    if not args.verbose:
        skips = [
            (sym, detail)
            for status, sym, detail in getattr(syncer, "_problems", [])
            if status == "SKIP"
        ]
        if skips:
            print(f"跳过/失败 {len(skips)} 只（未落盘，下轮按磁盘断点自动重试）：")
            for sym, why in skips[:30]:
                print(f"  - {sym}: {why[:80]}")
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
