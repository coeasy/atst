# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""7709 标准族行情类解析器：0x044E 证券数量 / 0x053E 批量行情(L2) / 0x0530 实时行情。

P11-3 自 ``std7709.py`` 拆出（REFACTOR_PLAN_v11），内容逐字搬移。
"""

from __future__ import annotations

from typing import Any

from ...codec.primitive import (
    PRICE_SCALE_QUOTES,
    BinaryReader,
    decode_gbk,
)
from ...errors import IntegrityViolation, ParseError
from ..commands import Family
from ..registry import TIER_L2, BaseParser, register_parser
from ._std7709_common import (
    SHARES_PER_LOT,
)

# 0x044E 市场代码数量                              ✅ golden-verified

# --------------------------------------------------------------------------- #


@register_parser(0x044E, family=Family.STANDARD, name="SECURITY_COUNT", head=0)
class SecurityCountParser(BaseParser):
    """响应：``uint16 count``。



    请求体：``<H market>`` + 4 字节补零。

    """

    def parse_payload(self, reader: BinaryReader, **ctx: Any) -> list[dict[str, Any]]:

        return [{"count": reader.uint16()}]


# --------------------------------------------------------------------------- #


# 0x053E 旧版批量行情（原生五档）                    ⚠️ 部分主站已停用

# --------------------------------------------------------------------------- #


@register_parser(
    0x053E,
    family=Family.STANDARD,
    name="QUOTES_LEGACY",
    head=2,
    tier=TIER_L2,
)
class QuotesLegacyParser(BaseParser):
    """批量行情（五档盘口）。

    深审 M29：tier 显式声明为 L2——旧实现缺省落入 register_parser 默认
    L1（confidence 1.0），与命令表 tier=TIER_DECLARED 的账目不符；
    「未真机锁定」的解析器不应自报满置信。

    .. warning::

       ⚠️ 实测多台主站对该命令无响应（疑似已下线）。

       新版本请用 **0x054C**。本解析器保留以兼容老主站，

       若响应为空会抛出 :class:`ParseError` 并由分派器降级到 L2 通用解析。

    单次请求上限 **60 只**（服务端限制，超出需分批）。
    """

    MAX_SYMBOLS_PER_REQUEST = 60

    def parse_payload(self, reader: BinaryReader, **ctx: Any) -> list[dict[str, Any]]:

        price_scale = int(ctx.get("price_scale", PRICE_SCALE_QUOTES))

        count = self.guarded_count(reader, ctx, 8)

        rows: list[dict[str, Any]] = []

        for _ in range(count):
            if reader.remaining < 8:
                break

            code = decode_gbk(reader.bytes(6))

            row: dict[str, Any] = {"code": code}

            for key in ("price", "last_close", "open", "high", "low"):
                row[key] = round(reader.leb128() / price_scale, 4)

            row["volume"] = int(reader.tdx_float())

            row["amount"] = round(reader.tdx_float(), 4)

            for side in ("bid", "ask"):
                levels = []

                for _lvl in range(5):
                    if reader.remaining < 6:
                        break

                    levels.append(
                        {
                            "price": round(reader.leb128() / price_scale, 4),
                            "volume": int(reader.tdx_float()),
                        }
                    )

                row[side] = levels

            rows.append(row)

        if not rows and count:
            raise ParseError(
                f"QUOTES_LEGACY 声称 {count} 条但未能解析出任何记录"
                "（该命令可能已被主站停用，建议改用 0x054C）",
                context={"count": count},
            )

        return rows


# --------------------------------------------------------------------------- #


# 0x0530 实时行情快照（单只）                        ✅ golden-verified

# --------------------------------------------------------------------------- #


@register_parser(0x0530, family=Family.STANDARD, name="REALTIME_QUOTE", head=0)
class RealtimeQuoteParser(BaseParser):
    """实时行情快照（✅ 实测，10 只标的 × 3 台主站交叉验证）。



    这是**当前唯一可用**的实时行情命令。经典的批量命令

    ``0x053E`` / ``0x054C`` / ``0x0532`` / ``0x0450`` 在实测的 3 台主站上

    **全部无响应**（ReadTimeout），判定为该代服务端已下线，故实时行情

    只能**逐只请求**。



    请求体（8 字节，✅ 实测）::



        <B 0x01><B market><6s code>



    .. danger::

       ``market`` 字节语义与其余命令**相反**，请务必用

       :func:`quote_request_market` 换算（深→1，沪→0）。

       且本命令**一次只能查一只**：2 只标的的请求体在实测中超时不返回。



    响应布局（✅ 实测，缓冲区按字节精确耗尽）::



        offset 0     uint8    market   回声；0=深 1=沪（**标准语义**，与请求侧相反）

        offset 1     6s       code     回声代码

        offset 7     leb128   a        语义未识别（1~2 字节，恒使价格落在 offset 9）

        offset 9     leb128   price        现价

                    leb128   last_close - price

                    leb128   open       - price

                    leb128   high       - price

                    leb128   low        - price

                    leb128   u4         语义未识别（≈1.32e7，随 wall-clock ≈100/s 增长，

                                        个股间存在恒定偏移；非 tdx_float、非时间戳，

                                        原样保留在 ``extra['_u4']``）

                    leb128   -price     恒等于 -price，用作**自校验哨兵**

                    leb128   volume     成交量，单位「手」

                    leb128   v2         语义未识别（小值）

                    tdx_float amount    成交额，单位「元」

                    ...      tail       五档盘口 + 附加字段（⚠️ 精确布局未锁定，

                                         按字节耗尽后原样保留为整型序列，

                                         不臆造 bid/ask 价格）



    价格标度为 **100**（不是 K 线的 1000）；成交量按「手」下发，

    解析时 ×100 折算为「股」，以满足全局单位契约。



    交叉校验结果（与 0x052D 日线真值比对，10/10 命中 OHL）::



        600519  昨收=1297.40 开=1297.99 高=1305.00 低=1286.00   ✅

        601398  昨收=   7.86 开=   7.95 高=   8.07 低=   7.90   ✅

        000651  昨收=  39.00 开=  38.90 高=  39.21 低=  38.73   ✅

        300750  昨收= 368.50 开= 366.60 高= 367.33 低= 357.30   ✅



    .. warning::

       本命令存在「**错答案 + 合法帧**」风险：请求 market 写反时，服务端

       返回一个 56 字节、回声 ``600839``、载荷全零的响应。因此解析器

       **强制校验回声代码**（可用 ``strict_echo=False`` 关闭），

       不匹配即抛 :class:`ParseError`，绝不静默返回脏数据。

    """

    #: 记录中最小的已验证字段数（price + 4 差分 + u4 + 哨兵 + 量 + v2）

    MIN_FIELDS = 9

    def parse_payload(self, reader: BinaryReader, **ctx: Any) -> list[dict[str, Any]]:

        price_scale = int(ctx.get("price_scale", PRICE_SCALE_QUOTES))

        strict_echo = bool(ctx.get("strict_echo", True))

        expect_code = str(ctx.get("code") or "").strip()

        expect_market = ctx.get("market")

        market = reader.uint8()

        code = decode_gbk(reader.bytes(6))

        # --- 回声校验：拦截「错答案 + 合法帧」 ------------------------------ #

        if strict_echo and expect_code and code != expect_code:
            raise IntegrityViolation(
                f"0x0530 回声代码不匹配：请求 {expect_code!r} 但服务端返回 {code!r}。"
                "这通常意味着请求体的 market 字节写反了（0x0530 的 market 语义"
                "与其余命令相反，深市=1 沪市=0），请改用 quote_request_market() 换算",
                context={"expected": expect_code, "got": code, "market": market},
            )

        if strict_echo and expect_market is not None and market != int(expect_market):
            raise IntegrityViolation(
                f"0x0530 回声市场不匹配：期望 {expect_market} 但服务端返回 {market}",
                context={"expected": expect_market, "got": market, "code": code},
            )

        # --- 占位响应检测 ---------------------------------------------------- #

        rest_peek = reader.rest()

        if not rest_peek.strip(b"\x00"):
            raise IntegrityViolation(
                f"0x0530 返回空载荷（回声 {market}/{code}）：标的可能不存在或请求 market 字节有误",
                context={"code": code, "market": market},
            )

        # offset 7..8 是 2 字节不透明区：有时编码成 1 个 2 字节 varint

        # （如 600519 → cc 08），有时是 2 个 1 字节 varint（如 600000 → 4f 0b）。

        # 无论哪种，**价格恒定落在 offset 9**（10 只标的 × 3 台主站实测一致），

        # 因此这里直接按定长跳过，避免变长解读带来的对齐漂移。

        if reader.remaining < 12:
            raise ParseError(
                f"0x0530 响应过短（{len(reader.buf)} 字节），无法包含已验证字段",
                context={"code": code, "len": len(reader.buf)},
            )

        reader.seek(7)

        hdr = reader.bytes(2)

        if reader.pos != 9:  # pragma: no cover - seek 已保证
            raise ParseError("0x0530 内部定位错误", context={"pos": reader.pos})

        price = reader.leb128()

        d_last_close = reader.leb128()

        d_open = reader.leb128()

        d_high = reader.leb128()

        d_low = reader.leb128()

        u4 = reader.leb128()

        sentinel = reader.leb128()

        volume_lots = reader.leb128()

        v2 = reader.leb128()

        amount = reader.tdx_float()

        row: dict[str, Any] = {
            "market": market,
            "code": code,
            "price": round(price / price_scale, 4),
            "last_close": round((price + d_last_close) / price_scale, 4),
            "open": round((price + d_open) / price_scale, 4),
            "high": round((price + d_high) / price_scale, 4),
            "low": round((price + d_low) / price_scale, 4),
            "volume": int(round(volume_lots * SHARES_PER_LOT)),
            "amount": round(float(amount), 4),
            "volume_unit": "share",
        }

        extra: dict[str, Any] = {"_hdr": hdr.hex(), "_u4": u4, "_v2": v2}

        warnings: list[str] = []

        # 哨兵自校验：sentinel 应恒等于 -price

        if sentinel != -price:
            warnings.append(f"0x0530 自校验哨兵异常：期望 {-price} 实际 {sentinel}，字段可能已漂移")

        # 数值合理性：成交额应落在 [low*volume, high*volume]

        if row["volume"] > 0:
            lo, hi = row["low"] * row["volume"], row["high"] * row["volume"]

            if not (lo * 0.5 <= row["amount"] <= hi * 1.5):
                warnings.append(
                    f"0x0530 成交额 {row['amount']:.0f} 落在 OHLC×成交量区间 "
                    f"[{lo:.0f}, {hi:.0f}] 之外，疑似解码漂移"
                )

        # 五档盘口 + 附加字段的**精确布局尚未完全锁定**（不同标的尾部长度

        # 差异显著：73~99 字节，末段疑似含 4 字节定长成交额类字段）。但「不丢包」

        # 契约要求**按字节耗尽**整个响应帧，因此这里把剩余字节全部按 LEB128

        # 解码为整型序列原样保留，供后续黄金样本校正；**绝不臆造 bid/ask 价格**，

        # 以免把未经验证的布局当成事实输出。

        tail_ints: list[int] = []

        while reader.remaining > 0:
            try:
                tail_ints.append(reader.leb128())

            except Exception:
                # 末段若非纯 LEB128（如遗留的定长字段），原样保留剩余字节

                extra["raw_tail_hex"] = reader.rest().hex()

                break

        if tail_ints:
            extra["tail_leb128"] = tail_ints

        if warnings:
            extra["warnings"] = warnings

        row["extra"] = extra

        row["bid"] = []

        row["ask"] = []

        return [row]
