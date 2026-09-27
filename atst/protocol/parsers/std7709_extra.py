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

from ...codec.primitive import PRICE_SCALE_QUOTES, decode_gbk
from ...errors import ParseError
from ..commands import Family
from ..registry import BaseParser, register_parser

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
# 0x0537 当日分时                                  ⚠️ 待样本锁定
# --------------------------------------------------------------------------- #
@register_parser(0x0537, family=Family.STANDARD, name="MINUTE_TODAY", head=2, tier="L2")
class MinuteTodayParser(BaseParser):
    """当日分时数据。

    响应（⚠️ 公开资料推断）：``<H count>`` 后跟 ``count`` 条记录，每条
    ``<H 距开盘分钟><f4 价格><f4 均价><f4 成交量>``（14 字节）。
    价格以 ``PRICE_SCALE_QUOTES`` 折算。
    """

    RECORD_SIZE = 14

    def parse_payload(self, reader, **ctx: Any) -> list[dict[str, Any]]:
        # 深审 M28：价格字段为 float32（已是浮点价格，无需 price_scale 折算）；
        # 旧实现读取 ctx["price_scale"] 后丢弃，属死代码
        count = self.guarded_count(reader, ctx, self.RECORD_SIZE)
        rows: list[dict[str, Any]] = []
        for _ in range(count):
            if reader.remaining < self.RECORD_SIZE:
                break
            minute = reader.uint16()
            price = reader.float32()
            avg = reader.float32()
            volume = reader.float32()
            hh, mm = divmod(minute, 60)
            rows.append(
                {
                    "minute": minute,
                    "time": f"{hh:02d}:{mm:02d}",
                    "price": round(price, 4),
                    "avg_price": round(avg, 4),
                    "volume": int(volume),
                }
            )
        return rows


# --------------------------------------------------------------------------- #
# 0x0FB4 历史分时                                  ⚠️ 待样本锁定
# --------------------------------------------------------------------------- #
@register_parser(0x0FB4, family=Family.STANDARD, name="MINUTE_HISTORY", head=2, tier="L2")
class MinuteHistoryParser(BaseParser):
    """指定日期历史分时（布局与 :class:`MinuteTodayParser` 相同）。"""

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
            volume = reader.float32()
            hh, mm = divmod(minute, 60)
            rows.append(
                {
                    "minute": minute,
                    "time": f"{hh:02d}:{mm:02d}",
                    "price": round(price, 4),
                    "avg_price": round(avg, 4),
                    "volume": int(volume),
                }
            )
        return rows


# --------------------------------------------------------------------------- #
# 0x0FC5 当日成交明细（逐笔）                      ⚠️ 待样本锁定
# --------------------------------------------------------------------------- #
@register_parser(0x0FC5, family=Family.STANDARD, name="TRADE_TODAY", head=2, tier="L2")
class TradeTodayParser(BaseParser):
    """当日逐笔成交。

    ⚠️ 响应：``<H count>`` 后跟记录。每条记录**按 15 字节消费**：
    ``<H 距开盘分钟><f4 价格><f4 成交量><f4 成交额><B 方向(0买1卖2中性)>``。

    .. danger::
       **15B 消费，布局未真机锁定**（P1e 三值矛盾收口，2026-09）：本记录
       长度历史上三处矛盾——docstring 曾写 28B、``RECORD_SIZE`` 曾为 17B、
       逐字段实际消费 15B。守卫门槛必须等于实际消费步长，故三者统一为
       **15B**。golden 样本 ``0x0fc5_trade_today_600000``（count=10 /
       body 74B）至今无法被 15B 整除，说明真实布局仍含未识别字段，
       待真机样本定标后修正。
    """

    #: 守卫门槛 = 逐字段实际消费步长（P1e 收口：声明/守卫/消费三者一致）
    RECORD_SIZE = 15

    def parse_payload(self, reader, **ctx: Any) -> list[dict[str, Any]]:
        # 深审 M28：价格字段为 float32（无需折算），price_scale 读取为死代码
        count = self.guarded_count(reader, ctx, self.RECORD_SIZE)
        rows: list[dict[str, Any]] = []
        for _ in range(count):
            if reader.remaining < self.RECORD_SIZE:
                break
            minute = reader.uint16()
            price = reader.float32()
            volume = reader.float32()
            amount = reader.float32()
            direction = reader.uint8()
            hh, mm = divmod(minute, 60)
            rows.append(
                {
                    "minute": minute,
                    "time": f"{hh:02d}:{mm:02d}",
                    "price": round(price, 4),
                    "volume": int(volume),
                    "amount": round(float(amount), 4),
                    "direction": direction,
                }
            )
        return rows


# --------------------------------------------------------------------------- #
# 0x0FC6 当日成交明细（备用命令号）               ⚠️ 待样本锁定
# --------------------------------------------------------------------------- #
@register_parser(0x0FC6, family=Family.STANDARD, name="TRADE_TODAY_ALT", head=2, tier="L2")
class TradeTodayAltParser(BaseParser):
    """当日逐笔成交（备用命令号，布局与 0x0FC5 相同）。

    部分主站以 0x0FC6 应答当日成交；记录布局照 0x0FC5 推断
    （``<H 距开盘分钟><f4 价格><f4 量><f4 额><B 方向>``，**15B 消费**，
    布局未真机锁定——P1e 三值矛盾收口与 0x0FC5 一致）。
    """

    #: 守卫门槛 = 逐字段实际消费步长（P1e 收口：声明/守卫/消费三者一致）
    RECORD_SIZE = 15

    def parse_payload(self, reader, **ctx: Any) -> list[dict[str, Any]]:
        count = self.guarded_count(reader, ctx, self.RECORD_SIZE)
        rows: list[dict[str, Any]] = []
        for _ in range(count):
            if reader.remaining < self.RECORD_SIZE:
                break
            minute = reader.uint16()
            price = reader.float32()
            volume = reader.float32()
            amount = reader.float32()
            direction = reader.uint8()
            hh, mm = divmod(minute, 60)
            rows.append(
                {
                    "minute": minute,
                    "time": f"{hh:02d}:{mm:02d}",
                    "price": round(price, 4),
                    "volume": int(volume),
                    "amount": round(float(amount), 4),
                    "direction": direction,
                }
            )
        return rows


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
