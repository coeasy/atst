# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""BaseWebSource 重试退避 + 失败双桶计数（P10-3 自 base.py 拆出）。

承载 W3/W4/C7-web 的重试常量、指数退避与可重试状态码判定，
以及失败双桶（传输桶 / 解析桶）计数与接口下线检测；
以 :class:`_BaseRetryMixin` 形式被 :class:`~tstdx.web.base.BaseWebSource` 组合，
行为与拆分前完全一致。
"""

from __future__ import annotations

import random

from ..errors import SourceDeprecated

__all__ = [
    "MAX_RETRIES_CAP",
    "MAX_BACKOFF_SECONDS",
    "DEFAULT_ACQUIRE_TIMEOUT",
    "_BaseRetryMixin",
]

# --------------------------------------------------------------------------- #
# 重试 / 限流常量（W3/W4/C7-web）
# --------------------------------------------------------------------------- #
#: ``max_retries`` 钳制上限（防用户传入超大值导致分钟级退避）
MAX_RETRIES_CAP = 5
#: 单次退避上限（秒），指数退避 ``0.5*2**n`` 的封顶值
MAX_BACKOFF_SECONDS = 8.0
#: 可重试状态码：反爬(403) / 限流(429)；5xx 视为服务端瞬时错误一并重试。
#: 404/400 等确定性客户端错误**不在集合内**——直接失败不重试（W4）。
_RETRYABLE_STATUS_CODES = frozenset({403, 429})
#: 阻塞式 acquire 的默认总超时（秒）：无 timeout 的 ``block=True``
#: 不再条件性死等（C7-web）
DEFAULT_ACQUIRE_TIMEOUT = 30.0


def _is_retryable_status(status: int) -> bool:
    """403/429/5xx 可重试；404/400 等确定性错误不重试。"""
    return status in _RETRYABLE_STATUS_CODES or 500 <= status <= 599


def _backoff_delay(attempt: int) -> float:
    """指数退避 + 抖动，封顶 :data:`MAX_BACKOFF_SECONDS`。"""
    return min((0.5 * (2**attempt)) * (0.8 + random.random() * 0.4), MAX_BACKOFF_SECONDS)


class _BaseRetryMixin:
    """失败双桶计数 + 重试退避辅助（BaseWebSource 组合用，W3/W4）。

    * ``_failures``——**传输失败桶**：HTTP 非 2xx、断连、超时等；
      达 :attr:`DEPRECATE_AFTER_FAILURES` 判定源下线。
    * ``_parse_failures``——**解析失败桶**：响应可取但解析失败（非 JSON、
      结构变更）；达 :attr:`DEPRECATE_AFTER_PARSE_FAILURES`（默认为传输
      阈值的 2 倍）判定源下线。两桶独立计数，避免「解析 bug」与「源下线」
      互相污染（§2 P1 #11）；任一次成功请求双桶清零。
    """

    #: 连续**传输**失败多少次后判定为「接口下线」
    DEPRECATE_AFTER_FAILURES = 20
    #: 连续**解析**失败多少次后判定为「接口下线」（解析异常通常先于下线，
    #: 给 2 倍容忍度，避免一次前端改版立即熔断）
    DEPRECATE_AFTER_PARSE_FAILURES = 40

    # -- 失败计数（分桶） --------------------------------------------------- #
    def _record_failure(self, *, parse: bool = False) -> None:
        """记录一次失败：传输桶（默认）或解析桶。"""
        if parse:
            self._parse_failures += 1
        else:
            self._failures += 1

    def _reset_failures(self) -> None:
        """成功请求后双桶清零（源已被证明存活）。"""
        self._failures = 0
        self._parse_failures = 0

    def _check_deprecated(self) -> None:
        """任一失败桶达阈值即判定接口下线。"""
        if self._failures >= self.DEPRECATE_AFTER_FAILURES:
            raise SourceDeprecated(
                f"{self.source_name} 连续传输失败 {self._failures} 次，判定为接口下线",
                context={
                    "source": self.source_name,
                    "bucket": "transport",
                    "failures": self._failures,
                },
            )
        if self._parse_failures >= self.DEPRECATE_AFTER_PARSE_FAILURES:
            raise SourceDeprecated(
                f"{self.source_name} 连续解析失败 {self._parse_failures} 次，判定为接口下线",
                context={
                    "source": self.source_name,
                    "bucket": "parse",
                    "failures": self._parse_failures,
                },
            )
