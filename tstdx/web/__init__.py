# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""HTTP Web 行情源（§33）：TDX 主站不可用时的降级路径。

Quick start::

    from tstdx.web import get_quotes
    quotes = get_quotes(["sh600519", "sz000001"], source="tencent")
    print(quotes[0].price, quotes[0].volume)   # 元 / 股（已归一化）

或走统一降级::

    from tstdx.web import WebQuoteClient
    client = WebQuoteClient()          # 按配置的 enabled_sources 顺序
    quotes = client.quotes(["sh600519"])

高层门面（原生命名）::

    from tstdx.web.facade import web_session
    sess = web_session("sina")
    sess.quotes(["000001"])            # list[Quote]
    sess.all_market(node="hs_a")       # list[Quote]
    sess.klines("sh600519")            # list[Bar]

能力矩阵（``list_sources(capability="...")`` 可检索）:

====================  ==========================================
capability            提供该能力的源
====================  ==========================================
``quote``             sina / tencent / eastmoney / hk / us / global / market_stat
``hk_quote``          hk (腾讯) / hk_sina (新浪港股)
``us_quote``          us (腾讯美股)
``kline``             kline / minute_kline
``minute``            minute / trends
``tick``              ticks / trends
``rank``              sina / eastmoney / rank
``fund_flow``         fund_flow / northbound
``limit_pool``        limit_pool
``corporate``         corporate
``longhu``            longhu
``news``              news
``suggest``           suggest
``wencai``            wencai（i问财自然语言选股，cookie 由调用方注入）
``fx``                boc
``baidu``             baidu（百度财经：kline/minute/tick/quote，仅 A 股）
====================  ==========================================

惰性导入（Q4-2，PEP 562）：本包 ``__init__`` 只常驻零依赖的
``sources``（SourceSpec 注册表）与 ``base``（stdlib HTTP 底座），
其余 18 个 Source / 门面子模块按需经 :data:`_LAZY` 映射加载，
``from tstdx.web import X`` 的全部既有导入路径保持不变。
"""

from __future__ import annotations

import importlib
import logging
from collections.abc import Mapping, Sequence
from typing import Any

from ..domain.models import Quote
from ..errors import AllSourcesExhausted, TdxError, WebSourceError
from .base import BaseWebSource, RateLimiter, build_client, normalize_symbol
from .sources import (
    BAIDU,
    BOC,
    CORPORATE,
    DEFAULT_FALLBACK_ORDER,
    EASTMONEY,
    FUND,
    FUND_FLOW,
    GLOBAL,
    HK,
    HK_SINA,
    HOT_RANK,
    INDEX_CONS,
    JSL,
    KLINE,
    LHB,
    LIMIT_POOL,
    MARGIN,
    MARKET_STAT,
    MINUTE,
    MINUTE_KLINE,
    NEWS,
    NORTHBOUND,
    RANK,
    SINA,
    SINA_FUND_FLOW,
    STOCK_CHANGES,
    SUGGEST,
    TENCENT,
    TICKS,
    TRENDS,
    US,
    WENCAI,
    get_source,
    list_sources,
)

__all__ = [
    "WebQuoteClient",
    "get_quotes",
    "get_kline",
    "get_rates",
    "create_source",
    "web_session",
    "WebQuoteSession",
    "SinaSource",
    "TencentSource",
    "EastmoneySource",
    "JslSource",
    "HkSource",
    "UsSource",
    "TencentExternalSource",
    "SinaHkSource",
    "HK_SINA",
    "KlineSource",
    "BocSource",
    "MinuteKlineSource",
    "MinuteSource",
    "SuggestSource",
    "SinaIndustryBoardSource",
    "SinaBoardListSource",
    "SinaBoardMemberSource",
    "TencentBoardRankSource",
    "EastmoneyBoardSource",
    "SinaHistoryKlineSource",
    "EastmoneyHistoryKlineSource",
    "TencentTickSource",
    "EastmoneyTrendsSource",
    "TencentGlobalSource",
    "TencentMarketStatSource",
    "EastmoneyRankSource",
    "EastmoneyFundFlowSource",
    "EastmoneyLimitPoolSource",
    "EastmoneyStockChangesSource",
    "EastmoneyNorthboundSource",
    "SinaFundFlowSource",
    "EastmoneyProfileSource",
    "EastmoneyNoticeSource",
    "EastmoneyResearchSource",
    "EastmoneyShareholderSource",
    "EastmoneyBlockTradeSource",
    "EastmoneyUnlockSource",
    "EastmoneyPerformanceSource",
    "EastmoneyDataCenterSource",
    "EastmoneyTopListSource",
    "SinaNewsSource",
    "REASON_LABELS",
    "parse_lhb_row",
    "WencaiSource",
    "EastmoneyHotRankSource",
    "BaiduSource",
    "FundSource",
    "EastmoneyMarginSource",
    "EastmoneyIndexConstituentsSource",
    "BaseWebSource",
    "WebSourceError",
    "RateLimiter",
    "build_client",
    "get_source",
    "normalize_symbol",
    "list_sources",
    "VolumeNormalizer",
    "register_normalizer",
    "normalize_volume",
    "normalize_amount",
    "normalize_quote",
    "normalize_bar",
]

logger = logging.getLogger(__name__)

# --- 惰性导出（Q4-2）：符号 → 所属子模块 ----------------------------------- #
# 常驻符号（本模块直接定义）：WebQuoteClient / get_quotes / get_kline /
# get_rates / create_source；其余 __all__ 符号全部走 _LAZY。
_LAZY: dict[str, str] = {
    # adapters.py
    "SinaSource": "adapters",
    "TencentSource": "adapters",
    "EastmoneySource": "adapters",
    "JslSource": "adapters",
    "HkSource": "adapters",
    "UsSource": "adapters",
    "TencentExternalSource": "adapters",
    "SinaHkSource": "adapters",
    "KlineSource": "adapters",
    "BocSource": "adapters",
    # adapters_baidu.py
    "BaiduSource": "adapters_baidu",
    # adapters_ext.py
    "MinuteKlineSource": "adapters_ext",
    "MinuteSource": "adapters_ext",
    "SuggestSource": "adapters_ext",
    # adapters_fund.py
    "FundSource": "adapters_fund",
    # adapters_fund.py
    # adapters_margin.py
    "EastmoneyMarginSource": "adapters_margin",
    # adapters_index.py
    "EastmoneyIndexConstituentsSource": "adapters_index",
    # boards.py
    "SinaIndustryBoardSource": "boards",
    "SinaBoardListSource": "boards",
    "SinaBoardMemberSource": "boards",
    "TencentBoardRankSource": "boards",
    "EastmoneyBoardSource": "boards",
    # corporate.py
    "EastmoneyProfileSource": "corporate",
    "EastmoneyNoticeSource": "corporate",
    "EastmoneyResearchSource": "corporate",
    "EastmoneyShareholderSource": "corporate",
    "EastmoneyBlockTradeSource": "corporate",
    "EastmoneyUnlockSource": "corporate",
    "EastmoneyPerformanceSource": "corporate",
    "EastmoneyDataCenterSource": "corporate",
    # facade.py
    "WebQuoteSession": "facade",
    "web_session": "facade",
    # fin_report.py
    "EastmoneyF10ReportSource": "fin_report",
    "to_eastmoney_secucode": "fin_report",
    # governance.py
    "EastmoneyExecutiveHoldSource": "governance",
    "EastmoneyShareholderChangeSource": "governance",
    "EastmoneyOrgProfileSource": "governance",
    "EastmoneyRatingForecastSource": "governance",
    # fundflow.py
    "EastmoneyRankSource": "fundflow",
    "EastmoneyFundFlowSource": "fundflow",
    "EastmoneyLimitPoolSource": "fundflow",
    "EastmoneyStockChangesSource": "fundflow",
    "EastmoneyNorthboundSource": "fundflow",
    "SinaFundFlowSource": "fundflow",
    # global_market.py
    "TencentGlobalSource": "global_market",
    "TencentMarketStatSource": "global_market",
    # history.py
    "SinaHistoryKlineSource": "history",
    "EastmoneyHistoryKlineSource": "history",
    # hot_rank.py
    "EastmoneyHotRankSource": "hot_rank",
    # longhu.py
    "REASON_LABELS": "longhu",
    "EastmoneyTopListSource": "longhu",
    "parse_lhb_row": "longhu",
    # news.py
    "SinaNewsSource": "news",
    # normalize.py
    "VolumeNormalizer": "normalize",
    "register_normalizer": "normalize",
    "normalize_volume": "normalize",
    "normalize_amount": "normalize",
    "normalize_quote": "normalize",
    "normalize_bar": "normalize",
    # ticks.py
    "TencentTickSource": "ticks",
    "EastmoneyTrendsSource": "ticks",
    # wencai.py
    "WencaiSource": "wencai",
}


def __getattr__(name: str) -> Any:
    if name in _LAZY:
        mod = importlib.import_module(f".{_LAZY[name]}", __name__)
        value = getattr(mod, name)
        globals()[name] = value  # 后续访问直接命中模块 globals
        return value
    if name == "_ADAPTERS":
        # 惰性注册表：首次访问才 import 各 Source 模块并构建
        # （tests/web/test_registry_consistency.py 直接 ``from tstdx.web
        # import _ADAPTERS`` 遍历，返回真实 dict，访问接口不变）。
        adapters = _build_adapters()
        globals()["_ADAPTERS"] = adapters
        return adapters
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(_LAZY) | {"_ADAPTERS"})


# --- 适配器注册表（惰性构建，Q4-2） ---------------------------------------- #
# 源名 → (子模块, 类名)。首次访问 _ADAPTERS / create_source 时才实际
# import 各 Source 子模块（原先为包初始化期全量 import）。
_ADAPTER_SPECS: dict[str, tuple[str, str]] = {
    SINA: ("adapters", "SinaSource"),
    TENCENT: ("adapters", "TencentSource"),
    EASTMONEY: ("adapters", "EastmoneySource"),
    JSL: ("adapters", "JslSource"),
    HK: ("adapters", "HkSource"),
    HK_SINA: ("adapters", "SinaHkSource"),
    US: ("adapters", "UsSource"),
    KLINE: ("adapters", "KlineSource"),
    BOC: ("adapters", "BocSource"),
    MINUTE_KLINE: ("adapters_ext", "MinuteKlineSource"),
    MINUTE: ("adapters_ext", "MinuteSource"),
    SUGGEST: ("adapters_ext", "SuggestSource"),
    # —— 扩展源（2026-09 新增）——
    TICKS: ("ticks", "TencentTickSource"),
    TRENDS: ("ticks", "EastmoneyTrendsSource"),
    GLOBAL: ("global_market", "TencentGlobalSource"),
    MARKET_STAT: ("global_market", "TencentMarketStatSource"),
    RANK: ("fundflow", "EastmoneyRankSource"),
    FUND_FLOW: ("fundflow", "EastmoneyFundFlowSource"),
    LIMIT_POOL: ("fundflow", "EastmoneyLimitPoolSource"),
    STOCK_CHANGES: ("fundflow", "EastmoneyStockChangesSource"),
    NORTHBOUND: ("fundflow", "EastmoneyNorthboundSource"),
    #: 基本面入口默认给 F10 基础资料（最轻量、最常用）
    CORPORATE: ("corporate", "EastmoneyProfileSource"),
    LHB: ("longhu", "EastmoneyTopListSource"),
    NEWS: ("news", "SinaNewsSource"),
    SINA_FUND_FLOW: ("fundflow", "SinaFundFlowSource"),
    WENCAI: ("wencai", "WencaiSource"),
    HOT_RANK: ("hot_rank", "EastmoneyHotRankSource"),
    BAIDU: ("adapters_baidu", "BaiduSource"),
    FUND: ("adapters_fund", "FundSource"),
    MARGIN: ("adapters_margin", "EastmoneyMarginSource"),
    INDEX_CONS: ("adapters_index", "EastmoneyIndexConstituentsSource"),
}


def _build_adapters() -> dict[str, type[BaseWebSource]]:
    """实例化注册表：import 各 Source 子模块并解析类引用（仅首次调用）。"""
    registry: dict[str, type[BaseWebSource]] = {}
    for name, (submodule, cls_name) in _ADAPTER_SPECS.items():
        mod = importlib.import_module(f".{submodule}", __name__)
        registry[name] = getattr(mod, cls_name)
    return registry


def create_source(
    name: str,
    *,
    headers: Mapping[str, str] | None = None,
    timeout: float = 5.0,
    max_retries: int = 2,
    rate_limit: float | None = None,
    cookie: str | None = None,
) -> BaseWebSource:
    """按名称创建适配器实例。"""
    adapters = _build_adapters() if "_ADAPTERS" not in globals() else globals()["_ADAPTERS"]
    if name not in adapters:
        raise KeyError(f"未知行情源: {name!r}；已知: {sorted(adapters)}")
    return adapters[name](
        headers=headers,
        timeout=timeout,
        max_retries=max_retries,
        rate_limit=rate_limit,
        cookie=cookie,
    )


class WebQuoteClient:
    """HTTP Web 行情统一客户端，按配置顺序在多个源之间降级（§33.6）。

    Parameters
    ----------
    sources:
        降级顺序；None 时取配置 ``web.enabled_sources``，再兜底 DEFAULT_FALLBACK_ORDER。
    """

    def __init__(
        self,
        sources: Sequence[str] | None = None,
        *,
        headers: Mapping[str, str] | None = None,
        timeout: float = 5.0,
        max_retries: int = 2,
        rates: Mapping[str, int | float] | None = None,
        cookie: str | None = None,
    ) -> None:
        self.sources = list(sources) if sources else self._config_sources()
        self.headers = dict(headers or {})
        self.timeout = timeout
        self.max_retries = max_retries
        self.rates = dict(rates or {})
        self.cookie = cookie
        self._instances: dict[str, BaseWebSource] = {}
        self.errors: list[tuple[str, BaseException]] = []

    @staticmethod
    def _config_sources() -> list[str]:
        try:
            from ..config import get_config

            cfg = get_config()
            if cfg.web.enabled_sources:
                return list(cfg.web.enabled_sources)
        except Exception:
            pass
        return list(DEFAULT_FALLBACK_ORDER)

    def _instance(self, name: str) -> BaseWebSource:
        if name not in self._instances:
            self._instances[name] = create_source(
                name,
                headers=self.headers or None,
                timeout=self.timeout,
                max_retries=self.max_retries,
                rate_limit=self.rates.get(name),
                cookie=self.cookie,
            )
        return self._instances[name]

    def quotes(self, symbols: Sequence[str]) -> list[Quote]:
        """按降级顺序尝试各源，返回第一个成功的完整结果。"""
        symbols = [normalize_symbol(s) for s in symbols]
        self.errors.clear()
        for name in self.sources:
            try:
                return self._instance(name).fetch(symbols)
            except Exception as exc:  # noqa: BLE001  降级链兜底（W6）
                if isinstance(exc, TdxError):
                    # 捕获全部 TdxError（含 ReadTimeout/ConnectionFailed 等传输层
                    # 异常），确保单源超时不会中断多源降级链（P0 #4）
                    self.errors.append((name, exc))
                else:
                    # W6: 非 TdxError（如上游结构变化触发的 AttributeError/KeyError、
                    # 源名拼错的 KeyError）按「源不可用」分类记日志后继续降级，
                    # 不再中断整条链
                    logger.warning(
                        "Web 源 %s 抛出非 TdxError 异常 %s: %s —— 继续降级",
                        name,
                        type(exc).__name__,
                        exc,
                    )
                    self.errors.append((name, exc))
                continue
        raise AllSourcesExhausted(
            f"全部 HTTP Web 源失败: {[n for n, _ in self.errors]}",
            context={
                "sources": list(self.sources),
                "errors": [f"{n}: {e}" for n, e in self.errors],
            },
        )

    def klines(
        self, symbol: str, *, period: str = "day", count: int = 320, adjust: str = "qfq"
    ) -> list[Any]:
        """日/周/月 K 线（当前仅腾讯 KlineSource 提供）。"""
        symbol = normalize_symbol(symbol)
        for name in (*[s for s in self.sources if s == KLINE], KLINE):
            try:
                return self._instance(name).fetch_bars(  # type: ignore[attr-defined]
                    symbol, period=period, count=count, adjust=adjust
                )
            except Exception as exc:  # noqa: BLE001
                self.errors.append((name, exc))
                continue
        raise AllSourcesExhausted(
            "无可用 K 线源",
            context={"sources": self.sources},
        )

    def close(self) -> None:
        for inst in self._instances.values():
            inst.close()
        self._instances.clear()

    def __enter__(self) -> WebQuoteClient:
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()


def get_quotes(
    symbols: Sequence[str],
    *,
    source: str | None = None,
    headers: Mapping[str, str] | None = None,
    timeout: float = 5.0,
    cookie: str | None = None,
) -> list[Quote]:
    """获取实时行情（已归一化到「股 / 元」契约）。

    Parameters
    ----------
    symbols:
        ``["sh600519", "sz000001"]`` 或 ``["600519"]``（缺省市场按前缀推断）。
    source:
        指定单一源；None 时按降级顺序自动选择。
    """
    if source:
        src = create_source(source, headers=headers, timeout=timeout, cookie=cookie)
        try:
            return src.fetch([normalize_symbol(s) for s in symbols])
        finally:
            src.close()  # P1 #14: 单次调用也释放 httpx 连接池
    with WebQuoteClient(headers=headers, timeout=timeout, cookie=cookie) as c:
        return c.quotes(symbols)


def get_kline(
    symbol: str, *, period: str = "day", count: int = 320, adjust: str = "qfq"
) -> list[Any]:
    """获取 K 线（HTTP 源）。"""
    with WebQuoteClient(sources=[KLINE]) as c:
        return c.klines(symbol, period=period, count=count, adjust=adjust)


def get_rates() -> list[dict[str, Any]]:
    """中行外汇牌价。"""
    # Q4-2: 惰性解析 BocSource（经模块 globals / __getattr__，测试
    # monkeypatch ``tstdx.web.BocSource`` 的路径保持有效）。
    if "BocSource" in globals():
        boc_cls: Any = globals()["BocSource"]
    else:
        boc_cls = __getattr__("BocSource")

    src = boc_cls()
    try:
        return src.fetch_rates()
    finally:
        src.close()  # P1 #14: 释放连接池
