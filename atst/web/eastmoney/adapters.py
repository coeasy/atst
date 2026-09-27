# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""东财系 HTTP Web 行情适配器（实时行情 / 历史 K 线 / 融资融券 / 指数成分）。

从 ``atst.web.adapters``、``atst.web.history``、``atst.web.adapters_margin``
和 ``atst.web.adapters_index`` 拆分归组而来。继承链在本 provider 内闭合：

- :class:`EastmoneySource` / :class:`EastmoneyHistoryKlineSource` 继承
  :class:`~atst.web.base._EastmoneyJson` 或 :class:`~atst.web.base.BaseWebSource`
- :class:`EastmoneyMarginSource` / :class:`EastmoneyIndexConstituentsSource`
  继承 :class:`~atst.web.corporate.EastmoneyDataCenterSource`
  （后者本身继承 ``_EastmoneyJson``，同属东财域）。
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

from ...domain.models import Bar, Quote
from ...errors import SourceDeprecated, WebSourceError
from ..base import (
    BaseWebSource,
    _EastmoneyJson,
    normalize_symbol,
    split_symbol,
    to_eastmoney_secid,
)
from ..base import (
    num_f as _f,
)
from ..base import (
    num_i as _i,
)
from ..corporate import EastmoneyDataCenterSource
from ..sources import EASTMONEY, INDEX_CONS, MARGIN

__all__ = [
    "EastmoneySource",
    "EastmoneyHistoryKlineSource",
    "EastmoneyMarginSource",
    "EastmoneyIndexConstituentsSource",
    "US_MARKET_SEGMENTS",
    "eastmoney_secid_for_market",
    "INDEX_TYPE_MAP",
]


# --------------------------------------------------------------------------- #
# 东财：实时行情
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
# 东财：历史 K 线
# --------------------------------------------------------------------------- #

#: 美股 secid 市场段候选：105=纳斯达克(NASDAQ)、106=纽交所(NYSE)、
#: 107=美交所(AMEX)。同一字母代码只挂在其中一个市场，逐个探测直至命中。
US_MARKET_SEGMENTS = ("105", "106", "107")


def eastmoney_secid_for_market(symbol: str) -> str | list[str]:
    """atst 符号 → 东财 secid（支持 hk/us 外部市场）。

    Returns
    -------
    str:
        确定性映射（沪 ``1.x`` / 深·北 ``0.x`` / 港 ``116.x``）。
    list[str]:
        美股候选列表 ``["105.X", "106.X", "107.X"]``（字母代码的市场归属
        需探测：请求空市场返回 ``data=None``，命中市场返回 klines）。
    """
    m, code = split_symbol(symbol)
    if m == "hk":
        return f"116.{code}"
    if m == "us":
        return [f"{seg}.{code.upper()}" for seg in US_MARKET_SEGMENTS]
    return to_eastmoney_secid(symbol)


class EastmoneyHistoryKlineSource(BaseWebSource):
    """东财历史 K 线（push2his；含成交额，支持不复权/前/后复权）。

    支持 A 股 / 港股 / 美股：

    * A 股：secid 沪 ``1.x`` / 深·北 ``0.x``，量「手」→ ``×100`` 到股。
    * 港股：secid ``116.xxxxx``，量已是「股」。
    * 美股：secid 市场段（105/106/107）未知，逐个探测直至命中 klines。

    多主机容灾：主站对高频 IP 有断连风控（RemoteDisconnected），
    按 ``HOSTS`` 顺序 failover，全部失败才抛错。
    """

    #: 主站 → 备站（92 分流节点）→ 延时镜像（延时渠道，稳定性最高）
    HOSTS = (
        "https://push2his.eastmoney.com",
        "https://92.push2his.eastmoney.com",
        "https://push2delay.eastmoney.com",
    )
    #: build_url 的 URL 前缀（此前缺失——调用 build_url 运行时 AttributeError）
    BASE = "https://push2his.eastmoney.com"
    encoding = "utf-8"  # 东财 push2his JSON 为 UTF-8（W10）
    FIELDS1 = "f1,f2,f3,f4,f5,f6"
    FIELDS2 = "f51,f52,f53,f54,f55,f56,f57"  # date,open,close,high,low,vol,amount

    #: atst period → 东财 klt（1min=klt 1；5min–day 见东财 push2his 规范）
    KLTS = {"1min": 1, "5min": 5, "15min": 15, "30min": 30, "60min": 60, "day": 101}

    @property
    def source_name(self) -> str:
        return EASTMONEY

    def build_url(self, symbols: Sequence[str], **kwargs: Any) -> str:
        secid = (
            str(kwargs.get("secid"))
            if kwargs.get("secid")
            else to_eastmoney_secid(symbols[0] if symbols else "")
        )
        klt = int(kwargs.get("klt", 101))
        fqt = int(kwargs.get("fqt", 1))
        lmt = int(kwargs.get("count", 320))
        end = str(kwargs.get("end", "20500101"))
        return (
            f"{self.BASE}?secid={secid}&klt={klt}&fqt={fqt}&lmt={lmt}"
            f"&end={end}&fields1={self.FIELDS1}&fields2={self.FIELDS2}"
        )

    def fetch_bars(
        self,
        symbol: str,
        *,
        period: str = "day",
        count: int = 320,
        adjust: str = "qfq",
        end: str = "20500101",
    ) -> list[Bar]:
        """获取历史 K 线（含成交额）。

        Parameters
        ----------
        symbol:
            ``600519`` / ``sh600519`` / ``hk00700`` / ``usAAPL`` 均可。
        period:
            ``5min`` / ``15min`` / ``30min`` / ``60min`` / ``day``。
        count:
            返回根数。
        adjust:
            ``""`` 不复权 / ``qfq`` 前复权（默认）/ ``hfq`` 后复权。
        end:
            截止日 ``YYYYMMDD``；默认 ``20500101`` 表示至今。
        """
        sym = normalize_symbol(symbol)
        try:
            klt = self.KLTS[period]
        except KeyError:
            # P1 #13: 未知周期显式报错（旧实现静默回退 101=day）
            raise ValueError(f"未知 K 线周期 {period!r}；可选: {sorted(self.KLTS)}") from None
        try:
            fqt = {"": 0, "none": 0, "qfq": 1, "hfq": 2}[str(adjust).lower()]
        except KeyError:
            # P1 #13: 未知复权显式报错（旧实现静默回退 qfq，价格口径漂移）
            raise ValueError(f"未知复权方式 {adjust!r}；可选: ['', 'none', 'qfq', 'hfq']") from None
        self._check_deprecated()  # 深审 M13：下线检测前置（旧实现裸 get/post 绕过失败桶）
        self.rate_limiter.acquire(self.source_name)

        secid_or_list = eastmoney_secid_for_market(sym)
        candidates = secid_or_list if isinstance(secid_or_list, list) else [secid_or_list]
        mkt = sym[:2].lower()

        last_exc: Exception | None = None
        for secid in candidates:
            for host in self.HOSTS:
                url = (
                    f"{host}/api/qt/stock/kline/get?secid={secid}"
                    f"&klt={klt}&fqt={fqt}&lmt={count}&end={end}"
                    f"&fields1={self.FIELDS1}&fields2={self.FIELDS2}"
                )
                try:
                    text = self._request_text(
                        url,
                        encoding="utf-8",
                        err_msg="东财历史 K 线请求失败",
                        retries=0,
                    )
                    bars = self.parse_bars(text, sym)
                except (WebSourceError, SourceDeprecated) as exc:
                    # 4xx/解析类错误换 host 再试；断连类最终统一抛出
                    last_exc = exc
                    continue
                except Exception as exc:  # noqa: BLE001  断连/超时等传输错误
                    last_exc = WebSourceError(
                        f"东财历史 K 线传输失败: {exc}",
                        context={"source": EASTMONEY, "host": host},
                        cause=exc,
                    )
                    continue
                if bars:
                    return bars
                if not candidates[1:]:
                    # 单候选（A 股 / 港股）：空结果先换下一 host 复核，全部
                    # 主机皆空才定论（深审 M11：旧实现首个 host 空结果即返回
                    # []——主站被风控时的假 200 空响应会伪装成「无数据」）。
                    if not bars and host != self.HOSTS[-1]:
                        continue
                    if (
                        not bars
                        and mkt in ("hk", "us")
                        and "push2delay" in host
                        and last_exc is not None
                    ):
                        # 主站/备站均被风控断连、仅延时镜像可达且其不提供
                        # 历史 K 线——空结果不可信（渠道限制≠无数据），且
                        # 港股/美股无降级源，按第八轮原则抛可读错误而非静默 []。
                        raise WebSourceError(
                            "东财历史 K 线仅延时镜像可达（主站/备站疑似对本 IP 风控），"
                            "该渠道不提供历史 K 线，港股/美股亦无降级源，请稍后重试",
                            context={"source": EASTMONEY, "host": host, "symbol": sym},
                        )
                    return bars
                # 美股市场段探测：空 klines 换下一市场段（最后一个候选见上）
                break
        if last_exc is not None:
            raise last_exc
        # 全候选均成功返回但无数据（如美股代码在 105/106/107 均无 K 线）
        return []

    def parse_bars(self, text: str, symbol: str | None = None) -> list[Bar]:
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise SourceDeprecated(
                "东财历史 K 线非 JSON",
                context={"source": EASTMONEY, "sample": text[:120]},
                cause=exc,
            ) from exc
        rows = (payload.get("data") or {}).get("klines") or []
        # 量口径按市场：A 股「手」×100 到股；港股/美股已是「股」不缩放
        mkt = (symbol or "")[:2].lower() if symbol else ""
        vol_scale = 1.0 if mkt in ("hk", "us") else 100.0
        bars: list[Bar] = []
        for row in rows:
            parts = str(row).split(",")
            if len(parts) < 7:
                continue
            # [date, open, close, high, low, volume, amount]
            bars.append(
                Bar(
                    datetime=parts[0],
                    open=_f(parts[1]),
                    close=_f(parts[2]),
                    high=_f(parts[3]),
                    low=_f(parts[4]),
                    volume=int(_f(parts[5]) * vol_scale),
                    amount=_f(parts[6]),
                )
            )
        return bars

    def parse(self, text: str, symbols: Sequence[str], **kwargs: Any) -> list[Bar]:
        symbol = kwargs.get("symbol") or (symbols[0] if symbols else "")
        return self.parse_bars(text, symbol)


# --------------------------------------------------------------------------- #
# 东财：融资融券（个股明细）
# --------------------------------------------------------------------------- #

#: 报表字段 → 归一化字段（金额单位元，量纲照抄不做缩放）
_MARGIN_FIELD_MAP: tuple[tuple[str, str], ...] = (
    ("RZYE", "rzye"),
    ("RZMRE", "rzmre"),
    ("RZCHE", "rzche"),
    ("RZJME", "rzjme"),
    ("RQYE", "rqye"),
    ("RQYL", "rqyl"),
    ("RQMCL", "rqmcl"),
    ("RZRQYE", "rzrqye"),
    ("RZRQYECZ", "rzrqye_cz"),
    ("RZYEZB", "rzyezb"),
    ("SZ", "total_mv"),
    ("SPJ", "close"),
    ("ZDF", "pct_change"),
)


def _to_f(v: Any) -> float | None:
    if v in (None, "", "-"):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


class EastmoneyMarginSource(EastmoneyDataCenterSource):
    """东财融资融券个股明细（数据型源，``list[dict]``）。

    Quick start::

        from atst.web.eastmoney.adapters import EastmoneyMarginSource
        src = EastmoneyMarginSource()
        rows = src.fetch_margin("600519", days=10)   # -> list[dict]，DATE 倒序
        src.close()
    """

    def __init__(self, **kwargs: Any) -> None:
        super().__init__("RPTA_WEB_RZRQ_GGMX", **kwargs)

    @property
    def source_name(self) -> str:
        return MARGIN

    def fetch_margin(
        self,
        symbol: str,
        *,
        days: int = 0,
        page: int = 1,
        size: int = 20,
        all_pages: bool = False,
        max_pages: int = 50,
    ) -> list[dict[str, Any]]:
        """个股融资融券明细（``DATE`` 倒序）。

        Parameters
        ----------
        symbol:
            A 股代码（``600519`` / ``sh600519``）；须为两融标的，
            非标的返回空列表。
        days:
            取最近 N 个交易日（``0`` = 全部，受 ``page``/``all_pages`` 约束）。
        page / size / all_pages / max_pages:
            分页参数（语义同 :meth:`EastmoneyDataCenterSource.fetch_rows`）。
        """
        from ...domain.symbol import split_symbol

        _, code = split_symbol(symbol)
        rows = self.fetch_rows(
            filters=[f'scode="{code}"'],
            sort_columns="DATE",
            sort_types="-1",
            page=page,
            size=days if days else size,
            all_pages=all_pages,
            max_pages=max_pages,
        )
        if days > 0:
            rows = rows[:days]
        return rows

    # -- 解析 ---------------------------------------------------------------- #
    def parse_margin(self, text: str) -> list[dict[str, Any]]:
        """解析 datacenter JSON 文本 → 归一化行（罐头测试与离线复放用）。"""
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise SourceDeprecated(
                "融资融券报表返回非 JSON",
                context={"source": MARGIN, "sample": text[:200]},
                cause=exc,
            ) from exc
        return self._normalize(payload)

    def parse_rows(self, payload: Any) -> list[dict[str, Any]]:
        return self._normalize(payload)

    def _normalize(self, payload: Any) -> list[dict[str, Any]]:
        data = (payload or {}).get("result") or {}
        rows = data.get("data") or []
        out: list[dict[str, Any]] = []
        for r in rows:
            if not isinstance(r, dict):
                continue
            extra = {
                k: r[k]
                for k in r
                if k not in {src for src, _ in _MARGIN_FIELD_MAP}
                and k
                not in (
                    "DATE",
                    "SCODE",
                    "SECNAME",
                    "TRADE_MARKET",
                    "SECUCODE",
                    "TRADE_MARKET_CODE",
                    "KCB",
                    "MARKET",
                )
            }
            out.append(
                {
                    "date": str(r.get("DATE") or "")[:10],
                    "code": str(r.get("SCODE") or ""),
                    "name": str(r.get("SECNAME") or ""),
                    "market": str(r.get("MARKET") or ""),
                    **{dst: _to_f(r.get(src)) for src, dst in _MARGIN_FIELD_MAP},
                    "extra": extra,
                }
            )
        return out


# --------------------------------------------------------------------------- #
# 东财：指数成分股
# --------------------------------------------------------------------------- #

#: 指数代码 → 东财 ``RPT_INDEX_TS_COMPONENT`` 的 ``TYPE`` 值
#: （2026-09 实测计数 + 中证官网 XLS 交叉验证）。
INDEX_TYPE_MAP: dict[str, str] = {
    "000300": "1",  # 沪深300（300 只）
    "000016": "2",  # 上证50（50 只）
    "000905": "3",  # 中证500（500 只）
    "000688": "4",  # 科创50（50 只）
    "930050": "5",  # 中证A50（500 只）
    "000510": "6",  # 中证A500（500 只）
    "000852": "7",  # 中证1000（1000 只）
    "399850": "8",  # 深证50（50 只）
    "399330": "9",  # 深证100（100 只）
    "899050": "10",  # 北证50（50 只）
    "000010": "11",  # 上证180（180 只）
    "000903": "12",  # 中证A100（100 只）
    "932000": "13",  # 中证2000（2000 只）
}

#: 单页上限（datacenter-web 报表页容量）
_PAGE_SIZE = 500
#: 翻页安全上限（防御接口异常导致死循环）
_MAX_PAGES = 20


def _fopt(value: Any) -> float | None:
    """None 保留变体：指数成分可选字段（weight/pe 等）缺失时保持 None，不填充 0。"""
    return None if value is None else _f(value)


def _s(value: Any) -> str:
    return "" if value is None else str(value)


def _resolve_index_code(index: str) -> str:
    """``sh000300`` / ``000300.SH`` / ``000300`` → ``000300``。"""
    s = str(index).strip().lower()
    for market in ("sh", "sz", "bj"):
        if s.startswith(market):
            s = s[len(market) :]
            break
    return s.split(".")[0]


class EastmoneyIndexConstituentsSource(EastmoneyDataCenterSource):
    """东财指数成分股数据源。

    能力：``index_constituents``（指数成分股列表）。

    Quick start::

        from atst.web.eastmoney.adapters import EastmoneyIndexConstituentsSource
        src = EastmoneyIndexConstituentsSource()
        rows = src.fetch_constituents("000300")   # -> list[dict]（沪深300 约 300 只）
        src.close()
    """

    JSON_LABEL = "指数成分"

    def __init__(self, **kwargs: Any) -> None:
        super().__init__("RPT_INDEX_TS_COMPONENT", **kwargs)

    @property
    def source_name(self) -> str:
        return INDEX_CONS

    def fetch_constituents(self, index: str) -> list[dict[str, Any]]:
        """指数成分股列表（分页拉全量）。

        Parameters
        ----------
        index:
            指数代码（``000300`` / ``000905`` / ``930050`` 等），可带市场前缀
            （``sh000300`` / ``sz399330`` / ``bj899050``）。

        Returns
        -------
        ``list[dict]``，每条含 code / name / secucode / weight / industry /
        region / price / change_pct / pe / eps / roe / bps / total_shares /
        free_shares / free_cap / type；``weight`` 仅部分指数族提供
        （沪深300 / 上证50 / 中证500 / 科创50 有值，其余可能为 ``None``）。
        """
        code = _resolve_index_code(index)
        try:
            type_ = INDEX_TYPE_MAP[code]
        except KeyError:
            raise WebSourceError(
                f"未知指数代码: {code!r}；支持: {sorted(INDEX_TYPE_MAP)}",
                context={"source": INDEX_CONS, "index": index},
            ) from None

        rows: list[dict[str, Any]] = []
        page = 1
        while True:
            batch = self.fetch_rows(
                filters=[f'TYPE="{type_}"'],
                sort_columns="",
                sort_types="-1",
                page=page,
                size=_PAGE_SIZE,
            )
            rows.extend(batch)
            if not batch or len(batch) < _PAGE_SIZE or page >= _MAX_PAGES:
                break
            page += 1
        return [self._normalize(r) for r in rows]

    @staticmethod
    def _normalize(r: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "code": _s(r.get("SECURITY_CODE")),
            "name": _s(r.get("SECURITY_NAME_ABBR")),
            "secucode": _s(r.get("SECUCODE")),
            "weight": _fopt(r.get("WEIGHT")),
            "industry": _s(r.get("INDUSTRY")),
            "region": _s(r.get("REGION")),
            "price": _fopt(r.get("CLOSE_PRICE")),
            "change_pct": _fopt(r.get("CHANGE_RATE")),
            "pe": _fopt(r.get("PE")),
            "eps": _fopt(r.get("EPS")),
            "roe": _fopt(r.get("ROE")),
            "bps": _fopt(r.get("BPS")),
            "total_shares": _fopt(r.get("TOTAL_SHARES")),
            "free_shares": _fopt(r.get("FREE_SHARES")),
            "free_cap": _fopt(r.get("FREE_CAP")),
            "type": _s(r.get("TYPE")),
        }
