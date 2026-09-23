# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""扩展 HTTP Web 适配器：分钟 K 线 / 当日分时 / 代码联想（§33 扩展）。

接口事实（来自公开接口与实际响应样本，tstdx 自有实现）:

**腾讯分钟 K 线** ``ifzq.gtimg.cn/appstock/app/kline/mkline``::

    {"code": 0, "data": {"qt": {...}, "m5": [
        ["202608311000", "1690.00", "1691.50", "1692.00", "1689.80",
         "1234.50", {}, "12.34"], ...]}}
    // 行结构: [datetime, open, close, high, low, volume(手), {}, ?]
    // 周期参数同时出现在路径与查询（mkline?param=sh600519,m5,,,N）

**腾讯当日分时** ``web.ifzq.gtimg.cn/appstock/app/minute/query``::

    {"code": 0, "data": {"sh600519": {"data": {"data": [
        "0930 1690.00 270 35045730.00", ...], "date": "20260831"},
        "qt": {...}}}}
    // 每点: "HHMM 价格 累计量(手) 累计额(元)"——量/额为**累计值**，
    // parse 时逐点差分还原为分钟增量。

**新浪代码联想** ``suggest3.sinajs.cn/suggest/type=11,13&key=...``::

    var suggestvalue="贵州茅台,11,600519,sh600519,贵州茅台,,贵州茅台,99,1,,;
    永茂泰,11,605208,sh605208,...";
    // 分号分隔多条记录；每条逗号分隔：名称,类型码,代码,带市场代码,...
    // 第 4 列形如 sh600519 / sz000001 / bj430047，直接给出市场归属。

.. warning::
   接口随时可能改版；全部适配器继承 :class:`~tstdx.web.base.BaseWebSource`
   的下线检测（连续失败达阈值抛 :class:`~tstdx.errors.SourceDeprecated`）。
"""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from typing import Any

from ..domain.models import Bar, MinutePoint
from ..errors import SourceDeprecated, WebSourceError
from ._paginate import decode_kline_payload, fetch_bars_paged, rows_to_bars
from .base import BaseWebSource, to_tencent_symbol
from .base import num_f as _f
from .limits import TENCENT_KLINE_MAX
from .sources import MINUTE, MINUTE_KLINE, SUGGEST

logger = logging.getLogger(__name__)

__all__ = [
    "MinuteKlineSource",
    "MinuteSource",
    "SuggestSource",
]

#: 规范周期拼写 → 腾讯 mkline 周期参数。本表只收 :data:`tstdx.domain.period
#: .CANONICAL_PERIODS` 的分钟档，且每一行都真的服务它键上写的那个周期
#: （``Nmin`` → ``mN``）。旧表另抄了 ``m1``/``m5``…五个别名键——别名归域内那份
#: 唯一词表管，公开面先规范再下发（第 19 轮，与 G13 同一条裁决）。
_MKLINE_PERIODS = {
    "1min": "m1",
    "5min": "m5",
    "15min": "m15",
    "30min": "m30",
    "60min": "m60",
}


def _mkline_period(period: str) -> str:
    """规范周期 → 腾讯 mkline 参数；未知值显式报错（P1 #13，不静默回退 m5）。"""
    try:
        return _MKLINE_PERIODS[period]
    except KeyError:
        raise ValueError(
            f"腾讯分钟 K 线不服务周期 {period!r}（这一面只有 {sorted(_MKLINE_PERIODS)}）"
        ) from None


# --------------------------------------------------------------------------- #
# 分钟 K 线（腾讯）
# --------------------------------------------------------------------------- #
class MinuteKlineSource(BaseWebSource):
    """分钟 K 线（腾讯 ifzq mkline 接口，**仅 A 股**）。

    .. note::
        该接口仅覆盖 A 股（``sh/sz/bj``）：港股 / 美股传入后腾讯返回
        ``code=-1`` 空数据，直接调用本源会抛出明确错误。港股 / 美股
        分钟 K 线请走 :class:`~tstdx.web.history.EastmoneyHistoryKlineSource`
        （东财 push2his，``hk`` secid=116.x、``us`` secid=105/106/107 探测）；
        :meth:`~tstdx.web.session.WebQuoteSession.klines` 已按市场自动路由。

        成交量单位：A 股腾讯返回「手」需 ``×100`` 到股。

        .. warning::
            腾讯 mkline 响应**不含成交额字段**，故 :class:`~tstdx.domain.models.Bar`
            的 ``amount`` 恒为 ``0.0``（合约语义：不可得即置 0，绝不捏造）。
    """

    BASE = "https://ifzq.gtimg.cn/appstock/app/kline/"
    encoding = "utf-8"  # 腾讯 ifzq JSON 为 UTF-8

    @property
    def source_name(self) -> str:
        return MINUTE_KLINE

    def build_url(self, symbols: Sequence[str], **kwargs: Any) -> str:
        symbol = to_tencent_symbol(symbols[0])
        period = _mkline_period(kwargs.get("period", "5min"))
        count = int(kwargs.get("count", 240))
        # v5 PG4：与 fqkline 同源实测口径——超限静默截断/空返回，钳制+告警
        if count > TENCENT_KLINE_MAX:
            logger.warning(
                "腾讯分钟 K 线 count=%d 超接口实测上限 %d，已钳制",
                count,
                TENCENT_KLINE_MAX,
            )
            count = TENCENT_KLINE_MAX
        return f"{self.BASE}mkline?param={symbol},{period},,,{count}"

    def fetch_bars(
        self,
        symbol: str,
        *,
        period: str = "5min",
        count: int = 240,
    ) -> list[Bar]:
        """获取分钟 K 线。

        Parameters
        ----------
        symbol:
            证券代码（``600519`` / ``sh600519`` / ``hk00700`` / ``usAAPL`` 均可，
            但 hk/us 分钟 K 线腾讯不支持，将抛出明确错误）。
        period:
            ``1min`` / ``5min`` / ``15min`` / ``30min`` / ``60min``。
        count:
            返回根数（默认 240 = 一个交易日 5 分钟线）。
        """
        symbol = to_tencent_symbol(symbol)

        def _fetch_page(seg: int, end: str | None) -> list[Bar]:
            del seg, end  # mkline 不支持 end 翻页，超限由 build_url 钳制并告警
            url = self.build_url([symbol], period=period, count=count)
            text = self._request_text(url, encoding="utf-8")
            return self.parse_bars(text, symbol, period=period)

        # v9 Q4-1：与 fqkline 共用的分页拉取器（paging=False：单请求语义逐字保留）
        return fetch_bars_paged(
            symbol=symbol,
            count=count,
            max_per_req=TENCENT_KLINE_MAX,
            fetch_page=_fetch_page,
            paging=False,
            empty_error=None,
            source=MINUTE_KLINE,
            log_label="腾讯分钟 K 线",
        )

    def parse_bars(self, text: str, symbol: str, *, period: str = "5min") -> list[Bar]:
        payload = decode_kline_payload(text, msg="分钟 K 线返回非 JSON", source=MINUTE_KLINE)
        code = payload.get("code")
        if code != 0:
            # 腾讯 mkline 对港股/美股返回 code=-1 空数据：分钟 K 线该源不支持
            if symbol[:2].lower() in ("hk", "us"):
                raise WebSourceError(
                    f"腾讯 mkline 不提供港股/美股分钟 K 线（symbol={symbol} 返回 "
                    f"code={code}）。可改用 sess.minute('{symbol}') 取当日分时，"
                    f"或 sess.klines('{symbol}', period='day') 取日 K。",
                    context={"source": MINUTE_KLINE, "symbol": symbol},
                )
            raise SourceDeprecated(
                f"分钟 K 线返回错误码 {code}",
                context={"source": MINUTE_KLINE},
            )
        data = payload.get("data") or {}
        node = data.get(symbol) or {}
        key = _mkline_period(period)
        rows = node.get(key) or node.get("mkline") or []
        if not rows:
            # 布局兜底：部分响应把行情数组直接放在 data 层
            rows = data.get(key) or data.get("mkline") or []
        if not rows and symbol[:2].lower() in ("hk", "us"):
            raise WebSourceError(
                f"腾讯 mkline 未返回港股/美股分钟 K 线数据（symbol={symbol}）。"
                f"可改用 sess.minute('{symbol}') 取当日分时。",
                context={"source": MINUTE_KLINE, "symbol": symbol},
            )
        # 成交量单位按市场区分：A 股腾讯返回「手」需 ×100 到股；
        # 港股/美股（若未来放开）直接为「股」不再缩放。
        # v9 Q4-1：行 → Bar 组装与按市场缩放上收至 _paginate（与 fqkline 共用；
        # with_amount=False：mkline 响应不含成交额字段，amount 恒 0.0）
        return rows_to_bars(rows, symbol=symbol, with_amount=False)

    def parse(self, text: str, symbols: Sequence[str], **kwargs: Any) -> list[Bar]:
        return self.parse_bars(
            text, to_tencent_symbol(symbols[0]), period=kwargs.get("period", "5min")
        )


# --------------------------------------------------------------------------- #
# 当日分时（腾讯）
# --------------------------------------------------------------------------- #
class MinuteSource(BaseWebSource):
    """当日分时（腾讯 minute/query 接口）。

    返回 ``list[MinutePoint]``；接口返回的量/额为**累计值**，
    解析时逐点差分还原为分钟增量。
    """

    BASE = "https://web.ifzq.gtimg.cn/appstock/app/minute/query"
    encoding = "utf-8"  # 腾讯 minute/query JSON 为 UTF-8

    @property
    def source_name(self) -> str:
        return MINUTE

    def build_url(self, symbols: Sequence[str], **kwargs: Any) -> str:
        symbol = to_tencent_symbol(symbols[0])
        return f"{self.BASE}?code={symbol}"

    def fetch_minute(self, symbol: str) -> list[MinutePoint]:
        """获取当日 1 分钟分时序列。"""
        symbol = to_tencent_symbol(symbol)
        url = self.build_url([symbol])
        text = self._request_text(url, encoding="utf-8")
        return self.parse_minute(text, symbol)

    def parse_minute(self, text: str, symbol: str) -> list[MinutePoint]:
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise SourceDeprecated(
                "分时返回非 JSON",
                context={"source": MINUTE, "sample": text[:200]},
                cause=exc,
            ) from exc
        if payload.get("code") != 0:
            raise SourceDeprecated(
                f"分时返回错误码 {payload.get('code')}",
                context={"source": MINUTE},
            )
        node = ((payload.get("data") or {}).get(symbol) or {}).get("data") or {}
        rows = node.get("data") or []
        date = str(node.get("date", ""))
        out: list[MinutePoint] = []
        prev_vol = 0.0
        prev_amt = 0.0
        for r in rows:
            parts = str(r).split()
            # W6: 守卫与消费宽度对齐——本行消费 parts[0..3]（累计额在第 4 列），
            # 旧守卫 ``< 3`` 会让恰好 3 列的行在 parts[3] 处 IndexError
            if len(parts) < 4:
                continue
            hhmm = parts[0]
            price = _f(parts[1])
            cum_vol = _f(parts[2]) * 100.0  # 手 → 股
            cum_amt = _f(parts[3])  # 已是元
            out.append(
                MinutePoint(
                    time=f"{date} {hhmm[:2]}:{hhmm[2:4]}" if date else hhmm,
                    price=price,
                    volume=int(max(cum_vol - prev_vol, 0)),
                    avg_price=0.0,
                    amount=max(cum_amt - prev_amt, 0.0),
                )
            )
            prev_vol, prev_amt = cum_vol, cum_amt
        return out

    def parse(self, text: str, symbols: Sequence[str], **kwargs: Any) -> list[MinutePoint]:
        return self.parse_minute(text, to_tencent_symbol(symbols[0]))


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
        from urllib.parse import quote

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
