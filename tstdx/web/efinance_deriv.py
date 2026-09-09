# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""期货 / 可转债（债券）Web 行情适配器（efinance ``futures`` / ``bond`` 模块对标）。

TDX 7727 扩展行情服务（期货 / 商品期权）在 2026-09 实测主站池整体不可达，
本模块改用**东财 push2 / push2his** 作为 Web 降级通路，对标 efinance 的
``futures`` 与 ``bond`` 公开能力：

====================  ==========================================
efinance 函数         本模块 / 门面方法
====================  ==========================================
``futures.get_futures_base_info``  :meth:`EastmoneyFuturesSource.fetch_base_info` / ``UnifiedQuoteAPI.futures_base_info``
``futures.get_realtime_quotes``     :meth:`EastmoneyFuturesSource.fetch_realtime` / ``UnifiedQuoteAPI.futures_realtime``
``futures.get_quote_history``       :meth:`EastmoneyFuturesSource.fetch_kline` / ``UnifiedQuoteAPI.futures_kline``
``futures.get_deal_detail``         :meth:`EastmoneyFuturesSource.fetch_deal_detail` / ``UnifiedQuoteAPI.futures_trades``
``bond.get_base_info``              :meth:`EastmoneyBondSource.fetch_base_info` / ``UnifiedQuoteAPI.bond_base_info``
``bond.get_realtime_quotes``        :meth:`EastmoneyBondSource.fetch_realtime` / ``UnifiedQuoteAPI.bond_realtime``
``bond.get_quote_history``          :meth:`EastmoneyBondSource.fetch_kline` / ``UnifiedQuoteAPI.bond_kline``
``bond.get_history_bill``           :meth:`EastmoneyBondSource.fetch_history_bill` / ``UnifiedQuoteAPI.bond_history_bill``
``bond.get_today_bill``             :meth:`EastmoneyBondSource.fetch_today_bill` / ``UnifiedQuoteAPI.bond_today_bill``
``bond.get_deal_detail``            :meth:`EastmoneyBondSource.fetch_deal_detail` / ``UnifiedQuoteAPI.bond_trades``
====================  ==========================================

期货 secid 格式沿用东财：``市场段.合约代码``（郑商所 ``115`` / 大商所 ``114`` /
上期所 ``113`` / 中金所 ``8`` / 上海能源 ``142``），与 efinance 的行情 ID
（``115.ZCM``）一致，可直接透传。

.. note::
   期货 / 债券的 push2 字段口径与 A 股存在差异，解析层均为容错实现，结构
   不匹配时抛 :class:`~tstdx.errors.SourceDeprecated`。
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from ..domain.models import Bar
from ..errors import SourceDeprecated, WebSourceError
from ._base_em import _EastmoneyJson
from .base import num_f, num_i, to_eastmoney_secid

__all__ = ["EastmoneyFuturesSource", "EastmoneyBondSource"]

#: 期货各交易所市场段（东财 secid 前缀）
_FUTURES_MARKETS = ("113", "114", "115", "142", "8")
_FUTURES_FS = ",".join(f"m:{m}" for m in _FUTURES_MARKETS)

_PUSH2_HOSTS = (
    "https://push2.eastmoney.com",
    "https://92.push2.eastmoney.com",
    "https://push2delay.eastmoney.com",
)
_PUSH2HIS_HOSTS = (
    "https://push2his.eastmoney.com",
    "https://92.push2his.eastmoney.com",
    "https://push2delay.eastmoney.com",
)

_CLIST_FIELDS = "f12,f13,f14,f3,f104,f105,f106,f128"
_SNAPSHOT_FIELDS = "f12,f13,f14,f43,f44,f45,f46,f47,f48,f57,f58,f60,f168,f169,f170"


def _s(value: Any) -> str:
    return "" if value is None else str(value)


class _Push2Base(_EastmoneyJson):
    """push2 / push2his 通用取数（复用 :meth:`_EastmoneyJson._get_json` 的主机 failover）。"""

    HOSTS = _PUSH2_HOSTS

    def _json(self, path: str, *, host: str | None = None) -> dict[str, Any]:
        return self._get_json(path, host=host)

    @staticmethod
    def _parse_klines(payload: Any) -> list[Bar]:
        """解析 push2his ``klines`` 字段（date,open,close,high,low,vol,amount）。"""
        data = (payload or {}).get("data") or {}
        rows = data.get("klines") or []
        bars: list[Bar] = []
        for row in rows:
            if not isinstance(row, str):
                continue
            parts = row.split(",")
            if len(parts) < 7:
                continue
            try:
                bars.append(
                    Bar(
                        datetime=parts[0],
                        open=num_f(parts[1]),
                        close=num_f(parts[2]),
                        high=num_f(parts[3]),
                        low=num_f(parts[4]),
                        volume=int(num_f(parts[5])),
                        amount=num_f(parts[6]),
                    )
                )
            except (ValueError, IndexError):
                continue
        return bars

    @staticmethod
    def _period_to_klt(period: str) -> int:
        table = {
            "1min": 1,
            "5min": 5,
            "15min": 15,
            "30min": 30,
            "60min": 60,
            "day": 101,
            "week": 102,
            "month": 103,
        }
        try:
            return table[period]
        except KeyError:
            raise ValueError(f"未知周期 {period!r}；可选: {sorted(table)}") from None

    @staticmethod
    def _adjust_to_fqt(adjust: str) -> int:
        table = {"": 0, "none": 0, "qfq": 1, "hfq": 2}
        try:
            return table[str(adjust).lower()]
        except KeyError:
            raise ValueError(f"未知复权 {adjust!r}；可选: ['', 'none', 'qfq', 'hfq']") from None


class EastmoneyFuturesSource(_Push2Base):
    """期货行情（东财 push2 / push2his 降级通路）。

    Quick start::

        from tstdx.web.efinance_deriv import EastmoneyFuturesSource
        src = EastmoneyFuturesSource()
        info = src.fetch_base_info()                 # -> list[dict] 全市场期货
        q    = src.fetch_realtime("115.ZCM")         # -> dict
        k    = src.fetch_kline("115.ZCM", period="day", count=200)
        t    = src.fetch_deal_detail("115.ZCM")      # -> list[dict]
        src.close()
    """

    JSON_LABEL = "期货push2"

    @property
    def source_name(self) -> str:
        return "eastmoney"

    def build_url(self, symbols: Sequence[str], **kwargs: Any) -> str:
        raise NotImplementedError("EastmoneyFuturesSource 为数据型源")

    def parse(self, text: str, symbols: Sequence[str], **kwargs: Any) -> list[Any]:
        return []

    # -- 基础信息（全市场期货） ---------------------------------------------- #
    def fetch_base_info(self) -> list[dict[str, Any]]:
        """全市场期货基础信息（代码 / 名称 / 市场 / 涨跌幅等）。"""
        path = f"/api/qt/clist/get?pn=1&pz=2000&fs={_FUTURES_FS}&fields={_CLIST_FIELDS}"
        payload = self._json(path)
        diff = ((payload.get("data") or {}).get("diff")) or []
        out: list[dict[str, Any]] = []
        for r in diff:
            if not isinstance(r, dict):
                continue
            mkt = _s(r.get("f13"))
            code = _s(r.get("f12"))
            out.append(
                {
                    "quote_id": f"{mkt}.{code}" if mkt and code else code,
                    "code": code,
                    "name": _s(r.get("f14")),
                    "market": mkt,
                    "change_pct": num_f(r.get("f3")),
                    "pre_close": num_f(r.get("f104")),
                    "open": num_f(r.get("f105")),
                    "price": num_f(r.get("f106")),
                    "market_type": _s(r.get("f128")),
                }
            )
        return out

    # -- 实时行情 ----------------------------------------------------------- #
    def fetch_realtime(self, quote_id: str) -> dict[str, Any]:
        """单只期货实时快照（五档 / 涨跌 / 涨跌幅，best-effort 口径）。"""
        secid = quote_id if "." in quote_id else f"115.{quote_id}"
        path = f"/api/qt/stock/get?secid={secid}&fields={_SNAPSHOT_FIELDS}"
        payload = self._json(path)
        d = payload.get("data") or {}
        if not d:
            raise SourceDeprecated(
                "期货快照返回空", context={"source": "eastmoney", "secid": secid}
            )
        # 期货价格字段普遍 ×100（f43 最新价 / f170 涨跌幅）
        return {
            "quote_id": secid,
            "code": _s(d.get("f12")),
            "name": _s(d.get("f14")),
            "price": num_f(d.get("f43")) / 100.0,
            "pre_close": num_f(d.get("f44")) / 100.0,
            "open": num_f(d.get("f45")) / 100.0,
            "high": num_f(d.get("f46")) / 100.0,
            "low": num_f(d.get("f47")) / 100.0,
            "volume": int(num_f(d.get("f48"))),
            "change_pct": num_f(d.get("f170")) / 100.0,
        }

    # -- K 线 --------------------------------------------------------------- #
    def fetch_kline(
        self,
        quote_id: str,
        *,
        period: str = "day",
        count: int = 320,
        adjust: str = "",
    ) -> list[Bar]:
        """期货历史 K 线（push2his）。"""
        secid = quote_id if "." in quote_id else f"115.{quote_id}"
        klt = self._period_to_klt(period)
        fqt = self._adjust_to_fqt(adjust)
        path = (
            f"/api/qt/stock/kline/get?secid={secid}&klt={klt}&fqt={fqt}"
            f"&lmt={count}&end=20500101"
            "&fields1=f1,f2,f3,f4,f5&fields2=f51,f52,f53,f54,f55,f56,f57"
        )
        last_exc: Exception | None = None
        for host in _PUSH2HIS_HOSTS:
            try:
                payload = self._json(path, host=host)
            except (WebSourceError, SourceDeprecated) as exc:
                last_exc = exc
                continue
            bars = self._parse_klines(payload)
            if bars:
                return bars
        if last_exc:
            raise last_exc
        return []

    # -- 成交明细 ----------------------------------------------------------- #
    def fetch_deal_detail(self, quote_id: str, *, max_count: int = 1000) -> list[dict[str, Any]]:
        """期货当日分时成交（push2 drgtx，best-effort）。"""
        secid = quote_id if "." in quote_id else f"115.{quote_id}"
        path = f"/api/qt/stock/drgtx/get?secid={secid}&maxcnt={max_count}"
        payload = self._json(path)
        rows = (payload.get("data") or {}).get("details") or []
        out: list[dict[str, Any]] = []
        for r in rows:
            if not isinstance(r, str):
                continue
            parts = r.split(",")
            if len(parts) < 4:
                continue
            out.append(
                {
                    "time": parts[0],
                    "price": num_f(parts[1]),
                    "volume": num_i(parts[2]),
                    "bs": parts[3] if len(parts) > 3 else "",
                }
            )
        return out


class EastmoneyBondSource(_Push2Base):
    """可转债 / 债券行情（交易所上市，复用 A 股 push2 / push2his 通路）。

    债券与 A 股同处沪 / 深市场，secid 沿用 ``1.代码`` / ``0.代码``；资金流
    复用 :class:`~tstdx.web.fundflow.EastmoneyFundFlowSource`。
    """

    JSON_LABEL = "债券push2"

    @property
    def source_name(self) -> str:
        return "eastmoney"

    def build_url(self, symbols: Sequence[str], **kwargs: Any) -> str:
        raise NotImplementedError("EastmoneyBondSource 为数据型源")

    def parse(self, text: str, symbols: Sequence[str], **kwargs: Any) -> list[Any]:
        return []

    def _secid(self, code: str) -> str:
        return to_eastmoney_secid(code)

    def fetch_realtime(self, codes: Sequence[str]) -> list[dict[str, Any]]:
        """批量债券实时快照。"""
        out: list[dict[str, Any]] = []
        for code in codes:
            secid = self._secid(code)
            path = f"/api/qt/stock/get?secid={secid}&fields={_SNAPSHOT_FIELDS}"
            try:
                payload = self._json(path)
            except (WebSourceError, SourceDeprecated):
                continue
            d = payload.get("data") or {}
            if not d:
                continue
            out.append(
                {
                    "code": _s(d.get("f12")),
                    "name": _s(d.get("f14")),
                    "price": num_f(d.get("f43")) / 100.0,
                    "pre_close": num_f(d.get("f44")) / 100.0,
                    "open": num_f(d.get("f45")) / 100.0,
                    "high": num_f(d.get("f46")) / 100.0,
                    "low": num_f(d.get("f47")) / 100.0,
                    "volume": int(num_f(d.get("f48"))),
                    "change_pct": num_f(d.get("f170")) / 100.0,
                }
            )
        return out

    def fetch_base_info(self, codes: Sequence[str]) -> list[dict[str, Any]]:
        """债券基础信息（批量实时快照的别名，对齐 efinance ``bond.get_base_info``）。"""
        return self.fetch_realtime(codes)

    #: 可转债市场段（沪 128 / 深 80）—— 零售最关注的债券集合
    _BOND_FS = "m:128,m:80"

    def fetch_all_base_info(self) -> list[dict[str, Any]]:
        """全市场债券基础信息（对标 efinance ``bond.get_all_base_info``）。

        通过 push2 全量列表枚举可转债（沪 / 深），返回与 :meth:`fetch_base_info`
        同构的 ``[{"code","name","market","change_pct","pre_close","price"}, ...]``。
        """
        path = (
            f"/api/qt/clist/get?pn=1&pz=5000&fs={self._BOND_FS}"
            f"&fields={_CLIST_FIELDS}"
        )
        payload = self._json(path)
        diff = ((payload.get("data") or {}).get("diff")) or []
        out: list[dict[str, Any]] = []
        for r in diff:
            if not isinstance(r, dict):
                continue
            mkt = _s(r.get("f13"))
            code = _s(r.get("f12"))
            out.append(
                {
                    "code": code,
                    "name": _s(r.get("f14")),
                    "market": mkt,
                    "change_pct": num_f(r.get("f3")) / 100.0,
                    "pre_close": num_f(r.get("f104")) / 100.0,
                    "price": num_f(r.get("f106")) / 100.0,
                }
            )
        return out

    def fetch_kline(
        self,
        code: str,
        *,
        period: str = "day",
        count: int = 320,
        adjust: str = "",
    ) -> list[Bar]:
        """债券历史 K 线（push2his）。"""
        secid = self._secid(code)
        klt = self._period_to_klt(period)
        fqt = self._adjust_to_fqt(adjust)
        path = (
            f"/api/qt/stock/kline/get?secid={secid}&klt={klt}&fqt={fqt}"
            f"&lmt={count}&end=20500101"
            "&fields1=f1,f2,f3,f4,f5&fields2=f51,f52,f53,f54,f55,f56,f57"
        )
        last_exc: Exception | None = None
        for host in _PUSH2HIS_HOSTS:
            try:
                payload = self._json(path, host=host)
            except (WebSourceError, SourceDeprecated) as exc:
                last_exc = exc
                continue
            bars = self._parse_klines(payload)
            if bars:
                return bars
        if last_exc:
            raise last_exc
        return []

    def fetch_history_bill(self, code: str, *, count: int = 10) -> list[dict[str, Any]]:
        """债券历史资金流（主力 / 超大单 / 大单 / 中单 / 小单）。"""
        from .fundflow import EastmoneyFundFlowSource

        src = EastmoneyFundFlowSource(client=self.client)
        try:
            return src.fetch_history(code, period="day", count=count)
        finally:
            src.close()

    def fetch_today_bill(self, code: str) -> dict[str, Any] | None:
        """债券当日资金流（单只五档分档）。"""
        from .fundflow import EastmoneyFundFlowSource

        src = EastmoneyFundFlowSource(client=self.client)
        try:
            rows = src.fetch_flow([code])
        finally:
            src.close()
        return rows[0] if rows else None

    def fetch_deal_detail(self, code: str, *, max_count: int = 1000) -> list[dict[str, Any]]:
        """债券当日分时成交（push2 drgtx，best-effort）。"""
        secid = self._secid(code)
        path = f"/api/qt/stock/drgtx/get?secid={secid}&maxcnt={max_count}"
        payload = self._json(path)
        rows = (payload.get("data") or {}).get("details") or []
        out: list[dict[str, Any]] = []
        for r in rows:
            if not isinstance(r, str):
                continue
            parts = r.split(",")
            if len(parts) < 4:
                continue
            out.append(
                {
                    "time": parts[0],
                    "price": num_f(parts[1]),
                    "volume": num_i(parts[2]),
                    "bs": parts[3] if len(parts) > 3 else "",
                }
            )
        return out
