# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""命令行入口（§35）：``tstdx`` 控制台脚本。

CLI is an external error boundary. Normal command failures are rendered from the
same safe :class:`tstdx.error_envelope.ErrorEnvelope` used by service surfaces;
process-control ``KeyboardInterrupt`` keeps its native exit semantics.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Sequence

from ..error_envelope import to_error_envelope
from ._common import _fmt, _pct, _print_bars_table, _print_rows, _print_table, _resolve_hosts
from .cmds_hosts import _cmd_feedback, _cmd_probe, _cmd_serve, _cmd_server_test
from .cmds_market import (
    _cmd_bars,
    _cmd_blocks,
    _cmd_count,
    _cmd_f10,
    _cmd_goods,
    _cmd_info,
    _cmd_list,
    _cmd_quotes,
    _cmd_quotes_snapshot,
    _cmd_stream,
    _cmd_trades,
    _cmd_version,
)
from .cmds_web import (
    _cmd_adjusted_bars,
    _cmd_all_market,
    _cmd_baidu,
    _cmd_changes,
    _cmd_fund,
    _cmd_hot,
    _cmd_index,
    _cmd_minute_klines,
)
from .parser import build_parser

__all__ = ["build_parser", "main"]


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except KeyboardInterrupt:  # pragma: no cover
        print("已中断", file=sys.stderr)
        return 130
    except Exception as exc:  # CLI boundary: never catches BaseException
        envelope = to_error_envelope(exc)
        print(
            json.dumps({"error": envelope.to_dict()}, ensure_ascii=False),
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
