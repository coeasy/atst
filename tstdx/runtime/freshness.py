# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""Run-time currentness verifier: prove the claim or say the claim is unproven.

``QuerySpec.currentness`` used to be a *declaration* only — it travelled into the
plan and was read by nobody (F-44).  ``docs/providers/tdx.md`` §5 promised the
opposite: when the freshness profile cannot be proven, strict mode must answer
:class:`~tstdx.errors.FreshnessViolation` instead of passing a file off as
same-day data.  This module is the judgement for the one face where the runtime
can decide it *without* inventing an age threshold: the executing channel reads
local files (``ChannelSpec.local``), so no evidence exists that the on-disk
data reaches the current business day.

The rule is deliberately narrow.  ``live`` on a non-live channel is already
rejected at planning time as an input error (:meth:`QueryPlanner.compile`), and
a non-live web channel under ``business`` is a source-side property the registry
does not encode — refusing those would be inventing policy, not proving a claim.
What stays unproven is registered as such instead of being silently promoted.
"""

from __future__ import annotations

from ..diagnostics import WarningCode, record_warning
from ..errors import FreshnessViolation
from ..providers import PROVIDERS
from ..query import CurrentnessMode, QueryPlan, _parse_currentness

__all__ = ["verify_currentness"]

#: 只有"要求当期"的口径需要证据；``auto``/``historical`` 声明的是"源所能给的历史"，
#: 任何 Provider 都默认满足，没有可违反的契约。
_REQUIRES_PROOF: frozenset[CurrentnessMode] = frozenset(
    {CurrentnessMode.LIVE, CurrentnessMode.BUSINESS}
)


def _unproven_reason(plan: QueryPlan) -> str | None:
    """Return why this plan's currentness claim cannot be proven, or ``None``."""
    mode = _parse_currentness(plan.spec.currentness)
    if mode not in _REQUIRES_PROOF:
        return None
    channel = PROVIDERS.get(plan.provider).channel(plan.channel)
    if not channel.local:
        return None
    return (
        f"currentness={mode.value!r} 要求当期数据，而执行 channel {channel.id!r} 读本地文件"
        "（ChannelSpec.local=True）：运行期没有任何判据能证明文件已覆盖当期"
    )


def verify_currentness(plan: QueryPlan, *, strict: bool) -> None:
    """Check the plan's currentness claim against what its channel can prove.

    ``strict`` is the executor's shared switch (see
    :func:`~tstdx.runtime.executor._strict_requested`): an unprovable claim
    becomes a :class:`FreshnessViolation` before any I/O, and a plain
    ``currentness_unproven`` caveat on the result otherwise — never silence.
    """
    reason = _unproven_reason(plan)
    if reason is None:
        return
    context = {
        "provider": plan.provider,
        "channel": plan.channel,
        "capability": plan.spec.capability,
        "currentness": plan.spec.currentness,
        "local": True,
        "reason": reason,
        "fallback": False,
        "provider_switch_allowed": False,
    }
    if strict:
        raise FreshnessViolation(
            f"无法证明 currentness 契约：{reason}",
            context=context,
        )
    record_warning(
        WarningCode.CURRENTNESS_UNPROVEN,
        reason,
        #: 指向调用方（verify → execute → 用户代码），而不是库内部帧。
        stacklevel=3,
    )
