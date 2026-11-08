# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""基本面与公司行为适配器（§33 扩展）——v2/W7 拆分后的稳定导出面。

实现按域落在三个模块：

* :mod:`atst.sources._corporate_common`：报表名白名单、代码守卫、空集判据；
* :mod:`atst.sources._corporate_financial`：datacenter 报表族 / F10 / 公告 / 研报；
* :mod:`atst.sources._corporate_market`：股东 / 大宗 / 解禁 / 业绩 / 预告 / IPO。

``atst.sources.corporate`` 保留原有导出面，外部 import 路径不变。
"""

from __future__ import annotations

__all__ = [
    "EastmoneyDataCenterSource",
    "EastmoneyProfileSource",
    "EastmoneyNoticeSource",
    "EastmoneyResearchSource",
    "EastmoneyShareholderSource",
    "EastmoneyBlockTradeSource",
    "EastmoneyUnlockSource",
    "EastmoneyPerformanceSource",
    "EastmoneyForecastSource",
    "EastmoneyIpoSource",
    "VALID_REPORTS",
    "EMPTY_RESULT_CODE",
    "is_empty_result",
    "report_code_for",
]

from ._corporate_common import (
    EMPTY_RESULT_CODE,
    VALID_REPORTS,
    is_empty_result,
    report_code_for,
)
from ._corporate_financial import (
    EastmoneyDataCenterSource,
    EastmoneyNoticeSource,
    EastmoneyProfileSource,
    EastmoneyResearchSource,
)
from ._corporate_market import (
    EastmoneyBlockTradeSource,
    EastmoneyForecastSource,
    EastmoneyIpoSource,
    EastmoneyPerformanceSource,
    EastmoneyShareholderSource,
    EastmoneyUnlockSource,
)
