# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""7709 标准协议族的 L1 精确解析器。



.. important::

   本文件中标注 **✅ golden-verified** 的解析器，其字段布局已通过

   ``tests/golden/7709/`` 下自采集样本回归验证：**按字节精确耗尽缓冲区**，

   且数值通过交叉校验（如 ``均价 = amount / volume`` 落在 OHLC 区间内）。

   其余解析器标注 ⚠️ 表示结构来自公开资料推断，待样本锁定。



已实现 L1 的命令

----------------

===========  ====================  ==========  ==========================================

命令号       名称                   状态        记录布局

===========  ====================  ==========  ==========================================

0x044E       SECURITY_COUNT        ✅          uint16 数量

0x052D       SECURITY_BARS         ✅          dt + 4×LEB128 差分价格 + 2×自定义浮点

0x000F       CAPITAL_CHANGES       ⚠️          待样本锁定

0x0010       FINANCE_INFO          ⚠️          待样本锁定

0x053E       QUOTES_LEGACY         ⚠️          部分主站已停用，优先 0x054C

===========  ====================  ==========  ==========================================



其余命令走 :mod:`atst.protocol.generic` 的 L2/L3 兜底。

P11-3（REFACTOR_PLAN_v11）：解析器实现拆分至 ``_std7709_common`` /
``_std7709_quote`` / ``_std7709_bars`` 三模块；本文件保留为**注册与再导出**
门面——import 本模块即完成 6 个 L1 解析器的 ``register_parser`` 注册，
全部历史导入路径（``from atst.protocol.parsers.std7709 import X``）不变。

"""

from __future__ import annotations

from ._std7709_bars import (  # noqa: F401
    CapitalChangesParser,
    FinanceInfoParser,
    SecurityBarsParser,
)
from ._std7709_common import (  # noqa: F401
    DAYLIKE_CATEGORIES,
    MINUTELIKE_CATEGORIES,
    SHARES_PER_LOT,
    VOLUME_LOT_CATEGORIES,
    KlineCategory,
    Market,
    build_realtime_quote_body,
    infer_market,
    quote_request_market,
)
from ._std7709_quote import (  # noqa: F401
    QuotesLegacyParser,
    RealtimeQuoteParser,
    SecurityCountParser,
)

__all__ = [
    "SecurityCountParser",
    "SecurityBarsParser",
    "CapitalChangesParser",
    "QuotesLegacyParser",
    "FinanceInfoParser",
    "RealtimeQuoteParser",
    "KlineCategory",
    "DAYLIKE_CATEGORIES",
    "MINUTELIKE_CATEGORIES",
    "VOLUME_LOT_CATEGORIES",
    "SHARES_PER_LOT",
    "Market",
    "infer_market",
    "quote_request_market",
    "build_realtime_quote_body",
]
