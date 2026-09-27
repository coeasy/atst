# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""Web 数据源接口上限常量（v5 PG4：集中管理，实测标定）。

每个常量都必须附**实测证据**与日期；无实测依据的钳制宁可不加——
错误的上限会白白阉割数据能力（如东财 kline ``lmt`` 实测支持 3000+，
若按早期审计假设钳 800 反而降级）。

实测方法：直接请求目标接口并统计返回条数，见
``docs/archive/plans/OPTIMIZATION_PLAN_v5.md`` §5.1 PG4 与修改记录。
"""

from __future__ import annotations

__all__ = [
    "TENCENT_KLINE_MAX",
]


#: 腾讯 ifzq K 线接口单请求条数上限（fqkline / mkline 通用钳制）。
#:
#: **实测标定（2026-09-05，sh600519 day qfq，web.ifzq.gtimg.cn）**：
#:
#: ======  ==========  ============================
#: 请求     返回        结论
#: ======  ==========  ============================
#: 800     800 根      满额兑现
#: 2000    640 根      **静默截断**（且不足 800）
#: 4000    null        整体返回空（JSON data 缺失）
#: ======  ==========  ============================
#:
#: 超限请求不仅截断还可能整体失败，故单次请求统一钳到实测安全值 800；
#: 钳制发生时由调用方打日志告警。需要更长历史不必自己分段：v5 PG8 已落地为
#: C9——:meth:`atst.web.tencent.adapters.KlineSource.fetch_bars` 在 ``count`` 超过
#: 本常量时按 ``end`` 日期自动分段请求并拼接去重（分页逻辑在 ``_paginate``），
#: 调用方拿到的仍是完整 ``count`` 根。本常量因此只约束**单段**大小。
TENCENT_KLINE_MAX = 800
