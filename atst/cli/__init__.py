# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""v13 command-line entrypoint.

CLI is a transport adapter over Client. Business routing is forbidden here;
normal failures are rendered through the same ErrorEnvelope contract used by
HTTP/WS/MCP. Process-control KeyboardInterrupt preserves native exit semantics.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Sequence

from ..error_envelope import to_error_envelope
from ..errors import TdxError
from .parser import build_parser
from .runtime_commands import (
    _cmd_changes,
    _cmd_list,
    _cmd_quotes_snapshot,
    _cmd_serve,
    cmd_stream,
)

# Legacy v12 command handlers re-exported for backward-compatible test imports.
_cmd_stream = cmd_stream

__all__ = [
    "build_parser",
    "main",
    "_cmd_changes",
    "_cmd_list",
    "_cmd_quotes_snapshot",
    "_cmd_serve",
    "_cmd_stream",
]


def _emit_error_envelope(exc: Exception) -> None:
    print(
        json.dumps({"error": to_error_envelope(exc).to_dict()}, ensure_ascii=False),
        file=sys.stderr,
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args) or 0)
    except KeyboardInterrupt:  # pragma: no cover
        print("已中断", file=sys.stderr)
        return 130
    except TdxError as exc:
        # v13：领域错误（TdxError 家族）走规范化信封 + 专用退出码 2，
        # 与原生未捕获异常（E9000 / 退出码 1）区分，便于脚本判定失败类别。
        _emit_error_envelope(exc)
        return 2
    except Exception as exc:
        _emit_error_envelope(exc)
        return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
