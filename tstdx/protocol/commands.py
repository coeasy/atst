# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""TDX 命令号登记表（§5）。

这是"协议全覆盖"的**账本**：整个 16 位命令空间中的每一个已知命令都在此登记，
未登记者走 :mod:`tstdx.protocol.generic` 的 L2 通用解析 + L3 原始透传，永不丢包。

每条命令的 ``tier`` 字段标明当前支持级别::

    L1  已实现精确解析（有 spec、有 golden、有单元测试）
    L2  仅有通用启发式解析（结构未定，返回带置信度的结果）
    D   已声明但未验证（等待 Golden 抓包校正后升级为 L1）

.. important::
   标 ``verified=False`` 的命令其语义来自公开资料推断，**必须**通过
   ``tests/golden/`` 中自采集样本校正后才能置为 True 并升级到 L1。
   这是"洁净室"流程的一部分（见 ``ORIGINALITY/``）。
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass

__all__ = [
    "Family",
    "Command",
    "COMMANDS",
    "CMD",
    "cmd",
    "get_command",
    "by_family",
    "by_status",
    "unknown_command_ids",
    "TIER_L1",
    "TIER_L2",
    "TIER_DECLARED",
    "STATUS_ONLINE",
    "STATUS_OFFLINE",
    "STATUS_DEGRADED",
]

TIER_L1 = "L1"
TIER_L2 = "L2"
TIER_DECLARED = "D"

#: 命令运行时状态（「实测下线事实」的结构化归宿，区别于语义验证 verified）
STATUS_ONLINE = "online"
STATUS_OFFLINE = "offline"  # 多主站实测无响应
STATUS_DEGRADED = "degraded"  # 可用但需回退/仅部分主站


class Family:
    """协议族。"""

    STANDARD = "quotation"  # 7709 标准
    EXTENDED = "ex_quotation"  # 7727 扩展市场
    MAC = "mac_quotation"  # 7709 MAC 专属服务器
    F10 = "f10"  # 7615 / TQLEX 资料网关
    GOODS = "goods"  # 商品语义（期货/期权/外汇）


@dataclass(frozen=True)
class Command:
    """一条命令的登记信息。"""

    cmd: int
    name: str
    family: str = Family.STANDARD
    tier: str = TIER_DECLARED
    verified: bool = False
    #: 运行时状态（实测）：online 默认 / offline 多主站无响应 / degraded 需回退
    status: str = STATUS_ONLINE
    #: 一行语义。无 PROTOCOL_SPEC 条目的命令以此为其唯一描述，且随 ``_guard_offline``
    #: 的报错文案直接到达调用方——账本里每条命令都必须填。
    summary: str = ""

    @property
    def hex(self) -> str:
        return f"0x{self.cmd:04x}"

    @property
    def port(self) -> int:
        return {
            Family.STANDARD: 7709,
            Family.EXTENDED: 7727,
            Family.MAC: 7709,
            Family.GOODS: 7727,
            Family.F10: 7709,
        }.get(self.family, 7709)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Command {self.hex} {self.name} [{self.family}/{self.tier}]>"


def _c(cmd: int, name: str, summary: str = "", **kw) -> Command:
    return Command(cmd=cmd, name=name, summary=summary, **kw)


_STD: list[Command] = [
    _c(
        0x000D,
        "HANDSHAKE",
        "连接握手：服务端日期时间、交易时段、主站名、产品标识",
        tier=TIER_L2,
        verified=True,
    ),
    _c(0x0FDB, "LOGIN2", "二次登录/初始化（部分主站要求）", tier=TIER_L2),
    _c(0x0004, "HEARTBEAT", "心跳保活", tier=TIER_L2, verified=True),
    _c(0x0015, "PING", "连接测试", tier=TIER_L2),
    _c(
        0x044E,
        "SECURITY_COUNT",
        "市场代码数量",
        tier=TIER_L1,
        verified=True,
    ),
    _c(
        0x044D,
        "SECURITY_LIST",
        "代码表（证券列表，分页 1000/页）",
        tier=TIER_L2,
        verified=True,
        status=STATUS_OFFLINE,
    ),
    _c(0x0450, "SECURITY_LIST_LEGACY", "旧版证券列表", tier=TIER_DECLARED, status=STATUS_OFFLINE),
    _c(0x0452, "PRICE_LIMIT", "特殊品种涨跌停限制表", tier=TIER_DECLARED),
    _c(
        0x052D,
        "SECURITY_BARS",
        "K 线 / 周期线（日/周/月/季/年/分钟）",
        tier=TIER_L1,
        verified=True,
    ),
    _c(0x0FD1, "SPARKLINE", "小走势图（sparkline）", tier=TIER_DECLARED),
    _c(
        0x0537,
        "MINUTE_TODAY",
        "当日分时数据（inferred；深市有响应但真实记录布局仍待 golden 锁定）",
        tier=TIER_L2,
        verified=False,
        status=STATUS_DEGRADED,
    ),
    _c(
        0x0FB4,
        "MINUTE_HISTORY",
        "指定日期历史分时",
        tier=TIER_L2,
        status=STATUS_OFFLINE,
    ),
    _c(
        0x0FEB,
        "MINUTE_RECENT",
        "近期历史分时（多日；2026-09 探测服务端直接断连，判定不识别）",
        tier=TIER_DECLARED,
        status=STATUS_OFFLINE,
    ),
    _c(
        0x051B,
        "MINUTE_SUBPLOT",
        "分时副图（量比/委比等；2026-09 探测有响应但为 2B 空布局，语义待采）",
        tier=TIER_DECLARED,
        status=STATUS_DEGRADED,
    ),
    _c(
        0x0530,
        "REALTIME_QUOTE",
        "实时行情快照（单只，当前唯一可用）",
        tier=TIER_L1,
        verified=True,
    ),
    _c(
        0x053E,
        "QUOTES_LEGACY",
        "旧版批量行情（原生五档）",
        tier=TIER_DECLARED,
        status=STATUS_OFFLINE,
    ),
    _c(
        0x054C,
        "QUOTES_SNAPSHOT",
        "批量行情快照（多主站实测已下线；client 自动回退逐只 0x0530）",
        tier=TIER_DECLARED,
        status=STATUS_OFFLINE,
    ),
    _c(
        0x0547,
        "QUOTES_DEPTH_PUSH",
        "五档刷新 / push 队列（实时推送）",
        tier=TIER_L2,
        verified=True,
    ),
    _c(0x054B, "QUOTES_BY_CATEGORY", "分类行情（按市场或板块分页排序）", tier=TIER_DECLARED),
    _c(
        0x0FC5,
        "TRADE_TODAY",
        "当日成交明细（inferred；真实记录布局尚未由 golden 锁定）",
        tier=TIER_L2,
        verified=False,
    ),
    _c(0x0FC6, "TRADE_TODAY_ALT", "当日成交明细（备用命令号）", tier=TIER_DECLARED),
    _c(0x0FB5, "TRADE_HISTORY", "历史成交明细", tier=TIER_DECLARED),
    _c(
        0x000F,
        "CAPITAL_CHANGES",
        "股本变迁 / 除权除息（GBBQ，inferred；真实记录布局尚未由 golden 锁定）",
        tier=TIER_L2,
        verified=False,
    ),
    _c(
        0x0010,
        "FINANCE_INFO",
        "财务基础信息（股本/EPS/资产负债等，inferred；字段序尚未由 golden 锁定）",
        tier=TIER_L2,
        verified=False,
    ),
    _c(0x001E, "FINANCE_EXT", "扩展财务数据（多期报表）", tier=TIER_DECLARED),
    _c(
        0x06B9,
        "FILE_DOWNLOAD",
        "服务器文件分块读取（F10/资讯正文）",
        tier=TIER_L2,
    ),
    _c(
        0x051A,
        "VOLUME_PRICE_DIST",
        "量价分布（筹码分布；2026-09 三主站实测无响应，client 方法保留待参数校正）",
        tier=TIER_DECLARED,
        status=STATUS_OFFLINE,
    ),
    _c(0x051C, "INDEX_MOMENTUM", "指数动量", tier=TIER_DECLARED),
    _c(0x053F, "LIMIT_UP_POOL", "涨停板行情", tier=TIER_DECLARED),
    _c(0x0563, "ABNORMAL_MOVE", "异动监测", tier=TIER_DECLARED),
    _c(
        0x056A,
        "AUCTION_SNAPSHOT",
        "集合竞价过程快照（2026-09 三主站实测无响应，client 方法保留待参数校正）",
        tier=TIER_DECLARED,
        status=STATUS_OFFLINE,
    ),
    _c(0x0552, "RANK_LIST", "排行榜数据", tier=TIER_DECLARED),
    _c(0x0FB0, "HISTORY_BARS_EXT", "扩展历史 K 线", tier=TIER_DECLARED),
    _c(0x0FC3, "TRADE_SUMMARY", "成交汇总", tier=TIER_DECLARED),
    _c(
        0x07E5,
        "BLOCK_QUOTES",
        "板块行情（2026-09 三主站实测无响应，client 方法保留待参数校正）",
        tier=TIER_DECLARED,
        status=STATUS_OFFLINE,
    ),
    _c(0x0FDD, "SERVER_INFO", "服务器信息/公告", tier=TIER_DECLARED),
    _c(0x0020, "UNKNOWN_0020", "未命名命令（待抓包确认）", tier=TIER_DECLARED),
    _c(0x0520, "UNKNOWN_0520", "未命名命令（待抓包确认）", tier=TIER_DECLARED),
    _c(0x0FA8, "UNKNOWN_0FA8", "未命名命令（待抓包确认）", tier=TIER_DECLARED),
]

_EXT: list[Command] = [
    _c(0x000D, "EX_HANDSHAKE", "扩展市场握手", family=Family.EXTENDED, tier=TIER_L2),
    _c(0x0004, "EX_HEARTBEAT", "扩展市场心跳", family=Family.EXTENDED, tier=TIER_L2),
    _c(0x0100, "EX_MARKET_COUNT", "扩展市场数量", family=Family.EXTENDED),
    _c(0x0101, "EX_MARKET_LIST", "扩展市场列表", family=Family.EXTENDED),
    _c(0x0102, "EX_INSTRUMENT_COUNT", "品种数量", family=Family.EXTENDED),
    _c(0x0103, "EX_INSTRUMENT_LIST", "品种列表", family=Family.EXTENDED),
    _c(0x0104, "EX_INSTRUMENT_BARS", "扩展市场 K 线", family=Family.EXTENDED),
    _c(0x0105, "EX_INSTRUMENT_QUOTE", "扩展市场实时报价", family=Family.EXTENDED),
    _c(0x0106, "EX_INSTRUMENT_TRADE", "扩展市场成交明细", family=Family.EXTENDED),
    _c(0x0107, "EX_INSTRUMENT_MINUTE", "扩展市场分时", family=Family.EXTENDED),
    _c(0x0108, "EX_INSTRUMENT_INFO", "品种基础信息", family=Family.EXTENDED),
    _c(0x0109, "EX_BATCH_QUOTE", "批量报价", family=Family.EXTENDED),
    _c(0x010A, "EX_HK_QUOTE", "港股实时", family=Family.EXTENDED),
    _c(0x010B, "EX_US_QUOTE", "美股实时", family=Family.EXTENDED),
    _c(0x010C, "EX_FUTURE_QUOTE", "期货实时", family=Family.EXTENDED),
    _c(0x010D, "EX_FX_QUOTE", "外汇实时", family=Family.EXTENDED),
    _c(0x010E, "EX_OPTION_QUOTE", "期权实时", family=Family.EXTENDED),
]

_MAC: list[Command] = [
    _c(0x120F, "MAC_BLOCK_LIST", "板块列表", family=Family.MAC),
    _c(0x1210, "MAC_BLOCK_MEMBERS", "板块成分股", family=Family.MAC),
    _c(0x1300, "MAC_UNIFIED_BARS", "统一 K 线", family=Family.MAC),
    _c(0x1301, "MAC_UNIFIED_QUOTE", "统一报价", family=Family.MAC),
    _c(0x1400, "MAC_FUND_FLOW", "资金流向", family=Family.MAC),
    _c(0x1500, "MAC_MAINFORCE", "主力监控", family=Family.MAC),
    _c(0x1600, "MAC_AUCTION", "竞价数据", family=Family.MAC),
    _c(0x1700, "MAC_MULTIDAY_MINUTE", "多日分时", family=Family.MAC),
    _c(0x2000, "MAC_BLOCK_QUOTE", "板块行情", family=Family.MAC),
    _c(0x2100, "MAC_INDEX_BARS", "指数 K 线", family=Family.MAC),
    _c(0x2200, "MAC_RANK", "综合排名", family=Family.MAC),
    _c(0x2300, "MAC_DDE", "DDE 决策", family=Family.MAC),
    _c(0x2400, "MAC_CHIP", "筹码分布", family=Family.MAC),
    _c(0x2500, "MAC_NEWS", "资讯", family=Family.MAC),
    _c(0x2560, "MAC_CLIENT_INFO", "客户端信息", family=Family.MAC),
    _c(0x2562, "MAC_HEARTBEAT", "MAC 心跳", family=Family.MAC),
]

_GOODS: list[Command] = [
    _c(0x0200, "GOODS_COUNT", "商品数量", family=Family.GOODS),
    _c(0x0201, "GOODS_LIST", "商品列表", family=Family.GOODS),
    _c(0x0202, "GOODS_BARS", "商品 K 线", family=Family.GOODS),
    _c(0x0203, "GOODS_QUOTE", "商品实时报价", family=Family.GOODS),
    _c(0x0204, "GOODS_TRADE", "商品成交明细", family=Family.GOODS),
    _c(0x0205, "GOODS_MINUTE", "商品分时", family=Family.GOODS),
    _c(0x0206, "GOODS_INFO", "商品基础信息", family=Family.GOODS),
    _c(0x0207, "GOODS_HOLDING", "持仓量/持仓排名", family=Family.GOODS),
    _c(0x0208, "GOODS_OPTION_GREEKS", "期权希腊字母", family=Family.GOODS),
    _c(0x0209, "GOODS_FX_RATE", "外汇牌价", family=Family.GOODS),
    _c(0x020A, "GOODS_CALENDAR", "交易日历/合约到期", family=Family.GOODS),
]

_F10: list[Command] = [
    _c(0x0001, "F10_CATALOG", "F10 栏目目录清单", family=Family.F10, tier=TIER_L2),
    _c(
        0x0002,
        "F10_TEXT",
        "F10 栏目正文（GBK 文本，由 0x06B9 下载）",
        family=Family.F10,
        tier=TIER_L2,
    ),
]

COMMANDS: dict[tuple[str, int], Command] = {}
for _cmds in (_STD, _EXT, _MAC, _GOODS, _F10):
    for _c_ in _cmds:
        COMMANDS[(_c_.family, _c_.cmd)] = _c_

CMD: dict[str, int] = {}
for _c_ in COMMANDS.values():
    _k_ = _c_.name.lower()
    if _k_ not in CMD:
        CMD[_k_] = _c_.cmd


def cmd(name: str) -> int:
    """命令名 → 命令号。

    账本 85 行的名字逐名唯一（按 ``lower()`` 后比较），所以 ``CMD[name]`` 不需要族
    上下文；那条唯一性由 ``tests/unit/test_commands.py`` 钉住。要整行就组合
    ``get_command(cmd(name), family)``。
    """
    try:
        return CMD[name]
    except KeyError as exc:
        raise KeyError(
            f"未登记命令名 {name!r}；已登记 {len(CMD)} 条，参见 protocol/commands.py"
        ) from exc


def get_command(cmd: int, family: str = Family.STANDARD) -> Command | None:
    """按 ``(族, 命令号)`` 取账本行；未登记者返回 ``None``。

    ``None`` 不是错误：未登记号照旧由 :mod:`tstdx.protocol.generic` 走 L2 通用解析 +
    L3 原始透传，永不丢包。
    """
    return COMMANDS.get((family, cmd))


def by_family(family: str) -> Iterator[Command]:
    """一个协议族的全部账本行，按命令号升序。

    族名是 :class:`Family` 的常量值（``Family.STANDARD == "quotation"``）。未登记的族名
    得到空序列，不报错——它表达的是"该族无登记"，与调用方写错族名同形，故拼写要靠常量。
    """
    for (fam, _), command in sorted(COMMANDS.items()):
        if fam == family:
            yield command


def unknown_command_ids(family: str = Family.STANDARD) -> list[Command]:
    """语义尚未由 golden 样本校正（``verified=False``）的账本行。

    名字里的 "unknown" 指**协议语义未定**，不是"这条命令不存在"：这些号照样发得出去，
    L2 通用解析照样出结果。返回的是 :class:`Command` 行而不是裸命令号，要号请取
    ``[c.cmd for c in unknown_command_ids(...)]``。
    """
    return [command for command in by_family(family) if not command.verified]


def by_status(status: str, family: str | None = None) -> list[Command]:
    """按运行时状态（``STATUS_ONLINE`` / ``OFFLINE`` / ``DEGRADED``）列出该状态的全部行。

    状态是「实测下线/降级事实」的归宿，与语义验证 ``verified`` 是两根独立的轴。发包前的
    fail-fast 读的是**单行的** ``status``（``tstdx/client/core.py`` 的 ``_guard_offline``
    经 ``get_command`` 取行），本函数只负责「按状态列全部行」这一侧。
    """
    return [
        command
        for (_, _), command in sorted(COMMANDS.items())
        if command.status == status and (family is None or command.family == family)
    ]
