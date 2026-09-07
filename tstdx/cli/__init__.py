# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""命令行入口（§35）：``tstdx`` 控制台脚本（REFACTOR_PLAN_v8 P3 起为包）。

子命令
------
* ``version``             —— 打印版本号
* ``bars SYMBOL``         —— 拉取 K 线（默认日线 320 根）
* ``quotes SYM [SYM...]`` —— 实时行情快照
* ``count MARKET``        —— 某市场证券总数（0=深 1=沪 或 sh/sz）
* ``info SYMBOL``         —— 财务基础信息
* ``server-test``         —— 主站测速排名
* ``serve``               —— 启动 HTTP 行情网关（uvicorn，40+ 端点，准确数量以启动日志为准）
* ``list MARKET``         —— 代码表（0x044D，分页拉取）
* ``quotes-snapshot SYM…`` —— 批量行情快照（优先 0x054C）
* ``trades SYMBOL``       —— 当日逐笔成交（0x0FC5）
* ``blocks``              —— 板块行情（0x07E5）
* ``goods SYMBOL``        —— 商品行情（期货/期权/外汇，7727）
* ``f10 SYMBOL``          —— F10 资料（默认列栏目目录；--file 下载并解析正文）
* ``stream SYM…``         —— 实时行情流（轮询+增量+重连）
* ``changes``             —— 盘中异动池（16 类异动，东财 Web 源）
* ``hot``                 —— 股吧个股人气榜（东财 Web 源）
* ``feedback submit/stats`` —— 反馈上报（脱敏）/ 本地使用统计
* ``probe CMD``           —— 未知命令主动探测（非交易时段门禁 + 归档 DRAFT）

模块布局：``_common``（表格输出/公共参数）+ ``cmds_market`` / ``cmds_web`` /
``cmds_hosts``（按域的命令实现）+ ``parser``（argparse 装配）。
本 ``__init__`` 保持 ``tstdx.cli:main`` 控制台脚本入口与
``from tstdx.cli import build_parser, main`` 的历史导入路径不变。

全部命令走 :class:`~tstdx.client.TdxClient`（在线）。离线调试可用
``tstdx bars 600000 --source cache`` 走 golden 缓存回放（需 ``--golden`` 指向
``tests/golden``）。
"""

from __future__ import annotations

import sys
from collections.abc import Sequence

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

# 上一节显式 import 的 _cmd_* 与 helpers 即兼容导出面（测试/下游引用保持不变）。


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except KeyboardInterrupt:  # pragma: no cover
        print("已中断", file=sys.stderr)
        return 130


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
