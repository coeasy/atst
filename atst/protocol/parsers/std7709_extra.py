# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""7709 标准协议族——扩展 L1 解析器（代码表 / 分时 / 成交 / 批量行情 / 板块 / 文件下载）。

.. danger:: 洁净室标记
   本文件中标注 ⚠️ 的解析器，其字段布局来自**公开资料推断 + 协议常识**，
   **尚未通过 ``tests/golden/`` 自采集样本回归**。它们作为 L1 尝试优先被分派，
   但内部带有**结构守卫**：一旦检测到记录数 / 缓冲区长度 / 数值合理性不自洽，
   立即抛 :class:`~atst.errors.ParseError`，由 :func:`~atst.protocol.registry.dispatch`
   自动降级到 L2 通用解析 / L3 原始透传，**绝不把臆造的布局当成事实输出**。

   换句话说：这里的代码是「功能占位 + 正确性边界」，等待 golden 抓包后
   升级为 ✅ 精确解析（届时把类上的注释从 ⚠️ 改为 ✅ 即可）。
"""

from __future__ import annotations

from typing import Any

from ...codec.primitive import PRICE_SCALE_QUOTES, BinaryReader, decode_gbk
from ...errors import ParseError
from ..commands import Family
from ..registry import BaseParser, register_parser
from ._std7709_common import SHARES_PER_LOT

__all__ = [
    "SecurityListParser",
    "MinuteTodayParser",
    "MinuteHistoryParser",
    "TradeTodayParser",
    "TradeTodayAltParser",
    "QuotesSnapshotParser",
    "QuotesDepthPushParser",
    "FileDownloadParser",
    "BlockQuotesParser",
    "VolumePriceDistParser",
    "AuctionSnapshotParser",
    "IndexMomentumParser",
]


# --------------------------------------------------------------------------- #
# 0x044D 代码表                                  ⚠️ 待样本锁定
# --------------------------------------------------------------------------- #
@register_parser(0x044D, family=Family.STANDARD, name="SECURITY_LIST", head=2, tier="L2")
class SecurityListParser(BaseParser):
    """证券列表（分页 1000/页）。

    响应：``<H count>`` 后跟若干定长记录。

    ⚠️ 记录布局公开资料有两种说法（21 字节 / 29 字节），本解析器以
    ``record_size`` 上下文驱动（默认 29），并做长度守卫。
    """

    DEFAULT_RECORD_SIZE = 29

    def parse_payload(self, reader, **ctx: Any) -> list[dict[str, Any]]:
        record_size = int(ctx.get("record_size", self.DEFAULT_RECORD_SIZE))
        count = self.guarded_count(reader, ctx, record_size)
        rows: list[dict[str, Any]] = []
        for _ in range(count):
            if reader.remaining < record_size:
                break
            rec = reader.bytes(record_size)
            # ⚠️ 字段偏移为公开资料推断：market(2) + code(6) + name(8) + type(1) + ...
            market = int.from_bytes(rec[0:2], "little")
            code = decode_gbk(rec[2:8])
            name = decode_gbk(rec[8:16])
            symbol_type = rec[16] if len(rec) > 16 else 0
            rows.append({"market": market, "code": code, "name": name, "type": symbol_type})
        if rows and len(rows) != count:
            # 数量不自洽 → 交给 L2，避免截断误解
            raise ParseError(
                f"0x044D 记录数不自洽：声明 {count} 实际解析 {len(rows)}",
                context={"count": count, "parsed": len(rows)},
            )
        return rows


# --------------------------------------------------------------------------- #
# 0x0537 当日分时                                  ✅ 2026-10-21 实测锁定
# --------------------------------------------------------------------------- #
@register_parser(0x0537, family=Family.STANDARD, name="MINUTE_TODAY", head=2, tier="L1")
class MinuteTodayParser(BaseParser):
    """当日分时数据（命令 ``0x0537``）。

    请求体（12 字节，✅ 2026-10-21 锁定）::

        <H market><6s code><I 0>

    **市场在前**。历史上本仓发的是 ``<6s code><H market><I 0>``（代码在前），
    服务端按市场在前解读时前 2 字节读成 ASCII 码点（沪市 ``'6''6'`` = 0x3636
    越域），上证直接回 4 字节空桩；深市靠服务端对残缺代码的宽容才碰巧有响应
    ——能解出东西纯属侥幸，不能当契约（存档见
    ``tests/golden/quotation/_archive_wrong_request/``）。

    响应（✅ 2026-10-21 深/沪双市场实测锁定）::

        <H num><2B 0>
        num x { leb128 price_delta | leb128 avg_delta | leb128 volume }

    * ``leb128`` 即 :meth:`BinaryReader.leb128`（7709 标准族价格差分编码，
      首字节 ``[7]=续行 [6]=符号 [5..0]=数据``，续行 7 位/字节）。
    * **价格 / 均价是「基准 + 增量」**：首条记录给绝对值，其后各条给
      **相对首条的增量**（不是相对上一条）。价格单位 0.01 元，均价单位
      0.0001 元。
    * ``volume`` 是**绝对量**，单位为手（× ``SHARES_PER_LOT`` 得股数）。

    实测判据（sh600000 / sz000001 各 240 条满日样本）：与同标的同日
    ``0x052D`` 1 分钟 K 线**逐条对齐**——``price`` 与 K 线收盘价 240/240 相符、
    ``volume`` 与 K 线成交量 240/240 相符、``avg_price`` 与 K 线累计量加权均价
    239/240 相符（唯一不符的是首条：首条基准即绝对值，不做二次叠加）。
    缓冲区按字节精确耗尽（1223/1223、1196/1196）。

    .. note::
       **与 pytdx 的分歧**（:pypi:`pytdx` ``GetMinuteTimeData``）值得记一笔：
       它对 ``price_delta`` 做**逐笔累加**（``last_price += price_raw``），
       且整个丢弃第二个字段。拿同一份真机载荷按它的写法复算，240 条里只有
       2 条与 K 线收盘价相符（市场横盘时增量≈0，两种模型偶然重合）——累计模型
       在这条命令上是错的，本解析器的「基准 + 增量」才是被 K 线证实的那一个。
       同族的 ``0x0FC5`` 恰恰相反：那里累计模型是对的（见
       :class:`TradeTodayParser`）。两条命令的差分语义不同，别互相套用。
    """

    #: 每条最短 = 3 个 1 字节 leb128
    RECORD_SIZE = 3

    def parse_payload(self, reader, **ctx: Any) -> list[dict[str, Any]]:
        count = self.guarded_count(reader, ctx, self.RECORD_SIZE)
        reader.skip(2)  # <H num> 之后 2 个恒零字节（pytdx 同样 pos += 4）
        return _decode_minute_records(reader, count)


def _decode_minute_records(reader: BinaryReader, count: int) -> list[dict[str, Any]]:
    """0x0537 / 0x0FB4 共用的分时记录解码（布局见 :class:`MinuteTodayParser`）。"""
    rows: list[dict[str, Any]] = []
    base_price = 0
    base_avg = 0
    for index in range(count):
        price_delta = reader.leb128()
        avg_delta = reader.leb128()
        volume = reader.leb128()
        if index == 0:
            # 首条：两个价格字段给的是绝对值，不是相对基准的增量
            base_price = price_delta
            base_avg = avg_delta
            price_units = price_delta
            avg_units = avg_delta
        else:
            price_units = base_price + price_delta
            avg_units = base_avg + avg_delta
        rows.append(
            {
                "index": index,
                "minute": _minute_label(index),
                "time": _time_label(index),
                "price": round(price_units / PRICE_SCALE_QUOTES, 4),
                "avg_price": round(avg_units / _AVG_PRICE_SCALE, 4),
                "volume": volume * SHARES_PER_LOT,
            }
        )
    return rows


def _minute_label(index: int) -> int:
    """分时记录序号 → 当日分钟数（对齐 ``0x052D`` 1 分钟 K 线的 bar 标签）。

    第 0..119 条对应 09:31..11:30（上午收 11:30），第 120..239 条对应
    13:01..15:00（下午收 15:00）。标签取 bar **结束**分钟，与 K 线一致。
    """
    if index < _AM_RECORDS:
        return 9 * 60 + 31 + index
    return 13 * 60 + 1 + (index - _AM_RECORDS)


def _time_label(index: int) -> str:
    hh, mm = divmod(_minute_label(index), 60)
    return f"{hh:02d}:{mm:02d}"


#: 上午分时记录条数（09:31..11:30）
_AM_RECORDS = 120
#: 均价字段的单位：0.0001 元（实测标定，见 MinuteTodayParser 文档字符串）
_AVG_PRICE_SCALE = 10000


# --------------------------------------------------------------------------- #
# 0x0FC5 当日成交明细（逐笔）                      ✅ 2026-10-21 实测锁定
# --------------------------------------------------------------------------- #
@register_parser(0x0FC5, family=Family.STANDARD, name="TRADE_TODAY", head=2, tier="L1")
class TradeTodayParser(BaseParser):
    """当日逐笔成交（命令 ``0x0FC5``）。

    请求体（12 字节，✅ 2026-10-21 锁定）::

        <H market><6s code><H start><H count>

    **市场在前**（与 :class:`MinuteTodayParser` 同源的旧 bug：本仓曾发
    ``<6s code><H market><H start><H count>``，上证回 2 字节空桩）。
    ``start`` 是**自最新一笔往回数的偏移**：``start=0`` 返回当日最后
    ``count`` 笔，``start=count`` 继续往前翻（实测翻页边界不重不漏）。

    响应（✅ 2026-10-21 深/沪双市场实测锁定）::

        <H num>
        num x { <H time_minutes> | leb128 price_delta | leb128 vol | leb128 num
                | leb128 buyorsell | leb128 reserved }

    * ``time_minutes`` 是**当日分钟数**（0 = 00:00，即该笔成交的真实时钟
      分钟）；15:05..15:30 为盘后固定价格交易时段，属正常值域。
    * ``price_delta`` 是**逐笔累加**的增量（与 0x0537 的「基准 + 增量」不同）：
      ``price = Σ price_delta``，单位 0.01 元。
    * ``vol`` 单位为手；``num`` 为该笔成交的委托单量；``buyorsell`` 为方向。
    * ``reserved`` 实测样本恒为 0，仅消费不输出。

    实测判据：

    * 4 份存量 golden 样本（76/75/75/77 字节）与实采载荷（75/215 字节）
      全部**按字节精确耗尽**；
    * 末笔累加价与同时刻 ``0x0530`` 实时行情价一致（沪 9.66 / 深 11.41）；
    * 全日逐笔（沪 4304 笔 / 深 4537 笔，09:15 开盘竞价至 15:20/15:30 盘后）
      按分钟汇总后与同日 ``0x052D`` 1 分钟 K 线逐桶对账：**237/240 桶成交量
      完全相等**，余下 3 桶全部是竞价并入——开盘竞价（09:25）与 09:30 连续
      竞价之和恰好等于 09:31 桶、尾盘 14:59 的空桶加上收盘竞价（15:00）
      恰好等于 15:00 桶、11:29 桶含 11:30:00 的收边那一笔；
    * 连续竞价期间每一笔的价格都落在所属 K 线 bar 的 ``[low, high]`` 内
      （100%；越界的全部是 14:57–15:00 收盘竞价期间的虚拟成交价，不属于
      任何连续竞价 bar）。

    .. note::
       **逐笔时间与 K 线 bar 标签差一分钟**，这不是 bug：K 线 bar 标签取
       **bar 结束分钟**（09:31 桶覆盖 09:30:00–09:30:59），而逐笔的
       ``time_minutes`` 是该笔成交的**真实时钟分钟**。把逐笔按标签 m 汇总、
       与 K 线标签 m+1 的桶对账即得上文的 237/240。开盘竞价成交（标签 09:25）
       不属任何连续竞价分钟，被 K 线并进 09:31 桶。

       ``buyorsell`` 观测到的取值为 0/1（连续竞价主买/主卖）、2（收盘竞价）、
       5（盘后固定价格）、8（量恒 0 的哨兵记录）；0/1/2 之外的含义参考实现
       （pytdx）同样不做解释，这里原样透传不猜。
    """

    #: 每条最短 = 2(时间) + 5 个 1 字节 leb128
    RECORD_SIZE = 7

    def parse_payload(self, reader, **ctx: Any) -> list[dict[str, Any]]:
        count = self.guarded_count(reader, ctx, self.RECORD_SIZE)
        return _decode_tick_records(reader, count)


def _decode_tick_records(reader: BinaryReader, count: int) -> list[dict[str, Any]]:
    """0x0FC5 / 0x0FC6 共用的逐笔记录解码（布局见 :class:`TradeTodayParser`）。"""
    rows: list[dict[str, Any]] = []
    last_price = 0
    for _ in range(count):
        minute = reader.uint16()
        price_delta = reader.leb128()
        volume = reader.leb128()
        orders = reader.leb128()
        buyorsell = reader.leb128()
        reader.leb128()  # reserved：实测恒 0
        last_price += price_delta
        hh, mm = divmod(minute, 60)
        rows.append(
            {
                "minute": minute,
                "time": f"{hh:02d}:{mm:02d}",
                "price": round(last_price / PRICE_SCALE_QUOTES, 4),
                "volume": volume * SHARES_PER_LOT,
                "orders": orders,
                "direction": buyorsell,
            }
        )
    return rows


# --------------------------------------------------------------------------- #
# 0x0FB4 历史分时                                  ⚠️ 布局同 0x0537，命令已停答
# --------------------------------------------------------------------------- #
@register_parser(0x0FB4, family=Family.STANDARD, name="MINUTE_HISTORY", head=2, tier="L2")
class MinuteHistoryParser(BaseParser):
    """指定日期历史分时（命令 ``0x0FB4``）。

    记录布局与 :class:`MinuteTodayParser` 相同（✅ 2026-10-21 按 0x0537 同族
    实测结论同步），但**命令本身已停答**：账本登记 ``STATUS_OFFLINE``
    （2026-09-06 三主站 × 多日期实测超时），客户端 fail-fast 抛
    :class:`CommandOffline`，本解析器不承担在线举证责任。
    """

    RECORD_SIZE = 3

    def parse_payload(self, reader, **ctx: Any) -> list[dict[str, Any]]:
        count = self.guarded_count(reader, ctx, self.RECORD_SIZE)
        reader.skip(2)
        return _decode_minute_records(reader, count)


# --------------------------------------------------------------------------- #
# 0x0FC6 当日成交明细（备用命令号）               ⚠️ 布局同 0x0FC5
# --------------------------------------------------------------------------- #
@register_parser(0x0FC6, family=Family.STANDARD, name="TRADE_TODAY_ALT", head=2, tier="L2")
class TradeTodayAltParser(BaseParser):
    """当日逐笔成交（备用命令号，布局与 :class:`TradeTodayParser` 相同）。

    部分主站以 0x0FC6 应答当日成交；记录布局照 0x0FC5 的实测结论同步
    （``<H time_minutes>`` + 5 个 leb128，价格逐笔累加）。该命令号无客户端
    入口，只能经通用 ``request`` 口手工发包。
    """

    RECORD_SIZE = 7

    def parse_payload(self, reader, **ctx: Any) -> list[dict[str, Any]]:
        count = self.guarded_count(reader, ctx, self.RECORD_SIZE)
        return _decode_tick_records(reader, count)


# --------------------------------------------------------------------------- #
# 0x054C 批量行情快照（五档）                      ⚠️ 多主站已停用
# --------------------------------------------------------------------------- #
@register_parser(0x054C, family=Family.STANDARD, name="QUOTES_SNAPSHOT", head=2, tier="L2")
class QuotesSnapshotParser(BaseParser):
    """批量行情快照（含五档盘口）。

    ⚠️ 实测多台主站对该命令无响应（疑似已下线），保留为兼容路径。
    布局照 0x0530 单只结构推广到批量：``<H count>`` + 每只需耗尽整段
    （含尾部未锁定五档），与单只一致——故直接复用逐只逻辑。
    """

    def parse_payload(self, reader, **ctx: Any) -> list[dict[str, Any]]:
        from .std7709 import RealtimeQuoteParser

        scale = int(ctx.get("price_scale", PRICE_SCALE_QUOTES))
        count = self.guarded_count(reader, ctx, 8)
        single = RealtimeQuoteParser()
        rows: list[dict[str, Any]] = []
        # count 已知时按单只 RealtimeQuoteParser 逐段解析；ctx 显式合并透传
        # （price_scale/strict_echo 以本层覆盖为准），保证告警缓冲等上下文连续
        inner_ctx = {**ctx, "price_scale": scale, "strict_echo": False}
        for _ in range(count):
            if reader.remaining < 8:
                break
            # 单只解析需要 market/code 上下文；这里尝试从首字节回声自取
            save_pos = reader.pos
            try:
                result = single.parse_payload(reader, **inner_ctx)
                rows.extend(result)
            except Exception as exc:
                # 「致命不降级」契约：IntegrityViolation 等已识别的数据完整性
                # 违规必须原样上抛；只有非 fatal 的布局失配才回退位置并停止
                if getattr(exc, "fatal", False):
                    raise
                reader.pos = save_pos
                self.warn_ctx(ctx, f"0x054C 第 {len(rows)} 条之后解析失败已停止: {exc}")
                break
        if not rows and count:
            raise ParseError(
                "0x054C 未能解析出任何记录（主站可能已下线，建议改用 0x0530 逐只请求）",
                context={"count": count},
            )
        return rows


# --------------------------------------------------------------------------- #
# 0x0547 五档刷新 / push 队列                      ⚠️ 待样本锁定
# --------------------------------------------------------------------------- #
@register_parser(0x0547, family=Family.STANDARD, name="QUOTES_DEPTH_PUSH", head=2, tier="L2")
class QuotesDepthPushParser(BaseParser):
    """五档刷新 / push 队列项。

    ⚠️ 与 0x0530 类似但为「增量推送」语义，布局待样本锁定。
    这里先用 0x0530 单只解析器兜底，并标注 ``push=True``。
    """

    def parse_payload(self, reader, **ctx: Any) -> list[dict[str, Any]]:
        from .std7709 import RealtimeQuoteParser

        scale = int(ctx.get("price_scale", PRICE_SCALE_QUOTES))
        try:
            result = RealtimeQuoteParser().parse_payload(
                reader, **{**ctx, "price_scale": scale, "strict_echo": False}
            )
        except Exception as exc:
            # 「致命不降级」契约：fatal（IntegrityViolation）原样上抛，
            # 只有非 fatal 的失败才包装为 ParseError 走降级
            if getattr(exc, "fatal", False):
                raise
            raise ParseError(f"0x0547 解析失败: {exc}", cause=exc) from exc
        for r in result:
            r["push"] = True
        return result


# --------------------------------------------------------------------------- #
# 0x06B9 服务器文件分块读取                        ⚠️ 待样本锁定
# --------------------------------------------------------------------------- #
@register_parser(0x06B9, family=Family.STANDARD, name="FILE_DOWNLOAD", head=0, tier="L2")
class FileDownloadParser(BaseParser):
    """文件分块读取响应：``<I total_len>`` + 文件字节片段。

    ⚠️ 头部布局存在「uint32 总长」与「双 uint16（total + packet_len）」两种
    公开资料口径，待 golden 样本锁定；当前按 uint32 总长实现。深审 M27：
    旧实现 ``uint32() if remaining >= 4 else uint16()`` 的 else 分支因入口
    长度检查（:325）永不可达，属死代码，已清除。
    """

    def parse_payload(self, reader, **ctx: Any) -> list[dict[str, Any]]:
        if reader.remaining < 4:
            raise ParseError("0x06B9 响应过短", context={"remaining": reader.remaining})
        total_len = reader.uint32()
        # 剩余即本包文件字节
        data = reader.rest()
        return [{"total_len": total_len, "data_len": len(data), "data": data}]


# --------------------------------------------------------------------------- #
# 0x07E5 板块行情                                  ⚠️ 待样本锁定
# --------------------------------------------------------------------------- #
@register_parser(0x07E5, family=Family.STANDARD, name="BLOCK_QUOTES", head=2, tier="L2")
class BlockQuotesParser(BaseParser):
    """板块行情：``<H count>`` + 每只需 ``<6s code><f4 涨跌幅>...``。

    ⚠️ 字段序列待样本锁定，这里按「代码 + 涨跌幅 + 现价 + 成交量」推断。
    """

    RECORD_SIZE = 22

    def parse_payload(self, reader, **ctx: Any) -> list[dict[str, Any]]:
        count = self.guarded_count(reader, ctx, self.RECORD_SIZE)
        rows: list[dict[str, Any]] = []
        for _ in range(count):
            if reader.remaining < self.RECORD_SIZE:
                break
            code = decode_gbk(reader.bytes(6))
            pct = reader.float32()
            price = reader.float32()
            volume = reader.tdx_float()
            amount = reader.tdx_float()
            rows.append(
                {
                    "code": code,
                    "pct_change": round(pct, 4),
                    "price": round(price, 4),
                    "volume": int(volume),
                    "amount": round(float(amount), 4),
                }
            )
        return rows


# --------------------------------------------------------------------------- #
# 0x051A 量价分布（筹码分布）                      ⚠️ 待样本锁定
# --------------------------------------------------------------------------- #
@register_parser(0x051A, family=Family.STANDARD, name="VOLUME_PRICE_DIST", head=2, tier="L2")
class VolumePriceDistParser(BaseParser):
    """量价分布 / 筹码分布。

    ⚠️ 响应为「价格档位 + 该档累计成交量」序列，具体分箱数待样本锁定。
    这里按 ``count`` 个 ``<f4 price><tdx_float volume>`` 对解析。
    """

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


# --------------------------------------------------------------------------- #
# 0x056A 集合竞价过程快照                          ⚠️ 待样本锁定
# --------------------------------------------------------------------------- #
@register_parser(0x056A, family=Family.STANDARD, name="AUCTION_SNAPSHOT", head=2, tier="L2")
class AuctionSnapshotParser(BaseParser):
    """集合竞价过程快照。

    ⚠️ 响应为竞价时段逐笔撮合：``<H count>`` + 每只需 ``<H 秒><f4 价格><tdx_float 成交量>``。
    """

    RECORD_SIZE = 10

    def parse_payload(self, reader, **ctx: Any) -> list[dict[str, Any]]:
        # 深审 M28：价格为 float32（无需折算），price_scale 读取为死代码
        count = self.guarded_count(reader, ctx, self.RECORD_SIZE)
        rows: list[dict[str, Any]] = []
        for _ in range(count):
            if reader.remaining < self.RECORD_SIZE:
                break
            second = reader.uint16()
            price = reader.float32()
            volume = reader.tdx_float()
            rows.append(
                {
                    "second": second,
                    "price": round(price, 4),
                    "volume": int(volume),
                }
            )
        return rows


# --------------------------------------------------------------------------- #
# 0x051C 指数动量                                  ⚠️ 待样本锁定
# --------------------------------------------------------------------------- #
@register_parser(0x051C, family=Family.STANDARD, name="INDEX_MOMENTUM", head=2, tier="L2")
class IndexMomentumParser(BaseParser):
    """指数动量（领涨/领跌成分）。"""

    RECORD_SIZE = 12

    def parse_payload(self, reader, **ctx: Any) -> list[dict[str, Any]]:
        count = self.guarded_count(reader, ctx, self.RECORD_SIZE)
        rows: list[dict[str, Any]] = []
        for _ in range(count):
            if reader.remaining < self.RECORD_SIZE:
                break
            code = decode_gbk(reader.bytes(6))
            pct = reader.float32()
            _unk = reader.uint16()
            rows.append({"code": code, "pct_change": round(pct, 4)})
        return rows
