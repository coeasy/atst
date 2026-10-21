# Copyright (c) 2026 atst contributors
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
from ...diagnostics import WarningCode, record_warning
from ...errors import ParseError, ProtocolError
from ..commands import Family
from ..registry import TIER_L2, BaseParser, register_parser
from ._std7709_common import (
    DAYLIKE_CATEGORIES,
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

        #: 指数：日线族的原始成交量在既有类别规则之外**还要再小 100 倍**。
        #:
        #: 实测（``sh000001``，``volume_unit="share"`` 禁用换算直取服务端原值）::
        #:
        #:     cat      amt/raw_volume    raw 的单位
        #:     4 (日)      1549.27      手        ← ×100 后 15.49 元
        #:     5/6/9/10/11 上面 ×100     手/100
        #:
        #: 1549 元不可能是全市场的成交量加权均价（沪市最贵的贵州茅台也就 1300 元，
        #: 而它是 2300 只里的一只）；×100 后的 15.49 元与常识吻合。腾讯同一日给同量级
        #: 值、新浪给的是 ×100 的值——三家 cross-check 一致。
        #: 对照股票完全正常：cat 4 的 raw 按「股」（amt/raw = 1281 元 = 真实股价）、
        #: cat 5+ 的 raw 按「手」，类别规则已经处理对了。所以这一条只对指数生效。
        #: 指数修正是**独立于单位声明**的一条事实（指数日线族的原值在名义单位之外
        #: 还要再小 100 倍），因此判据是「不是 share」而不是「是 auto」：
        #: ``volume_unit="lot"`` 声明的是「原值单位是手」，它没有取消指数这条事实，
        #: 原先 ``volume_unit == "auto"`` 的写法会让显式声明 lot 的调用方少乘 100
        #: （指数日线族只拿到 1/100）。反过来 ``share`` 必须排除——它的语义是
        #: 「直取服务端原值、不做任何换算」（探测器正是靠它读原值定标上面这张表），
        #: 在这里再乘就把定标用的尺子本身改了。
        if volume_unit != "share" and index_mode and category in DAYLIKE_CATEGORIES:
            lot_factor *= SHARES_PER_LOT
        elif volume_unit == "auto" and index_mode:
            #: 分钟族是另一回事，不能跟着乘：原始 volume 按服务端**名义口径**等于
            #: amount/100（实测 1min/5min/15min/30min 每一根比值都精确是 100.00，而股票
            #: 分钟线是该分钟的真实价）。它不是成交股数——乘 100 只会得到成交额本身。
            #: 同日 5 分钟 bar 与新浪交叉：amount 完全吻合、volume 差 6.49 倍，证实这个
            #: 字段是名义值。处置是如实告警而不是伪造一个数：指数要量，请用日线。
            #:
            #: 说「按名义口径」而不是「恒等于」是实测逼出来的：2026-10 实测指数 60min 的
            #: 当日首根（10:30）服务端下发了一个比名义口径低约 97 倍的 volume，这条绝对
            #: 断言本身就不成立（偏离由下面的事后核验单独报出）。
            record_warning(
                WarningCode.INDEX_MINUTE_VOLUME_NOMINAL,
                f"0x052D 指数分钟线（category={category}）的成交量是服务端名义值"
                "（按名义口径等于成交额/100），不是真实成交股数；"
                "指数要量请改用日线或换 Web 源",
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

        #: 名义口径的**事后核验**：与上面那条「这个字段是名义值」互补——那条说明字段性质，
        #: 这条报告服务端自己下发的不一致。指数分钟族的 volume 按上面刻画应等于 amount/100；
        #: 实测 1min/5min/15min/30min 精确吻合，唯一偏离的是指数 60min 当日首根（10:30，
        #: 低约 97 倍）。判据只抓「低一个数量级以上」的一侧——正常行（比值 100）与股票行
        #: （比值≈收盘价）都不触发，唯一的实采异常必然触发，所以它不会变成噪声。
        if volume_unit == "auto" and index_mode and category in MINUTELIKE_CATEGORIES:
            low = [
                row for row in bars if row["amount"] > 0 and row["volume"] * 1000 < row["amount"]
            ]
            if low:
                sample = low[0]
                record_warning(
                    WarningCode.INDEX_MINUTE_VOLUME_INCONSISTENT,
                    f"0x052D 指数分钟线（category={category}）有 {len(low)} 根的成交量比"
                    "服务端名义口径（= 成交额/100）低一个数量级以上"
                    f"（例：{sample['datetime']} volume={sample['volume']} "
                    f"amount={sample['amount']}）——这是服务端下发的不一致值"
                    "（实测见 60min 当日首根），不是解码错位；该字段不可当作可信成交量",
                )

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

        #: 两族之外的 category 没有已证逆向的 datetime 布局：按 uint32 猜会把整行
        #: 后续字段全部错位（F-78）。fail-closed 而不是静默猜测。
        if category not in DAYLIKE_CATEGORIES:
            raise ProtocolError(
                f"7709 K 线未知周期 category={category}："
                "datetime 既不在分钟族（uint16+uint16）也不在日线族（uint32）的已证布局内，拒绝猜测",
                context={"category": category, "field": "datetime"},
            )

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


@register_parser(0x000F, family=Family.STANDARD, name="CAPITAL_CHANGES", head=2, tier=TIER_L2)
class CapitalChangesParser(BaseParser):
    """除权除息（GBBQ）。

    请求体（9 字节，✅ 2026-10-21 真机实测修正）::

        <H 1><B market><6s code>

    即「固定前缀 ``01 00`` + 市场字节 + 6 字节代码」。**顺序反过来越界**：
    旧实现发 ``<6s code><H market>``（代码在前），服务端按 ``market@2 /
    code@3:9`` 解读后整个记录结构错位——``market`` 读到 ASCII ``'0'``（48）、
    ``code`` 只剩半个代码，且前 2 字节被当成「请求条数」读成 0x3030，服务器
    于是回一大块缓冲残留（F-37 的历史结论正来源于此：四份旧样本全是错请求采的，
    首部 ``uint16`` 与正文长度不整除、offset×步长全组合试解无一致解）。

    响应结构（✅ 2026-10-21 深/沪多只标的重放）::

        <H blocks><blocks × [<B market><6s code><H num><num × 29B 记录>]>

    单条 29 字节记录::

        <B market><6s code><B 0><I date(YYYYMMDD)><B category><16B 分类数据>

    分类数据（16B）按 ``category`` 解读：``1``（除权除息）是四个 float32
    （分红 / 配股价 / 送转股 / 配股比例）；其余类别是四组股本数（盘前流通 /
    前总股本 / 盘后流通 / 后总股本，单位万股，float32）——2026-10-21 实测
    茅台 2002-07-26「10 送 1」前后两组值恰好成 1.1 倍，确认是 float32 而非
    TDX 自定义浮点。

    .. warning::

       ⚠️ **字段口径仍未由本仓 golden 锁定（F-37）**：记录布局已按公开参考
       实现（pytdx ``GetXdXrInfo``）与 2026-10-21 真机交叉验证，日期/类别/四元组
       均已对上业务常识，但仓内 golden 样本仍是「错请求」年代采的，未重采前
       字段语义**不保证**（条数与结构可用）。

       解析器对块头做**回声校验**：``code`` / ``market`` 与请求上下文不符时
       发 ``field_out_of_domain`` 告警而不是静默返回脏数据。
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

    #: 已验证记录长度：7B 头 + 1B + 4B 日期 + 1B 类别 + 16B 分类数据

    RECORD_SIZE = 29

    def parse_payload(self, reader: BinaryReader, **ctx: Any) -> list[dict[str, Any]]:

        count = reader.uint16()

        if count == 0:
            return []

        expect_code = str(ctx.get("code") or "").strip()
        expect_market = ctx.get("market")

        rows: list[dict[str, Any]] = []

        for block in range(count):
            if reader.remaining < 9:
                # 请求几条就回几块；块读不动即止（旧错请求回的缓冲残留也走这里）
                self.warn_ctx(
                    ctx,
                    f"第 {block} 个证券块不足 9 字节（剩余 {reader.remaining}B），已停止解析",
                )
                break

            market = reader.uint8()

            code = decode_gbk(reader.bytes(6))

            # --- 回声校验：块头必须与请求一致（错请求时代的错位形状） -------- #
            self._check_echo(ctx, market, code, expect_market, expect_code)

            num = reader.uint16()

            if num == 0:
                continue

            size = int(ctx.get("record_size", 0) or 0) or self.RECORD_SIZE

            if size < 9:
                raise ParseError(
                    f"除权除息记录长度异常: size={size}",
                    context={"size": size},
                )

            guarded = count_guard(num, size, reader.remaining)

            if guarded != num:
                self.warn_ctx(
                    ctx,
                    f"count 失真已钳制：声明 {num} 条，"
                    f"按 {size}B/条 与剩余 {reader.remaining}B 只能容纳 {guarded} 条",
                )
                num = guarded

            for j in range(num):
                if reader.remaining < size:
                    self.warn_ctx(
                        ctx,
                        f"第 {block} 块第 {j} 条记录不完整"
                        f"（剩余 {reader.remaining}B/{size}B），已停止解析",
                    )
                    break

                rows.append(self._decode(reader.bytes(size), size))

        return rows

    @staticmethod
    def _check_echo(
        ctx: dict[str, Any],
        market: int,
        code: str,
        expect_market: Any,
        expect_code: str,
    ) -> None:
        """块头回声与请求不符时留痕（旧错请求的形状：market=48 / 半个代码）。"""
        problems: list[str] = []
        if expect_code and code != expect_code:
            problems.append(f"code={code!r} != 请求 {expect_code!r}")
        if expect_market is not None and market != int(expect_market):
            problems.append(f"market={market} != 请求 {int(expect_market)}")
        if problems:
            record_warning(
                WarningCode.FIELD_OUT_OF_DOMAIN,
                f"0x000f 块头回声不符（{'；'.join(problems)}）——"
                "请求体的 market/code 顺序或字段布局可能已漂移，字段值不可信"
                f"（块头 market={market} code={code!r}）",
            )

    def _decode(self, rec: bytes, size: int) -> dict[str, Any]:

        market = rec[0]

        code = decode_gbk(rec[1:7])

        category = rec[12] if size > 12 else 0

        row: dict[str, Any] = {
            "market": market,
            "code": code,
            "category": category,
            "category_name": self.CATEGORY.get(category, f"未知({category})"),
        }

        if size >= 12:
            (date_raw,) = struct.unpack_from("<I", rec, 8)

            row["date"] = self._decode_date(date_raw)

        if size >= self.RECORD_SIZE:
            values = [round(v, 6) for v in struct.unpack_from("<ffff", rec, 13)]

            row["values"] = values

            if category == 1:
                # 除权除息：分红 / 配股价 / 送转股 / 配股比例
                row.update(
                    dividend=values[0],
                    rights_price=values[1],
                    bonus_ratio=values[2],
                    rights_ratio=values[3],
                )
            else:
                # 其余类别是四组股本数（万股）：盘前流通 / 前总股本 / 盘后流通 / 后总股本
                row.update(
                    panqianliutong=values[0],
                    qianzongguben=values[1],
                    panhouliutong=values[2],
                    houzongguben=values[3],
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

    请求体（9 字节，✅ 2026-10-21 真机实测修正）::

        <H 1><B market><6s code>

    响应结构（✅ 2026-10-21 深/沪 8 只标的重放，记录头与请求逐一相符）::

        <H count><count × 143B 记录>

    单条 143 字节记录::

        <B market><6s code>
        <f 流通股本(万股)><H 省份码><H 行业码>
        <I 更新日期(YYYYMMDD)><I 上市日期(YYYYMMDD)>
        <30 × f 总股本(万股) … 保留2>

    **为什么旧实现是错的**：它发的是 ``<6s code><H market>``（代码在前），
    服务端按 ``market@2 / code@3:9`` 解读请求，于是记录头整体错位——
    ``market`` 读到 ASCII ``'0'``（48）、``code`` 只剩半个代码；且前 2 字节被
    当成「请求条数」读成 ``0x3030``，服务器回 100 条记录（仅第 1 条是本人，
    其余是服务端缓冲残留，内容每次抓包都不同）。请求顺序修正后请求 1 条回 1 条。

    .. warning::

       ⚠️ 字段序按公开参考实现（pytdx ``GetFinanceInfo`` / injoyai-tdx
       ``model_finance.go``）交叉锁定，并与 2026-10-21 真机数值对拍（总股本 /
       流通股本 / 上市日期均吻合）；本仓 golden 样本仍是「错请求」年代采的，
       未重采前字段语义**不保证**（F-37），条数 / 记录头 / 结构可用。

       解析器对记录头做**回声校验**：``code`` / ``market`` 与请求上下文不符时
       发 ``field_out_of_domain`` 告警而不是静默返回脏数据。
    """

    #: 已验证记录长度：7B 头 + 4+2+2+4+4 类型化字段 + 30×4B float32

    RECORD_SIZE = 143

    def parse_payload(self, reader: BinaryReader, **ctx: Any) -> list[dict[str, Any]]:

        count = reader.uint16()

        if count == 0:
            return []

        body = reader.rest()

        size = int(ctx.get("record_size", 0) or 0)

        if size <= 0:
            size = len(body) // count if count else 0

        if size < self.RECORD_SIZE:
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

        expect_code = str(ctx.get("code") or "").strip()
        expect_market = ctx.get("market")

        rows: list[dict[str, Any]] = []

        for i in range(count):
            rec = body[i * size : (i + 1) * size]

            if len(rec) < size:
                # 尾记录短于声明步长（截断流）——停止解析并留痕
                self.warn_ctx(ctx, f"第 {i} 条记录不完整（{len(rec)}/{size}B），已停止解析")
                break

            market = rec[0]

            code = decode_gbk(rec[1:7])

            # --- 回声校验：记录头必须与请求一致（错请求时代的错位形状） ------ #
            self._check_echo(ctx, market, code, expect_market, expect_code)

            # 7B 头之后：<fHHII> + 30 × float32
            liutong, province, industry, updated, ipo = struct.unpack_from("<fHHII", rec, 7)

            tail = rec[23:]

            n = len(tail) // 4

            values = [round(v, 6) for v in struct.unpack_from(f"<{n}f", tail, 0)] if n else []

            rows.append(
                {
                    "market": market,
                    "code": code,
                    "float_shares": round(float(liutong), 6),
                    "province": int(province),
                    "industry": int(industry),
                    "updated_date": int(updated),
                    "ipo_date": int(ipo),
                    "values": values,
                }
            )

        return rows

    @staticmethod
    def _check_echo(
        ctx: dict[str, Any],
        market: int,
        code: str,
        expect_market: Any,
        expect_code: str,
    ) -> None:
        """记录头回声与请求不符时留痕（旧错请求的形状：market=48 / 半个代码）。"""
        problems: list[str] = []
        if expect_code and code != expect_code:
            problems.append(f"code={code!r} != 请求 {expect_code!r}")
        if expect_market is not None and market != int(expect_market):
            problems.append(f"market={market} != 请求 {int(expect_market)}")
        if problems:
            record_warning(
                WarningCode.FIELD_OUT_OF_DOMAIN,
                f"0x0010 记录头回声不符（{'；'.join(problems)}）——"
                "请求体的 market/code 顺序或字段布局可能已漂移，字段值不可信"
                f"（记录头 market={market} code={code!r}）",
            )


# --------------------------------------------------------------------------- #
