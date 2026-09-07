# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""``python -m tstdx`` 入口（与 ``tstdx`` 控制台脚本等价）。"""

from __future__ import annotations

import sys

if __name__ == "__main__":  # pragma: no cover
    from .cli import main

    sys.exit(main())
