# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""``python -m atst`` 入口（与 ``atst`` 控制台脚本等价）。"""

from __future__ import annotations

import sys

if __name__ == "__main__":  # pragma: no cover
    from .cli import main

    sys.exit(main())
