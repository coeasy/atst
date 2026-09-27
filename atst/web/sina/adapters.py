# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""新浪系 HTTP Web 行情适配器（实时行情 / 港股行情 / 历史 K 线 / 代码联想）。

从 ``atst.web.adapters``、``atst.web.adapters_ext`` 和 ``atst.web.history``
拆分归组而来，继承链在本 provider 内闭合：

- :class:`SinaSource` → :class:`SinaHkSource`
- :class:`SinaHistoryKlineSource` / :class:`SuggestSource` 直接继承
  :class:`~atst.web.base.BaseWebSource`。
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
from ...domain.models import Bar, Level, Quote
from ...errors import SourceDeprecated, WebSourceError
from ..base import BaseWebSource, normalize_symbol, to_sina_symbol
from ..base import num_f as _f
from ..base import num_i as _i
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
                bid=self._levels(f, 10, 5, vol_first=True),
                ask=self._levels(f, 20, 5, vol_first=True),
                extra={"name": f[0], "date": f[30], "time": f[31]},
            )
            quotes.append(self.normalize_quote(q))
        if not quotes:
            raise SourceDeprecated(
                "新浪返回空数据（接口可能已改版或需要 Referer）",
                context={"source": SINA, "sample": text[:200]},
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

            from atst.web import create_source
            quotes = create_source("sina").fetch_all(node="hs_a", page_size=100)

        Parameters
        ----------
        node:
            新浪市场节点：``hs_a`` 沪深 A 股 / ``hs_b`` B 股 / ``cyb`` 创业板
            / ``sh_a`` 沪 A / ``sz_a`` 深 A 等。
        page_size:
            每页条数（新浪上限 100）。
        max_pages:
            页数上限（防失控）；``None`` 时取 ``DEFAULT_MAX_PAGES``
            （100 页）硬上限——P2 #1：翻页终止不再完全依赖服务端空页。
        workers:
            并发分页线程数（M3）：先取第 1 页探测，再按 ``workers`` 路并发
            拉取后续页；令牌桶在 `_request_text` 内统一控速，不会超配额。
        """
        # 避免循环 import：DEFAULT_MAX_PAGES 定义在 tencent.adapters
        from ..tencent.adapters import DEFAULT_MAX_PAGES

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
        bars: list[Bar] = []
        for r in rows:
            if not isinstance(r, dict) or "day" not in r:
                continue
            bars.append(
                Bar(
                    datetime=str(r.get("day", "")),
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
