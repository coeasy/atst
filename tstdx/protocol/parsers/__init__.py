# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""协议解析器集合。

导入本包会触发全部 ``@register_parser`` 注册（标准扩展 / 商品 / 7727 扩展 /
MAC 专属 / F10 资料网关）。所有解析器在导入时即注册到
:data:`~tstdx.protocol.registry.PARSERS`，由 :func:`~tstdx.protocol.registry.dispatch`
按命令号分派。
"""

# 标准族核心解析器
# F10 资料网关
from .f10 import (  # noqa: F401
    F10CatalogParser,
    F10Section,
    parse_f10_text,
    split_f10_sections,
)

# 商品语义（期货 / 期权 / 外汇）
from .goods import (  # noqa: F401
    GoodsBarsParser,
    GoodsCalendarParser,
    GoodsCountParser,
    GoodsFxRateParser,
    GoodsHoldingParser,
    GoodsInfoParser,
    GoodsListParser,
    GoodsMinuteParser,
    GoodsOptionGreeksParser,
    GoodsQuoteParser,
    GoodsTradeParser,
)

# MAC 专属族
from .mac import (  # noqa: F401
    MacAuctionParser,
    MacBlockListParser,
    MacBlockMembersParser,
    MacBlockQuoteParser,
    MacChipParser,
    MacClientInfoParser,
    MacDdeParser,
    MacFundFlowParser,
    MacHeartbeatParser,
    MacIndexBarsParser,
    MacMainForceParser,
    MacMultidayMinuteParser,
    MacNewsParser,
    MacRankParser,
    MacUnifiedBarsParser,
    MacUnifiedQuoteParser,
)
from .std7709 import (  # noqa: F401
    DAYLIKE_CATEGORIES,
    MINUTELIKE_CATEGORIES,
    CapitalChangesParser,
    FinanceInfoParser,
    KlineCategory,
    QuotesLegacyParser,
    RealtimeQuoteParser,
    SecurityBarsParser,
    SecurityCountParser,
    build_realtime_quote_body,
    infer_market,
    quote_request_market,
)

# 标准族扩展解析器（代码表 / 分时 / 成交 / 批量行情 / 板块 / 文件下载 …）
from .std7709_extra import (  # noqa: F401
    AuctionSnapshotParser,
    BlockQuotesParser,
    FileDownloadParser,
    IndexMomentumParser,
    MinuteHistoryParser,
    MinuteTodayParser,
    QuotesDepthPushParser,
    QuotesSnapshotParser,
    SecurityListParser,
    TradeTodayAltParser,
    TradeTodayParser,
    VolumePriceDistParser,
)

# 7727 扩展市场（港股 / 美股 / 期货 / 外汇）
from .std7727 import (  # noqa: F401
    ExBatchQuoteParser,
    ExFutureQuoteParser,
    ExFxQuoteParser,
    ExHkQuoteParser,
    ExInstrumentBarsParser,
    ExInstrumentCountParser,
    ExInstrumentInfoParser,
    ExInstrumentListParser,
    ExInstrumentMinuteParser,
    ExInstrumentQuoteParser,
    ExInstrumentTradeParser,
    ExMarketCountParser,
    ExMarketListParser,
    ExOptionQuoteParser,
    ExUsQuoteParser,
)

__all__ = [
    # 标准族核心（std7709）
    "SecurityCountParser",
    "SecurityBarsParser",
    "CapitalChangesParser",
    "QuotesLegacyParser",
    "FinanceInfoParser",
    "RealtimeQuoteParser",
    "KlineCategory",
    "DAYLIKE_CATEGORIES",
    "MINUTELIKE_CATEGORIES",
    "build_realtime_quote_body",
    "infer_market",
    "quote_request_market",
    # 标准族扩展（std7709_extra）
    "SecurityListParser",
    "MinuteTodayParser",
    "MinuteHistoryParser",
    "TradeTodayParser",
    "TradeTodayAltParser",
    "QuotesSnapshotParser",
    "QuotesDepthPushParser",
    "FileDownloadParser",
    "BlockQuotesParser",
    "VolumePriceDistParser",
    "AuctionSnapshotParser",
    "IndexMomentumParser",
    # F10 资料网关
    "F10CatalogParser",
    "F10Section",
    "parse_f10_text",
    "split_f10_sections",
    # 商品语义（goods）
    "GoodsBarsParser",
    "GoodsCalendarParser",
    "GoodsCountParser",
    "GoodsFxRateParser",
    "GoodsHoldingParser",
    "GoodsInfoParser",
    "GoodsListParser",
    "GoodsMinuteParser",
    "GoodsOptionGreeksParser",
    "GoodsQuoteParser",
    "GoodsTradeParser",
    # MAC 专属族
    "MacAuctionParser",
    "MacBlockListParser",
    "MacBlockMembersParser",
    "MacBlockQuoteParser",
    "MacChipParser",
    "MacClientInfoParser",
    "MacDdeParser",
    "MacFundFlowParser",
    "MacHeartbeatParser",
    "MacIndexBarsParser",
    "MacMainForceParser",
    "MacMultidayMinuteParser",
    "MacNewsParser",
    "MacRankParser",
    "MacUnifiedBarsParser",
    "MacUnifiedQuoteParser",
    # 7727 扩展市场
    "ExBatchQuoteParser",
    "ExFutureQuoteParser",
    "ExFxQuoteParser",
    "ExHkQuoteParser",
    "ExInstrumentBarsParser",
    "ExInstrumentCountParser",
    "ExInstrumentInfoParser",
    "ExInstrumentListParser",
    "ExInstrumentMinuteParser",
    "ExInstrumentQuoteParser",
    "ExInstrumentTradeParser",
    "ExMarketCountParser",
    "ExMarketListParser",
    "ExOptionQuoteParser",
    "ExUsQuoteParser",
]
