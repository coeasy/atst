# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""CLI 命令处理器的 re-export shim（P2-3/F-5 按域拆分后的兼容层）。

实现按域拆到四个模块（M7）：v13 数据命令在 :mod:`atst.cli.cmds_kernel`，hosts /
server-test / serve / feedback / probe 在 :mod:`atst.cli.cmds_hosts`，Web 源门面薄壳
在 :mod:`atst.cli.cmds_web`，直连 market 在 :mod:`atst.cli.cmds_market`。
:mod:`atst.cli.parser` 的既有 import（全部指向本模块）不改一个字；历史测试按
``atst.cli.runtime_commands._cmd_*`` 取处理器的路径也继续成立。拆分后错误出口统一为
"异常上抛交 :func:`atst.cli.main` 信封"，本层只转发，不再有实现。
"""

from __future__ import annotations

from .cmds_hosts import (
    _age_text,
    _cmd_feedback,
    _cmd_hosts_audit,
    _cmd_hosts_discover,
    _cmd_hosts_list,
    _cmd_hosts_scan,
    _cmd_probe,
    _cmd_serve,
    _cmd_server_test,
    _parse_cmd_number,
)
from .cmds_kernel import (
    _policy,
    _print,
    cmd_bars,
    cmd_capabilities,
    cmd_minute,
    cmd_query,
    cmd_quotes,
    cmd_security_count,
    cmd_security_list,
    cmd_snapshot,
    cmd_stream,
    cmd_trades,
    cmd_version,
)
from .cmds_market import (
    _cmd_blocks,
    _cmd_f10,
    _cmd_goods,
    _cmd_list,
    _cmd_quotes_snapshot,
    _cmd_universe,
)
from .cmds_web import (
    _cell,
    _ClientRows,
    _cmd_adjusted_bars,
    _cmd_all_market,
    _cmd_baidu,
    _cmd_changes,
    _cmd_daily_enriched,
    _cmd_fund,
    _cmd_hot,
    _cmd_index,
    _cmd_margin,
    _cmd_minute_klines,
    _cmd_sector_flow,
)

__all__ = [
    "_ClientRows",
    "_age_text",
    "_cell",
    "_cmd_adjusted_bars",
    "_cmd_all_market",
    "_cmd_baidu",
    "_cmd_blocks",
    "_cmd_changes",
    "_cmd_daily_enriched",
    "_cmd_f10",
    "_cmd_feedback",
    "_cmd_fund",
    "_cmd_goods",
    "_cmd_hot",
    "_cmd_hosts_audit",
    "_cmd_hosts_discover",
    "_cmd_hosts_list",
    "_cmd_hosts_scan",
    "_cmd_index",
    "_cmd_list",
    "_cmd_margin",
    "_cmd_minute_klines",
    "_cmd_probe",
    "_cmd_quotes_snapshot",
    "_cmd_sector_flow",
    "_cmd_serve",
    "_cmd_server_test",
    "_cmd_universe",
    "_parse_cmd_number",
    "_policy",
    "_print",
    "cmd_bars",
    "cmd_capabilities",
    "cmd_minute",
    "cmd_query",
    "cmd_quotes",
    "cmd_security_count",
    "cmd_security_list",
    "cmd_snapshot",
    "cmd_stream",
    "cmd_trades",
    "cmd_version",
]
