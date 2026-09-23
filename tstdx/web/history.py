# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""历史行情 K 线适配器：新浪 / 东财双源（§33 扩展）。

接口事实（2026-08 真实抓包验证，tstdx 自有实现）:

**新浪历史 K 线** ``quotes.sina.cn/cn/api/json_v2.php/CN_MarketDataService.getKLineData``::

    [{"day":"2026-08-31 14:55:00","open":"1289.500","high":"1289.730",
      "low":"1288.700","close":"1289.010","volume":"63000",
      "amount":"81221914.1958"}, ...]
    // scale ∈ {5,15,30,60,120,240,1200}（1200 实测可用，480 为空）
    // 分线含 amount；日线（240）实测**无 amount 字段**
    // volume 已是股；datalen 为返回根数

**东财历史 K 线** ``push2his.eastmoney.com/api/qt/stock/kline/get``::

    {"data":{"klines":["2026-08-31,1297.99,1299.52,1305.00,1286.00,23248,3003033720.00",...]}}
    // 行 = date,open,close,high,low,volume,amount
    // klt ∈ {5,15,30,60,101(日)}；fqt ∈ {0不复权,1前复权,2后复权}
    // lmt=根数；end=截止日（YYYYMMDD 或 20500101 表示至今）
    // secid: 沪 1.xxxxx / 深·北 0.xxxxx / 港 116.xxxxx / 美 105|106|107.CODE
    // 量口径按市场：A 股「手」×100；港股/美股已是「股」不缩放
    //   （2026-09-01 实测：hk 1713100×438.4≈amount 7.55 亿、us 同理吻合）

与 :class:`~tstdx.web.adapters.KlineSource`（腾讯 fqkline）构成三源互备：
日线三源、分钟线三源（新浪/东财/腾讯 mkline）；港股/美股分钟 K 线由
东财源独家承接（腾讯 mkline 不支持 hk/us）。
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

from ..domain.models import Bar
from ..errors import SourceDeprecated, WebSourceError
from .base import (
    BaseWebSource,
    split_symbol,
    to_eastmoney_secid,
)
from .base import (
    num_f as _f,
)
from .sources import EASTMONEY, SINA

__all__ = [
    "SinaHistoryKlineSource",
    "EastmoneyHistoryKlineSource",
    "US_MARKET_SEGMENTS",
    "eastmoney_secid_for_market",
]


# --------------------------------------------------------------------------- #
# 新浪：历史 K 线
# --------------------------------------------------------------------------- #
class SinaHistoryKlineSource(BaseWebSource):
    """新浪历史 K 线（分钟 5–1200 / 日线，含分线成交额）。"""

    BASE = "https://quotes.sina.cn/cn/api/json_v2.php/CN_MarketDataService.getKLineData"
    encoding = "utf-8"  # 新浪 json_v2 响应为 UTF-8（W10）

    #: tstdx period → 新浪 scale（分钟值；日线=240）
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
        from .base import normalize_symbol

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
# 东财：历史 K 线
# --------------------------------------------------------------------------- #
#: 美股 secid 市场段候选：105=纳斯达克(NASDAQ)、106=纽交所(NYSE)、
#: 107=美交所(AMEX)。同一字母代码只挂在其中一个市场，逐个探测直至命中。
US_MARKET_SEGMENTS = ("105", "106", "107")


def eastmoney_secid_for_market(symbol: str) -> str | list[str]:
    """tstdx 符号 → 东财 secid（支持 hk/us 外部市场）。

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

    #: tstdx period → 东财 klt（1min=klt 1；5min–day 见东财 push2his 规范）
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
        from .base import normalize_symbol

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
