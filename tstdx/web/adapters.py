# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""7 个 HTTP Web 行情源适配器实现（§33.7）。

各源返回格式的逆向事实（来自公开接口文档与实际响应样本）:

**新浪** ``hq.sinajs.cn/list=sh600519``::

    var hq_str_sh600519="名称,今开,昨收,现价,最高,最低,买一价,卖一价,
    成交量,成交额,买一量,买一价,...,买五量,买五价,卖一量,卖一价,...,卖五量,卖五价,日期,时间";

**腾讯** ``qt.gtimg.cn/q=sh600519``::

    v_sh600519="1~名称~代码~现价~昨收~今开~成交量(手)~外盘~内盘~
    买一量~买一价~...~买五量~买五价~卖一量~卖一价~...~卖五量~卖五价~
    最近逐笔~时间~涨跌~涨跌%~最高~最低~价格/成交量/成交额~成交量~成交额(万)~
    换手率~市盈率~~最高~最低~振幅~流通市值~总市值~市净率~涨停价~跌停价~量比~委差~均价~...";

**东财** ``push2.eastmoney.com/api/qt/stock/get?secid=1.600519&fields=...``::

    {"data": {"f43": 169550, "f44": ..., "f47": ..., "f48": ..., "f57": "600519", "f58": "名称", "f60": 169000}}
    // f43 最新价（×100 整数）、f44 最高、f45 最低、f46 今开、
    // f47 成交量（手）、f48 成交额（元）、f60 昨收

.. warning::
   这些接口随时可能改版或下线。所有适配器都继承
   :class:`~tstdx.web.base.BaseWebSource` 的下线检测，
   连续失败达阈值会抛 :class:`~tstdx.errors.SourceDeprecated`。
"""

from __future__ import annotations

import itertools
import json
import logging
import re
import threading
import time
from collections.abc import Mapping, Sequence
from typing import Any

from ..diagnostics import WarningCode, record_warning
from ..domain.models import Bar, Level, Quote
from ..errors import ReadTimeout, SourceDeprecated, WebSourceError
from ._paginate import (
    decode_kline_payload,
    fetch_bars_paged,
    rows_to_bars,
    warn_amount_all_zero,
)
from .base import (
    BaseWebSource,
    _EastmoneyJson,
    to_eastmoney_secid,
    to_sina_symbol,
    to_tencent_symbol,
)
from .base import (
    num_f as _f,
)
from .base import (
    num_i as _i,
)
from .limits import TENCENT_KLINE_MAX
from .normalize import (
    KLINE,
    TENCENT,
)
from .sources import BOC, EASTMONEY, HK, JSL, SINA, US

logger = logging.getLogger(__name__)

__all__ = [
    "SinaSource",
    "TencentSource",
    "EastmoneySource",
    "JslSource",
    "HkSource",
    "KlineSource",
    "BocSource",
]

#: ``fetch_all`` 未显式给 ``max_pages`` 时的硬上限（P2 #1）：
#: 分页终止不再完全依赖服务端空页，防止失控翻页。
DEFAULT_MAX_PAGES = 100


def _levels(
    fields: Sequence[str], start: int, count: int = 5, vol_first: bool = True
) -> list[Level]:
    """从交错数组解析盘口档位。

    Parameters
    ----------
    vol_first:
        True 表示排列为 ``量,价,量,价...``（新浪）；False 表示 ``价,量``（东财）。
    """
    out: list[Level] = []
    for i in range(count):
        a = start + i * 2
        b = a + 1
        if b >= len(fields):
            break
        va, vb = fields[a], fields[b]
        price, vol = (_f(vb), _i(va)) if vol_first else (_f(va), _i(vb))
        out.append(Level(price=price, volume=vol))
    return out


# --------------------------------------------------------------------------- #
# 新浪
# --------------------------------------------------------------------------- #
class SinaSource(BaseWebSource):
    """新浪财经实时行情。"""

    BASE = "https://hq.sinajs.cn/list="

    @property
    def source_name(self) -> str:
        return SINA

    def build_url(self, symbols: Sequence[str], **kwargs: Any) -> str:
        codes = ",".join(to_sina_symbol(s) for s in symbols)
        return f"{self.BASE}{codes}"

    def parse(self, text: str, symbols: Sequence[str], **kwargs: Any) -> list[Quote]:
        quotes: list[Quote] = []
        # 形如: var hq_str_sh600519="...";
        pattern = re.compile(r'hq_str_([a-z0-9]+)="([^"]*)"')
        for match in pattern.finditer(text):
            raw_code, payload = match.group(1), match.group(2)
            if not payload:
                continue
            f = payload.split(",")
            if len(f) < 32:
                raise WebSourceError(
                    f"新浪响应字段不足: {len(f)} < 32",
                    context={"code": raw_code, "source": SINA},
                )
            q = Quote(
                code=raw_code,
                price=_f(f[3]),
                last_close=_f(f[2]),
                open=_f(f[1]),
                high=_f(f[4]),
                low=_f(f[5]),
                volume=_i(f[8]),
                amount=_f(f[9]),
                bid=_levels(f, 10, 5, vol_first=True),
                ask=_levels(f, 20, 5, vol_first=True),
                extra={"name": f[0], "date": f[30], "time": f[31]},
            )
            quotes.append(self.normalize_quote(q))
        if not quotes:
            raise SourceDeprecated(
                "新浪返回空数据（接口可能已改版或需要 Referer）",
                context={"source": SINA, "sample": text[:200]},
            )
        return quotes

    # -- 全市场（新浪 Market_Center.getHQNodeData 接口） --------------------- #
    ALL_MARKET_URL = (
        "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
        "Market_Center.getHQNodeData"
    )

    def fetch_all(
        self,
        node: str = "hs_a",
        page_size: int = 80,
        max_pages: int | None = None,
        workers: int = 3,
    ) -> list[Quote]:
        """分页拉取全市场行情摘要（沪深 A 股等）。

        接口为 ``Market_Center.getHQNodeData``，每页即返回行情摘要，
        无需先拉代码表再逐批查询，可直接独立调用::

            from tstdx.web import create_source
            quotes = create_source("sina").fetch_all(node="hs_a", page_size=100)

        Parameters
        ----------
        node:
            新浪市场节点：``hs_a`` 沪深 A 股 / ``hs_b`` B 股 / ``cyb`` 创业板
            / ``sh_a`` 沪 A / ``sz_a`` 深 A 等。
        page_size:
            每页条数（新浪上限 100）。
        max_pages:
            页数上限（防失控）；``None`` 时取 :data:`DEFAULT_MAX_PAGES`
            （100 页）硬上限——P2 #1：翻页终止不再完全依赖服务端空页。
        workers:
            并发分页线程数（M3）：先取第 1 页探测，再按 ``workers`` 路并发
            拉取后续页；令牌桶在 `_request_text` 内统一控速，不会超配额。
        """
        effective_max_pages = DEFAULT_MAX_PAGES if max_pages is None else max(1, int(max_pages))
        workers = max(1, min(int(workers), 6))

        def _fetch_rows(page: int) -> list[Quote]:
            url = (
                f"{self.ALL_MARKET_URL}?page={page}&num={page_size}"
                f"&sort=symbol&asc=1&node={node}&_s_r_a=init"
            )
            text = self._request_text(url, encoding="gbk")
            try:
                rows = json.loads(text)
            except json.JSONDecodeError as exc:
                raise SourceDeprecated(
                    "新浪全市场返回非 JSON",
                    context={"source": SINA, "sample": text[:200]},
                    cause=exc,
                ) from exc
            if not isinstance(rows, list):
                # rows 非 list（如错误体 {"result": {...}}）→ 结构变更
                raise SourceDeprecated(
                    "新浪全市场返回结构异常（非行列表）",
                    context={"source": SINA, "sample": text[:160]},
                )
            return [self._quote_from_market_row(r) for r in rows]

        # 第 1 页：探测 + 短页直接返回（避免并发空转）
        first = _fetch_rows(1)
        if not first:
            raise SourceDeprecated("新浪全市场返回空", context={"source": SINA, "node": node})
        if len(first) < page_size or effective_max_pages == 1:
            return first

        out: dict[int, list[Quote]] = {1: first}
        stop = threading.Event()
        lock = threading.Lock()
        next_page = itertools.count(2)
        last_valid = [effective_max_pages + 1]
        failed_pages: list[int] = []  # v5 PG7：重试后仍失败的页码（结束后补拉+告警）

        def _fetch_page_with_retry(page: int) -> list[Quote]:
            """单页退避重试（v5 PG7）：SourceDeprecated（结构下线）不重试，
            其余 WebSourceError（429 限速等待超时/读超时/断连）退避重试 2 次。"""
            last_exc: WebSourceError | None = None
            for attempt in range(3):
                if attempt:
                    time.sleep(0.5 * attempt)
                try:
                    return _fetch_rows(page)
                except SourceDeprecated:
                    raise
                except WebSourceError as exc:  # 含 WebRateLimited / ReadTimeout 包装
                    last_exc = exc
            raise last_exc  # type: ignore[misc]

        def worker() -> None:
            while not stop.is_set():
                with lock:
                    page = next(next_page)
                if page > effective_max_pages:
                    return
                try:
                    quotes = _fetch_page_with_retry(page)
                except SourceDeprecated:
                    with lock:
                        last_valid[0] = min(last_valid[0], page)
                    stop.set()
                    return
                except WebSourceError:
                    # v5 PG7：重试耗尽——记录失败页并继续其余页，
                    # 不再让异常逃逸 worker 线程击穿整个 fetch_all
                    with lock:
                        failed_pages.append(page)
                    continue
                if not quotes:
                    # 空页：不含有效数据，终止（本页不计入）
                    with lock:
                        last_valid[0] = min(last_valid[0], page)
                    stop.set()
                    return
                with lock:
                    out[page] = quotes
                if len(quotes) < page_size:
                    # 短页：最后余数页，本身含有效数据，计入后终止
                    with lock:
                        last_valid[0] = min(last_valid[0], page + 1)
                    stop.set()
                    return

        threads = [threading.Thread(target=worker, daemon=True) for _ in range(workers)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # v5 PG7：失败页串行补拉一轮（此时并发风暴已过，限流窗口已冷却）
        for page in list(failed_pages):
            try:
                out[page] = _fetch_rows(page)
                failed_pages.remove(page)
            except WebSourceError:
                pass

        result: list[Quote] = []
        for page in range(1, last_valid[0]):
            result.extend(out.get(page, ()))
        if failed_pages:
            record_warning(
                WarningCode.WEB_SINA_PAGES_MISSING,
                f"新浪全市场 fetch_all(node={node}) 以下分页拉取失败（已含重试与补拉），"
                f"结果缺页: {sorted(failed_pages)}",
                stacklevel=2,
            )
        if not result:
            raise SourceDeprecated("新浪全市场返回空", context={"source": SINA, "node": node})
        return result

    def _quote_from_market_row(self, r: Mapping[str, Any]) -> Quote:
        """把 Market_Center 一条记录转成归一化后的 Quote。"""
        tick = str(r.get("ticktime", ""))
        if " " in tick:
            date, _, time_ = tick.partition(" ")
        else:  # 非交易时段 ticktime 可能只含时间
            date, time_ = "", tick
        q = Quote(
            code=str(r.get("symbol", "")),
            price=_f(r.get("trade")),
            last_close=_f(r.get("settlement")),
            open=_f(r.get("open")),
            high=_f(r.get("high")),
            low=_f(r.get("low")),
            volume=_i(r.get("volume")),
            amount=_f(r.get("amount")),
            bid=[Level(price=_f(r.get("buy")), volume=0)],
            ask=[Level(price=_f(r.get("sell")), volume=0)],
            extra={
                "name": str(r.get("name", "")),
                "date": date,
                "time": time_,
                "change_pct": _f(r.get("changepercent")),
                "change": _f(r.get("pricechange")),
                "per": r.get("per"),
                "pb": r.get("pb"),
                # 深审 M30：新浪榜接口 mktcap/nmc 单位为**万元**——换算为元
                # （与东财 f116/f117 同口径），键名不变、单位统一。
                "mktcap": _f(r.get("mktcap")) * 1e4,
                "nmc": _f(r.get("nmc")) * 1e4,
                "turnoverratio": _f(r.get("turnoverratio")),
            },
        )
        return self.normalize_quote(q)


# --------------------------------------------------------------------------- #
# 腾讯
# --------------------------------------------------------------------------- #
class TencentSource(BaseWebSource):
    """腾讯财经实时行情（含港股、期货）。"""

    BASE = "https://qt.gtimg.cn/q="

    @property
    def source_name(self) -> str:
        return TENCENT

    def build_url(self, symbols: Sequence[str], **kwargs: Any) -> str:
        codes = ",".join(to_tencent_symbol(s) for s in symbols)
        return f"{self.BASE}{codes}"

    def parse(self, text: str, symbols: Sequence[str], **kwargs: Any) -> list[Quote]:
        quotes: list[Quote] = []
        # 美股代码含大写（如 usAAPL），故代码段允许大写
        pattern = re.compile(r'v_([a-zA-Z0-9]+)="([^"]*)"')
        for match in pattern.finditer(text):
            raw_code, payload = match.group(1), match.group(2)
            if not payload:
                continue
            f = payload.split("~")
            if len(f) < 30:
                raise WebSourceError(
                    f"腾讯响应字段不足: {len(f)} < 30",
                    context={"code": raw_code, "source": TENCENT},
                )
            extra: dict[str, Any] = {"name": f[1]}
            if len(f) > 30:
                extra["time"] = f[30]
            if len(f) > 32:
                extra["pct_change"] = _f(f[32])
            if len(f) > 45:
                # 深审 M30：腾讯接口市值单位为**亿**——统一换算为元（与东财
                # f116/f117 同口径），下游跨源聚合/比较不再跳变数量级。
                extra["float_market_cap"] = _f(f[44]) * 1e8
                extra["total_market_cap"] = _f(f[45]) * 1e8
            if len(f) > 46:
                extra["pb"] = _f(f[46])
            if len(f) > 48:
                extra["limit_up"] = _f(f[47])
                extra["limit_down"] = _f(f[48])
            if len(f) > 49:
                extra["volume_ratio"] = _f(f[49])
            if len(f) > 38:
                extra["turnover_rate"] = _f(f[38])
            if len(f) > 39:
                extra["pe"] = _f(f[39])

            q = Quote(
                code=raw_code,
                price=_f(f[3]),
                last_close=_f(f[4]),
                open=_f(f[5]),
                volume=_i(f[6]),
                high=_f(f[33]) if len(f) > 33 else _f(f[3]),
                low=_f(f[34]) if len(f) > 34 else _f(f[3]),
                amount=_f(f[37]) if len(f) > 37 else 0.0,
                bid=_levels(f, 9, 5, vol_first=True),
                ask=_levels(f, 19, 5, vol_first=True),
                extra=extra,
            )
            quotes.append(self.normalize_quote(q))
        if not quotes:
            raise SourceDeprecated(
                "腾讯返回空数据（接口可能已改版）",
                context={"source": TENCENT, "sample": text[:200]},
            )
        return quotes

    # -- 全市场（getBoardRankList 枚举代码 + qt.gtimg.cn 批量行情） ------------ #
    #: 节点 → 排行接口 board_code（对齐新浪 ``fetch_all`` 的 node 语义）
    RANK_BOARDS: dict[str, str] = {
        "hs_a": "aStock",  # 沪深 A 股
        "cyb": "cyb",  # 创业板
    }
    #: 排行接口单页条数上限（服务端上限 200）
    RANK_PAGE_MAX = 200
    #: qt.gtimg.cn 单请求批量条数（实测 120 路全量返回，保守取 100）
    TENCENT_BATCH = 100
    #: 腾讯排行接口（新版 getBoardRankList，offset/count 分页 JSON）
    RANK_URL = "https://proxy.finance.qq.com/cgi/cgi-bin/rank/hs/getBoardRankList"

    def fetch_all(
        self,
        node: str = "hs_a",
        page_size: int = 200,
        max_pages: int | None = None,
    ) -> list[Quote]:
        """分页拉取腾讯全市场行情摘要（U5）。

        全程单源（腾讯，非兜底），两步走：
        1. ``getBoardRankList`` 排行接口按 ``offset/count`` 枚举证券代码；
        2. 按 :attr:`TENCENT_BATCH` 切批走 ``qt.gtimg.cn`` 批量行情接口，
           复用 :meth:`fetch` 的限流 / 重试 / 归一化，返回完整 Quote。

        与新浪 ``fetch_all`` 的 ``node`` 语义对齐：``hs_a`` 沪深 A 股
        （默认）/ ``cyb`` 创业板；其余节点显式报错并给出可选值。
        """
        effective_max_pages = DEFAULT_MAX_PAGES if max_pages is None else max(1, int(max_pages))
        board = self.RANK_BOARDS.get(str(node).lower())
        if board is None:
            raise WebSourceError(
                f"腾讯全市场节点不支持 {node!r}；可选: {sorted(self.RANK_BOARDS)}",
                context={"source": TENCENT, "node": node},
            )
        codes = self._fetch_codes(board, page_size=page_size, max_pages=effective_max_pages)
        if not codes:
            raise SourceDeprecated(
                "腾讯全市场返回空代码表", context={"source": TENCENT, "node": node}
            )

        out: list[Quote] = []
        failed_codes: list[str] = []
        for i in range(0, len(codes), self.TENCENT_BATCH):
            batch = codes[i : i + self.TENCENT_BATCH]
            try:
                out.extend(self.fetch(batch))
            except (WebSourceError, ReadTimeout):
                # A4：单批失败先记录，最后统一退避重试一次；全部失败才判不可用
                failed_codes.extend(batch)
        if failed_codes:
            time.sleep(0.5)  # 单次退避，避免瞬断/限流连锁
            retried_failed: list[str] = []
            for i in range(0, len(failed_codes), self.TENCENT_BATCH):
                retry_batch = failed_codes[i : i + self.TENCENT_BATCH]
                try:
                    out.extend(self.fetch(retry_batch))
                except (WebSourceError, ReadTimeout):
                    retried_failed.extend(retry_batch)
            if retried_failed:
                # A4：不静默丢数据 —— 结束时一次性 UserWarning，列出失败样本
                record_warning(
                    WarningCode.WEB_TENCENT_BATCH_FAILED,
                    "腾讯全市场 fetch_all 单批重试后仍失败 "
                    f"{len(retried_failed)}/{len(codes)} 只代码"
                    f"（样本 {retried_failed[:5]}），本次结果可能不完整",
                    stacklevel=2,
                )
        if not out:
            raise SourceDeprecated(
                "腾讯全市场拉取全部失败",
                context={"source": TENCENT, "node": node, "codes": len(codes)},
            )
        return out

    def _fetch_codes(
        self, board: str, *, page_size: int = 200, max_pages: int = DEFAULT_MAX_PAGES
    ) -> list[str]:
        """按排行接口 offset 分页枚举证券代码（去重保序，单页失败即停）。"""
        page_size = max(1, min(int(page_size), self.RANK_PAGE_MAX))
        codes: list[str] = []
        seen: set[str] = set()
        offset = 0
        for _ in range(max_pages):
            url = (
                f"{self.RANK_URL}?board_code={board}&sort_type=price&direct=down"
                f"&offset={offset}&count={page_size}"
            )
            try:
                text = self._request_text(url, encoding="utf-8")
                rows = self._parse_rank_codes(text)
            except (WebSourceError, ReadTimeout):
                # 单页失败（瞬断/结构变更）→ 停止枚举，返回已收代码（同新浪 fetch_all 语义）
                break
            for c in rows:
                if c not in seen:
                    seen.add(c)
                    codes.append(c)
            if len(rows) < page_size:
                break  # 末页（不足一页）→ 终止
            offset += page_size
        return codes

    @staticmethod
    def _parse_rank_codes(text: str) -> list[str]:
        """解析 getBoardRankList JSON，抽出 ``code`` 列（非法体 → SourceDeprecated）。"""
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise SourceDeprecated(
                "腾讯排行接口返回非 JSON",
                context={"source": TENCENT, "sample": text[:200]},
                cause=exc,
            ) from exc
        data = payload.get("data") if isinstance(payload, dict) else None
        rows = data.get("rank_list") if isinstance(data, dict) else None
        if not isinstance(rows, list):
            raise SourceDeprecated(
                "腾讯排行接口返回结构异常（缺 rank_list）",
                context={"source": TENCENT, "sample": text[:160]},
            )
        return [str(r.get("code", "")) for r in rows if isinstance(r, dict) and r.get("code")]


# --------------------------------------------------------------------------- #
# 新浪港股
# --------------------------------------------------------------------------- #
class SinaHkSource(SinaSource):
    """新浪港股实时行情（``hq.sinajs.cn/list=hkXXXXX``）。

    港股字段布局（逗号分隔，约 19 列，与 A 股 ``hq_str`` 布局不同）：:

        [0]英文名 [1]中文名 [2]开盘 [3]昨收 [4]最高 [5]最低
        [6]最新价 [7]涨跌 [8]涨跌幅(%) [9]买一 [10]卖一
        [11]成交额(港元) [12]成交量(股) [13]市盈率 [14]市值(亿)
        [15]... [16]日期 [17]时间

    成交量单位为「股」（与全局契约一致），成交额以港元(HKD)计，
    ``extra["currency"]`` 标记原生币种。
    """

    @property
    def source_name(self) -> str:
        return HK

    def build_url(self, symbols: Sequence[str], **kwargs: Any) -> str:
        codes = ",".join(s if s.lower().startswith("hk") else f"hk{s}" for s in symbols)
        return f"{self.BASE}{codes}"

    def parse(self, text: str, symbols: Sequence[str], **kwargs: Any) -> list[Quote]:
        quotes: list[Quote] = []
        pattern = re.compile(r'hq_str_([a-z0-9]+)="([^"]*)"')
        for match in pattern.finditer(text):
            raw_code, payload = match.group(1), match.group(2)
            if not payload:
                continue
            f = payload.split(",")
            if len(f) < 13:
                raise WebSourceError(
                    f"新浪港股响应字段不足: {len(f)} < 13",
                    context={"code": raw_code, "source": HK},
                )
            extra: dict[str, Any] = {
                "name": f[1],
                "currency": "HKD",
                "change": _f(f[7]),
                "pct_change": _f(f[8]),
            }
            if len(f) > 17:
                extra["date"] = f[17]
            if len(f) > 18:
                extra["time"] = f[18]
            q = Quote(
                code=raw_code,
                price=_f(f[6]),
                last_close=_f(f[3]),
                open=_f(f[2]),
                high=_f(f[4]),
                low=_f(f[5]),
                volume=_i(f[12]),
                amount=_f(f[11]),
                bid=[],
                ask=[],
                extra=extra,
            )
            quotes.append(self.normalize_quote(q))
        if not quotes:
            raise SourceDeprecated(
                "新浪港股返回空数据（接口可能已改版或需要 Referer）",
                context={"source": HK, "sample": text[:200]},
            )
        return quotes


# --------------------------------------------------------------------------- #
# 东方财富
# --------------------------------------------------------------------------- #
class EastmoneySource(_EastmoneyJson):
    """东方财富行情（REST JSON，主机池 failover + R3 黑名单）。

    东财主站对高频 IP 有断连风控（``RemoteDisconnected``），单 BASE 固定
    push2 会随机全量失败。继承 :class:`_EastmoneyJson` 的 ``HOSTS`` 池
    （push2 → 92.push2 → push2delay）与进程级黑名单（TTL 内跳过失败主机）。
    """

    HOSTS: tuple[str, ...] = (
        "https://push2.eastmoney.com",
        "https://92.push2.eastmoney.com",
        "https://push2delay.eastmoney.com",
    )
    #: 与既有 ``BASE`` 保持一致的取数路径（GET /api/qt/stock/get）
    JSON_LABEL = "东财行情"

    encoding = "utf-8"  # 东财 JSON 为 UTF-8（W10：fetch() 不再硬编码 gbk）
    FIELDS = (
        "f43,f44,f45,f46,f47,f48,f49,f50,f51,f52,f57,f58,f60,"
        "f168,f169,f170,f171,f116,f117,f162,f167"
    )

    @property
    def source_name(self) -> str:
        return EASTMONEY

    def fetch(self, symbols: Sequence[str], **kwargs: Any) -> list[Quote]:
        """按主机池顺序拉取并解析行情（含限流 / 失败计数 / 黑名单）。"""
        symbol = symbols[0] if symbols else ""
        secid = to_eastmoney_secid(symbol)
        path_query = (
            f"/api/qt/stock/get?secid={secid}&fields={self.FIELDS}&_={int(kwargs.get('_t', 0))}"
        )
        self.rate_limiter.acquire(self.source_name)
        payload = self._get_json(path_query)
        return self._parse_payload(payload)

    def _parse_payload(self, payload: Mapping[str, Any]) -> list[Quote]:
        """把 ``/api/qt/stock/get`` 的 JSON payload 解析为 ``list[Quote]``。"""
        data = payload.get("data")
        if not data:
            raise SourceDeprecated(f"东财返回空数据: {payload}", context={"source": EASTMONEY})
        q = Quote(
            code=str(data.get("f57", "")),
            price=_f(data.get("f43")),
            high=_f(data.get("f44")),
            low=_f(data.get("f45")),
            open=_f(data.get("f46")),
            volume=_i(data.get("f47")),
            amount=_f(data.get("f48")),
            last_close=_f(data.get("f60")),
            extra={
                "name": data.get("f58", ""),
                "turnover_rate": _f(data.get("f168")),
                "change": _f(data.get("f169")) / 100,
                "pct_change": _f(data.get("f170")) / 100,
                "float_market_cap": _f(data.get("f116")),
                "total_market_cap": _f(data.get("f117")),
                "pe": _f(data.get("f162")) / 100,
                "pb": _f(data.get("f167")) / 100,
            },
        )
        return [self.normalize_quote(q)]


# --------------------------------------------------------------------------- #
# 港股 / 美股（腾讯 qt.gtimg.cn 外部市场布局）
# --------------------------------------------------------------------------- #
class TencentExternalSource(TencentSource):
    """腾讯外部市场（港股 / 美股）基类。

    腾讯对港股返回 ``100`` 前缀、美股返回 ``200`` 前缀的字段布局，与 A 股
    的 93 字段布局**核心 OHLCV 索引一致**（[3]价 / [4]昨收 / [5]开 /
    [31]涨跌 / [32]涨跌% / [33]高 / [34]低 / [37]额），但**不携带五档盘口**，
    且 [9..18] / [19..28] 不是买卖盘。A 股版 :class:`TencentSource` 直接把
    这些位置当 bid/ask 解析会得到垃圾值，故外部市场用本类独立解析。

    成交量单位为「股」（与全局契约一致，无需 ×100）；价格 / 成交额以原生
    币种计（港股 HKD、美股 USD），通过 ``extra["currency"]`` 透出，库不做
    汇率换算。
    """

    #: 子类覆盖：原生币种
    CURRENCY = "CNY"

    def parse(self, text: str, symbols: Sequence[str], **kwargs: Any) -> list[Quote]:
        quotes: list[Quote] = []
        # 美股代码含大写（如 usAAPL），故代码段允许大写
        pattern = re.compile(r'v_([a-zA-Z0-9]+)="([^"]*)"')
        for match in pattern.finditer(text):
            raw_code, payload = match.group(1), match.group(2)
            if not payload:
                continue
            f = payload.split("~")
            if len(f) < 38:
                raise WebSourceError(
                    f"腾讯外部市场响应字段不足: {len(f)} < 38",
                    context={"code": raw_code, "source": self.source_name},
                )
            extra: dict[str, Any] = {"name": f[1], "currency": self.CURRENCY}
            if len(f) > 30:
                extra["time"] = f[30]
            if len(f) > 32:
                extra["pct_change"] = _f(f[32])
            if len(f) > 38:
                extra["turnover_rate"] = _f(f[38])
            if len(f) > 39:
                extra["pe"] = _f(f[39])
            q = Quote(
                code=raw_code,
                price=_f(f[3]),
                last_close=_f(f[4]),
                open=_f(f[5]),
                volume=_i(f[6]),
                high=_f(f[33]) if len(f) > 33 else _f(f[3]),
                low=_f(f[34]) if len(f) > 34 else _f(f[3]),
                amount=_f(f[37]) if len(f) > 37 else 0.0,
                bid=[],
                ask=[],
                extra=extra,
            )
            quotes.append(self.normalize_quote(q))
        if not quotes:
            raise SourceDeprecated(
                "腾讯外部市场返回空数据（接口可能已改版）",
                context={"source": self.source_name, "sample": text[:200]},
            )
        return quotes


class HkSource(TencentExternalSource):
    """港股实时行情（腾讯协议，代码前缀 ``hk``）。

    .. note::
        旧实现直接复用 A 股解析，会把港股字段 [9..18] 误当五档买卖盘，
        得到垃圾盘口。本实现改用外部市场布局，已修正。
    """

    CURRENCY = "HKD"

    @property
    def source_name(self) -> str:
        return HK

    def build_url(self, symbols: Sequence[str], **kwargs: Any) -> str:
        codes = ",".join(s if s.lower().startswith("hk") else f"hk{s}" for s in symbols)
        return f"{self.BASE}{codes}"


class UsSource(TencentExternalSource):
    """美股实时行情（腾讯协议，代码前缀 ``us``）。

    例：``usAAPL`` / ``us00700``（港股代码在美股二次上市）。
    """

    CURRENCY = "USD"

    @property
    def source_name(self) -> str:
        return US

    def build_url(self, symbols: Sequence[str], **kwargs: Any) -> str:
        codes = ",".join(s if s.lower().startswith("us") else f"us{s}" for s in symbols)
        return f"{self.BASE}{codes}"


# --------------------------------------------------------------------------- #
# 日 K 线
# --------------------------------------------------------------------------- #
class KlineSource(BaseWebSource):
    """日 K 线（腾讯 ifzq 接口）。"""

    BASE = "https://ifzq.gtimg.cn/appstock/app/fqkline/get"
    encoding = "utf-8"  # 腾讯 ifzq JSON 为 UTF-8
    PERIODS = {
        "day": "day",
        "week": "week",
        "month": "month",
        "1min": "m1",
        "5min": "m5",
        "15min": "m15",
        "30min": "m30",
        "60min": "m60",
    }

    @property
    def source_name(self) -> str:
        return KLINE

    def build_url(self, symbols: Sequence[str], **kwargs: Any) -> str:
        symbol = to_tencent_symbol(symbols[0])
        period_key = kwargs.get("period", "day")
        if period_key not in self.PERIODS:
            # P1 #13: 未知周期显式报错，不再静默回退 day（错误数据比报错更危险）
            raise ValueError(f"未知 K 线周期 {period_key!r}；可选: {sorted(self.PERIODS)}")
        period = self.PERIODS[period_key]
        count = int(kwargs.get("count", 320))
        # v5 PG4：实测超限静默截断（2000→640）甚至整体空返回（4000→null），
        # 钳到实测安全上限并告警——静默截断比明示降级更危险。
        # C9：单次请求仍钳制；需要更长历史时由 fetch_bars 按日期分段拉全量
        if count > TENCENT_KLINE_MAX:
            logger.warning(
                "腾讯 K 线 count=%d 超接口实测上限 %d，已钳制（fetch_bars 会自动分段取全量）",
                count,
                TENCENT_KLINE_MAX,
            )
            count = TENCENT_KLINE_MAX
        fq = kwargs.get("adjust", "qfq")
        end = str(kwargs.get("end") or "")  # C9：分段请求右边界（YYYY-MM-DD，可为空）
        param = f"{symbol},{period},,{end},{count},{fq}"
        return f"{self.BASE}?param={param}"

    def fetch_bars(
        self, symbol: str, *, period: str = "day", count: int = 320, adjust: str = "qfq"
    ) -> list[Bar]:
        """获取 K 线（返回 :class:`Bar`，非 :class:`Quote`）。

        ``symbol`` 可为裸代码（``600519``）或带前缀（``sh600519``）。

        C9（v5 PG8 落地）：``count`` 超过 :data:`TENCENT_KLINE_MAX` 时
        **自动按日期分段**请求并拼接去重，调用方拿到完整 ``count`` 根，
        不再被钳制截断。
        """
        symbol = to_tencent_symbol(symbol)  # 响应 data 键为腾讯格式，需先归一化

        def _fetch_page(seg: int, end: str | None) -> list[Bar]:
            url = self.build_url([symbol], period=period, count=seg, adjust=adjust, end=end)
            text = self._request_text(url, encoding="utf-8")
            return self.parse_bars(text, symbol)

        # v9 Q4-1：分页向前翻页逻辑上收至 _paginate.fetch_bars_paged（与 mkline 共用）
        return fetch_bars_paged(
            symbol=symbol,
            count=count,
            max_per_req=TENCENT_KLINE_MAX,
            fetch_page=_fetch_page,
            paging=True,
            empty_error="腾讯 K 线分段拉取全部失败",
            source=KLINE,
            log_label="腾讯 K 线",
        )

    def parse_bars(self, text: str, symbol: str) -> list[Bar]:
        """解析腾讯 fqkline 响应为 ``list[Bar]``。

        .. note::
            腾讯日 K（``day``）响应**不含成交额字段**，此时
            :class:`~tstdx.domain.models.Bar`.``amount`` 为 ``0.0``
            （不可得即置 0，不捏造）；分线（如 ``m5``）含 amount。
        """
        payload = decode_kline_payload(text, msg="K 线返回非 JSON", source=KLINE)
        data = (payload.get("data") or {}).get(symbol) or {}
        rows = data.get("qfqday") or data.get("day") or data.get("qfq") or []
        if not rows:
            # 有些响应把数组放在 data 下的一层
            for v in data.values():
                if isinstance(v, list) and v and isinstance(v[0], list):
                    rows = v
                    break
        # v9 Q4-1：行 → Bar 组装与按市场缩放上收至 _paginate（与 mkline 共用）
        bars = rows_to_bars(rows, symbol=symbol, with_amount=True)
        # A5：腾讯日 K / 分钟 K 接口对部分周期不返回 amount 字段，
        # 若整批 amount 恒为 0（区别于个别缺失），一次性 UserWarning，非逐行
        warn_amount_all_zero(bars, symbol)
        return bars

    def parse(self, text: str, symbols: Sequence[str], **kwargs: Any) -> list[Quote]:
        bars = self.parse_bars(text, to_tencent_symbol(symbols[0]))
        if not bars:
            return []
        last = bars[-1]
        return [
            Quote(
                code=to_tencent_symbol(symbols[0]),
                price=last.close,
                open=last.open,
                high=last.high,
                low=last.low,
                volume=last.volume,
                amount=last.amount,
            )
        ]


# --------------------------------------------------------------------------- #
# 集思录（需 cookie）
# --------------------------------------------------------------------------- #
class JslSource(BaseWebSource):
    """集思录（可转债 / 分级基金 / ETF）。

    多数接口需要登录 cookie，未配置时构造即报错（见基类 ``needs_cookie``）。
    """

    BASE = "https://www.jisilu.cn/data/cbnew/cb_list/"
    encoding = "utf-8"  # 集思录 JSON 为 UTF-8

    @property
    def source_name(self) -> str:
        return JSL

    def build_url(self, symbols: Sequence[str], **kwargs: Any) -> str:
        return kwargs.get("url") or self.BASE

    def parse(self, text: str, symbols: Sequence[str], **kwargs: Any) -> list[Quote]:
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise SourceDeprecated(
                "集思录返回非 JSON（可能需要 cookie 或已改版）",
                context={"source": JSL, "sample": text[:200]},
                cause=exc,
            ) from exc
        rows = payload.get("rows") or payload.get("data") or []
        quotes: list[Quote] = []
        for row in rows:
            cell = row.get("cell", row) if isinstance(row, dict) else row
            if not isinstance(cell, Mapping):
                continue
            quotes.append(
                self.normalize_quote(
                    Quote(
                        code=str(cell.get("bond_id") or cell.get("id") or ""),
                        price=_f(cell.get("price") or cell.get("curr_iss_amt")),
                        last_close=_f(cell.get("last_price") or cell.get("pre_price")),
                        volume=_i(cell.get("volume")),
                        amount=_f(cell.get("amount")),
                        extra={"name": cell.get("bond_nm", ""), "raw": dict(cell)},
                    )
                )
            )
        return quotes


# --------------------------------------------------------------------------- #
# 中行汇率
# --------------------------------------------------------------------------- #
class BocSource(BaseWebSource):
    """中国银行外汇牌价（HTML 表格）。"""

    BASE = "https://srh.bankofchina.com/search/whpj/search_cn.jsp"
    encoding = "utf-8"  # 中行牌价页为 UTF-8

    @property
    def source_name(self) -> str:
        return BOC

    def build_url(self, symbols: Sequence[str], **kwargs: Any) -> str:
        return self.BASE

    def fetch_rates(self) -> list[dict[str, Any]]:
        text = self._request_text(self.BASE, encoding="utf-8")
        return self.parse_rates(text)

    @staticmethod
    def parse_rates(html: str) -> list[dict[str, Any]]:
        rows = re.findall(r"<tr>(.*?)</tr>", html, re.S)
        out: list[dict[str, Any]] = []
        for row in rows:
            cells = [
                re.sub(r"<[^>]+>", "", c).strip()
                for c in re.findall(r"<td.*?>(.*?)</td>", row, re.S)
            ]
            if len(cells) >= 6 and cells[0]:
                out.append(
                    {
                        "currency": cells[0],
                        "buy_rate": _f(cells[1]),
                        "cash_buy_rate": _f(cells[2]),
                        "sell_rate": _f(cells[3]),
                        "cash_sell_rate": _f(cells[4]),
                        "middle_rate": _f(cells[5]),
                        "publish_time": cells[6] if len(cells) > 6 else "",
                    }
                )
        return out

    def parse(self, text: str, symbols: Sequence[str], **kwargs: Any) -> list[Quote]:
        # 汇率不属于 Quote 语义，返回空；请用 fetch_rates()
        return []
