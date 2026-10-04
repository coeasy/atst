# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""百度财经数据源适配器（实时行情 / K 线 / 分时 / 逐笔）。

从 ``atst.web.adapters_baidu`` 拆分归组而来，唯一类
:class:`BaiduSource` 直接继承 :class:`~atst.web.base.BaseWebSource`。
"""

from __future__ import annotations

import json
import math
import time as _time
from collections.abc import Sequence
from typing import Any
from urllib.parse import urlencode

from ...domain.models import Bar, Level, MinutePoint, Quote, Tick
from ...errors import SourceDeprecated, WebSourceError
from ..base import BaseWebSource
from ..base import num_f as _f
from ..sources import BAIDU

__all__ = ["BaiduSource"]

#: 规范周期拼写 → 百度 ktype。百度这个端点只有日/周/月三档，所以本表只收
#: :data:`atst.domain.period.CANONICAL_PERIODS` 里服务得起的那三格；别名由公开面
#: 用 :func:`~atst.domain.period.normalize_bar_period` 一次解掉，不再在这里手抄。
#: 旧表手抄了 6 个别名键，其中 ``"1m": 3`` 把域内意为"1 分钟"的写法解成了**月线**
#: ——帧合法但内容是另一个周期，属 G15 那类错数（第 19 轮）。
_KLINE_KTYPES: dict[str, int] = {"day": 1, "week": 2, "month": 3}
#: 服务端单页根数上限
_MAX_COUNT = 250
_MAX_HISTORY_COUNT = 10_000
_VALUATION_INDICATORS = frozenset({"总市值", "市盈率(TTM)", "市盈率(静)", "市净率", "市现率"})
_VALUATION_PERIODS = frozenset({"近一年", "近三年", "近五年", "近十年", "全部"})


def _validate_history_count(count: int) -> int:
    if (
        isinstance(count, bool)
        or not isinstance(count, int)
        or not 1 <= count <= _MAX_HISTORY_COUNT
    ):
        raise ValueError(f"count 必须在 1..{_MAX_HISTORY_COUNT} 之间")
    return count


def _ktype(period: str) -> int:
    try:
        return _KLINE_KTYPES[period]
    except KeyError:
        raise ValueError(
            f"百度 K 线不服务周期 {period!r}（这一面只有 "
            f"{sorted(_KLINE_KTYPES)}，分钟线请走 mkline/腾讯面）"
        ) from None


def _vol_to_shares(v: Any) -> int:
    """手 → 股。"""
    return int(round(_f(v) * 100.0))


def _parse_amount(v: Any, ori: Any) -> float:
    """分时 amount 字段：优先 oriAmount（元）；否则解析含『万/亿』字符串。"""
    if ori is not None:
        try:
            return float(ori)
        except (TypeError, ValueError):
            pass
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip()
    if not s:
        return 0.0
    try:
        if s.endswith("万"):
            return float(s[:-1]) * 10_000.0
        if s.endswith("亿"):
            return float(s[:-1]) * 100_000_000.0
        return float(s)
    except ValueError:
        return 0.0


def _pure_code(symbol: str) -> str:
    """去市场前缀（sh/sz/bj）取裸代码；仅 A 股。"""
    s = symbol.lower()
    if s[:2] in ("sh", "sz", "bj") and s[2:].isdigit():
        return s[2:]
    if s.isdigit():
        return s
    raise WebSourceError(
        f"百度财经仅支持 A 股（sh/sz/bj），收到 {symbol!r}",
        context={"source": BAIDU, "symbol": symbol},
    )


class BaiduSource(BaseWebSource):
    """百度财经数据源（finance.pae.baidu.com/selfselect）。

    能力：``kline``（日/周/月，含 MA5/MA10/MA20 指标）/ ``minute``（当日分时）
    / ``tick``（逐笔）/ ``quote``（五档快照）。

    Quick start::

        from atst.web.baidu.adapters import BaiduSource
        src = BaiduSource()
        bars   = src.fetch_kline("600519", count=320)     # -> list[Bar]
        minute = src.fetch_minute("600519")               # -> list[MinutePoint]
        ticks  = src.fetch_ticks("600519")                # -> list[Tick]
        quote  = src.fetch_quote("600519")                # -> Quote
        src.close()
    """

    BASE = "https://finance.pae.baidu.com/selfselect/getstockquotation"
    encoding = "utf-8"  # 百度 JSON 为 UTF-8

    @property
    def source_name(self) -> str:
        return BAIDU

    def build_url(self, symbols: Sequence[str], **kwargs: Any) -> str:
        """构造 URL（默认 quote 快照路径，供基类 :meth:`fetch` 使用）。"""
        code = _pure_code(symbols[0])
        group = str(kwargs.get("group", "quotation_kline_ab"))
        params = [
            f"code={code}",
            "stockType=ab",
            f"group={group}",
            "finClientType=pc",
        ]
        if group == "quotation_kline_ab":
            kt = _ktype(str(kwargs.get("period", "day")))
            count = kwargs.get("count", 250)
            if isinstance(count, bool):
                raise ValueError(f"count 必须在 1..{_MAX_HISTORY_COUNT} 之间")
            if not isinstance(count, int):
                count = int(count)
            count = _validate_history_count(count)
            end = int(kwargs.get("end_time") or _time.time())
            params += [f"ktype={kt}", "all=0", f"count={count}", f"end_time={end}"]
        else:
            params += ["all=1"]
        return self.BASE + "?" + "&".join(params)

    # -- 私有工具 --------------------------------------------------------- #
    def _get_json(self, url: str) -> Any:
        text = self._request_text(url, encoding="utf-8")
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise SourceDeprecated(
                "百度财经返回非 JSON",
                context={"source": BAIDU, "sample": text[:200]},
                cause=exc,
            ) from exc

    # -- K 线 ------------------------------------------------------------- #
    def fetch_kline(
        self,
        symbol: str,
        *,
        period: str = "day",
        count: int = 320,
        end_time: int | None = None,
    ) -> list[Bar]:
        """日/周/月 K 线（含 MA5/MA10/MA20 指标于 ``extra``）。

        Parameters
        ----------
        symbol:
            A 股代码（``600519`` / ``sh600519`` / ``sz301086``）。
        period:
            ``day`` / ``week`` / ``month``。
        count:
            返回根数；超过单页上限(250)自动分页向前翻页收集。
        end_time:
            起始游标（unix 秒）；缺省当前时间。用于断点续取。

        Returns
        -------
        旧→新排列的 ``list[Bar]``。指标 ma5/ma10/ma20 挂到 ``extra``。
        """
        _validate_history_count(count)
        code = _pure_code(symbol)
        et = int(end_time or _time.time())
        raw: list[Bar] = []
        while len(raw) < count:
            page_count = min(_MAX_COUNT, count - len(raw))
            url = self.build_url(
                [code],
                group="quotation_kline_ab",
                period=period,
                count=page_count,
                end_time=et,
            )
            page = self._parse_kline(self._get_json(url), code)
            if not page:
                break
            had_previous_page = bool(raw)
            oldest = page[0].extra["_baidu_time"]
            if had_previous_page and oldest >= et:
                # 上游忽略游标或重复返回同页时及时停止，避免分页空转。
                break
            raw.extend(page)
            et = oldest - 1  # 最旧 bar 减 1 秒向前翻页
            if len(page) < page_count:
                break
        # 跨页整体排序（旧→新）并截断到 count
        raw.sort(key=lambda b: b.extra["_baidu_time"])
        bars = raw[-count:]
        # 去内部游标键
        for b in bars:
            b.extra.pop("_baidu_time", None)
        return bars

    def fetch_valuation_history(
        self, symbol: str, *, indicator: str = "市盈率(TTM)", period: str = "近一年"
    ) -> list[dict[str, Any]]:
        """百度股市通估值历史，返回 ``[{date, value, indicator}]``（旧→新）。

        市值、PE(TTM)/静态、PB、市现率由上游分别提供。百度没有该图表的统一
        股息率或流通股本序列；数值保留上游原生单位，市值单位不做未经验证的换算。
        """
        code = _pure_code(symbol)
        if indicator not in _VALUATION_INDICATORS:
            raise ValueError(f"indicator 必须是 {sorted(_VALUATION_INDICATORS)}")
        if period not in _VALUATION_PERIODS:
            raise ValueError(f"period 必须是 {sorted(_VALUATION_PERIODS)}")
        params = {
            "openapi": "1",
            "dspName": "iphone",
            "tn": "tangram",
            "client": "app",
            "query": indicator,
            "code": code,
            "word": "",
            "resource_id": "51171",
            "market": "ab",
            "tag": indicator,
            "chart_select": period,
            "industry_select": "",
            "skip_industry": "1",
            "finClientType": "pc",
        }
        payload = self._get_json("https://gushitong.baidu.com/opendata?" + urlencode(params))
        try:
            result = payload["Result"][0]["DisplayData"]["resultData"]["tplData"]["result"]
            chart = result["chartInfo"][0]["body"]
        except (KeyError, IndexError, TypeError) as exc:
            raise SourceDeprecated(
                "百度估值历史响应结构不符合预期",
                context={"source": BAIDU, "symbol": code, "indicator": indicator},
                cause=exc,
            ) from exc
        if not isinstance(chart, list):
            raise SourceDeprecated(
                "百度估值历史未返回数据序列",
                context={"source": BAIDU, "symbol": code, "indicator": indicator},
            )
        out: list[dict[str, Any]] = []
        for pair in chart:
            if not isinstance(pair, (list, tuple)) or len(pair) < 2:
                continue
            try:
                value = float(pair[1])
            except (TypeError, ValueError):
                value = None
            if value is not None and not math.isfinite(value):
                value = None
            out.append({"date": str(pair[0]), "value": value, "indicator": indicator})
        return sorted(out, key=lambda row: row["date"])

    def _parse_kline(self, payload: Any, code: str) -> list[Bar]:
        res = payload.get("Result") or []
        if not isinstance(res, list):
            return []
        out: list[Bar] = []
        for row in res:
            k = row.get("kline") if isinstance(row, dict) else None
            if not isinstance(k, dict):
                continue
            out.append(
                Bar(
                    datetime=str(row.get("date", "")),
                    open=_f(k.get("open")),
                    high=_f(k.get("high")),
                    low=_f(k.get("low")),
                    close=_f(k.get("close")),
                    # 口径坑：volume 字段=成交额(元)；amount 字段=成交量(手)
                    volume=_vol_to_shares(k.get("amount")),
                    amount=_f(k.get("volume")),
                    extra={
                        "pre_close": _f(k.get("preClose")),
                        "change": _f(k.get("increase")),
                        "pct_change": _f(str(k.get("netChangeRatio")).rstrip("%")),
                        "turnover_rate": _f(k.get("turnoverratio")),
                        "ma5": _f((row.get("ma5") or {}).get("avgPrice")),
                        "ma10": _f((row.get("ma10") or {}).get("avgPrice")),
                        "ma20": _f((row.get("ma20") or {}).get("avgPrice")),
                        "_baidu_time": int(_f(row.get("time"), 0.0)),
                    },
                )
            )
        # 旧→新（接口分页可能交叉，这里统一排序去重）
        out.sort(key=lambda b: b.extra["_baidu_time"])
        seen: set[str] = set()
        uniq: list[Bar] = []
        for b in out:
            if b.datetime in seen:
                continue
            seen.add(b.datetime)
            uniq.append(b)
        return uniq

    def parse_bars(self, text: str, symbol: str, *, period: str = "day") -> list[Bar]:
        code = _pure_code(symbol)
        return self._parse_kline(self._loads(text), code)

    # -- 分时 ------------------------------------------------------------- #
    def fetch_minute(self, symbol: str) -> list[MinutePoint]:
        """当日 1 分钟分时序列（旧→新）。"""
        code = _pure_code(symbol)
        url = self.build_url([code], group="quotation_minute_ab")
        return self._parse_minute(self._get_json(url), code)

    def _parse_minute(self, payload: Any, code: str) -> list[MinutePoint]:
        res = payload.get("Result")
        if not isinstance(res, dict):
            return []
        out: list[MinutePoint] = []
        for p in res.get("priceinfo") or []:
            if not isinstance(p, dict):
                continue
            ts = str(p.get("datetime") or "")
            out.append(
                MinutePoint(
                    time=ts,
                    price=_f(p.get("price")),
                    avg_price=_f(p.get("avgPrice")),
                    volume=_vol_to_shares(p.get("volume")),
                    amount=_parse_amount(p.get("amount"), p.get("oriAmount")),
                )
            )
        return out

    def parse_minute(self, text: str, symbol: str) -> list[MinutePoint]:
        code = _pure_code(symbol)
        return self._parse_minute(self._loads(text), code)

    # -- 逐笔 ------------------------------------------------------------- #
    def fetch_ticks(self, symbol: str, *, limit: int = 200) -> list[Tick]:
        """当日逐笔成交明细（默认 200 条）。"""
        code = _pure_code(symbol)
        url = self.build_url([code], group="quotation_minute_ab")
        return self._parse_ticks(self._get_json(url), code, limit=limit)

    def _parse_ticks(self, payload: Any, code: str, *, limit: int = 200) -> list[Tick]:
        res = payload.get("Result")
        if not isinstance(res, dict):
            return []
        out: list[Tick] = []
        for d in (res.get("detailinfos") or [])[:limit]:
            if not isinstance(d, dict):
                continue
            flag = str(d.get("bsFlag", "")).upper()
            if flag == "B":
                direction = 0
            elif flag == "S":
                direction = 1
            else:
                direction = 2
            out.append(
                Tick(
                    time=str(d.get("formatTime") or d.get("time") or ""),
                    price=_f(d.get("price")),
                    volume=_vol_to_shares(d.get("volume")),
                    num=len(out),
                    buyorsell=direction,
                )
            )
        return out

    def parse_ticks(self, text: str, symbol: str, *, limit: int = 200) -> list[Tick]:
        code = _pure_code(symbol)
        return self._parse_ticks(self._loads(text), code, limit=limit)

    # -- 五档快照 ---------------------------------------------------------- #
    def fetch_quote(self, symbol: str) -> Quote:
        """五档快照（含分时收盘价、均价、涨跌、五档盘口）。"""
        code = _pure_code(symbol)
        url = self.build_url([code], group="quotation_minute_ab")
        return self._parse_quote(self._get_json(url), code)

    def _parse_quote(self, payload: Any, code: str) -> Quote:
        res = payload.get("Result")
        if not isinstance(res, dict):
            return Quote(code=code)
        cur = res.get("cur") or {}
        basic = res.get("basicinfos") or {}
        price = _f(cur.get("price"))
        increase = _f(cur.get("increase"))
        q = Quote(
            code=str(basic.get("code") or code),
            datetime=str(cur.get("time") or ""),
            price=price,
            last_close=round(price - increase, 4) if increase else 0.0,
            volume=_vol_to_shares(cur.get("totalVolume")),
            amount=_f(cur.get("totalAmount")),
        )
        q.extra = {
            "name": str(basic.get("name") or ""),
            "avg_price": _f(cur.get("avgPrice")),
            "pct_change": _f(str(cur.get("ratio")).rstrip("%")),
        }
        for lv in (res.get("buyinfos") or [])[:5]:
            if isinstance(lv, dict):
                q.bid.append(Level(_f(lv.get("bidprice")), _vol_to_shares(lv.get("bidvolume"))))
        for lv in (res.get("askinfos") or [])[:5]:
            if isinstance(lv, dict):
                q.ask.append(Level(_f(lv.get("askprice")), _vol_to_shares(lv.get("askvolume"))))
        return q

    # -- 基类出口 ---------------------------------------------------------- #
    def parse(self, text: str, symbols: Sequence[str], **kwargs: Any) -> list[Any]:
        """多态出口：按 kwargs 决定解析器（供 :meth:`fetch` 及直接 parse 调用）。"""
        code = _pure_code(symbols[0])
        kind = str(kwargs.get("kind") or kwargs.get("group", "quotation_kline_ab"))
        payload = self._loads(text)
        if "minute" in kind:
            return self._parse_minute(payload, code)
        if kind == "tick":
            return self._parse_ticks(payload, code)
        if kind == "quote":
            return [self._parse_quote(payload, code)]
        return self._parse_kline(payload, code)

    @staticmethod
    def _loads(text: str) -> Any:
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise SourceDeprecated(
                "百度财经返回非 JSON",
                context={"source": BAIDU, "sample": text[:200]},
                cause=exc,
            ) from exc
