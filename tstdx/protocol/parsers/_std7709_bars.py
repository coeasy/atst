# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""7709 标准族 K 线 / 股本变迁 / 财务解析器：0x052D / 0x000F / 0x0010。

P11-3 自 ``std7709.py`` 拆出（REFACTOR_PLAN_v11），内容逐字搬移。
"""

from __future__ import annotations

import struct
from typing import Any

from ...codec.primitive import (
    PRICE_SCALE_BARS,
    BinaryReader,
    count_guard,
    decode_gbk,
)
from ...errors import ParseError
from ..commands import Family
from ..registry import TIER_L2, BaseParser, register_parser
from ._std7709_common import (
    MINUTELIKE_CATEGORIES,
    SHARES_PER_LOT,
    VOLUME_LOT_CATEGORIES,
    KlineCategory,
)

# 0x052D K 线                                      ✅ golden-verified

# --------------------------------------------------------------------------- #


@register_parser(0x052D, family=Family.STANDARD, name="SECURITY_BARS", head=2)
class SecurityBarsParser(BaseParser):
    """K 线 / 周期线。



    请求体（26 字节，✅ 实测）::



        <H market><6s code><H category><H 1><H start><H count><I 0><I 0><H 0>



    响应记录布局（✅ 实测，缓冲区按字节精确耗尽）::



        datetime   日线及以上: uint32 YYYYMMDD

                   分钟线:     uint16 lc16 日期 + uint16 当日分钟数

        leb128     open_diff   开盘价相对基准的增量

        leb128     close_diff  收盘价相对**开盘价**的增量

        leb128     high_diff   最高价相对**开盘价**的增量

        leb128     low_diff    最低价相对**开盘价**的增量

        tdx_float  volume      成交量（股）

        tdx_float  amount      成交额（元）

        [uint16×2] up/down     仅指数有效：上涨家数 + 下跌家数



    差分还原（基准 ``base`` 初值 0，其后取上一条的 ``close_abs``）::



        open_abs  = open_diff  + base

        close_abs = open_abs   + close_diff

        high_abs  = open_abs   + high_diff

        low_abs   = open_abs   + low_diff

        price     = abs / 1000.0



    单位归一（✅ 实测）

        :data:`VOLUME_LOT_CATEGORIES` 中的周期成交量以「手」下发，

        解析时统一 ×100 折算为「股」，以满足全局单位契约

        （price=元 / volume=股 / amount=元）。可用 ``volume_unit`` 上下文覆盖。



    .. note::

       指数 K 线尾部多 4 字节（涨跌家数）。解析器通过 ``index=True`` 上下文

       开关处理；默认关闭，因为股票与指数共用同一命令号。

    """

    #: 单次请求上限（服务端限制）

    MAX_BARS_PER_REQUEST = 800

    def parse_payload(self, reader: BinaryReader, **ctx: Any) -> list[dict[str, Any]]:

        category = int(ctx.get("category", KlineCategory.DAY))

        price_scale = int(ctx.get("price_scale", PRICE_SCALE_BARS))

        volume_encoding = str(ctx.get("volume_encoding", "tdx_float"))

        index_mode = bool(ctx.get("index", False))

        volume_unit = str(ctx.get("volume_unit", "auto")).lower()

        if volume_unit not in ("auto", "share", "lot"):
            raise ParseError(
                f"未知 volume_unit: {volume_unit!r}（可选 auto/share/lot）",
                context={"volume_unit": volume_unit},
            )

        lot_factor = (
            SHARES_PER_LOT
            if (
                volume_unit == "lot"
                or (volume_unit == "auto" and category in VOLUME_LOT_CATEGORIES)
            )
            else 1
        )

        count = self.guarded_count(reader, ctx, 16)

        bars: list[dict[str, Any]] = []

        base = 0

        for _i in range(count):
            # 最小记录 = 4(dt) + 4(最短 leb128) + 8(vol+amt) = 16

            if reader.remaining < 16:
                break

            dt = self._read_datetime(reader, category)

            open_diff = reader.leb128()

            close_diff = reader.leb128()

            high_diff = reader.leb128()

            low_diff = reader.leb128()

            volume = self._read_number(reader, volume_encoding)

            amount = self._read_number(reader, volume_encoding)

            open_abs = open_diff + base

            close_abs = open_abs + close_diff

            high_abs = open_abs + high_diff

            low_abs = open_abs + low_diff

            base = open_abs + close_diff  # 下一条的基准

            row: dict[str, Any] = {
                "datetime": f"{dt[0]:04d}-{dt[1]:02d}-{dt[2]:02d} {dt[3]:02d}:{dt[4]:02d}",
                "date": f"{dt[0]:04d}-{dt[1]:02d}-{dt[2]:02d}",
                "time": f"{dt[3]:02d}:{dt[4]:02d}",
                "open": round(open_abs / price_scale, 4),
                "close": round(close_abs / price_scale, 4),
                "high": round(high_abs / price_scale, 4),
                "low": round(low_abs / price_scale, 4),
                "volume": int(round(volume * lot_factor)),
                "amount": round(float(amount), 4),
            }

            row["volume_unit"] = "share"

            if index_mode and reader.remaining >= 4:
                row["up_count"] = reader.uint16()

                row["down_count"] = reader.uint16()

            bars.append(row)

        return bars

    # -- 内部 -------------------------------------------------------------- #

    @staticmethod
    def _read_datetime(reader: BinaryReader, category: int) -> tuple[int, int, int, int, int]:
        """返回 ``(year, month, day, hour, minute)``。"""

        if category in MINUTELIKE_CATEGORIES:
            raw_date = reader.uint16()

            minutes = reader.uint16()

            year = raw_date // 2048 + 2004

            remainder = raw_date % 2048

            month = remainder // 100

            day = remainder % 100

            return year, month, day, minutes // 60, minutes % 60

        raw = reader.uint32()

        year, month, day = raw // 10000, (raw // 100) % 100, raw % 100

        # 日线及以上无时间分量；约定指向该周期的结束时刻（收盘）

        return year, month, day, 15, 0

    @staticmethod
    def _read_number(reader: BinaryReader, encoding: str) -> float:

        if encoding == "tdx_float":
            return reader.tdx_float()

        if encoding == "uint32":
            return float(reader.uint32())

        if encoding == "float32":
            return reader.float32()

        if encoding == "varint":
            return float(reader.varint())

        raise ParseError(f"未知成交量编码: {encoding!r}", context={"encoding": encoding})


# --------------------------------------------------------------------------- #

# 0x000F 股本变迁 / 除权除息                        ⚠️ 待样本锁定

# --------------------------------------------------------------------------- #


@register_parser(0x000F, family=Family.STANDARD, name="CAPITAL_CHANGES", head=2)
class CapitalChangesParser(BaseParser):
    """除权除息（GBBQ）。



    请求体：``<6s code><H market>``（✅ 实测）。



    .. warning::

       ⚠️ 记录布局未按样本锁定。公开资料给出的 29 字节定长结构与

       实测响应长度不自洽，因此本解析器采用**自适应**策略：

       按缓冲区均分推断记录长度，并按已知语义做最佳努力解析。

       一旦取得 golden 样本即改为定长解析并置 ``verified=True``。

    """

    CATEGORY = {
        1: "除权除息",
        2: "送配股上市",
        3: "非流通股上市",
        4: "未知股本变动",
        5: "股本变化",
        6: "增发新股",
        7: "股份回购",
        8: "增发新股上市",
        9: "转配股上市",
        10: "可转债上市",
        11: "扩缩股",
        12: "非流通股缩股",
        13: "送认购权证",
        14: "送认沽权证",
    }

    #: 公开资料给出的定长记录大小（未验证）

    LEGACY_RECORD_SIZE = 29

    def parse_payload(self, reader: BinaryReader, **ctx: Any) -> list[dict[str, Any]]:

        count = reader.uint16()

        if count == 0:
            return []

        body = reader.rest()

        size = int(ctx.get("record_size", 0) or 0)

        if size <= 0:
            size = len(body) // count if count else 0

        if size < 9:
            # 太短，无法解析；保留原始字节由 L2 兜底

            raise ParseError(
                f"除权除息记录长度异常: count={count}, body={len(body)}, size={size}",
                context={"count": count, "body_len": len(body)},
            )

        # T3 钳制：ctx 指定的 record_size 大于实际 body 均分值时，
        # 声明数可能远超剩余字节容纳上限
        guarded = count_guard(count, size, len(body))

        if guarded != count:
            self.warn_ctx(
                ctx,
                f"count 失真已钳制：声明 {count} 条，"
                f"按 {size}B/条 与 body={len(body)}B 只能容纳 {guarded} 条",
            )
            count = guarded

        rows: list[dict[str, Any]] = []

        for i in range(count):
            rec = body[i * size : (i + 1) * size]

            if len(rec) < size:
                # 尾记录短于声明步长（截断流）——直接调用 _decode 会产生
                # 裸 struct.error/IndexError，这里停止解析并留痕
                self.warn_ctx(ctx, f"第 {i} 条记录不完整（{len(rec)}/{size}B），已停止解析")
                break

            rows.append(self._decode(rec, size))

        return rows

    def _decode(self, rec: bytes, size: int) -> dict[str, Any]:

        market = rec[0]

        code = decode_gbk(rec[1:7])

        category = rec[7] if size > 7 else 0

        row: dict[str, Any] = {
            "market": market,
            "code": code,
            "category": category,
            "category_name": self.CATEGORY.get(category, f"未知({category})"),
        }

        if size >= 13:
            (date_raw,) = struct.unpack_from("<I", rec, 9)

            row["date"] = self._decode_date(date_raw)

        if size >= 29:
            fh_qty, pg_price, sg_qty, pg_qty = struct.unpack_from("<ffff", rec, 13)

            row.update(
                dividend=round(fh_qty, 6),
                rights_price=round(pg_price, 6),
                bonus_ratio=round(sg_qty, 6),
                rights_ratio=round(pg_qty, 6),
            )

        return row

    @staticmethod
    def _decode_date(raw: int) -> str:

        year, month, day = raw // 10000, (raw // 100) % 100, raw % 100

        if 1990 <= year <= 2100 and 1 <= month <= 12 and 1 <= day <= 31:
            return f"{year:04d}-{month:02d}-{day:02d}"

        return f"{raw:08d}"


# --------------------------------------------------------------------------- #

# 0x0010 财务基础信息                               ⚠️ 待样本锁定

# --------------------------------------------------------------------------- #


@register_parser(0x0010, family=Family.STANDARD, name="FINANCE_INFO", head=2, tier=TIER_L2)
class FinanceInfoParser(BaseParser):
    """财务基础信息。



    请求体：``<6s code><H market>``（✅ 实测，响应约 14 KB）。



    .. warning::

       ⚠️ 响应是「若干条记录 + 每条若干字段」的双层结构，字段语义顺序

       依赖主站版本。本解析器先按**自适应记录长度**输出原始切片与

       float32 数组，语义映射交由 ``domain.models`` 按 profile 处理。

    """

    def parse_payload(self, reader: BinaryReader, **ctx: Any) -> list[dict[str, Any]]:

        count = reader.uint16()

        if count == 0:
            return []

        body = reader.rest()

        size = int(ctx.get("record_size", 0) or 0)

        if size <= 0:
            size = len(body) // count if count else 0

        if size < 8:
            raise ParseError(
                f"财务信息记录长度异常: count={count}, body={len(body)}, size={size}",
                context={"count": count, "body_len": len(body)},
            )

        # T3 钳制：ctx 指定的 record_size 大于实际 body 均分值时的失真防御
        guarded = count_guard(count, size, len(body))

        if guarded != count:
            self.warn_ctx(
                ctx,
                f"count 失真已钳制：声明 {count} 条，"
                f"按 {size}B/条 与 body={len(body)}B 只能容纳 {guarded} 条",
            )
            count = guarded

        rows: list[dict[str, Any]] = []

        for i in range(count):
            rec = body[i * size : (i + 1) * size]

            if len(rec) < size:
                # 尾记录短于声明步长（截断流）——停止解析并留痕
                self.warn_ctx(ctx, f"第 {i} 条记录不完整（{len(rec)}/{size}B），已停止解析")
                break

            market = rec[0]

            code = decode_gbk(rec[1:7])

            # 7 字节之后按 float32 数组展开（剩余长度非 4 倍数时截断）

            tail = rec[7:]

            n = len(tail) // 4

            values = [round(v, 6) for v in struct.unpack_from(f"<{n}f", tail, 0)] if n else []

            rows.append({"market": market, "code": code, "values": values})

        return rows


# --------------------------------------------------------------------------- #
