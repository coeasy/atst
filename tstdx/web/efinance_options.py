# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""ETF / 股指期权行情适配器（efinance 对标，东财 push2 / push2his 后端）。

期权 secid 格式沿用东财：``市场段.合约代码``，三个市场段：

===================  ===================================================
市场段              说明
===================  ===================================================
``m:10``           上证50ETF期权（上交所，674 合约）
``m:11``           沪深300股指期权（中金所，686 合约）
``m:12``           深证100ETF期权（深交所，452 合约）
===================  ===================================================

与期货的 :class:`~tstdx.web.efinance_deriv.EastmoneyFuturesSource` 区别：
期权合约含认购 / 认沽方向字段（f303），支持期权链查询（按市场段筛选）。

.. note::
   期权 K 线与逐笔成交接口在 push2 上不可用（2026-09 实测），仅分时
   和实时快照可用。结构不匹配时抛 :class:`~tstdx.errors.SourceDeprecated`。
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from ..errors import SourceDeprecated, WebSourceError
from ._base_em import _EastmoneyJson
from .base import num_f, num_i

__all__ = ["EastmoneyOptionsSource", "OPTIONS_MARKETS"]

#: 期权各市场段（东财 secid 前缀）
OPTIONS_MARKETS = {
    "10": "上证50ETF期权",
    "11": "沪深300股指期权",
    "12": "深证100ETF期权",
}

_PUSH2_HOSTS = (
    "https://push2.eastmoney.com",
    "https://92.push2.eastmoney.com",
    "https://push2delay.eastmoney.com",
)

_CLIST_FIELDS = "f12,f13,f14,f3,f43,f44,f45,f46,f47,f48,f60,f104,f105,f106"
_SNAPSHOT_FIELDS = (
    "f12,f13,f14,f43,f44,f45,f46,f47,f48,f57,f58,f60,"
    "f301,f302,f303,f304,f305,f306,f307,f308,f309,f310"
)

_OPTIONS_FS = ",".join(f"m:{m}" for m in OPTIONS_MARKETS)


def _s(value: Any) -> str:
    return "" if value is None else str(value)


class EastmoneyOptionsSource(_EastmoneyJson):
    """ETF / 股指期权行情（东财 push2 后端）。

    Quick start::

        from tstdx.web.efinance_options import EastmoneyOptionsSource
        src = EastmoneyOptionsSource()
        contracts = src.fetch_contract_list(market="11")       # 沪深300期权列表
        snapshot  = src.fetch_snapshot("11.IO2609-C-3900")     # 单合约快照
        trends    = src.fetch_trends("11.IO2609-C-3900")       # 当日分时
        src.close()
    """

    HOSTS = _PUSH2_HOSTS
    JSON_LABEL = "期权push2"

    @property
    def source_name(self) -> str:
        return "eastmoney"

    def build_url(self, symbols: Sequence[str], **kwargs: Any) -> str:
        raise NotImplementedError("EastmoneyOptionsSource 为数据型源")

    def parse(self, text: str, symbols: Sequence[str], **kwargs: Any) -> list[Any]:
        return []

    # -- 合约列表 ----------------------------------------------------------- #
    def fetch_contract_list(
        self,
        *,
        market: str = "",
        size: int = 200,
        page: int = 1,
    ) -> list[dict[str, Any]]:
        """期权合约列表（按市场段筛选）。

        Parameters
        ----------
        market:
            市场段（``"10"`` / ``"11"`` / ``"12"``）；空串=全部三个市场。
        size:
            单页数量（上限 500）。
        page:
            页码。

        Returns
        -------
        ``[{"quote_id","code","name","market","change_pct",
        "price","high","low","open","volume","amount",
        "pre_close","open_pct","close_pct"}, ...]``

        其中 ``price`` / ``pre_close`` 为元（f43/f60 ÷100），
        ``change_pct`` 为百分数，``volume`` 为手，``amount`` 为元。
        """
        fs = f"m:{market}" if market else _OPTIONS_FS
        size = max(1, min(int(size), 500))
        page = max(1, int(page))
        path = (
            f"/api/qt/clist/get?pn={page}&pz={size}&fs={fs}"
            f"&fields={_CLIST_FIELDS}"
        )
        payload = self._get_json(path)
        data = payload.get("data") or {}
        rows = data.get("diff") or []
        if isinstance(rows, dict):
            rows = list(rows.values())
        out: list[dict[str, Any]] = []
        for r in rows:
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
                    "change_pct": num_f(r.get("f3")) / 100.0,
                    "price": num_f(r.get("f43")) / 100.0,
                    "high": num_f(r.get("f44")) / 100.0,
                    "low": num_f(r.get("f45")) / 100.0,
                    "open": num_f(r.get("f46")) / 100.0,
                    "volume": int(num_f(r.get("f47"))),
                    "amount": num_f(r.get("f48")),
                    "pre_close": num_f(r.get("f60")) / 100.0,
                    "open_pct": num_f(r.get("f105")) / 100.0,
                    "close_pct": num_f(r.get("f104")) / 100.0,
                }
            )
        return out

    # -- 合约快照 ----------------------------------------------------------- #
    def fetch_snapshot(self, quote_id: str) -> dict[str, Any]:
        """单只期权合约快照（含认购/认沽方向与行权价等扩展字段）。

        Parameters
        ----------
        quote_id:
            东财 secid（``"11.IO2609-C-3900"`` 或 ``"10.10010971"``）。
            仅传合约代码时自动补 ``11.`` 前缀。

        Returns
        -------
        ``{"quote_id","code","name","price","pre_close","open","high","low",
        "volume","amount","change_pct","option_type","option_price_raw",
        "f303","f304","f305","f306","f307","f308","f309","f310"}``

        其中 ``option_type``: 1=认购 / 2=认沽；
        ``option_price_raw`` 为原始价格（f301，非 ÷100）。
        """
        secid = quote_id if "." in quote_id else f"11.{quote_id}"
        path = f"/api/qt/stock/get?secid={secid}&fields={_SNAPSHOT_FIELDS}"
        payload = self._get_json(path)
        d = payload.get("data") or {}
        if not d:
            raise SourceDeprecated(
                "期权快照返回空", context={"source": "eastmoney", "secid": secid}
            )
        f303 = d.get("f303")
        option_type = num_i(f303) if f303 is not None else 0
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
            "amount": num_f(d.get("f48")),  # 期权快照 f48 即成交额
            "change_pct": num_f(d.get("f301")) / 100.0,
            "option_type": option_type,
            "option_type_label": {1: "认购", 2: "认沽"}.get(option_type, ""),
            "option_price_raw": num_f(d.get("f301")),
            "f303": _s(f303),
            "f304": _s(d.get("f304")),
            "f305": _s(d.get("f305")),
            "f306": _s(d.get("f306")),
            "f307": _s(d.get("f307")),
            "f308": _s(d.get("f308")),
            "f309": _s(d.get("f309")),
            "f310": _s(d.get("f310")),
        }

    # -- 分时趋势 ----------------------------------------------------------- #
    def fetch_trends(
        self,
        quote_id: str,
        *,
        ndays: int = 1,
    ) -> list[dict[str, Any]]:
        """期权当日分时走势（push2 trends2）。

        仅部分市场段可用（m:11 沪深300股指期权 2026-09 实测可用；
        m:10 上证50ETF期权返回空）。不可用时返回空列表。

        Parameters
        ----------
        quote_id:
            东财 secid（``"11.IO2609-C-3900"``）。
        ndays:
            取最近 N 天（默认 1，即当日）。

        Returns
        -------
        ``[{"datetime","open","close","high","low","volume","amount",
        "avg_price"}, ...]``
        """
        secid = quote_id if "." in quote_id else f"11.{quote_id}"
        path = (
            f"/api/qt/stock/trends2/get?secid={secid}"
            "&fields1=f1,f2,f3,f4,f5,f6,f7,f8"
            "&fields2=f51,f52,f53,f54,f55,f56,f57,f58"
            f"&iscr=0&ndays={max(1, int(ndays))}"
        )
        try:
            payload = self._get_json(path)
        except (WebSourceError, SourceDeprecated):
            return []
        data = payload.get("data") or {}
        rows = data.get("trends") or []
        out: list[dict[str, Any]] = []
        for r in rows:
            if not isinstance(r, str):
                continue
            parts = r.split(",")
            if len(parts) < 8:
                continue
            try:
                out.append(
                    {
                        "datetime": parts[0],
                        "open": num_f(parts[1]),
                        "close": num_f(parts[2]),
                        "high": num_f(parts[3]),
                        "low": num_f(parts[4]),
                        "volume": int(num_f(parts[5])),
                        "amount": num_f(parts[6]),
                        "avg_price": num_f(parts[7]),
                    }
                )
            except (ValueError, IndexError):
                continue
        return out
