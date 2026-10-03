"""标的清单（universe）：按类别取"全市场有哪些标的"。

这是 ``0x044D SECURITY_LIST``（``atst list``）死掉之后的**替代路径**——
那个命令实测 E3035（多主站无响应）。代码表不再依赖单一协议，改由三个源
降级供给：

1. **磁盘代码表** ``<root>/universe.csv``（sync 脚本产出的那张表）——毫秒级；
2. **新浪行情中心节点**（``hs_a`` 沪深 A 股 / ``hs_b`` B 股 …）——秒级；
3. **tdx 段表探测**——按段枚举候选，用 ``bars`` 探出主站真认的那些，
   覆盖 ETF / LOF / 可转债 / 指数这些新浪没有节点入口的类别，分钟级。

用法::

    from atst.universe import list_universe

    list_universe("stock")     # 全部 A 股（含北交所）
    list_universe("etf")       # 全部 ETF
    list_universe("bse")       # 北交所
    list_universe("all")       # 七类全量
    list_universe("stock", source="sina", limit=500)   # 试水

    for sec in list_universe("stock"):
        print(sec.code, sec.name)

CLI::

    atst universe stock            # 打印前若干条 + 计数
    atst universe etf --json       # 机器可读
    atst universe all --dry-run    # 只看各源代价，不真拉

本模块**不是** capability：capability 表走 ``Client → QuerySpec → QueryPlan →
runtime`` 那条行情读取链路（登记新能力要动 194 个能力的口径、还要在 provider
表里显式声明），而"有哪些标的"是工具方法，不走那条链路——选它做替代路径，
比为了一个清单去动整套能力注册更轻。
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Final

from . import _sources
from ._classes import (
    ASSET_CLASSES,
    NODE_TO_CLASSES,
    SINA_NODES,
    TBD_SOURCES,
    AssetClass,
    Security,
    asset_class_names,
    class_of,
    class_of_directory,
    classify,
    classify_with_index,
)
from ._scan import iter_candidates, segment_candidates
from ._sources import SOURCE_ORDER, from_table

__all__ = [
    "ASSET_CLASSES",
    "DEFAULT_TABLE_ROOT",
    "NODE_TO_CLASSES",
    "SINA_NODES",
    "TBD_SOURCES",
    "AssetClass",
    "Security",
    "SOURCE_ORDER",
    "asset_class_names",
    "class_of",
    "class_of_directory",
    "classify",
    "classify_with_index",
    "from_table",
    "iter_candidates",
    "list_all_universe",
    "list_universe",
    "segment_candidates",
    "universe_report",
]

#: 代码表（``universe.csv``）所在目录的默认根（与 ``scripts/sync_daily_history.py`` 落盘位置一致）。
DEFAULT_TABLE_ROOT: Final[Path] = Path("data")


def list_universe(
    kind: str = "stock",
    *,
    source: str = "auto",
    root: Path | str | None = DEFAULT_TABLE_ROOT,
    limit: int | None = None,
    workers: int = 8,
    timeout: float = 8.0,
) -> list[Security]:
    """取某一类（或 ``"all"`` 全部类别）的标的清单。

    Parameters
    ----------
    kind
        类别名：``stock`` / ``bse`` / ``etf`` / ``lof`` / ``bond`` / ``index``
        / ``bshare``，或 ``"all"``（各类别分别降级取数后合并）。
    source
        ``auto`` 走「代码表 → 新浪 → tdx」；也可显式指定 ``table`` / ``sina``
        / ``tdx``——指定时拿不到会**抛** ``LookupError``，而不是悄悄降级成
        另一份数据（显式指定就该得到显式结果）。
    root
        代码表所在的根（默认 ``data/``，即 ``data/universe.csv``）。
        传 ``None`` 表示不读代码表。
    limit
        每类最多返回多少条（试水用），对 tdx 探测同时限制候选数。
        ``0``（或负数）就是**一条都不要**——按字面理解，别退化成"不限"。
    workers / timeout
        tdx 探测的并发与超时。

    Returns
    -------
    ``list[Security]``，按取数顺序（段/节点顺序）排列。

    Raises
    ------
    LookupError
        显式 ``source`` 指定但那一源拿不到结果。
    """
    if limit is not None and limit <= 0:
        return []
    if kind == "all":
        return list_all_universe(
            source=source, root=root, limit=limit, workers=workers, timeout=timeout
        )
    asset = class_of(kind)
    if source == "auto":
        rows, _ = _walk_sources(asset, root=root, limit=limit, workers=workers, timeout=timeout)
    else:
        rows = _single_source(
            asset, source, root=root, limit=limit, workers=workers, timeout=timeout
        )
    return rows


def list_all_universe(
    *,
    source: str = "auto",
    root: Path | str | None = DEFAULT_TABLE_ROOT,
    limit: int | None = None,
    workers: int = 8,
    timeout: float = 8.0,
) -> list[Security]:
    """全部类别的标的清单（每项标好 :attr:`Security.kind`）。

    显式 ``source`` 时**任一类**拿不到就抛 ``LookupError``——与
    :func:`list_universe` 同一条契约（"显式指定就该得到显式结果"）。所以
    ``source="sina"`` + 全部类别是拿不全的（新浪没有 ETF/LOF/可转债/指数入口），
    要按类别跳过就用 ``source="auto"``，或逐类调用 :func:`list_universe`。

    ``limit`` 是**每类**的上限（不是总量上限）：七类各取前 N 条，而不是
    "前 N 条之后不管哪一类"——否则 ``--limit 10`` 会只看到第一类。
    """
    if limit is not None and limit <= 0:
        return []
    out: list[Security] = []
    for asset in ASSET_CLASSES:
        if source == "auto":
            rows, _ = _walk_sources(asset, root=root, limit=limit, workers=workers, timeout=timeout)
        else:
            rows = _single_source(
                asset, source, root=root, limit=limit, workers=workers, timeout=timeout
            )
        out += rows
    return out


def universe_report(
    kinds: tuple[str, ...] = ("stock", "bse", "etf", "lof", "bond", "index", "bshare"),
    *,
    source: str = "auto",
    root: Path | str | None = DEFAULT_TABLE_ROOT,
    limit: int | None = None,
    workers: int = 8,
    timeout: float = 8.0,
    probe: bool = True,
) -> list[dict[str, Any]]:
    """按「源 × 类别」报告代价（条数 / 耗时 / 降级路径），CLI 打印用。

    这是 :func:`list_universe` 的"先看代价再真拉"版本：报告里会写清每个
    类别最后是从哪个源拿到的，避免"全市场 5 分钟"这种事发生在你没看提示
    的时候。

    ``probe=False`` 是**纯估算**（``--dry-run`` 用）：代码表命中的报真条数，
    有新浪节点的照常拉（十几秒，也就一次），**剩下的只报候选量不真探**——
    tdx 段表探测一次要十几分钟，让 ``--dry-run`` 去跑它就失去意义了。
    """
    report: list[dict[str, Any]] = []
    for name in kinds:
        asset = class_of(name)
        started = time.monotonic()
        error: str | None = None
        used: str = source
        if source == "auto":
            rows, used = _walk_sources(
                asset,
                root=root,
                limit=limit,
                workers=workers,
                timeout=timeout,
                skip_tdx=not probe,
            )
        else:
            try:
                rows = _single_source(
                    asset, source, root=root, limit=limit, workers=workers, timeout=timeout
                )
            except LookupError as exc:
                # 报告层不许炸：--dry-run --source sina 撞上"ETF 没有新浪节点"
                # 是正常的（不是故障），这一格就该写成 0 + 原因，别的类别照样出报告。
                rows, used, error = [], source, str(exc)
        elapsed = round(time.monotonic() - started, 2)
        record = {
            "kind": name,
            "label": asset.label,
            "source": used,
            "count": len(rows),
            "seconds": elapsed,
            "sina_node": asset.sina_node,
            "segments": len(asset.tdx_segments),
            "index_bit": asset.index_bit,
            "error": error,
        }
        if not probe and row_needed_real_probe(record):
            # 既没代码表、也没新浪节点：别真去探十几分钟，只报候选量
            record.update(source="tdx", count=0, estimate=len(asset.tdx_segments) * 1000)
        report.append(record)
    return report


def row_needed_real_probe(record: dict[str, Any]) -> bool:
    """这一行是否"还没拿到数、且代价来自 tdx 探测"——``--dry-run`` 该跳过真探。

    取值一律走 ``.get``：这个函数是公开面，调用方手里的 record 可能来自别处
    （少一个键就 ``KeyError`` 的公开函数，等于把内部形状漏给了使用者）。
    """
    return not record.get("count") and record.get("source") == "tdx"


def _walk_sources(
    asset: AssetClass,
    *,
    root: Path | str | None,
    limit: int | None,
    workers: int,
    timeout: float,
    skip_tdx: bool = False,
) -> tuple[list[Security], str]:
    """按代码表 → 新浪 → tdx 顺序降级；返回 ``(结果, 命中的源名)``。

    三类都空时返回 ``([], "none")`` 而不是抛错——"北交所客户端拒、段表空"是
    已知现实，这时候报错只会盖住真正该看的信息。

    ``skip_tdx`` 是 :func:`universe_report` 的 ``probe=False`` 用的：停在
    tdx 这一级就返回 ``([], "tdx")``——**不真去探**（一次十几分钟），但明确
    告诉调用方"这一类本来该由 tdx 供，代价是这些候选"。
    """
    if limit is not None and limit <= 0:
        # 一条都不要：不必为此开网（tdx 那一级是十几分钟的作业，"0 条"不该付这个代价）
        return [], "none"
    for name in _sources.SOURCE_ORDER:
        if skip_tdx and name == _sources.SOURCE_TDX:
            return [], name
        rows = _call_source(name, asset, root=root, limit=limit, workers=workers, timeout=timeout)
        if rows:
            return _cap(rows, limit), name
    return [], "none"


def _call_source(
    name: str,
    asset: AssetClass,
    *,
    root: Path | str | None,
    limit: int | None,
    workers: int,
    timeout: float,
) -> list[Security]:
    """调一个源；内部异常按"没拿下"处理（返回空列表）。"""
    if name == _sources.SOURCE_TABLE:
        if root is None:
            return []
        return _sources.from_table(Path(root), asset.name)
    if name == _sources.SOURCE_SINA:
        return _sources.from_sina(asset.name)
    if name == _sources.SOURCE_TDX:
        return _sources.from_tdx_scan(asset.name, workers=workers, timeout=timeout, limit=limit)
    raise LookupError(f"未知标的清单源 {name!r}；可选：{', '.join(_sources.source_names())}")


def _single_source(
    asset: AssetClass,
    source: str,
    *,
    root: Path | str | None,
    limit: int | None,
    workers: int,
    timeout: float,
) -> list[Security]:
    try:
        rows = _call_source(source, asset, root=root, limit=limit, workers=workers, timeout=timeout)
    except LookupError:
        raise  # 未知源名：原样抛出，别包一层听不出所以的错
    except Exception as exc:  # noqa: BLE001 - 显式指定源也要给得清楚
        raise LookupError(
            f"源 {source!r} 取 {asset.name} 失败：{type(exc).__name__}: {exc}"
        ) from exc
    if not rows:
        # 显式源拿空 = 这个源给不出这类标的。静默返回 [] 会让"为什么 ETF 是空的"
        # 变成一道没有线索的谜题——``auto`` 会去试下一个源，显式指定就不该替用户
        # 挪动数据源，只该说清楚。
        raise LookupError(
            f"源 {source!r} 没有 {asset.name} 的标的；"
            f"要自动降级（{' → '.join(_sources.source_names())}）就用 source='auto'"
        )
    return _cap(rows, limit)


def _cap(rows: list[Security], limit: int | None) -> list[Security]:
    """按 ``limit`` 截断；``None`` 表示不限。

    截断只在这一个地方做：早先 :func:`list_universe` 自己在末尾又切一刀，
    于是同名的 ``limit`` 在"单类"和"全类别"两条路径上含义不同（``all`` 那支
    对代码表/新浪拿到的行根本不截），是那种"参数写了不生效"的静默不一致。
    """
    return rows if limit is None else rows[: max(0, limit)]
