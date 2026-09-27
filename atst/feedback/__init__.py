# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""反馈子系统（Tier B / B1）。

集中导出反馈上报、遥测收集与用户统计模块。

**安全设计**
-----------
* 默认不发送任何数据——需设置环境变量 ``ATST_FEEDBACK=1`` 启用；
* ``ATST_FEEDBACK=dry-run`` 进入调试模式（仅打印，不发送）；
* 所有上报数据经过 7 步脱敏流水线处理，确保不含敏感信息。

模块组成
--------
* :mod:`atst.feedback.reporter` — 错误 / 用量 / 配置上报
* :mod:`atst.feedback.telemetry` — 内存遥测事件收集器
* :mod:`atst.feedback.stats` — 用户本地使用统计

典型用法::

    # 启用反馈
    import os
    os.environ["ATST_FEEDBACK"] = "1"

    from atst.feedback import FeedbackReporter

    reporter = FeedbackReporter()
    reporter.report_error(Exception("测试"))
    reporter.report_usage("bars", 12.5, "ok")
"""

from .reporter import FeedbackReporter
from .stats import UserStats
from .telemetry import TelemetryCollector

__all__ = [
    "FeedbackReporter",
    "TelemetryCollector",
    "UserStats",
]
