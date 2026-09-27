# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""工具 CLI 控制台编码助手（Windows GBK 控制台友好）。

Windows 控制台默认代码页 936（GBK），直接 ``print`` 含非 GBK 字符的
报告（如 ``✓`` / 中文标点回退字形）会抛 ``UnicodeEncodeError`` 或输出
乱码。所有 atst 工具的 ``main()`` 入口统一调用 :func:`setup_console`：
强制 stdout/stderr 为 UTF-8 并以 ``errors="replace"`` 容错。
"""

from __future__ import annotations

import contextlib
import sys

__all__ = ["setup_console"]


def setup_console() -> None:
    """把 stdout/stderr 重配置为 UTF-8（幂等；重定向流自动跳过）。"""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            with contextlib.suppress(ValueError, OSError):  # 已重定向等场景
                stream.reconfigure(encoding="utf-8", errors="replace")
