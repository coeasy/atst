# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""7727 扩展市场协议族解析器（港股 / 美股 / 期货 / 外汇，端口 7727）。

.. danger:: 洁净室标记
   布局来自公开资料推断，**尚未 golden 回归**。全部带结构守卫，异常即降级。
   扩展市场复用了 7709 的差分/tdx_float 编码体系，但**市场编号、价格标度、
   成交量单位**可能不同，本族以 ``price_scale=1000``、``volume_unit=share`` 起步。
"""

from __future__ import annotations

from typing import Any

from ...codec.primitive import PRICE_SCALE_BARS, PRICE_SCALE_QUOTES, decode_gbk
from ...errors import ParseError
from ..commands import Family
from ..registry import BaseParser, register_parser

__all__ = [
    "ExMarketCountParser",
    "ExMarketListParser",
    "ExInstrumentCountParser",
    "ExInstrumentListParser",
    "ExInstrumentBarsParser",
    "ExInstrumentQuoteParser",
    "ExInstrumentTradeParser",
    "ExInstrumentMinuteParser",
    "ExInstrumentInfoParser",
    "ExBatchQuoteParser",
    "ExHkQuoteParser",
    "ExUsQuoteParser",
    "ExFutureQuoteParser",
    "ExFxQuoteParser",
    "ExOptionQuoteParser",
]


# --------------------------------------------------------------------------- #
# 0x0100 扩展市场数量
# --------------------------------------------------------------------------- #
@register_parser(0x0100, family=Family.EXTENDED, name="EX_MARKET_COUNT", head=0, tier="L2")
class ExMarketCountParser(BaseParser):
    def parse_payload(self, reader, **ctx: Any) -> list[dict[str, Any]]:
        return [{"count": reader.uint16()}]


# --------------------------------------------------------------------------- #
# 0x0101 扩展市场列表
# --------------------------------------------------------------------------- #
@register_parser(0x0101, family=Family.EXTENDED, name="EX_MARKET_LIST", head=0, tier="L2")
class ExMarketListParser(BaseParser):
    """``<H count>`` + ``<H id><8s name(gbk)>``。"""

    RECORD_SIZE = 10

    def parse_payload(self, reader, **ctx: Any) -> list[dict[str, Any]]:
        count = self.guarded_count(reader, ctx, self.RECORD_SIZE)
        rows: list[dict[str, Any]] = []
        for _ in range(count):
            if reader.remaining < self.RECORD_SIZE:
                break
            mid = reader.uint16()
            name = decode_gbk(reader.bytes(8))
            rows.append({"market_id": mid, "name": name})
        return rows


# --------------------------------------------------------------------------- #
# 0x0102 品种数量 / 0x0103 品种列表
# --------------------------------------------------------------------------- #
@register_parser(0x0102, family=Family.EXTENDED, name="EX_INSTRUMENT_COUNT", head=0, tier="L2")
class ExInstrumentCountParser(BaseParser):
    def parse_payload(self, reader, **ctx: Any) -> list[dict[str, Any]]:
        return [{"count": reader.uint16()}]


@register_parser(0x0103, family=Family.EXTENDED, name="EX_INSTRUMENT_LIST", head=0, tier="L2")
class ExInstrumentListParser(BaseParser):
    """``<H count>`` + ``<H market><6s code><8s name>``。"""

    RECORD_SIZE = 16

    def parse_payload(self, reader, **ctx: Any) -> list[dict[str, Any]]:
        count = self.guarded_count(reader, ctx, self.RECORD_SIZE)
        rows: list[dict[str, Any]] = []
        for _ in range(count):
            if reader.remaining < self.RECORD_SIZE:
                break
            market = reader.uint16()
            code = decode_gbk(reader.bytes(6))
            name = decode_gbk(reader.bytes(8))
            rows.append({"market": market, "code": code, "name": name})
        return rows


# --------------------------------------------------------------------------- #
# 0x0104 扩展市场 K 线
# --------------------------------------------------------------------------- #
@register_parser(0x0104, family=Family.EXTENDED, name="EX_INSTRUMENT_BARS", head=2, tier="L2")
class ExInstrumentBarsParser(BaseParser):
    """与 0x052D 同构（leb128 差分 + tdx_float 量额），含持仓量。

    .. danger::
       **差分基准待真机样本裁决（P1c，禁止盲改）**：0x052D/0x0202 以
       **跨记录 running base** 还原差分（``open_abs = open_diff + 上一条
       close_abs``）；本解析器当前为 **per-record absolute**——每条
       ``open`` 直接取 leb128 值，``close/high/low`` 相对**本条** open，
       记录间**不**累加，与「同构」声明存在已知偏差。0x0104 从未真机确认
       （⚠️ 占位类），布局待真机样本锁定。当前行为由
       ``tests/protocol/test_f2_protocol_correctness.py::TestP1cDiffBase``
       锁定，真机样本到位后与该测试一起翻转。
    """

    def parse_payload(self, reader, **ctx: Any) -> list[dict[str, Any]]:
        scale = int(ctx.get("price_scale", PRICE_SCALE_BARS))
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
            volume = reader.tdx_float()
            amount = reader.tdx_float()
            oi = reader.uint32() if reader.remaining >= 4 else 0
            rows.append(
                {
                    "datetime": f"{dt:08d}",
                    "open": round(o / scale, 4),
                    "close": round(c / scale, 4),
                    "high": round(h / scale, 4),
                    "low": round(lo / scale, 4),
                    "volume": int(round(volume)),
                    "amount": round(float(amount), 4),
                    "open_interest": int(oi),
                }
            )
        return rows


# --------------------------------------------------------------------------- #
# 0x0105 扩展市场实时报价
# --------------------------------------------------------------------------- #
@register_parser(0x0105, family=Family.EXTENDED, name="EX_INSTRUMENT_QUOTE", head=0, tier="L2")
class ExInstrumentQuoteParser(BaseParser):
    """与 0x0530 同构（价格标度 100）。"""

    def parse_payload(self, reader, **ctx: Any) -> list[dict[str, Any]]:
        scale = int(ctx.get("price_scale", PRICE_SCALE_QUOTES))
        market = reader.uint8()
        code = decode_gbk(reader.bytes(6))
        if reader.remaining < 8:
            raise ParseError("0x0105 响应过短", context={"remaining": reader.remaining})
        reader.seek(7)
        reader.bytes(2)
        price = reader.leb128()
        d_last = reader.leb128()
        d_open = reader.leb128()
        d_high = reader.leb128()
        d_low = reader.leb128()
        _u4 = reader.leb128()
        _sentinel = reader.leb128()
        volume = reader.leb128()
        _v2 = reader.leb128()
        amount = reader.tdx_float()
        row: dict[str, Any] = {
            "market": market,
            "code": code,
            "price": round(price / scale, 4),
            "last_close": round((price + d_last) / scale, 4),
            "open": round((price + d_open) / scale, 4),
            "high": round((price + d_high) / scale, 4),
            "low": round((price + d_low) / scale, 4),
            "volume": int(round(volume * 100)),
            "amount": round(float(amount), 4),
        }
        tail: list[int] = []
        extra: dict[str, Any] = {}
        while reader.remaining > 0:
            try:
                tail.append(reader.leb128())
            except Exception:
                extra["raw_tail_hex"] = reader.rest().hex()
                break
        if tail:
            extra["tail_leb128"] = tail
        row["extra"] = extra
        return [row]


# --------------------------------------------------------------------------- #
# 0x0106 成交明细 / 0x0107 分时 / 0x0108 基础信息
# --------------------------------------------------------------------------- #
@register_parser(0x0106, family=Family.EXTENDED, name="EX_INSTRUMENT_TRADE", head=2, tier="L2")
class ExInstrumentTradeParser(BaseParser):
    """``<H count>`` + ``<H 秒><f4 价格><tdx_float 量><B 方向>``。"""

    RECORD_SIZE = 11

    def parse_payload(self, reader, **ctx: Any) -> list[dict[str, Any]]:
        count = self.guarded_count(reader, ctx, self.RECORD_SIZE)
        rows: list[dict[str, Any]] = []
        for _ in range(count):
            if reader.remaining < self.RECORD_SIZE:
                break
            second = reader.uint16()
            price = reader.float32()
            volume = reader.tdx_float()
            direction = reader.uint8()
            rows.append(
                {
                    "second": second,
                    "price": round(float(price), 4),
                    "volume": int(volume),
                    "direction": direction,
                }
            )
        return rows


@register_parser(0x0107, family=Family.EXTENDED, name="EX_INSTRUMENT_MINUTE", head=2, tier="L2")
class ExInstrumentMinuteParser(BaseParser):
    """``<H count>`` + ``<H 分钟><f4 价格><f4 均价><tdx_float 量>``。"""

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


@register_parser(0x0108, family=Family.EXTENDED, name="EX_INSTRUMENT_INFO", head=2, tier="L2")
class ExInstrumentInfoParser(BaseParser):
    """``<H count>`` + ``<6s code><8s name><f4 合约乘数><f4 最小变动>``。"""

    RECORD_SIZE = 32

    def parse_payload(self, reader, **ctx: Any) -> list[dict[str, Any]]:
        count = self.guarded_count(reader, ctx, self.RECORD_SIZE)
        rows: list[dict[str, Any]] = []
        for _ in range(count):
            if reader.remaining < self.RECORD_SIZE:
                break
            rec = reader.bytes(self.RECORD_SIZE)
            rows.append(
                {
                    "code": decode_gbk(rec[0:6]),
                    "name": decode_gbk(rec[6:14]),
                    "multiplier": round(_f4(rec, 14), 4),
                    "tick": round(_f4(rec, 18), 4),
                    "raw": rec.hex(),
                }
            )
        return rows


# --------------------------------------------------------------------------- #
# 0x0109 批量报价
# --------------------------------------------------------------------------- #
@register_parser(0x0109, family=Family.EXTENDED, name="EX_BATCH_QUOTE", head=2, tier="L2")
class ExBatchQuoteParser(BaseParser):
    """批量报价：``<H count>`` + 每条复用 :class:`ExInstrumentQuoteParser`。

    .. warning::
       **已知洁净室局限（待 golden 校正）**：单条报价解析器
       :class:`ExInstrumentQuoteParser` 的尾部（五档盘口）布局未锁定，会
       **贪婪消费到缓冲末尾**——因此本批量解析器对多记录载荷**只能解析出首条**
       并触发「记录截断」告警。真实批量记录定长/结束标记待真机样本锁定后，
       与本解析器一起翻转（不可在无样本时臆造记录长度）。
    """

    def parse_payload(self, reader, **ctx: Any) -> list[dict[str, Any]]:
        count = self.guarded_count(reader, ctx, 8)
        single = ExInstrumentQuoteParser()
        rows: list[dict[str, Any]] = []
        for _ in range(count):
            if reader.remaining < 8:
                break
            save = reader.pos
            try:
                rows.extend(single.parse_payload(reader, **ctx))
            except Exception as exc:
                # 「致命不降级」契约：fatal（IntegrityViolation）原样上抛，
                # 只有非 fatal 的布局失配才回退位置并停止批量解析
                if getattr(exc, "fatal", False):
                    raise
                reader.pos = save
                self.warn_ctx(ctx, f"0x0109 第 {len(rows)} 条之后解析失败已停止: {exc}")
                break
        return rows


# --------------------------------------------------------------------------- #
# 0x010A–0x010E 各市场报价（共用同构解析器）
# --------------------------------------------------------------------------- #
@register_parser(0x010A, family=Family.EXTENDED, name="EX_HK_QUOTE", head=0, tier="L2")
class ExHkQuoteParser(ExInstrumentQuoteParser):
    """港股实时（布局同 0x0105）。"""


@register_parser(0x010B, family=Family.EXTENDED, name="EX_US_QUOTE", head=0, tier="L2")
class ExUsQuoteParser(ExInstrumentQuoteParser):
    """美股实时（布局同 0x0105）。"""


@register_parser(0x010C, family=Family.EXTENDED, name="EX_FUTURE_QUOTE", head=0, tier="L2")
class ExFutureQuoteParser(ExInstrumentQuoteParser):
    """期货实时（布局同 0x0105）。"""


@register_parser(0x010D, family=Family.EXTENDED, name="EX_FX_QUOTE", head=0, tier="L2")
class ExFxQuoteParser(ExInstrumentQuoteParser):
    """外汇实时（布局同 0x0105）。"""


@register_parser(0x010E, family=Family.EXTENDED, name="EX_OPTION_QUOTE", head=0, tier="L2")
class ExOptionQuoteParser(ExInstrumentQuoteParser):
    """期权实时（布局同 0x0105）。"""


def _f4(buf: bytes, pos: int) -> float:
    import struct

    return struct.unpack_from("<f", buf, pos)[0]
