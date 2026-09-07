# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""弃用策略（§29）。

提供 @deprecated 装饰器、DeprecationPolicy 类、
以及弃用警告的统一管理。

支持特性：
- 标记弃用版本（since）
- 预计移除版本（removed_in）
- 迁移指南链接（migration_guide）
- 警告级别控制（warning / error）
"""

from __future__ import annotations

import warnings
from collections.abc import Callable
from functools import wraps
from typing import Any

__all__ = [
    "DeprecationPolicy",
    "deprecated",
    "DeprecationInfo",
    "get_deprecation_count",
    "reset_deprecation_count",
]

# 全局弃用计数器
_deprecation_count: int = 0


def get_deprecation_count() -> int:
    """获取当前会话的弃用警告总数。"""
    return _deprecation_count


def reset_deprecation_count() -> None:
    """重置弃用计数器（测试用）。"""
    global _deprecation_count
    _deprecation_count = 0


_DEPRECATED_INFO_FIELDS = [
    "since",
    "removed_in",
    "message",
    "migration_guide",
]


class DeprecationInfo:
    """弃用信息描述符。"""

    def __init__(
        self,
        since: str = "",
        removed_in: str = "",
        message: str = "",
        migration_guide: str = "",
    ) -> None:
        self.since = since
        self.removed_in = removed_in
        self.message = message
        self.migration_guide = migration_guide

    def __str__(self) -> str:
        parts = [self.message]
        if self.since:
            parts.append(f"since v{self.since}")
        if self.removed_in:
            parts.append(f"will be removed in v{self.removed_in}")
        if self.migration_guide:
            parts.append(f"see {self.migration_guide}")
        return "; ".join(parts)


class DeprecationPolicy:
    """弃用策略管理器。

    管理弃用警告的全局行为：

    * ``mode``：``"warning"`` 或 ``"error"``
    * ``since``：默认弃用起始版本
    * ``removal_gap``：默认移除间隔（minor 版本号）

    使用示例::

        policy = DeprecationPolicy(mode="warning", since="0.4.0")

        @policy.deprecated(reason="use new_api() instead")
        def old_api():
            ...
    """

    def __init__(
        self,
        mode: str = "warning",
        since: str = "",
        removal_gap: int = 2,
    ) -> None:
        self.mode = mode
        self.since = since
        self.removal_gap = removal_gap

    def deprecated(
        self,
        *,
        reason: str = "",
        since: str | None = None,
        removed_in: str | None = None,
        migration_guide: str = "",
    ) -> Callable:
        """装饰器工厂：标记函数或类为弃用。"""
        since = since or self.since
        if removed_in is None and since:
            # 计算预计移除版本
            parts = since.split(".")
            if len(parts) >= 2:
                try:
                    major = int(parts[0])
                    minor = int(parts[1]) + self.removal_gap
                    removed_in = f"{major}.{minor}.0"
                except ValueError:
                    removed_in = ""

        info = DeprecationInfo(
            since=since,
            removed_in=removed_in or "",
            message=reason or "Deprecated",
            migration_guide=migration_guide,
        )

        def decorator(obj: Any) -> Any:
            if isinstance(obj, type):
                return self._decorate_class(obj, info)
            return self._decorate_function(obj, info)

        return decorator

    def _decorate_function(self, func: Callable, info: DeprecationInfo) -> Callable:
        @wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            self._warn(func.__qualname__, info)
            return func(*args, **kwargs)

        # 动态属性挂载：mypy 无法静态表达 _Wrapped / type 的扩展属性
        wrapper._deprecation_info = info  # type: ignore[attr-defined]
        return wrapper

    def _decorate_class(self, cls: type, info: DeprecationInfo) -> type:
        policy = self
        original_init = cls.__init__  # type: ignore[misc]  # 动态元编程，运行时安全

        @wraps(original_init)
        def new_init(self: Any, *args: Any, **kwargs: Any) -> None:
            policy._warn(cls.__name__, info)
            original_init(self, *args, **kwargs)

        cls.__init__ = new_init  # type: ignore[misc]
        cls._deprecation_info = info  # type: ignore[attr-defined]
        return cls

    def _warn(self, name: str, info: DeprecationInfo) -> None:
        global _deprecation_count
        _deprecation_count += 1
        msg = f"{name} is deprecated: {info}"
        if self.mode == "error":
            raise DeprecationError(msg)
        warnings.warn(msg, DeprecationWarning, stacklevel=3)


class DeprecationError(Exception):
    """弃用模式为 'error' 时抛出的异常。"""

    pass


def deprecated(
    *,
    reason: str = "",
    since: str = "",
    removed_in: str = "",
    migration_guide: str = "",
) -> Callable:
    """全局弃用装饰器（默认使用 warning 模式）。

    使用示例::

        @deprecated(
            reason="use new_api() instead",
            since="0.4.0",
            removed_in="0.6.0",
        )
        def old_api():
            ...
    """
    _default_policy = DeprecationPolicy(mode="warning", since=since)
    return _default_policy.deprecated(
        reason=reason,
        since=since,
        removed_in=removed_in,
        migration_guide=migration_guide,
    )
