# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""新浪系 HTTP Web 行情适配器（实时行情 / 港股行情 / 历史 K 线 / 代码联想）。

从 ``atst.sources.adapters``、``atst.sources.adapters_ext`` 和 ``atst.sources.history``
拆分归组而来，继承链在本 provider 内闭合：

- :class:`SinaSource` → :class:`SinaHkSource`
- :class:`SinaHistoryKlineSource` / :class:`SuggestSource` 直接继承
  :class:`~atst.sources.base.BaseWebSource`。
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
from urllib.parse import quote

from ...diagnostics import WarningCode, record_warning
from ...domain.calendar import day_like_datetime
from ...domain.models import Bar, Level, Quote
from ...errors import SourceDeprecated, WebSourceError
from .._base_retry import _backoff_delay
from ..base import BaseWebSource, normalize_symbol, to_sina_symbol
from ..base import num_f as _f
from ..base import num_i as _i
from ..limits import SINA_KLINE_MAX, SINA_MARKET_PAGE_MAX
from ..sources import HK, SINA, SUGGEST

logger = logging.getLogger(__name__)

__all__ = [
    "SinaSource",
    "SinaHkSource",
    "SinaHistoryKlineSource",
    "SuggestSource",
]


# --------------------------------------------------------------------------- #
# 新浪：实时行情
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
        matches = list(pattern.finditer(text))
        empty_codes: list[str] = []  # 空 payload：源侧无此代码 / 停牌
        short_codes: list[str] = []  # 字段不足：结构漂移的坏行
        for match in matches:
            raw_code, payload = match.group(1), match.group(2)
            if not payload:
                # "无此代码"逐条跳过，不与"接口整体下线"混为一谈。
                empty_codes.append(raw_code)
                continue
            f = payload.split(",")
            if len(f) < 32:
                # 单条字段不足不再抛异常毁掉整批：跳过该行并告警，
                # 与 all_market 路径的缺字段行同口径（结果不完整但可用部分照常返回）。
                short_codes.append(raw_code)
                record_warning(
                    WarningCode.WEB_SINA_MARKET_FIELD_MISSING,
                    f"新浪响应字段不足: {len(f)} < 32，已跳过 {raw_code}；本批其余代码不受影响",
                    stacklevel=2,
                )
                continue
            q = Quote(
                code=raw_code,
                price=_f(f[3]),
                last_close=_f(f[2]),
                open=_f(f[1]),
                high=_f(f[4]),
                low=_f(f[5]),
                volume=_i(f[8]),
                amount=_f(f[9]),
                bid=self._levels(f, 10, 5, vol_first=True),
                ask=self._levels(f, 20, 5, vol_first=True),
                extra={"name": f[0], "date": f[30], "time": f[31]},
            )
            quotes.append(self.normalize_quote(q))
        if empty_codes:
            record_warning(
                WarningCode.WEB_SINA_MARKET_FIELD_MISSING,
                f"新浪 {empty_codes[:5]} 返回空报价（无此代码 / 停牌），已逐条跳过",
                stacklevel=2,
            )
        if not quotes:
            # 区分三种"零结果"：无任何匹配 = 接口可能已改版；有匹配但全短行 =
            # 结构漂移；有匹配但全空行 = 无此代码。reason 写进 context 供调用方分辨。
            if short_codes:
                reason = "short_rows"
            elif empty_codes:
                reason = "no_such_code"
            else:
                reason = "empty_response"
            raise SourceDeprecated(
                f"新浪返回的代码均无有效报价（reason={reason}，"
                f"字段不足={short_codes[:5]}，空报价={empty_codes[:5]}）；"
                "若请求的是有效代码则接口可能已改版或需要 Referer",
                context={
                    "source": SINA,
                    "reason": reason,
                    "sample": text[:200],
                    "short_codes": short_codes[:5],
                    "empty_codes": empty_codes[:5],
                },
            )
        return quotes

    @staticmethod
    def _levels(
        fields: Sequence[str], start: int, count: int = 5, vol_first: bool = True
    ) -> list[Level]:
        """从交错数组解析盘口档位。"""
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

    # -- 全市场（新浪 Market_Center.getHQNodeData 接口） --------------------- #
    ALL_MARKET_URL = (
        "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
        "Market_Center.getHQNodeData"
    )
    #: 节点标的总数（前置校验用）。实测 ``hs_a`` 5578 / ``hs_b`` 77 / ``sh_a``
    #: 2317 / ``sz_a`` 2906 / ``kcb`` 618 / ``cyb`` 1411；``bjs`` 与非法 node
    #: 返回 ``[]``（不是 0），故空数组按"可能无标的"处理，需结合首页判定。
    ALL_MARKET_COUNT_URL = (
        "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
        "Market_Center.getHQNodeStockCount"
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

            from atst.sources import create_source
            quotes = create_source("sina").fetch_all(node="hs_a", page_size=100)

        Parameters
        ----------
        node:
            新浪市场节点：``hs_a`` 沪深 A 股 / ``hs_b`` B 股 / ``cyb`` 创业板
            / ``sh_a`` 沪 A / ``sz_a`` 深 A 等。
        page_size:
            每页条数（默认 80；实测上限 100——``num`` 超过 100 时服务端只回
            100 行且不报错，故超限会被钳制为 100 并告警）。
        max_pages:
            页数上限（防失控）；``None`` 时取 ``DEFAULT_MAX_PAGES``
            （100 页）硬上限——P2 #1：翻页终止不再完全依赖服务端空页。
        workers:
            并发分页线程数（M3）：先取第 1 页探测，再按 ``workers`` 路并发
            拉取后续页；令牌桶在 `_request_text` 内统一控速，不会超配额。

        Notes
        -----
        拉分页前会**额外请求一次** ``Market_Center.getHQNodeStockCount`` 拿总数，
        用于区分三种此前同形的空结果：

        - 计数为 0（或计数与首页同时为空数组）→ 节点确实没有标的：
          发 ``web_sina_node_empty`` 告警并返回空列表（不是错误）。
        - 计数 > 0 但首页为空 → 上游异常回空：抛 :class:`SourceDeprecated`。
        - 计数可用时按总数推导页数（向上取整），取到的条数与总数不符会发
          ``web_sina_market_count_mismatch``；``max_pages`` 不足时给确定结论
          （``web_sina_page_limit`` 里写明缺多少条）。

        计数接口失败 / 返回非数字 / 结构异常 → 发 ``web_sina_count_unavailable``
        并降级为原来的分页探测行为，不会让能力整体失败。该计数请求只做一次、
        失败不重试；没有关闭开关（如需省这一次请求，直接改用 ``quotes()``
        批量查询自身代码表）。
        """
        # P2-4c（F11）：常量已上移 limits——不再跨 Provider 从 tencent.adapters 导入
        from ..limits import DEFAULT_MAX_PAGES

        effective_max_pages = DEFAULT_MAX_PAGES if max_pages is None else max(1, int(max_pages))
        workers = max(1, min(int(workers), 6))
        requested_page_size = int(page_size)
        page_size = max(1, min(requested_page_size, SINA_MARKET_PAGE_MAX))
        if page_size != requested_page_size:
            record_warning(
                WarningCode.WEB_SINA_PAGE_SIZE_CLAMPED,
                f"新浪全市场 fetch_all(node={node}) page_size={requested_page_size} "
                f"超出单页上限 {SINA_MARKET_PAGE_MAX}（实测 num 超限只回 100 行），已钳制",
                stacklevel=2,
            )

        lock = threading.Lock()
        # 页码 → (被丢弃行数, 缺失字段名)；join 后统一告警，避免并发重复刷屏。
        field_issues: dict[int, tuple[int, tuple[str, ...]]] = {}

        # -- 前置计数校验：只请求一次；失败降级，绝不让能力整体失败 ---------- #
        total, count_empty = self._count_node(node)
        if total is None and not count_empty:
            record_warning(
                WarningCode.WEB_SINA_COUNT_UNAVAILABLE,
                f"新浪全市场 fetch_all(node={node}) 计数接口不可用（失败/非数字/结构异常），"
                f"已降级为分页探测：空结果无法区分'节点无标的'与'上游异常'，"
                f"页数也无法由总数推导",
                stacklevel=2,
            )
        if total == 0:
            record_warning(
                WarningCode.WEB_SINA_NODE_EMPTY,
                f"新浪全市场 fetch_all(node={node}) 计数接口返回 0：该节点没有标的，返回空列表",
                stacklevel=2,
            )
            return []
        if total is not None:
            expected_pages = (total + page_size - 1) // page_size
            pages_allowed = min(effective_max_pages, expected_pages)
        else:
            expected_pages = 0
            pages_allowed = effective_max_pages

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
            quotes, dropped, missing = self._quotes_from_market_rows(rows, page=page)
            if dropped or missing:
                with lock:
                    field_issues[page] = (dropped, missing)
            return quotes

        def _emit_field_warning() -> None:
            if not field_issues:
                return
            dropped_total = sum(dropped for dropped, _ in field_issues.values())
            missing_keys = sorted({key for _, keys in field_issues.values() for key in keys})
            record_warning(
                WarningCode.WEB_SINA_MARKET_FIELD_MISSING,
                f"新浪全市场 fetch_all(node={node}) 缺字段记录：页码 {sorted(field_issues)}；"
                f"缺 symbol/非对象被丢弃 {dropped_total} 行；"
                f"缺 {missing_keys} 的行按 0 返回，结果不完整",
                stacklevel=3,
            )

        def _finalize(
            result: list[Quote], *, inferred_truncation: bool, failed: Sequence[int]
        ) -> None:
            """统一发字段 / 缺页 / 条数 / 页数上限告警。

            总数已知时给**确定结论**（缺多少条）；总数不可用时退回"末页仍为满页"
            的推断式告警，并说明无法确定缺多少条。
            """
            _emit_field_warning()
            if failed:
                record_warning(
                    WarningCode.WEB_SINA_PAGES_MISSING,
                    f"新浪全市场 fetch_all(node={node}) 以下分页拉取失败（已含重试与补拉），"
                    f"结果缺页: {sorted(failed)}",
                    stacklevel=3,
                )
            if total is not None:
                expected_min = min(total, page_size * pages_allowed)
                if len(result) < expected_min:
                    record_warning(
                        WarningCode.WEB_SINA_MARKET_COUNT_MISMATCH,
                        f"新浪全市场 fetch_all(node={node}) 条数与总数不符：计数={total}，"
                        f"硬上限内应取 {expected_min} 条，实取 {len(result)} 条，"
                        f"缺 {expected_min - len(result)} 条",
                        stacklevel=3,
                    )
                if expected_pages > effective_max_pages:
                    record_warning(
                        WarningCode.WEB_SINA_PAGE_LIMIT,
                        f"新浪全市场 fetch_all(node={node}) 总数={total} 需 {expected_pages} 页"
                        f"（每页 {page_size}），max_pages={effective_max_pages} 只允许取 "
                        f"{pages_allowed} 页，确定缺少 {total - len(result)} 条；请调大 max_pages",
                        stacklevel=3,
                    )
            elif inferred_truncation:
                record_warning(
                    WarningCode.WEB_SINA_PAGE_LIMIT,
                    f"新浪全市场 fetch_all(node={node}) 已达 max_pages={effective_max_pages} "
                    f"且末页仍为满页，结果可能被截断（计数不可用，无法确定缺多少条）；"
                    f"请调大 max_pages",
                    stacklevel=3,
                )

        # 第 1 页：探测 + 短页直接返回（避免并发空转）
        first = _fetch_rows(1)
        if not first:
            if total is not None:
                raise SourceDeprecated(
                    f"新浪全市场 node={node} 计数={total} 但首页为空（上游异常回空）",
                    context={"source": SINA, "node": node, "count": total},
                )
            if count_empty:
                record_warning(
                    WarningCode.WEB_SINA_NODE_EMPTY,
                    f"新浪全市场 fetch_all(node={node}) 计数与首页均为空数组："
                    f"该节点（或 node 名）没有标的，返回空列表（不是上游异常）",
                    stacklevel=2,
                )
                return []
            raise SourceDeprecated("新浪全市场返回空", context={"source": SINA, "node": node})
        if len(first) < page_size:
            _finalize(first, inferred_truncation=False, failed=())
            return first
        if pages_allowed == 1:
            _finalize(
                first,
                inferred_truncation=(total is None and effective_max_pages == 1),
                failed=(),
            )
            return first

        out: dict[int, list[Quote]] = {1: first}
        stop = threading.Event()
        next_page = itertools.count(2)
        last_valid = [pages_allowed + 1]
        failed_pages: list[int] = []  # v5 PG7：重试后仍失败的页码（结束后补拉+告警）

        def _fetch_page_with_retry(page: int) -> list[Quote]:
            """单页退避重试（v5 PG7）：SourceDeprecated（结构下线）不重试，
            其余 WebSourceError（429 限速等待超时/读超时/断连）退避重试 2 次。"""
            last_exc: WebSourceError | None = None
            for attempt in range(3):
                if attempt:
                    # P1-8/F10：自写退避收口到 _base_retry._backoff_delay
                    # （指数退避 + 抖动封顶，均值同旧 0.5s/1.0s 节奏）
                    time.sleep(_backoff_delay(attempt - 1))
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
                if page > pages_allowed:
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
        _finalize(
            result,
            inferred_truncation=(
                total is None
                and last_valid[0] == pages_allowed + 1
                and len(out.get(pages_allowed, ())) >= page_size
            ),
            failed=failed_pages,
        )
        if not result:
            raise SourceDeprecated("新浪全市场返回空", context={"source": SINA, "node": node})
        return result

    def _count_node(self, node: str) -> tuple[int | None, bool]:
        """查询 node 的标的总数（``getHQNodeStockCount``），用于分页前置校验。

        Returns
        -------
        tuple[int | None, bool]
            ``(N, False)``：拿到可用总数（N >= 0）；
            ``(None, True)``：上游明确返回空数组 ``[]``——实测 ``bjs`` 与非法
            node 都是空数组，需结合首页再判定"节点确实无标的"；
            ``(None, False)``：计数不可用（网络失败 / 非数字 / 非预期结构）。

        计数只用于校验与页数推导，绝不因为计数失败让整个能力失败；
        只发一次请求且不重试，避免拖慢全市场。
        """
        url = f"{self.ALL_MARKET_COUNT_URL}?node={quote(node, safe='')}"
        try:
            text = self._request_text(url, encoding="gbk")
        except (WebSourceError, OSError, ValueError, TypeError):
            return None, False
        try:
            payload = json.loads(text)
        except (json.JSONDecodeError, TypeError):
            return None, False
        if isinstance(payload, bool):
            return None, False
        if isinstance(payload, list):
            return (None, True) if not payload else (None, False)
        if isinstance(payload, int):
            return (payload, False) if payload >= 0 else (None, False)
        if isinstance(payload, str):
            stripped = payload.strip()
            return (int(stripped), False) if stripped.isdigit() else (None, False)
        return None, False

    #: 全市场行必须出现的字段。缺 ``symbol`` 的行整行不可用；其余缺字段按 0 返回。
    MARKET_REQUIRED_FIELDS: tuple[str, ...] = (
        "symbol",
        "trade",
        "settlement",
        "open",
        "high",
        "low",
        "volume",
        "amount",
    )

    def _quotes_from_market_rows(
        self, rows: list[Any], *, page: int
    ) -> tuple[list[Quote], int, tuple[str, ...]]:
        """校验并转换一页记录：缺 ``symbol`` 丢弃，过半不可用即判结构变更。"""
        quotes: list[Quote] = []
        dropped = 0
        missing: set[str] = set()
        for r in rows:
            if not isinstance(r, Mapping) or not str(r.get("symbol") or "").strip():
                dropped += 1
                continue
            missing.update(key for key in self.MARKET_REQUIRED_FIELDS if key not in r)
            quotes.append(self._quote_from_market_row(r))
        if rows and dropped * 2 >= len(rows):
            raise SourceDeprecated(
                "新浪全市场页面过半记录不可用（缺 symbol 或非对象）",
                context={"source": SINA, "page": page, "dropped": dropped, "rows": len(rows)},
            )
        return quotes, dropped, tuple(sorted(missing))

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
# 新浪：历史 K 线
# --------------------------------------------------------------------------- #
class SinaHistoryKlineSource(BaseWebSource):
    """新浪历史 K 线（分钟 5–1200 / 日线，含分线成交额）。"""

    BASE = "https://quotes.sina.cn/cn/api/json_v2.php/CN_MarketDataService.getKLineData"
    encoding = "utf-8"  # 新浪 json_v2 响应为 UTF-8（W10）

    #: atst period → 新浪 scale（分钟值；日线=240）
    #:
    #: 这里没有 ``"1min"``：新浪这个端点最细就是 5 分钟，旧表用 ``"1min": 5``
    #: 把 1 分钟请求悄悄换成了 5 分钟线——周期拼写里的分钟数必须等于它请求的
    #: scale，做不到就说"这一面不服务"（第 19 轮，G15）。
    SCALES = {
        "5min": 5,
        "15min": 15,
        "30min": 30,
        "60min": 60,
        "120min": 120,
        "day": 240,
        "1200min": 1200,
    }

    @property
    def source_name(self) -> str:
        return SINA

    def build_url(self, symbols: Sequence[str], **kwargs: Any) -> str:
        symbol = symbols[0] if symbols else ""
        scale = int(kwargs.get("scale", 240))
        datalen = int(kwargs.get("count", 320))
        return f"{self.BASE}?symbol={symbol}&scale={scale}&ma=no&datalen={datalen}"

    def fetch_bars(
        self,
        symbol: str,
        *,
        period: str = "day",
        count: int = 320,
        adjust: str = "",
    ) -> list[Bar]:
        """获取历史 K 线。

        Parameters
        ----------
        symbol:
            ``600519`` / ``sh600519`` 均可。
        period:
            ``5min`` / ``15min`` / ``30min`` / ``60min`` / ``120min`` /
            ``day`` / ``1200min``。本端点没有 1 分钟粒度，``1min`` 显式报错。
        count:
            返回根数。
        """
        if adjust not in ("", None):
            # 深审 M9：新浪历史接口只提供原始价——静默忽略 adjust 会把复权
            # 请求偷换成不复权数据（除权日附近价差可达数十元）。显式报错，
            # 让上层降级/路由到支持复权的东财源。
            raise WebSourceError(
                f"新浪历史 K 线不支持复权（adjust={adjust!r}）；请使用 source='eastmoney'",
                context={"source": SINA, "adjust": str(adjust)},
            )
        sym = normalize_symbol(symbol)
        try:
            scale = self.SCALES[period]
        except KeyError:
            # P1 #13: 未知周期显式报错——静默回退 240（day）会把周/月请求
            # 偷换成日线数据，错误数据比报错更危险
            raise ValueError(f"未知 K 线周期 {period!r}；可选: {sorted(self.SCALES)}") from None
        # 超限请求不是截断而是整体返回 null（JSON 裸 null）；旧实现把它直接当
        # 数组迭代，`for r in None` 抛裸 TypeError 被执行器收口成 E9000——调用方
        # 看不出是"请求超限"。单次钳到实测安全值并告警（该端点无日期游标、
        # 无法分段），需要更长历史请换源。
        if count > SINA_KLINE_MAX:
            logger.warning(
                "新浪 K 线 datalen=%d 超接口实测上限 %d，已钳制（该端点无法分段取全量，"
                "需要更长历史请改用 source='eastmoney' 或 provider='tencent'）",
                count,
                SINA_KLINE_MAX,
            )
            count = SINA_KLINE_MAX
        self._check_deprecated()  # 深审 M13：下线检测前置（旧实现裸 get/post 绕过失败桶）
        url = self.build_url([sym], scale=scale, count=count)
        return self.parse_bars(
            self._request_text(url, encoding="utf-8", err_msg="新浪历史 K 线请求失败")
        )

    def parse_bars(self, text: str) -> list[Bar]:
        try:
            rows = json.loads(text)
        except json.JSONDecodeError as exc:
            raise SourceDeprecated(
                "新浪历史 K 线非 JSON",
                context={"source": SINA, "sample": text[:120]},
                cause=exc,
            ) from exc
        if not isinstance(rows, list):
            # 超限请求返回的是 JSON 裸 `null`（实测 datalen=1971+），也可能被
            # 换成错误对象——二者都不是行数组。旧实现直接迭代 `rows`，null 时
            # 抛裸 TypeError → E9000；这里按"接口已改版/超限"给一条可读的
            # SourceDeprecated，而不是让调用方去猜 `NoneType` 是什么意思。
            raise SourceDeprecated(
                "新浪历史 K 线返回不是行数组（疑似请求条数超限或接口改版）",
                context={
                    "source": SINA,
                    "sample": text[:120],
                    "payload_type": type(rows).__name__,
                    "hint": f"count 上限 {SINA_KLINE_MAX}，更长历史请改用 eastmoney/tencent",
                },
            )
        bars: list[Bar] = []
        for r in rows:
            if not isinstance(r, dict) or "day" not in r:
                continue
            bars.append(
                Bar(
                    datetime=day_like_datetime(r.get("day", "")),
                    open=_f(r.get("open")),
                    high=_f(r.get("high")),
                    low=_f(r.get("low")),
                    close=_f(r.get("close")),
                    volume=int(_f(r.get("volume"))),  # 已是股
                    amount=_f(r.get("amount")),  # 分线才有；日线缺省 0
                )
            )
        return bars

    def parse(self, text: str, symbols: Sequence[str], **kwargs: Any) -> list[Bar]:
        return self.parse_bars(text)


# --------------------------------------------------------------------------- #
# 代码联想搜索（新浪）
# --------------------------------------------------------------------------- #
class SuggestSource(BaseWebSource):
    """证券代码联想搜索（新浪 smartbox 接口）。

    输入拼音 / 汉字 / 代码片段，返回候选证券列表。
    """

    BASE = "https://suggest3.sinajs.cn/suggest/type=11,13"

    @property
    def source_name(self) -> str:
        return SUGGEST

    def build_url(self, symbols: Sequence[str], **kwargs: Any) -> str:
        key = kwargs.get("key") or (symbols[0] if symbols else "")
        # 深审 M10：key 可含汉字/特殊字符——必须 percent-encode 后拼 URL
        return f"{self.BASE}&key={quote(str(key), safe='')}"

    def fetch_suggest(self, key: str, *, limit: int = 10) -> list[dict[str, str]]:
        """联想搜索。

        Parameters
        ----------
        key:
            查询串：拼音（``maotai``）/ 汉字（``茅台``）/ 代码（``600519``）。
        limit:
            返回候选上限。

        Returns
        -------
        ``[{"code": "600519", "name": "贵州茅台", "market": "sh"}, ...]``
        """
        url = self.build_url([], key=key)
        text = self._request_text(url, encoding="gbk")
        return self.parse_suggest(text, key=key, limit=limit)

    def parse_suggest(self, text: str, *, key: str = "", limit: int = 10) -> list[dict[str, str]]:
        """解析 ``var suggestvalue="..."`` 响应为候选列表。

        真实格式：分号分隔多条记录，每条逗号分隔——
        ``名称,类型码,代码,带市场代码,名称,...``（后续列可变）。
        """
        body = text
        if "=" in body:
            body = body.split("=", 1)[1]
        body = body.strip().strip(';"').strip('"').strip()
        if not body:
            return []
        out: list[dict[str, str]] = []
        for record in body.split(";"):
            record = record.strip()
            if not record:
                continue
            cols = record.split(",")
            if len(cols) < 4:
                continue
            name, code, full = cols[0], cols[2], cols[3].lower()
            # full 形如 sh600519 / sz000001：前 2 字母为市场，余下为代码
            if len(full) < 3 or not full[2:].isdigit():
                # 兜底：代码列自身必须是纯数字
                if not code.isdigit():
                    continue
                market, pure = "", code
            else:
                market, pure = full[:2], full[2:]
            if market not in ("sh", "sz", "bj", "hk", "us"):
                market = market or ""
            out.append(
                {
                    "code": pure,
                    "name": name,
                    "market": market,
                    "symbol": f"{market}{pure}",
                }
            )
            if len(out) >= limit:
                break
        return out

    def parse(self, text: str, symbols: Sequence[str], **kwargs: Any) -> list[Any]:
        return self.parse_suggest(text, key=str(kwargs.get("key", "")))
