# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License
"""argparse 装配（build_parser）：各子命令的参数定义。

REFACTOR_PLAN_v8 P3：自 ``tstdx/cli.py`` 拆出，行为不变。
"""

from __future__ import annotations

import argparse

from .cmds_hosts import (
    _cmd_feedback,
    _cmd_hosts_audit,
    _cmd_hosts_list,
    _cmd_hosts_scan,
    _cmd_probe,
    _cmd_serve,
    _cmd_server_test,
)
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
    _cmd_margin,
    _cmd_minute_klines,
    _cmd_sector_flow,
)

__all__ = ["build_parser"]


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="tstdx", description="通达信行情通用包命令行工具")

    # 真正发起 market/provider 请求的命令可显式覆盖 endpoint。
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--timeout", type=float, default=5.0, help="单请求超时（秒）")
    common.add_argument("--host", action="append", help="主站 host:port（可多次指定）")

    # host-management 命令不接受单个 --host selector；scan/list/audit 都操作
    # 候选池/排名整体。保留 timeout 的前置/后置兼容写法，同时避免子 parser
    # 的默认值覆盖 `tstdx hosts --timeout 2 audit` 已解析的父级值。
    timeout_common = argparse.ArgumentParser(add_help=False)
    timeout_common.add_argument("--timeout", type=float, default=5.0, help="单请求超时（秒）")
    timeout_override = argparse.ArgumentParser(add_help=False)
    timeout_override.add_argument(
        "--timeout",
        type=float,
        default=argparse.SUPPRESS,
        help="单请求超时（秒）",
    )

    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("version", parents=[common], help="打印版本号").set_defaults(func=_cmd_version)

    b = sub.add_parser("bars", parents=[common], help="拉取 K 线")
    b.add_argument("symbol")
    b.add_argument("--period", default="day")
    b.add_argument("--count", type=int, default=320)
    b.add_argument("--json", action="store_true")
    b.add_argument(
        "--source",
        choices=["tdx", "router", "cache"],
        default="tdx",
        help="数据源：tdx=在线主站（默认）；router/cache=降级+golden缓存回放（离线）",
    )
    b.add_argument("--golden", default=None, help="golden 缓存根目录（cache 源用）")
    b.set_defaults(func=_cmd_bars)

    q = sub.add_parser("quotes", parents=[common], help="实时行情快照")
    q.add_argument("symbols", nargs="+")
    q.add_argument("--json", action="store_true")
    q.add_argument(
        "--source",
        choices=["tdx", "router", "cache"],
        default="tdx",
        help="数据源：tdx=在线主站（默认）；router/cache=降级+golden缓存回放（离线）",
    )
    q.add_argument("--golden", default=None, help="golden 缓存根目录（cache 源用）")
    q.set_defaults(func=_cmd_quotes)

    c = sub.add_parser("count", parents=[common], help="市场证券总数")
    c.add_argument("market", help="0/1 或 sh/sz")
    c.set_defaults(func=_cmd_count)

    i = sub.add_parser("info", parents=[common], help="财务基础信息")
    i.add_argument("symbol")
    i.add_argument("--json", action="store_true")
    i.set_defaults(func=_cmd_info)

    s = sub.add_parser("server-test", parents=[timeout_common], help="主站测速排名")
    s.set_defaults(func=_cmd_server_test)

    hosts = sub.add_parser("hosts", parents=[timeout_common], help="主站管理")
    hs = hosts.add_subparsers(dest="hosts_command", required=True)
    hs.add_parser(
        "scan",
        parents=[timeout_override],
        help="并发测速候选池并写 STANDARD 排名文件",
    ).set_defaults(func=_cmd_hosts_scan)
    hs.add_parser(
        "list",
        parents=[timeout_override],
        help="查看当前生效的 STANDARD 主站池",
    ).set_defaults(func=_cmd_hosts_list)
    audit_p = hs.add_parser(
        "audit",
        parents=[timeout_override],
        help="5 族候选主站巡检（STANDARD/EXTENDED/MAC/GOODS/F10）",
    )
    audit_p.add_argument(
        "--family",
        choices=["quotation", "ex_quotation", "mac_quotation", "goods", "f10"],
        action="append",
        help="要巡检的协议族；可多次指定；默认全部 5 族",
    )
    audit_p.add_argument("--samples", type=int, default=1, help="每主机采样次数（默认 1）")
    audit_p.add_argument("--workers", type=int, default=16, help="并发度（默认 16）")
    audit_p.add_argument(
        "--report",
        default="./host_audit_report.json",
        help="巡检 JSON 报告输出路径",
    )
    audit_p.add_argument("--markdown", default="", help="可选 Markdown 摘要路径")
    audit_p.add_argument(
        "--ranking-file",
        default="~/.tstdx/server_ranking.json",
        help="STANDARD 运行时排名文件路径",
    )
    audit_p.add_argument("--strict", action="store_true", help="STANDARD 无 healthy 时退出 1")
    audit_p.add_argument("--quiet", action="store_true", help="关闭逐主机进度输出")
    audit_p.add_argument(
        "--no-save-ranking",
        action="store_true",
        help="不写入 STANDARD 排名文件（干跑）",
    )
    audit_p.add_argument(
        "--hosts-file",
        default=None,
        help="外部候选文件（支持纯文本/JSON list/JSON dict，社区贡献主站入口）",
    )
    audit_p.set_defaults(func=_cmd_hosts_audit)

    sv = sub.add_parser("serve", parents=[common], help="启动 HTTP 行情网关（uvicorn）")
    sv.add_argument("--bind", default="127.0.0.1", help="监听地址")
    sv.add_argument("--port", type=int, default=8000, help="监听端口")
    sv.set_defaults(func=_cmd_serve)

    lst = sub.add_parser("list", parents=[common], help="代码表（0x044D，分页拉取）")
    lst.add_argument("market", help="0/1 或 sh/sz/bj")
    lst.add_argument("--start", type=int, default=0)
    lst.add_argument("--count", type=int, default=1000, help="最多返回条数")
    lst.add_argument("--json", action="store_true")
    lst.set_defaults(func=_cmd_list)

    qs = sub.add_parser("quotes-snapshot", parents=[common], help="批量行情快照（优先 0x054C）")
    qs.add_argument("symbols", nargs="+")
    qs.add_argument("--json", action="store_true")
    qs.set_defaults(func=_cmd_quotes_snapshot)

    tr = sub.add_parser("trades", parents=[common], help="当日逐笔成交（0x0FC5）")
    tr.add_argument("symbol")
    tr.add_argument("--start", type=int, default=0)
    tr.add_argument("--count", type=int, default=0, help="0=全部")
    tr.add_argument("--json", action="store_true")
    tr.set_defaults(func=_cmd_trades)

    bl = sub.add_parser("blocks", parents=[common], help="板块行情（0x07E5）")
    bl.add_argument("--block-type", type=int, default=0, help="0概念/1行业/2地区/3指数")
    bl.add_argument("--count", type=int, default=1000)
    bl.add_argument("--json", action="store_true")
    bl.set_defaults(func=_cmd_blocks)

    gd = sub.add_parser("goods", parents=[common], help="商品行情（期货/期权/外汇，7727）")
    gd.add_argument("symbol")
    gd.add_argument("--kind", choices=["bars", "quote"], default="bars")
    gd.add_argument("--period", default="day")
    gd.add_argument("--count", type=int, default=320)
    gd.add_argument("--json", action="store_true")
    gd.set_defaults(func=_cmd_goods)

    f10p = sub.add_parser("f10", parents=[common], help="F10 资料（栏目目录/正文，7615）")
    f10p.add_argument("symbol")
    f10p.add_argument("--file", default=None, help="栏目文件名（如 gsgk.dat）；缺省列栏目目录")
    f10p.add_argument("--json", action="store_true")
    f10p.set_defaults(func=_cmd_f10)

    ab = sub.add_parser("adjusted-bars", parents=[common], help="复权 K 线（前/后/定点）")
    ab.add_argument("symbol")
    ab.add_argument("--method", choices=["qfq", "hfq", "fixed", "none"], default="qfq")
    ab.add_argument("--period", default="day")
    ab.add_argument("--count", type=int, default=320)
    ab.add_argument("--json", action="store_true")
    ab.set_defaults(func=_cmd_adjusted_bars)

    am = sub.add_parser("all-market", parents=[common], help="全市场行情摘要（Web 源）")
    am.add_argument("--node", default="hs_a", help="市场节点（hs_a/cyb/hk/us 等）")
    am.add_argument("--source", choices=["sina", "tencent"], default="sina")
    am.add_argument("--page-size", type=int, default=80)
    am.add_argument("--max-pages", type=int, default=None, help="最大页数（缺省全拉）")
    am.add_argument("--json", action="store_true")
    am.set_defaults(func=_cmd_all_market)

    mk = sub.add_parser("minute-klines", parents=[common], help="分钟 K 线（Web 源）")
    mk.add_argument("symbol")
    mk.add_argument("--period", default="5min", help="1min/5min/15min/30min/60min")
    mk.add_argument("--count", type=int, default=240)
    mk.add_argument("--json", action="store_true")
    mk.set_defaults(func=_cmd_minute_klines)

    bd = sub.add_parser("baidu", parents=[common], help="百度财经源（A 股 Web 源）")
    bd.add_argument("symbol")
    bd.add_argument("--kind", choices=["kline", "minute", "ticks", "quote"], default="kline")
    bd.add_argument("--period", default="day", help="K 线周期 day/week/month")
    bd.add_argument("--count", type=int, default=320, help="K 线根数（>250 自动分页）")
    bd.add_argument("--end-time", type=int, default=None, help="K 线断点游标（unix 秒）")
    bd.add_argument("--limit", type=int, default=200, help="逐笔条数上限")
    bd.add_argument("--json", action="store_true")
    bd.set_defaults(func=_cmd_baidu)

    fd = sub.add_parser("fund", parents=[common], help="东财基金源（净值/估值/列表）")
    fd.add_argument(
        "action",
        choices=["nav", "estimate", "list"],
        help="nav=历史净值 estimate=实时估值 list=基金列表",
    )
    fd.add_argument("code", nargs="?", default=None, help="6 位基金代码（nav/estimate 必填）")
    fd.add_argument("--page-size", type=int, default=100, help="历史净值单页条数")
    fd.add_argument("--page-index", type=int, default=1, help="历史净值页码（1 起）")
    fd.add_argument("--json", action="store_true")
    fd.set_defaults(func=_cmd_fund)

    ix = sub.add_parser("index", parents=[common], help="指数数据（成分股列表，Web 源）")
    ix.add_argument(
        "action",
        choices=["constituents"],
        help="constituents=指数成分股列表",
    )
    ix.add_argument(
        "code",
        nargs="?",
        default=None,
        help="指数代码（如 000300 沪深300 / 000905 中证500 / 930050 中证A50）",
    )
    ix.add_argument("--json", action="store_true")
    ix.set_defaults(func=_cmd_index)

    mg = sub.add_parser("margin", parents=[common], help="融资融券明细（东财 Web 源）")
    mg.add_argument("symbol")
    mg.add_argument("--days", type=int, default=10, help="最近 N 个交易日（0=全部/按页）")
    mg.add_argument("--json", action="store_true")
    mg.set_defaults(func=_cmd_margin)

    sf = sub.add_parser("sector-flow", parents=[common], help="板块资金流排行（东财 Web 源）")
    sf.add_argument("--board", choices=["industry", "concept", "region"], default="industry")
    sf.add_argument("--sort", default="main_net", help="main_net/main_ratio/change_pct/amount")
    sf.add_argument("--limit", type=int, default=20)
    sf.add_argument("--json", action="store_true")
    sf.set_defaults(func=_cmd_sector_flow)

    st = sub.add_parser("stream", parents=[common], help="实时行情流（轮询+增量+重连）")
    st.add_argument("symbols", nargs="+")
    st.add_argument("--interval", type=float, default=1.0, help="轮询间隔（秒）")
    st.add_argument("--seconds", type=float, default=10.0, help="运行时长（秒）")
    st.add_argument("--diff", action="store_true", help="仅输出变化字段")
    st.set_defaults(func=_cmd_stream)

    ch = sub.add_parser("changes", parents=[common], help="盘中异动池（16 类异动，Web 源）")
    ch.add_argument("--types", default="", help="逗号分隔异动类型（如 8201,8193）；空=全部")
    ch.add_argument("--page", type=int, default=1)
    ch.add_argument("--size", type=int, default=30)
    ch.add_argument("--json", action="store_true")
    ch.set_defaults(func=_cmd_changes)

    hot = sub.add_parser("hot", parents=[common], help="股吧个股人气榜（Web 源）")
    hot.add_argument("--page", type=int, default=1)
    hot.add_argument("--size", type=int, default=30)
    hot.add_argument("--json", action="store_true")
    hot.set_defaults(func=_cmd_hot)

    fb = sub.add_parser("feedback", parents=[common], help="反馈上报（7 步脱敏，默认不发）")
    fb_sub = fb.add_subparsers(dest="feedback_command", required=True)

    fsub = fb_sub.add_parser("submit", help="上报一条错误/问题描述（脱敏后发送或落盘）")
    fsub.add_argument("--message", required=True, help="问题描述（会经 7 步脱敏流水线）")
    fsub.add_argument("--endpoint", default=None, help="上报端点 URL（缺省用环境变量）")
    fsub.add_argument("--store-dir", default=None, help="本地落盘目录（缺省用环境变量）")
    fsub.set_defaults(func=_cmd_feedback)

    fstat = fb_sub.add_parser("stats", help="本地用户使用统计快照（按进程独立）")
    fstat.add_argument("--json", action="store_true")
    fstat.set_defaults(func=_cmd_feedback)

    pb = sub.add_parser("probe", parents=[common], help="未知命令主动探测（默认拒绝盘中）")
    pb.add_argument("cmd", help="命令号（如 0x053e 或十进制 1342）")
    pb.add_argument("--market", type=int, default=0, help="请求市场编号（0=深 1=沪）")
    pb.add_argument("--code", default="000001", help="6 位代码（默认 000001）")
    pb.add_argument("--archive-dir", default="PROTOCOL_SPEC/UNKNOWN", help="DRAFT 归档目录")
    pb.add_argument("--rate-limit", type=float, default=1.0, help="探测速率 req/s（钳制 [0.1, 5]）")
    pb.add_argument(
        "--allow-trading-hours",
        action="store_true",
        help="允许盘中探测（默认拒绝；仅维护者环境使用）",
    )
    pb.add_argument("--json", action="store_true")
    pb.set_defaults(func=_cmd_probe)
    return p
