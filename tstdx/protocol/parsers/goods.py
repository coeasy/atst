# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""商品语义协议族解析器（期货 / 期权 / 外汇，走独立协议组 0x0200–0x020A）。

.. danger:: 洁净室标记
   本族命令的字段布局来自公开资料推断（参考商品语义协议组文档），**尚未通过
   golden 样本回归**。所有解析器带结构守卫，解析异常即降级到 L2/L3，绝不臆造。

   商品与股票最大差异：价格标度、成交量单位、是否含**持仓量**字段。本族统一
   以 ``price_scale=1000``、``volume_unit=share`` 起步，并通过 ``extra`` 保留
   未锁定字段，待样本校正。
"""

from __future__ import annotations

from typing import Any

from ...codec.primitive import PRICE_SCALE_BARS, PRICE_SCALE_QUOTES, decode_gbk
from ...errors import ParseError
from ..commands import Family
from ..registry import BaseParser, register_parser

__all__ = [
    "GoodsCountParser",
    "GoodsListParser",
    "GoodsBarsParser",
    "GoodsQuoteParser",
    "GoodsMinuteParser",
    "GoodsTradeParser",
    "GoodsInfoParser",
    "GoodsHoldingParser",
    "GoodsOptionGreeksParser",
    "GoodsFxRateParser",
    "GoodsCalendarParser",
]


# --------------------------------------------------------------------------- #
# 0x0200 商品数量
# --------------------------------------------------------------------------- #
@register_parser(0x0200, family=Family.GOODS, name="GOODS_COUNT", head=2, tier="L2")
class GoodsCountParser(BaseParser):
    """某商品分类下的品种数量：``<H count>``。"""

    def parse_payload(self, reader, **ctx: Any) -> list[dict[str, Any]]:
        return [{"count": reader.uint16()}]


# --------------------------------------------------------------------------- #
# 0x0201 商品列表
# --------------------------------------------------------------------------- #
@register_parser(0x0201, family=Family.GOODS, name="GOODS_LIST", head=2, tier="L2")
class GoodsListParser(BaseParser):
    """商品列表：``<H count>`` + 每只需 ``<6s code><8s name(gbk)><H category>``。

    ⚠️ 字段顺序待样本锁定。
    """

    RECORD_SIZE = 16

    def parse_payload(self, reader, **ctx: Any) -> list[dict[str, Any]]:
        count = self.guarded_count(reader, ctx, self.RECORD_SIZE)
        rows: list[dict[str, Any]] = []
        for _ in range(count):
            if reader.remaining < self.RECORD_SIZE:
                break
            code = decode_gbk(reader.bytes(6))
            name = decode_gbk(reader.bytes(8))
            category = reader.uint16()
            rows.append({"code": code, "name": name, "category": category})
        return rows


# --------------------------------------------------------------------------- #
# 0x0202 商品 K 线（对标 0x052D，含持仓量）
# --------------------------------------------------------------------------- #
@register_parser(0x0202, family=Family.GOODS, name="GOODS_BARS", head=2, tier="L2")
class GoodsBarsParser(BaseParser):
    """商品 K 线。

    ⚠️ 与股票 0x052D 同构（leb128 差分价格 + tdx_float 量额），但股票 K 线尾部
    多出**持仓量** 4 字节。这里复用差分还原逻辑，并额外读取持仓量。
    """

    def parse_payload(self, reader, **ctx: Any) -> list[dict[str, Any]]:
        scale = int(ctx.get("price_scale", PRICE_SCALE_BARS))
        count = self.guarded_count(reader, ctx, 28)
        base = 0
        rows: list[dict[str, Any]] = []
        for _ in range(count):
            if reader.remaining < 28:
                break
            dt = reader.uint32()
            open_diff = reader.leb128()
            close_diff = reader.leb128()
            high_diff = reader.leb128()
            low_diff = reader.leb128()
            volume = reader.tdx_float()
            amount = reader.tdx_float()
            open_int = reader.uint32() if reader.remaining >= 4 else 0
            open_abs = open_diff + base
            close_abs = open_abs + close_diff
            high_abs = open_abs + high_diff
            low_abs = open_abs + low_diff
            base = close_abs
            rows.append(
                {
                    "datetime": f"{dt:08d}",
                    "open": round(open_abs / scale, 4),
                    "close": round(close_abs / scale, 4),
                    "high": round(high_abs / scale, 4),
                    "low": round(low_abs / scale, 4),
                    "volume": int(round(volume)),
                    "amount": round(float(amount), 4),
                    "open_interest": int(open_int),
                }
            )
        return rows


# --------------------------------------------------------------------------- #
# 0x0203 商品实时报价
# --------------------------------------------------------------------------- #
@register_parser(0x0203, family=Family.GOODS, name="GOODS_QUOTE", head=2, tier="L2")
class GoodsQuoteParser(BaseParser):
    """商品实时报价。

    ⚠️ 对标 0x0530，但商品行情尾部含**持仓量**与**结算价**。先按 0x0530 同构
    解析价格，再尝试读取持仓量/结算价；不足则保留在 ``extra``。
    """

    def parse_payload(self, reader, **ctx: Any) -> list[dict[str, Any]]:
        scale = int(ctx.get("price_scale", PRICE_SCALE_QUOTES))
        market = reader.uint8()
        code = decode_gbk(reader.bytes(6))
        if reader.remaining < 8:
            raise ParseError("0x0203 响应过短", context={"remaining": reader.remaining})
        reader.seek(7)
        reader.bytes(2)  # 不透明 2 字节
        price = reader.leb128()
        d_last = reader.leb128()
        d_open = reader.leb128()
        d_high = reader.leb128()
        d_low = reader.leb128()
        u4 = reader.leb128()
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
        extra: dict[str, Any] = {"_u4": u4}
        # 剩余字节尝试作为持仓量 + 结算价
        tail: list[int] = []
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
# 0x0204 商品成交明细
# --------------------------------------------------------------------------- #
@register_parser(0x0204, family=Family.GOODS, name="GOODS_TRADE", head=2, tier="L2")
class GoodsTradeParser(BaseParser):
    """商品逐笔成交：``<H count>`` + ``<H 秒><f4 价格><tdx_float 量><B 方向>``。"""

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


# --------------------------------------------------------------------------- #
# 0x0205 商品分时
# --------------------------------------------------------------------------- #
@register_parser(0x0205, family=Family.GOODS, name="GOODS_MINUTE", head=2, tier="L2")
class GoodsMinuteParser(BaseParser):
    """商品分时：``<H count>`` + ``<H 分钟><f4 价格><f4 均价><tdx_float 量>``。"""

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


# --------------------------------------------------------------------------- #
# 0x0206 商品基础信息
# --------------------------------------------------------------------------- #
@register_parser(0x0206, family=Family.GOODS, name="GOODS_INFO", head=2, tier="L2")
class GoodsInfoParser(BaseParser):
    """商品基础信息：``<6s code><8s name><f4 合约乘数><f4 最小变动价位>...``。"""

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
                    "multiplier": round(struct_unpack_f4(rec, 14), 4),
                    "tick": round(struct_unpack_f4(rec, 18), 4),
                    "raw": rec.hex(),
                }
            )
        return rows


# --------------------------------------------------------------------------- #
# 0x0207 持仓量 / 持仓排名
# --------------------------------------------------------------------------- #
@register_parser(0x0207, family=Family.GOODS, name="GOODS_HOLDING", head=2, tier="L2")
class GoodsHoldingParser(BaseParser):
    """持仓量 / 持仓排名：``<H count>`` + ``<6s code><tdx_float 持仓>``。"""

    RECORD_SIZE = 10

    def parse_payload(self, reader, **ctx: Any) -> list[dict[str, Any]]:
        count = self.guarded_count(reader, ctx, self.RECORD_SIZE)
        rows: list[dict[str, Any]] = []
        for _ in range(count):
            if reader.remaining < self.RECORD_SIZE:
                break
            code = decode_gbk(reader.bytes(6))
            oi = reader.tdx_float()
            rows.append({"code": code, "open_interest": int(oi)})
        return rows


# --------------------------------------------------------------------------- #
# 0x0208 期权希腊字母
# --------------------------------------------------------------------------- #
@register_parser(0x0208, family=Family.GOODS, name="GOODS_OPTION_GREEKS", head=2, tier="L2")
class GoodsOptionGreeksParser(BaseParser):
    """期权希腊字母：``<H count>`` + ``<6s code><f4 delta><f4 gamma><f4 vega><f4 theta><f4 rho>``。"""

    RECORD_SIZE = 26

    def parse_payload(self, reader, **ctx: Any) -> list[dict[str, Any]]:
        count = self.guarded_count(reader, ctx, self.RECORD_SIZE)
        rows: list[dict[str, Any]] = []
        for _ in range(count):
            if reader.remaining < self.RECORD_SIZE:
                break
            code = decode_gbk(reader.bytes(6))
            delta, gamma, vega, theta, rho = reader.unpack("fffff", 20)
            rows.append(
                {
                    "code": code,
                    "delta": round(delta, 6),
                    "gamma": round(gamma, 6),
                    "vega": round(vega, 6),
                    "theta": round(theta, 6),
                    "rho": round(rho, 6),
                }
            )
        return rows


# --------------------------------------------------------------------------- #
# 0x0209 外汇牌价
# --------------------------------------------------------------------------- #
@register_parser(0x0209, family=Family.GOODS, name="GOODS_FX_RATE", head=2, tier="L2")
class GoodsFxRateParser(BaseParser):
    """外汇牌价：``<H count>`` + ``<6s code><8s name><f4 现汇买入><f4 现钞买入><f4 卖出>``。"""

    RECORD_SIZE = 26

    def parse_payload(self, reader, **ctx: Any) -> list[dict[str, Any]]:
        count = self.guarded_count(reader, ctx, self.RECORD_SIZE)
        rows: list[dict[str, Any]] = []
        for _ in range(count):
            if reader.remaining < self.RECORD_SIZE:
                break
            code = decode_gbk(reader.bytes(6))
            name = decode_gbk(reader.bytes(8))
            bid_cash, bid_transfer, ask = reader.unpack("fff", 12)
            rows.append(
                {
                    "code": code,
                    "name": name,
                    "bid_cash": round(bid_cash, 6),
                    "bid_transfer": round(bid_transfer, 6),
                    "ask": round(ask, 6),
                }
            )
        return rows


# --------------------------------------------------------------------------- #
# 0x020A 交易日历 / 合约到期
# --------------------------------------------------------------------------- #
@register_parser(0x020A, family=Family.GOODS, name="GOODS_CALENDAR", head=2, tier="L2")
class GoodsCalendarParser(BaseParser):
    """交易日历 / 合约到期：``<H count>`` + ``<6s code><I 到期日YYYYMMDD>``。"""

    RECORD_SIZE = 10

    def parse_payload(self, reader, **ctx: Any) -> list[dict[str, Any]]:
        count = self.guarded_count(reader, ctx, self.RECORD_SIZE)
        rows: list[dict[str, Any]] = []
        for _ in range(count):
            if reader.remaining < self.RECORD_SIZE:
                break
            code = decode_gbk(reader.bytes(6))
            (expire,) = reader.unpack("I", 4)
            rows.append({"code": code, "expire_date": f"{expire:08d}"})
        return rows


def struct_unpack_f4(buf: bytes, pos: int) -> float:
    import struct

    return struct.unpack_from("<f", buf, pos)[0]
