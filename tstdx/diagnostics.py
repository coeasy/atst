# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""Per-result data-completeness caveats: one emit point, two observers.

A provider can hand back a result that is *successfully retrieved but not what
was asked for* — an empty first page read as "history exhausted", a page limit
cut short, a missing dividend field. Before this module such facts only existed
as a :func:`warnings.warn` line in the calling process' stderr, so an HTTP / WS
/ MCP caller looking at the wire could not tell a clean result from a caveat
carrier (F-45).

Every caveat is therefore emitted exactly once through :func:`record_warning`,
which does two things with it:

* appends a :class:`ResultWarning` to the query-local sink installed by
  :func:`warning_sink` (the executor installs one per query, so the fact rides
  inside ``QueryResult.meta.warnings`` and reaches every service face);
* emits the same text as a :class:`UserWarning`, preserving the long-standing
  in-process behaviour.

The sink is a :class:`~contextvars.ContextVar`, not a module global: concurrent
requests in threads and asyncio tasks each collect their own caveats, and a
warning raised outside any query (CLI tooling, import-time audits) simply has no
sink to write to.
"""

from __future__ import annotations

import contextlib
import contextvars
import warnings
from collections.abc import Iterator
from dataclasses import dataclass
from enum import Enum
from typing import Any

__all__ = [
    "WarningCode",
    "ResultWarning",
    "record_warning",
    "warning_sink",
]


class WarningCode(str, Enum):
    """The closed set of caveat kinds a result can carry.

    Declared as an Enum rather than a string set so a typo cannot create a
    silent sixth observation: every emission site references a member, and the
    architecture gate fails when a declared member has no emission site.
    """

    #: ``bars`` 分页因盘中锚点漂移提前终止：实取根数 < 请求根数，且不是历史耗尽。
    BARS_ANCHOR_DRIFT = "bars_anchor_drift"
    #: ``bars`` 首页即空响应：判据取服务端当次声明数——0 是该标的无此周期历史，N>0 却回 0
    #: 个记录字节是空桩；两者都不是"更早的历史已取完"（那只会表现为短页）。
    BARS_EMPTY_FIRST_PAGE = "bars_empty_first_page"
    #: 解码层对某一页的判断：实收记录数少于声明数、字段布局哨兵异常等。
    DECODE_CAVEAT = "decode_caveat"
    #: ``export_security_list`` 首页即空响应：不代表该市场没有证券。
    SECURITY_LIST_EMPTY_FIRST_PAGE = "security_list_empty_first_page"
    #: ``export_security_list`` 在 ``max_pages`` 内未取尽（最后一页仍为满页）。
    SECURITY_LIST_PAGE_LIMIT = "security_list_page_limit"
    #: 分块文件下载累计字节数小于服务端报告的 ``total_len``。
    FILE_DOWNLOAD_SHORT = "file_download_short"
    #: 复权事件缺前收盘价：每股现金红利被忽略，价格因子是近似值。
    ADJUST_PREV_CLOSE_MISSING = "adjust_prev_close_missing"
    #: 交易日历未覆盖该年：节假日按无节假日处理。
    CALENDAR_YEAR_UNCOVERED = "calendar_year_uncovered"
    #: 新浪全市场分页重试后仍缺页。
    WEB_SINA_PAGES_MISSING = "web_sina_pages_missing"
    #: 腾讯全市场单批重试后仍失败，结果不完整。
    WEB_TENCENT_BATCH_FAILED = "web_tencent_batch_failed"
    #: 腾讯 K 线整批 ``amount`` 恒为 0：该字段不可用于计算。
    WEB_TENCENT_AMOUNT_ALL_ZERO = "web_tencent_amount_all_zero"
    #: 东财报表在 ``max_pages`` 内未取尽。
    WEB_EASTMONEY_PAGE_LIMIT = "web_eastmoney_page_limit"
    #: 声明的 ``currentness`` 要求当期数据，而执行 channel 给不出可判据的证据（本地文件）。
    #: 见 :mod:`tstdx.runtime.freshness`；``strict=True`` 时它不是告警而是失败。
    CURRENTNESS_UNPROVEN = "currentness_unproven"


@dataclass(frozen=True, slots=True)
class ResultWarning:
    """One caveat attached to the result it describes."""

    code: WarningCode
    message: str

    def __post_init__(self) -> None:
        if not isinstance(self.code, WarningCode):
            raise ValueError(f"warning code must be a WarningCode, got {self.code!r}")
        if not self.message.strip():
            raise ValueError("warning message must not be empty")

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code.value, "message": self.message}


_WARN_SINK: contextvars.ContextVar[list[ResultWarning] | None] = contextvars.ContextVar(
    "tstdx_warning_sink", default=None
)


def _emit(code: WarningCode, message: str, *, stacklevel: int) -> None:
    warnings.warn(f"[{code.value}] {message}", UserWarning, stacklevel=stacklevel + 2)


def record_warning(
    code: WarningCode,
    message: str,
    *,
    stacklevel: int = 2,
    stderr: bool = True,
) -> None:
    """Emit one caveat: into the active query sink (if any) and as ``UserWarning``.

    ``stacklevel`` keeps the meaning it has for a :func:`warnings.warn` written
    straight at the emit site, so converting a call does not move who the
    warning points at.

    ``stderr=False`` records the caveat on the result without nagging the
    process again. It exists for the emit sites that already deduplicate their
    warning in their own state (an adjustment hint, an uncovered calendar year):
    that dedupe is a courtesy to the terminal, and it must not decide whether a
    *particular* result says "I carry this defect".
    """
    if not isinstance(code, WarningCode):
        raise TypeError(f"unknown warning code {code!r}（新增类别请在 WarningCode 里声明）")
    sink = _WARN_SINK.get()
    if sink is not None:
        sink.append(ResultWarning(code=code, message=message))
    if stderr:
        # +2：跳过 record_warning 与 _emit 这两层转发帧，保持发射点原有的指向。
        _emit(code, message, stacklevel=stacklevel)


@contextlib.contextmanager
def warning_sink() -> Iterator[list[ResultWarning]]:
    """Collect caveats raised while the block runs, isolated per thread/task."""
    collected: list[ResultWarning] = []
    token = _WARN_SINK.set(collected)
    try:
        yield collected
    finally:
        _WARN_SINK.reset(token)
