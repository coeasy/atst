# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""标的清单的三个取数源：磁盘代码表 / 新浪节点 / tdx 段表探测。

三个源都返回 ``list[Security]``，**空列表代表"这一源没拿下、继续降级"**——
不抛异常。理由：代码表是个"能用就行"的东西，为了一个源挂掉让整条命令崩掉
不划算；正确的动作是换下一个源，最后一个源都拿不到才该报错。

限流纪律：新浪这条走 :meth:`atst.web.sina.adapters.SinaSource.fetch_all`
（已内置令牌桶 + 并发分页 + 退避重试），**不**自己再发裸请求；tdx 那条走
:class:`atst.client.TdxClient`，与同步脚本共用的同一套主站池。
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import suppress
from datetime import date
from pathlib import Path
from typing import Any, Final, Protocol, cast

from ._classes import NODE_TO_CLASSES, Security, class_of, classify

__all__ = [
    "SOURCE_TABLE",
    "SOURCE_SINA",
    "SOURCE_TDX",
    "from_table",
    "from_sina",
    "from_tdx_scan",
    "source_names",
]


class _MarketCenterFetcher(Protocol):
    """``SinaSource.fetch_all`` 的形状。

    ``create_source`` 的静态返回是 ``BaseWebSource``，``fetch_all`` 不在它的面上；
    而 ``atst.universe`` 用的恰恰是这个方法，所以把最小依赖写成一个
    :class:`~typing.Protocol` 比 ``getattr`` 或 ``Any`` 都诚实——拼写错了 mypy 会报。
    """

    def fetch_all(
        self,
        node: str = "hs_a",
        page_size: int = 80,
        max_pages: int | None = None,
        workers: int = 3,
    ) -> Sequence[Any]: ...


#: 取数源名（CLI ``--source`` 的取值）。
SOURCE_TABLE: Final[str] = "table"
SOURCE_SINA: Final[str] = "sina"
SOURCE_TDX: Final[str] = "tdx"

#: 自动（代码表 → 新浪 → tdx）时按此顺序尝试。
SOURCE_ORDER: Final[tuple[str, ...]] = (SOURCE_TABLE, SOURCE_SINA, SOURCE_TDX)

#: 读代码表时最多看多少行。全市场 5000+ 行是常态，给到 20 万行是"够用但有限"，
#: 不是性能调参——它挡的是"文件被写成了别的东西"这种事故。
_MAX_TABLE_LINES: Final[int] = 200_000


def source_names() -> tuple[str, ...]:
    """全部可用源名（含 ``auto`` 由调用方处理）。"""
    return SOURCE_ORDER


# --------------------------------------------------------------------------- #
# 源 1：磁盘代码表（<root>/universe.csv）
# --------------------------------------------------------------------------- #
def from_table(root: Path, kind: str) -> list[Security]:
    """读 sync 脚本产出的代码表；拿不到返回空列表。

    表格两列（``代码,类别``）；只有一列时按前缀现判类别（老格式兼容）。
    命中不到目标类别是**正常**（比如只要 ETF 而表里没有 ETF 行），不算失败。
    """
    path = Path(root) / "universe.csv"
    if not path.exists():
        return []
    hits: list[Security] = []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []
    for lineno, raw in enumerate(text.splitlines()):
        if lineno >= _MAX_TABLE_LINES:
            # 防御：文件被写坏（或被别的程序写成了日志）时，别把整份内容都当表读。
            # 判据挂在**循环头**上——挂在末尾那种写法对"一行都不合法"的文件等于没有。
            break
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        heads = [part.strip() for part in raw.split(",")]
        code = heads[0]
        if len(code) < 2:
            continue
        # 表头行（无前缀的代码）跳过
        if code[:2].lower() not in ("sh", "sz", "bj"):
            continue
        declared = heads[1].lower() if len(heads) > 1 and heads[1] else ""
        if declared:
            if declared != kind:
                continue
        elif classify(code) != kind:
            # 老格式只有一列代码：按前缀现判类别，否则整张表会被当成同一个类别
            # 交出去（问 ETF 却拿到 A 股）。
            continue
        hits.append(Security(code=code, name=heads[2] if len(heads) > 2 else "", kind=kind))
    return _dedup(hits)


# --------------------------------------------------------------------------- #
# 源 2：新浪行情中心节点
# --------------------------------------------------------------------------- #
def from_sina(kind: str, *, page_size: int = 100, workers: int = 3) -> list[Security]:
    """新浪 ``Market_Center`` 节点取数（实测全 A 约 15 秒）。

    新浪没有 ETF / LOF / 可转债 / 指数的节点（实测 ``etf_hq`` / ``bond_hq`` /
    ``index`` 等均返回空），那几个类别会拿到空列表并自动降级到 tdx 段表探测。
    """
    return from_sina_multi((kind,), page_size=page_size, workers=workers)


def from_sina_multi(
    kinds: tuple[str, ...], *, page_size: int = 100, workers: int = 3
) -> list[Security]:
    """多类别一次取：按节点聚合去重，共享节点（``hs_a`` 供 stock + bse）只拉一次。

    ``hs_a`` 返回的包里混着 ``bj*`` 北交所代码，所以拉回来要按 :func:`~atst
    .universe._classes.classify` 重新拆分类别——不能把整包都算作 stock，
    否则北交所会被当成 A 股塞进 ``day/stock/``。

    "哪个节点该拆出哪几类"读 :data:`~atst.universe._classes.NODE_TO_CLASSES`
    （由类别表推导），本函数不自己手抄一张节点表——手抄的那份会在换节点后变旧。
    """
    from atst.web import create_source  # 惰性：模块导入不该建连接

    # 顺序必须确定：集合的迭代序会随哈希种子变，"同一份数据两次跑出不同顺序"会让
    # 输出、快照测试、以及"两条链路结果应当一致"的判据全部变成偶发红。
    wanted = tuple(kind for kind in kinds if class_of(kind).sina_node)
    if not wanted:
        return []
    #: 节点 → 本次要从中拆出的类别（取交集：只要 stock 就别把 bj* 也拆出来）
    per_node: dict[str, tuple[str, ...]] = {}
    for kind in wanted:
        node = class_of(kind).sina_node
        if node:
            per_node[node] = tuple(k for k in NODE_TO_CLASSES[node] if k in wanted)

    out: list[Security] = []
    #: fetch_all 自带令牌桶，一个源实例一个限流器；按节点新建会让同一个进程里
    #: 并存多个限流器（各算各的配额），所以整轮共用一个。
    fetcher: _MarketCenterFetcher | None = None
    for node, node_kinds in per_node.items():
        if fetcher is None:
            try:
                # create_source 的静态返回是 BaseWebSource（拿得到 fetch_all 的是
                # SinaSource），这里把"我们依赖的那一点型别"显式声明出来，别靠 getattr 蒙。
                fetcher = cast("_MarketCenterFetcher", create_source("sina"))
            except Exception:  # noqa: BLE001 - 建不出来就整条降级
                return []
        try:
            quotes = fetcher.fetch_all(node=node, page_size=page_size, workers=workers)
        except Exception:  # noqa: BLE001 - 单节点挂了降级，不让整条命令崩
            continue
        for quote in quotes:
            kind = classify(quote.code)
            if kind in node_kinds:
                out.append(
                    Security(code=quote.code, name=str(quote.extra.get("name", "")), kind=kind)
                )
    return _dedup(out)


# --------------------------------------------------------------------------- #
# 源 3：tdx 段表探测（全类别兜底）
# --------------------------------------------------------------------------- #
def from_tdx_scan(
    kind: str,
    *,
    workers: int = 8,
    timeout: float = 8.0,
    limit: int | None = None,
    attempts: int = 2,
) -> list[Security]:
    """按段表枚举候选代码，用 ``bars(count=1)`` 探出主站真认的那些。

    两道判据缺一不可（与 sync 脚本同口径）：**非空首页** + **日期键是真日历
    日期**。只判"非空"会收进 ``sh999999`` 这种主站回 ``0080-26-13`` 的垃圾，
    它此后每一轮同步都会白耗一次请求还注定失败。

    ``limit`` 限制探测的候选数（试水用），``None`` 表示整个段。返回顺序是
    **候选顺序**（段顺序 × 000-999），与并发探测的完成先后无关。
    """
    from atst.client import TdxClient

    asset = class_of(kind)
    if not asset.tdx_segments:
        return []
    from ._scan import iter_candidates

    candidates = list(iter_candidates(asset.tdx_segments))
    if limit:
        candidates = candidates[: int(limit)]
    if not candidates:
        return []

    found: set[str] = set()
    client = TdxClient(timeout=timeout, slots_per_host=max(1, workers))
    try:
        with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
            futures = {
                pool.submit(_probe_once, client, code, asset.index_bit, attempts): code
                for code in candidates
            }
            for future in as_completed(futures):
                code = futures[future]
                try:
                    rows = future.result()
                except Exception:  # noqa: BLE001 - 单只探测失败不影响其它
                    continue
                if rows and _is_real_date_key(rows[0].get("datetime", "")):
                    found.add(code)
    finally:
        _shutdown(client)
    # 结果按**候选顺序**出，不按 ``as_completed`` 的完成序：线程完成顺序随并发
    # 负载与网络抖动变，同一份数据两次跑出两份顺序，CLI 输出、快照判据、以及
    # "两条取数链路结果应当一致"的比较全都会变成偶发红。顺序纪律与
    # :func:`from_sina_multi` 是同一条。
    return [Security(code=code, name="", kind=kind) for code in candidates if code in found]


def _probe_once(client: Any, code: str, index_bit: bool, attempts: int) -> list[Any]:
    """探一只；传输层异常按"再试一次"，全部失败才抛。

    退避只发生在**还有下一次**的时候——在最后一次失败后再睡一觉纯属白等，
    而这段代码跑在段表探测的热路径上（一次几万个候选）。
    """
    total = max(1, attempts)
    last: Exception | None = None
    for attempt in range(total):
        try:
            return list(client.bars(code, period="day", count=1, index=index_bit))
        except Exception as exc:  # noqa: BLE001 - 传输层异常按"再试一次"
            last = exc
            if attempt < total - 1:
                time.sleep(0.05 * total)
    raise RuntimeError(str(last) or type(last).__name__)


def _is_real_date_key(value: Any) -> bool:
    """日期键必须是能构造出来的 ``YYYY-MM-DD``（挡住 0080-26-13 / 8414-91-57）。"""
    text = str(value or "")[:10]
    if len(text) < 10:
        return False
    try:
        date.fromisoformat(text)
    except ValueError:
        return False
    return True


def _shutdown(client: Any) -> None:
    closer = getattr(client, "close", None)
    if callable(closer):
        with suppress(Exception):  # 收尾不抛
            closer()


def _dedup(items: list[Security]) -> list[Security]:
    seen: set[str] = set()
    out: list[Security] = []
    for item in items:
        if item.code in seen:
            continue
        seen.add(item.code)
        out.append(item)
    return out
