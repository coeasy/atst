# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""行情高层门面（原生命名）。

定位：兼容门面（吸收自 pytdx 语义），生产建议用 :class:`~tstdx.facade.api.UnifiedQuoteAPI` / :class:`~tstdx.client.TdxClient`。

在 tstdx 自有客户端之上提供标准市场（A 股）、扩展市场（港股 / 美股 / 期货 /
外汇）与商品 / 期权三类高层门面，方法名统一为 tstdx 原生命名，不再沿用任何
第三方客户端的字段或方法约定。

方法映射（tstdx 原生）
----------------------
* :meth:`HqClient.bars`      → :meth:`~tstdx.client.TdxClient.bars`
* :meth:`HqClient.quotes`    → :meth:`~tstdx.client.TdxClient.quotes`
* :meth:`ExHqClient.ex_bars` → :meth:`~tstdx.client.ExMarketClient.ex_bars`
* :meth:`ExHqClient.ex_quotes` → :meth:`~tstdx.client.ExMarketClient.ex_quote`
* :meth:`OptionClient.goods_bars` → :meth:`~tstdx.client.GoodsClient.goods_bars`
* :meth:`OptionClient.goods_quotes` → :meth:`~tstdx.client.GoodsClient.goods_quote`
* :func:`market_client`      → 工厂：按市场类型返回对应门面

``FREQUENCY_PERIOD_MAP`` 记录第三方常见的整数频率码 → tstdx period 名称，便于迁移。
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from ..errors import CompatibilityWarning

__all__ = [
    "HqClient",
    "ExHqClient",
    "OptionClient",
    "market_client",
    "FREQUENCY_PERIOD_MAP",
]

#: 整数频率码 → tstdx period 名称（第三方库常见约定，便于迁移）。
#: 0=5min 1=15min 2=30min 3=1h 4=day 5=week 6=month 7=1min 8=season 9=year
FREQUENCY_PERIOD_MAP: dict[int, str] = {
    0: "5min",
    1: "15min",
    2: "30min",
    3: "60min",
    4: "day",
    5: "week",
    6: "month",
    7: "1min",
    8: "season",
    9: "year",
}


def market_client(market: str = "std", **kwargs: Any) -> Any:
    """工厂：按市场类型返回对应高层门面。

    ``market`` ∈ ``{std, ex, option}``：
    ``std`` → :class:`HqClient`；``ex`` → :class:`ExHqClient`；
    ``option`` → :class:`OptionClient`。
    """
    mapping = {
        "std": HqClient,
        "standard": HqClient,
        "ex": ExHqClient,
        "extend": ExHqClient,
        "option": OptionClient,
        "goods": OptionClient,
    }
    cls = mapping.get(str(market).lower())
    if cls is None:
        import warnings

        warnings.warn(f"未知 market={market!r}，回退到 std", CompatibilityWarning, stacklevel=2)
        cls = HqClient
    return cls(**kwargs)


class HqClient:
    """标准市场（A 股）高层门面。

    .. deprecated:: v7
        兼容门面：请直接使用 :class:`~tstdx.client.TdxClient`
        （本类是其薄包装）；v8 评估删除。
    """

    def __init__(self, *, timeout: float = 5.0, **client_kwargs: Any) -> None:
        from ..client import TdxClient

        self._client = TdxClient(timeout=timeout, **client_kwargs)

    def __enter__(self) -> HqClient:
        self._client.__enter__()
        return self

    def __exit__(self, *exc: Any) -> None:
        self._client.__exit__(*exc)

    def close(self) -> None:
        self._client.close()

    # -- K 线 / 分钟线 ------------------------------------------------------ #
    def bars(
        self,
        symbol: str,
        period: str = "day",
        offset: int = 0,
        limit: int = 320,
        *,
        as_format: str = "dict",
    ) -> list[dict[str, Any]]:
        """拉取 K 线（tstdx period 命名）。

        ``period`` 为 tstdx period 名称（如 ``"day"`` / ``"5min"``）；
        如需从整数频率码映射，可先经 :data:`FREQUENCY_PERIOD_MAP`。
        ``offset`` 为起始偏移，``limit`` 为根数。
        """
        rows = self._client.bars(
            symbol, period=period, count=limit, start=offset, as_format=as_format
        )
        return rows

    def index_bars(
        self, symbol: str, period: str = "day", offset: int = 0, limit: int = 320
    ) -> list[dict[str, Any]]:
        """指数 K 线：底层与个股同命令，显式传 ``index=True``（P1a：指数响应尾部多 4 字节涨跌家数）。"""
        return self._client.bars(
            symbol, period=period, count=limit, start=offset, index=True, as_format="dict"
        )

    # -- 实时行情 ----------------------------------------------------------- #
    def quotes(self, symbols: str | Sequence[str]) -> list[dict[str, Any]]:
        """实时行情快照（多标的）。"""
        if isinstance(symbols, str):
            symbols = [symbols]
        return self._client.quotes(symbols, as_format="dict")

    # -- 其它 --------------------------------------------------------------- #
    def security_count(self, market: int | str = 0) -> int:
        return self._client.security_count(market)

    def security_list(self, market: int | str = 0, start: int = 0) -> list[dict[str, Any]]:
        return self._client.security_list(market, start=start)

    def finance_info(self, symbol: str) -> dict[str, Any]:
        return self._client.finance_info(symbol)


class ExHqClient:
    """扩展市场（港股 / 美股 / 期货 / 外汇）高层门面。"""

    def __init__(self, *, timeout: float = 5.0, **client_kwargs: Any) -> None:
        from ..client import ExMarketClient

        self._client = ExMarketClient(timeout=timeout, **client_kwargs)

    def __enter__(self) -> ExHqClient:
        self._client.__enter__()
        return self

    def __exit__(self, *exc: Any) -> None:
        self._client.__exit__(*exc)

    def close(self) -> None:
        self._client.close()

    def ex_bars(
        self,
        symbol: str,
        period: str = "day",
        offset: int = 0,
        limit: int = 320,
    ) -> list[dict[str, Any]]:
        return self._client.ex_bars(
            symbol, period=period, count=limit, start=offset, as_format="dict"
        )

    def ex_quotes(self, symbols: str | Sequence[str]) -> list[dict[str, Any]]:
        if isinstance(symbols, str):
            symbols = [symbols]
        # 逐只请求：ExMarketClient.ex_quote 仅接受单只 symbol（旧实现把
        # Sequence 直接透传，多标的调用运行时必炸）。
        out: list[dict[str, Any]] = []
        for sym in symbols:
            rows = self._client.ex_quote(sym, as_format="dict")
            out.extend(rows if isinstance(rows, list) else [rows])
        return out


class OptionClient:
    """商品 / 期权高层门面（期货 / 期权 / 外汇）。"""

    def __init__(self, *, timeout: float = 5.0, **client_kwargs: Any) -> None:
        from ..client import GoodsClient

        self._client = GoodsClient(timeout=timeout, **client_kwargs)

    def __enter__(self) -> OptionClient:
        self._client.__enter__()
        return self

    def __exit__(self, *exc: Any) -> None:
        self._client.__exit__(*exc)

    def close(self) -> None:
        self._client.close()

    def goods_bars(
        self,
        symbol: str,
        period: str = "day",
        offset: int = 0,
        limit: int = 320,
    ) -> list[dict[str, Any]]:
        return self._client.goods_bars(
            symbol, period=period, count=limit, start=offset, as_format="dict"
        )

    def goods_quotes(self, symbol: str) -> list[dict[str, Any]]:
        rows = self._client.goods_quote(symbol, as_format="dict")
        if isinstance(rows, list):
            return rows
        return [rows]
