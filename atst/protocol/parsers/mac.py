# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""MAC 专属协议族解析器（7709 MAC 服务器，命令号 0x120F–0x2562）。

.. danger:: 洁净室标记
   MAC 族是通达信 **PC 客户端专有** 的分析数据协议（板块 / 资金流向 / 主力 / DDE /
   筹码分布等），布局公开资料极少，**全部待 golden 回归**。本文件给出「功能占位 +
   结构守卫」式解析：能解析出 count 与逐条原始切片，绝不臆造字段语义。
"""

from __future__ import annotations

from typing import Any

from ...codec.primitive import decode_gbk
from ..commands import Family
from ..registry import BaseParser, register_parser

__all__ = [
    "MacBlockListParser",
    "MacBlockMembersParser",
    "MacUnifiedBarsParser",
    "MacUnifiedQuoteParser",
    "MacFundFlowParser",
    "MacMainForceParser",
    "MacAuctionParser",
    "MacMultidayMinuteParser",
    "MacBlockQuoteParser",
    "MacIndexBarsParser",
    "MacRankParser",
    "MacDdeParser",
    "MacChipParser",
    "MacNewsParser",
    "MacClientInfoParser",
    "MacHeartbeatParser",
]


@register_parser(0x120F, family=Family.MAC, name="MAC_BLOCK_LIST", head=2, tier="L2")
class MacBlockListParser(BaseParser):
    """板块列表：``<H count>`` + ``<8s name(gbk)><H id>``。"""

    RECORD_SIZE = 10

    def parse_payload(self, reader, **ctx: Any) -> list[dict[str, Any]]:
        count = self.guarded_count(reader, ctx, self.RECORD_SIZE)
        rows: list[dict[str, Any]] = []
        for _ in range(count):
            if reader.remaining < self.RECORD_SIZE:
                break
            name = decode_gbk(reader.bytes(8))
            bid = reader.uint16()
            rows.append({"name": name, "block_id": bid})
        return rows


@register_parser(0x1210, family=Family.MAC, name="MAC_BLOCK_MEMBERS", head=2, tier="L2")
class MacBlockMembersParser(BaseParser):
    """板块成分股：``<H count>`` + ``<6s code>``。"""

    RECORD_SIZE = 6

    def parse_payload(self, reader, **ctx: Any) -> list[dict[str, Any]]:
        count = self.guarded_count(reader, ctx, self.RECORD_SIZE)
        return [
            {"code": decode_gbk(reader.bytes(self.RECORD_SIZE))}
            for _ in range(count)
            if reader.remaining >= self.RECORD_SIZE
        ]


@register_parser(0x1300, family=Family.MAC, name="MAC_UNIFIED_BARS", head=2, tier="L2")
class MacUnifiedBarsParser(BaseParser):
    """统一 K 线：布局同 0x052D（leb128 差分 + tdx_float 量额，含持仓量扩展）。

    .. danger::
       **差分基准待真机样本裁决（P1c，禁止盲改）**：0x052D/0x0202 的价格
       差分以**跨记录 running base** 还原（``open_abs = open_diff + 上一条
       close_abs``，记录间累加）；本解析器当前为 **per-record absolute**——
       每条 ``open`` 直接取 leb128 值，``close/high/low`` 相对**本条** open，
       记录间**不**累加。两种基准互斥且数值相差数量级级联；0x1300 从未真机
       确认（⚠️ 占位类），布局待真机样本锁定。当前行为由
       ``tests/protocol/test_f2_protocol_correctness.py::TestP1cDiffBase``
       锁定，真机样本到位后与该测试一起翻转，勿提前"对齐" 0x052D。
    """

    def parse_payload(self, reader, **ctx: Any) -> list[dict[str, Any]]:
        scale = int(ctx.get("price_scale", 1000))
        count = self.guarded_count(reader, ctx, 28)
        rows: list[dict[str, Any]] = []
        for _ in range(count):
            if reader.remaining < 28:
                break
            # P1c：per-record absolute —— o 不跨记录累加（勿改成 0x052D 的 base 模式）
            dt = reader.uint32()
            o = reader.leb128()
            c = o + reader.leb128()
            h = o + reader.leb128()
            lo = o + reader.leb128()
            vol = reader.tdx_float()
            amt = reader.tdx_float()
            rows.append(
                {
                    "datetime": f"{dt:08d}",
                    "open": round(o / scale, 4),
                    "close": round(c / scale, 4),
                    "high": round(h / scale, 4),
                    "low": round(lo / scale, 4),
                    "volume": int(round(vol)),
                    "amount": round(float(amt), 4),
                }
            )
        return rows


@register_parser(0x1301, family=Family.MAC, name="MAC_UNIFIED_QUOTE", head=0, tier="L2")
class MacUnifiedQuoteParser(BaseParser):
    """统一报价：布局同 0x0530（标度 100）。"""

    def parse_payload(self, reader, **ctx: Any) -> list[dict[str, Any]]:
        scale = int(ctx.get("price_scale", 100))
        market = reader.uint8()
        code = decode_gbk(reader.bytes(6))
        reader.seek(7)
        reader.bytes(2)
        price = reader.leb128()
        d_last = reader.leb128()
        d_open = reader.leb128()
        d_high = reader.leb128()
        d_low = reader.leb128()
        _u4 = reader.leb128()
        _s = reader.leb128()
        vol = reader.leb128()
        _v2 = reader.leb128()
        amt = reader.tdx_float()
        return [
            {
                "market": market,
                "code": code,
                "price": round(price / scale, 4),
                "last_close": round((price + d_last) / scale, 4),
                "open": round((price + d_open) / scale, 4),
                "high": round((price + d_high) / scale, 4),
                "low": round((price + d_low) / scale, 4),
                "volume": int(round(vol * 100)),
                "amount": round(float(amt), 4),
            }
        ]


@register_parser(0x1400, family=Family.MAC, name="MAC_FUND_FLOW", head=2, tier="L2")
class MacFundFlowParser(BaseParser):
    """资金流向：``<H count>`` + ``<6s code><f4 主力净流入><f4 散户>``。"""

    RECORD_SIZE = 14

    def parse_payload(self, reader, **ctx: Any) -> list[dict[str, Any]]:
        count = self.guarded_count(reader, ctx, self.RECORD_SIZE)
        rows: list[dict[str, Any]] = []
        for _ in range(count):
            if reader.remaining < self.RECORD_SIZE:
                break
            code = decode_gbk(reader.bytes(6))
            main, retail = reader.unpack("ff", 8)
            rows.append({"code": code, "main_net": round(main, 2), "retail_net": round(retail, 2)})
        return rows


@register_parser(0x1500, family=Family.MAC, name="MAC_MAINFORCE", head=2, tier="L2")
class MacMainForceParser(BaseParser):
    """主力监控：``<H count>`` + ``<6s code><f4 净买额>``。"""

    RECORD_SIZE = 10

    def parse_payload(self, reader, **ctx: Any) -> list[dict[str, Any]]:
        count = self.guarded_count(reader, ctx, self.RECORD_SIZE)
        rows: list[dict[str, Any]] = []
        for _ in range(count):
            if reader.remaining < self.RECORD_SIZE:
                break
            code = decode_gbk(reader.bytes(6))
            (net,) = reader.unpack("f", 4)
            rows.append({"code": code, "net_buy": round(net, 2)})
        return rows


@register_parser(0x1600, family=Family.MAC, name="MAC_AUCTION", head=2, tier="L2")
class MacAuctionParser(BaseParser):
    """竞价数据：``<H count>`` + ``<H 秒><f4 价格><tdx_float 量>``。"""

    RECORD_SIZE = 10

    def parse_payload(self, reader, **ctx: Any) -> list[dict[str, Any]]:
        count = self.guarded_count(reader, ctx, self.RECORD_SIZE)
        rows: list[dict[str, Any]] = []
        for _ in range(count):
            if reader.remaining < self.RECORD_SIZE:
                break
            second = reader.uint16()
            price = reader.float32()
            volume = reader.tdx_float()
            rows.append({"second": second, "price": round(float(price), 4), "volume": int(volume)})
        return rows


@register_parser(0x1700, family=Family.MAC, name="MAC_MULTIDAY_MINUTE", head=2, tier="L2")
class MacMultidayMinuteParser(BaseParser):
    """多日分时：``<H count>`` + ``<H 分钟><f4 价格><f4 均价><tdx_float 量>``。"""

    RECORD_SIZE = 14

    def parse_payload(self, reader, **ctx: Any) -> list[dict[str, Any]]:
        count = self.guarded_count(reader, ctx, self.RECORD_SIZE)
        rows: list[dict[str, Any]] = []
        for _ in range(count):
            if reader.remaining < self.RECORD_SIZE:
                break
            minute = reader.uint16()
            price = reader.float32()
            avg = reader.float32()
            volume = reader.tdx_float()
            hh, mm = divmod(minute, 60)
            rows.append(
                {
                    "minute": minute,
                    "time": f"{hh:02d}:{mm:02d}",
                    "price": round(float(price), 4),
                    "avg_price": round(float(avg), 4),
                    "volume": int(volume),
                }
            )
        return rows


@register_parser(0x2000, family=Family.MAC, name="MAC_BLOCK_QUOTE", head=2, tier="L2")
class MacBlockQuoteParser(BaseParser):
    """板块行情：``<H count>`` + ``<6s code><f4 涨跌幅><f4 现价>``。"""

    RECORD_SIZE = 14

    def parse_payload(self, reader, **ctx: Any) -> list[dict[str, Any]]:
        count = self.guarded_count(reader, ctx, self.RECORD_SIZE)
        rows: list[dict[str, Any]] = []
        for _ in range(count):
            if reader.remaining < self.RECORD_SIZE:
                break
            code = decode_gbk(reader.bytes(6))
            pct, price = reader.unpack("ff", 8)
            rows.append({"code": code, "pct_change": round(pct, 4), "price": round(price, 4)})
        return rows


@register_parser(0x2100, family=Family.MAC, name="MAC_INDEX_BARS", head=2, tier="L2")
class MacIndexBarsParser(MacUnifiedBarsParser):
    """指数 K 线（布局同统一 K 线）。"""


@register_parser(0x2200, family=Family.MAC, name="MAC_RANK", head=2, tier="L2")
class MacRankParser(BaseParser):
    """综合排名：``<H count>`` + ``<6s code><f4 涨幅><H 排名>``。"""

    RECORD_SIZE = 12

    def parse_payload(self, reader, **ctx: Any) -> list[dict[str, Any]]:
        count = self.guarded_count(reader, ctx, self.RECORD_SIZE)
        rows: list[dict[str, Any]] = []
        for _ in range(count):
            if reader.remaining < self.RECORD_SIZE:
                break
            code = decode_gbk(reader.bytes(6))
            pct = reader.float32()
            rank = reader.uint16()
            rows.append({"code": code, "pct_change": round(pct, 4), "rank": rank})
        return rows


@register_parser(0x2300, family=Family.MAC, name="MAC_DDE", head=2, tier="L2")
class MacDdeParser(BaseParser):
    """DDE 决策：``<H count>`` + ``<6s code><f4 DDX><f4 DDY><f4 DDZ>``。"""

    RECORD_SIZE = 18

    def parse_payload(self, reader, **ctx: Any) -> list[dict[str, Any]]:
        count = self.guarded_count(reader, ctx, self.RECORD_SIZE)
        rows: list[dict[str, Any]] = []
        for _ in range(count):
            if reader.remaining < self.RECORD_SIZE:
                break
            code = decode_gbk(reader.bytes(6))
            ddx, ddy, ddz = reader.unpack("fff", 12)
            rows.append(
                {"code": code, "ddx": round(ddx, 4), "ddy": round(ddy, 4), "ddz": round(ddz, 4)}
            )
        return rows


@register_parser(0x2400, family=Family.MAC, name="MAC_CHIP", head=2, tier="L2")
class MacChipParser(BaseParser):
    """筹码分布：``<H count>`` + ``<f4 price><tdx_float volume>`` 对。"""

    PAIR_SIZE = 8

    def parse_payload(self, reader, **ctx: Any) -> list[dict[str, Any]]:
        count = self.guarded_count(reader, ctx, self.PAIR_SIZE)
        rows: list[dict[str, Any]] = []
        for _ in range(count):
            if reader.remaining < self.PAIR_SIZE:
                break
            price = reader.float32()
            volume = reader.tdx_float()
            rows.append({"price": round(price, 4), "volume": int(volume)})
        return rows


@register_parser(0x2500, family=Family.MAC, name="MAC_NEWS", head=2, tier="L2")
class MacNewsParser(BaseParser):
    """资讯：``<H count>`` + ``<8s time><8s title(gbk)><H len>`` + 正文片段。"""

    def parse_payload(self, reader, **ctx: Any) -> list[dict[str, Any]]:
        count = self.guarded_count(reader, ctx, 18)
        rows: list[dict[str, Any]] = []
        for _ in range(count):
            if reader.remaining < 18:
                break
            time = decode_gbk(reader.bytes(8))
            title = decode_gbk(reader.bytes(8))
            length = reader.uint16()
            if length > reader.remaining:
                # 深审 L13：声明正文长度超过剩余字节——静默截断改为可观测告警
                # （对齐 §2-17 截断自检：静默丢数据必须留痕）
                self.warn_ctx(
                    ctx,
                    f"资讯正文截断：声明 {length} 字节，仅剩 {reader.remaining} 字节",
                )
            body = decode_gbk(reader.bytes(min(length, reader.remaining))) if length else ""
            rows.append({"time": time, "title": title, "body": body})
        return rows


@register_parser(0x2560, family=Family.MAC, name="MAC_CLIENT_INFO", head=0, tier="L2")
class MacClientInfoParser(BaseParser):
    """客户端信息：返回一段 GBK 文本，原样保留。"""

    def parse_payload(self, reader, **ctx: Any) -> list[dict[str, Any]]:
        text = decode_gbk(reader.rest())
        return [{"text": text}]


@register_parser(0x2562, family=Family.MAC, name="MAC_HEARTBEAT", head=0, tier="L2")
class MacHeartbeatParser(BaseParser):
    """MAC 心跳：通常无载荷或单字节。

    .. note::
       ``alive`` 恒为 ``True`` 是**设计而非缺陷**：dispatch 能抵达本解析器，
       即证明响应帧已收到且 magic/长度自洽——「响应可解析」本身就是活性
       信号，与载荷内容无关。真实主站的心跳响应常态为空载荷或 1 字节，
       因此空 payload 判活是正确语义，本解析器不设 ParseError 路径；
       连接死活的否定证据由传输层超时/断连异常给出，不在此处伪造。

    .. versionchanged:: 1.1.x
       原实现 ``reader.remaining >= 0`` 为恒真表达式（remaining 不可能为负），
       改为显式 ``True`` 并注明语义依据；输出行形状不变。
    """

    def parse_payload(self, reader, **ctx: Any) -> list[dict[str, Any]]:
        return [{"alive": True}]
