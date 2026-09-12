# Copyright (c) 2026 tstdx contributors
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
from .parser import build_parser

__all__ = ["build_parser", "main"]


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args) or 0)
    except KeyboardInterrupt:  # pragma: no cover
        print("已中断", file=sys.stderr)
        return 130
    except Exception as exc:
        envelope = to_error_envelope(exc)
        print(
            json.dumps({"error": envelope.to_dict()}, ensure_ascii=False),
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
