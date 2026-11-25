# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""Canonical query contracts and deterministic single-Provider planning.

P2-1：原 ``atst/query.py``（771 行）拆为包——契约（``spec``）、数据身份
（``fingerprint``）、规划（``planning``）。对外导入面不变：``from atst.query
import QuerySpec, QueryPlanner, ...`` 仍走本包出口，内部名
（``_canonical_unified_channel`` 等）同样在此命名空间可见。

本包仍然是无副作用契约层：planning 在任何服务 I/O 之前校验 capability、
Provider、Channel、period 与 currentness。一个 :class:`QueryPlan` 恒绑一个
Provider 与一个 canonical Channel；跨 Provider fallback 是 orchestration 政策，
planner 内永不发生。
"""

from .fingerprint import QueryFingerprint
from .planning import (
    QueryPlan,
    QueryPlanner,
    _canonical_unified_channel,
    _reject_core_channel_mismatch,
)
from .spec import (
    EXECUTED_OPTIONS,
    REJECTED_OPTIONS,
    SPEC_INT_FIELDS,
    CurrentnessMode,
    ExecutionBudget,
    QuerySpec,
    _parse_currentness,
)

__all__ = [
    "CurrentnessMode",
    "ExecutionBudget",
    "QueryFingerprint",
    "QueryPlan",
    "QueryPlanner",
    "QuerySpec",
    "EXECUTED_OPTIONS",
    "REJECTED_OPTIONS",
    "SPEC_INT_FIELDS",
    "_canonical_unified_channel",
    "_parse_currentness",
    "_reject_core_channel_mismatch",
]
