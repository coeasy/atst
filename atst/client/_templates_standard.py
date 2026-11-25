# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""标准族协议模板（P2-2：自 ``_mixin.py`` 拆出的模板半边之一）。

``TdxClient`` / ``AsyncTdxClient`` 共用的标准族 ``_t_*`` 模板本体：0x052D K 线
（自动分页 + 截断语义，分页状态机见模块尾部的显式状态对象与纯函数）、
0x0530 逐只行情（坏标的隔离）、元数据类（0x044E / 0x000F / 0x0010 / 0x0537）、
通用命令入口、已下线命令的保留面（0x044D / 0x0FB4 / 0x07E5 / 0x056A / 0x051A）、
0x06B9 文件分块下载、0x054C 批量快照（offline 放行 + 逐只回退）与便捷快照。

每个模板是**同步生成器**：凡原 ``await`` / ``self._req`` / 调用其他公开方法的
位置以 ``yield _op_req(...)`` / ``yield _op_call(...)`` 挂起，由
:mod:`atst.client._trampoline` 的驱动器逐条执行；机制总述与 fail-closed
校验约束见 :mod:`atst.client._mixin` 顶层 docstring。
"""

from __future__ import annotations

import logging
import struct
from collections.abc import Generator, Mapping
from dataclasses import dataclass, field
from typing import Any

import atst.client as _client_pkg  # 包级符号经此转发（见 sync.py 说明）

from ..diagnostics import WarningCode, record_warning
from ..domain.finance import FINANCE_INFO_FIELDS, map_finance_values
from ..domain.integrity import row_violations
from ..domain.models import Bar, CapitalChange, Quote
from ..errors import (
    IntegrityViolation,
    ParseError,
    TdxError,
    TruncatedDataError,
    ValidationError,
)
from ..protocol.commands import CMD, Family
from ..protocol.parsers.std7709 import SecurityBarsParser, build_realtime_quote_body
from ..protocol.registry import TIER_L3
from ._trampoline import (
    _TPL_WARN_STACKLEVEL,
    OutputFormat,
    _op_call,
    _op_req,
    _TrampolineMixin,
    _TrampolineOp,
)
from .core import (  # B1：共享核心（纯协议构造，无 I/O）
    _emit,
    _encode_gbk_field,
    _normalize_symbols,
    _require_bool,
    _require_int,
    _require_output_format,
    _require_yyyymmdd,
    _row_to_bar,
    _row_to_capital,
    _row_to_quote,
    _standard_market_id,
    period_to_category,
    split_symbol,
)

#: ``quotes_snapshot`` 的 0x054C 单包标的数上限。历史实现一次打包全部标的，
#: ``>255`` 直接 struct.error 且绕过逐只回退设计；80 为保守分片值。
_QUOTES_SNAPSHOT_BATCH = 80

#: 0x052D 服务端单请求 K 线上限（v5 PG1：原 ``SecurityBarsParser`` 类常量
#: 是死代码，无任何调用方——现由 client 侧 ``bars()`` 真正消费，作为分页
#: 页大小。保持解析器常量为单一事实源，此处仅引用）。
MAX_BARS_PER_REQUEST = SecurityBarsParser.MAX_BARS_PER_REQUEST

logger = logging.getLogger("atst.client")


class _StandardFamilyTemplates(_TrampolineMixin):
    """标准族 ``_t_*`` 模板（P2-2 自 ``_ClientMixin`` 拆出；方法体逐字沿用）。

    继承 :class:`_TrampolineMixin` 只为让 mypy 在单类上下文里校验 ``self.timeout``
    / ``self._forward`` 等机制成员；运行期组合与既有 import 见 :mod:`atst.client._mixin`。
    """

    # ------------------------------------------------------------------ #
    # K 线 / 分钟线
    # ------------------------------------------------------------------ #
    def _t_bars(
        self,
        symbol: str,
        *,
        period: str,
        count: int,
        start: int,
        market: int | None,
        index: bool | None,
        as_format: OutputFormat,
        strict: bool,
    ) -> Any:
        """K 线 / 分钟线（命令 ``0x052D``，自动分页 + 截断语义）。

        空桩首页与锚点漂移截断的判据就在本模板里：瑕疵逐条发 ``UserWarning``（经
        ``Client`` 内核时同时进 ``ResultMeta.warnings``），``strict=True`` 时同一条判据
        直接抛 :class:`TruncatedDataError` 而不是返回后靠调用方自查。

        ``index`` 传 ``None`` 时按代码段自动推断（:func:`atst.universe.classify_with_index`）。
        这不是可选的便利：指数 K 线每条记录尾部多 4 字节涨跌家数，这个位走反了**整条记录
        流**就错位——日期变成 ``5243-00-00``、成交量变成 1e40，且第 1 条日期往往还对得上，
        比整页全错更难发现。2026-10-13 实测：本模板曾把缺省写死 ``False``，于是
        ``TdxClient.bars("sh000001")`` 直连调用必然错位（门面 :meth:`Client.bars` 与执行器
        已经各自推断过一遍，唯独这一层没接上）。

        P2-2：分页状态机提为显式状态对象（:class:`_BarsPageCursor`）与纯判定
        函数（:func:`_absorb_bars_page`、:func:`_bars_truncation_diagnosis`，见模块
        尾部）；本模板只保留发包/解码 I/O 与 strict 抛错/告警的分叉。
        """
        _require_output_format(as_format)
        if index is None:
            from ..universe import classify_with_index

            index = classify_with_index(symbol)[1]
        _require_bool("index", index)
        strict_mode = _require_bool("strict", strict)
        category = period_to_category(period)
        mkt, code = split_symbol(symbol)
        if market is not None:
            mkt = _standard_market_id(market)

        remaining = _require_int("count", count, minimum=0, maximum=0xFFFF)
        offset = _require_int("start", start, minimum=0, maximum=0xFFFF)
        if offset + remaining > 0x10000:
            raise ValidationError(
                f"start + count 超出 16-bit 分页地址空间: {offset} + {remaining}",
                context={"start": offset, "count": remaining},
            )

        state = _BarsPageCursor(offset=offset, remaining=remaining)
        while state.remaining > 0:
            page = min(state.remaining, MAX_BARS_PER_REQUEST)
            body = struct.pack(
                "<H6sHHHHIIH",
                mkt,
                code.encode("ascii")[:6].ljust(6, b"\x00"),
                category,
                1,
                state.offset,
                page,
                0,
                0,
                0,
            )
            frame = yield _op_req(CMD["security_bars"], body, timeout=self.timeout)
            #: 逐页的解码判断（"声明 N 实收 M"）必须上 wire，转发口见
            #: :func:`_forward_decode_caveats`（F-51 的原始症状就长在这里）。
            result = self._forward(
                _client_pkg.dispatch(frame, category=category, family=self.family, index=index),
                f"bars({symbol!r}) 分页解码",
            )
            if _absorb_bars_page(state, result.rows, page, meta=result.meta) is not None:
                break

        if (diagnosis := _bars_truncation_diagnosis(state, symbol, period, count)) is not None:
            warn_code, msg, context = diagnosis
            if strict_mode:
                raise TruncatedDataError(msg, context=context)
            record_warning(warn_code, msg, stacklevel=_TPL_WARN_STACKLEVEL)
        #: 出门前统一按时间**升序**：分页拼出来的原始序列是"锯齿"的——页内升序、
        #: 页间倒退（每页都比上一页旧），所以 ``bars[0]`` 并非最早、``bars[-1]`` 并非
        #: 最新。（线路实测 sz000001/16000 根：``bars[0]``=2023-06-15 而 ``bars[-1]``
        #: =1993-02-16，两端都不是极值。）回归测试用的 ``_PagePool`` 恰好是页内降序，
        #: 拼出来整体单调，把这个形状遮住了——所以此前既无判据也无文档。
        #: 升序后契约唯一且可用：``bars[0]`` = 最早一根，``bars[-1]`` = 最新一根。
        #: 稳定性保证同一时间戳的相对次序不变；``_emit`` 对 rows/tuple/dataframe
        #: 三种出口都保留顺序。
        state.bars.sort(key=lambda bar: bar.datetime)
        return _emit(state.bars, as_format)

    # ------------------------------------------------------------------ #
    # 实时行情（逐只 0x0530）
    # ------------------------------------------------------------------ #
    def _t_quotes(
        self,
        symbols: Any,
        *,
        as_format: OutputFormat,
        strict: bool = False,
        _collect: list[tuple[str, BaseException]] | None,
    ) -> Any:
        """实时行情快照（命令 ``0x0530``，逐只请求；坏标的隔离见 sync 壳）。

        ``strict=True`` 时，解析器自带的完整性判据（哨兵失配 / 成交额越界，
        落在 ``extra['_warnings']``）当场把这一只判为失败，而不是返回一份
        "帧合法、值可疑"的数据让调用方自己猜——实测不存在代码 ``sh999999``
        的主站响应就长这样：回声匹配、载荷齐全、量能荒谬，静默成功的形状
        与真行情一模一样。
        """
        _require_output_format(as_format)
        normalized_symbols = _normalize_symbols(symbols)
        out: list[Quote] = []
        errors: list[tuple[str, BaseException]] = _collect if _collect is not None else []
        for sym in normalized_symbols:
            # 坏标的隔离：符号解析/请求体构造失败只淘汰这一只（此前在
            # try 之外，一只坏标的会中断整批）
            try:
                mkt, code = split_symbol(sym)
                body = build_realtime_quote_body(code, mkt)
            except TdxError as exc:
                errors.append((sym, exc))
                continue
            try:
                frame = yield _op_req(CMD["realtime_quote"], body, timeout=self.timeout)
            except TdxError as exc:
                errors.append((sym, exc))
                continue
            result = self._forward(
                _client_pkg.dispatch(
                    frame, code=code, market=mkt, price_scale=100, family=self.family
                ),
                f"quotes({sym!r}) {CMD['realtime_quote']:#06x} 解码",
            )
            if not result.rows:
                errors.append((sym, ParseError(f"0x0530 无解析结果: {result.warnings}")))
                continue
            row = result.rows[0]
            if strict:
                drift = list((row.get("extra") or {}).get("_warnings") or [])
                if drift:
                    errors.append(
                        (
                            sym,
                            IntegrityViolation(
                                f"quotes({sym!r}) strict：0x0530 自校验发现字段漂移，拒绝返回可疑行情。"
                                + "；".join(drift),
                                context={"symbol": sym, "drift": drift, "strict": True},
                            ),
                        )
                    )
                    continue
            out.append(_row_to_quote(row))
        if _collect is None:
            self._set_last_errors(errors)
        return _emit(out, as_format)

    # ------------------------------------------------------------------ #
    # 元数据类
    # ------------------------------------------------------------------ #
    def _t_security_count(self, market: Any) -> Generator[_TrampolineOp, Any, int]:
        """某市场的证券总数（命令 ``0x044E``）。"""
        market_id = _standard_market_id(market)
        body = struct.pack("<H", market_id) + b"\x00" * 4
        frame = yield _op_req(CMD["security_count"], body, timeout=self.timeout)
        result = self._forward(
            _client_pkg.dispatch(frame, family=self.family),
            f"security_count(market={market_id}) {CMD['security_count']:#06x} 解码",
        )
        if not result.rows:
            raise ParseError("0x044E 无解析结果", context={"market": market_id})
        return int(result.rows[0].get("count", 0))

    def _t_capital_changes(self, symbol: str) -> Generator[_TrampolineOp, Any, list[CapitalChange]]:
        """除权除息 / 股本变迁（命令 ``0x000F``）。

        请求体（9 字节，✅ 2026-10-21 实测修正）::

            <H 1><B market><6s code>

        即「固定前缀 ``01 00`` + 市场字节 + 6 字节代码」。**顺序反过来越界**：
        2026-10-21 实测发现旧实现发的是 ``<6s code><H market>``（代码在前），
        服务端按 ``market@2 / code@3:9`` 解读后整个记录头错位——解出的
        ``market`` 恒为 ASCII ``'0'``（48）、``code`` 只剩半个代码，且不带任何
        解码告警（F-37 的历史结论正来源于此：四份旧样本全是错请求采的）。
        修正请求顺序后，深/沪多只标的重放，响应头 ``market(1)+code(6)`` 与请求
        逐一相符，事件日期/类别全部落回合理域。

        响应结构::

            <H 1><B market><6s code><H num><num × 29B 记录>

        每条 29 字节记录::

            <B market><6s code><B 0><I date(YYYYMMDD)><B category><16B 分类数据>

        .. warning::
           ⚠️ **字段口径仍未由本仓 golden 锁定（F-37）**：记录布局已按公开参考
           实现（pytdx / injoyai-tdx）与 2026-10-21 真机交叉验证，日期/类别/四元组
           均已对上业务常识，但仓内 golden 样本仍是「错请求」年代采的，未重采前
           字段语义**不保证**（条数与结构可用）。``category`` 语义表见
           :attr:`CapitalChangesParser.CATEGORY`。
        """
        mkt, code = split_symbol(symbol)
        code6 = code.encode("ascii")[:6].ljust(6, b"\x00")
        body = struct.pack("<HB6s", 1, mkt, code6)
        frame = yield _op_req(CMD["capital_changes"], body, timeout=self.timeout)
        result = self._forward(
            _client_pkg.dispatch(frame, code=code, market=mkt, family=self.family),
            f"capital_changes({symbol!r}) {CMD['capital_changes']:#06x} 解码",
        )
        return [_row_to_capital(row) for row in result.rows]

    def _t_finance_info(self, symbol: str) -> Generator[_TrampolineOp, Any, dict[str, Any]]:
        """财务基础信息（命令 ``0x0010``）。

        请求体（9 字节，✅ 2026-10-21 实测修正）::

            <H 1><B market><6s code>

        即「固定前缀 ``01 00`` + 市场字节 + 6 字节代码」。**顺序反过就越界**：
        旧实现发 ``<6s code><H market>``（代码在前），服务端按 ``market@2 /
        code@3:9`` 解读后记录头整体错位——``market`` 读到 ASCII ``'0'``（48）、
        ``code`` 只剩半个代码；前 2 字节被当成「请求条数」读成 0x3030，服务器
        于是回 100 条记录（只有第 1 条是本人，其余 99 条是服务端缓冲残留）。
        修正后请求 1 条即回 1 条干净记录（143 字节），响应头与请求逐一相符。

        响应结构::

            <H 1><B market><6s code>
            <f 流通股本><H 省份><H 行业><I 更新日期><I 上市日期>
            <30 × f 总股本…保留2>

        .. warning::
           ⚠️ **字段序按公开参考实现（pytdx / injoyai-tdx）交叉锁定，但本仓 golden
           未重采（F-37）**：2026-10-21 深/沪 8 只标的重放，总股本/流通股本/上市
           日期均与公开财务数据吻合；在重采 golden 前，字段语义**不保证**，
           条数、记录头与结构可用。
        """
        mkt, code = split_symbol(symbol)
        code6 = code.encode("ascii")[:6].ljust(6, b"\x00")
        body = struct.pack("<HB6s", 1, mkt, code6)
        frame = yield _op_req(CMD["finance_info"], body, timeout=self.timeout)
        result = self._forward(
            _client_pkg.dispatch(frame, code=code, market=mkt, family=self.family),
            f"finance_info({symbol!r}) {CMD['finance_info']:#06x} 解码",
        )
        if not result.rows:
            return {}
        row = dict(result.rows[0])
        values = list(row.get("values", []) or [])
        out: dict[str, Any] = {"market": row.get("market", mkt), "code": row.get("code", code)}
        # 类型化记录头（解析器已命名，非 values 尾部数组）
        for key in ("float_shares", "province", "industry", "updated_date", "ipo_date"):
            if row.get(key) is not None:
                out[key] = row[key]
        out.update(map_finance_values(values, FINANCE_INFO_FIELDS))
        out["values"] = values
        return out

    def _t_minute_today(self, symbol: str) -> Generator[_TrampolineOp, Any, list[Any]]:
        """当日分时数据（命令 ``0x0537``，✅ L1 / verified）。

        请求体（12 字节，✅ 双市场 golden 锁定）::

            <H market><6s code><I 0>

        **市场在前、代码在后**——与 ``0x0010``/``0x000F`` 同一个坑：历史上本方法发
        的是 ``<6s code><H market><4 zeros>``（代码在前），服务端按
        ``market@0 / code@2:8`` 解读后**沪市（market=1）直接回空桩**（4 字节
        ``00 00 00 00``，声明 0 条），深市因为市场字节恰好落在代码位上而"碰巧"
        有响应。修正后深/沪多只标的均回 240 条（满日）分时记录。

        布局与交叉验证判据见
        :class:`atst.protocol.parsers.std7709_extra.MinuteTodayParser`
        （价格/均价「基准 + 增量」，与同日 0x052D 一分钟 K 线 240/240 对齐）。
        """
        mkt, code = split_symbol(symbol)
        code6 = code.encode("ascii")[:6].ljust(6, b"\x00")
        body = struct.pack("<H6sI", mkt, code6, 0)
        frame = yield _op_req(CMD["minute_today"], body, timeout=self.timeout)
        result = self._forward(
            _client_pkg.dispatch(frame, code=code, market=mkt, family=self.family),
            f"minute_today({symbol!r}) {CMD['minute_today']:#06x} 解码",
        )
        return result.rows

    # ------------------------------------------------------------------ #
    # 通用命令入口
    # ------------------------------------------------------------------ #
    def _t_request(
        self,
        cmd: int,
        body: bytes,
        *,
        ctx: Any,
        as_format: OutputFormat,
    ) -> Any:
        """任意命令 → 解析行（``as_format`` 自 v6 起真正生效）。"""
        _require_output_format(as_format)
        result = yield _op_call("request_result", cmd, body, ctx=ctx)
        return _emit(result.rows, as_format)

    def _t_request_result(self, cmd: int, body: bytes, *, ctx: Any) -> Any:
        """任意命令 → 完整 :class:`~atst.protocol.registry.ParseResult`（v6 A2）。"""
        command = _require_int("cmd", cmd, minimum=0, maximum=0xFFFF)
        if not isinstance(body, bytes):
            raise ParseError(
                f"body 必须是 bytes，收到 {type(body).__name__}",
                context={"field": "body", "value_type": type(body).__name__},
            )
        if ctx is not None and not isinstance(ctx, Mapping):
            raise ParseError(
                f"ctx 必须是 Mapping 或 None，收到 {type(ctx).__name__}",
                context={"field": "ctx", "value_type": type(ctx).__name__},
            )
        frame = yield _op_req(command, body, timeout=self.timeout)
        #: 这里是十几个公共方法（security_list / trade_today / block_* / goods_* /
        #: ex_* / f10 catalog …）共用的分派口：接一次，十几条面同时看得见解码判断。
        return self._forward(
            _client_pkg.dispatch(frame, family=self.family, **(dict(ctx) if ctx else {})),
            f"request({command:#06x}) 解码",
        )

    # ------------------------------------------------------------------ #
    # 更多标准命令
    # ------------------------------------------------------------------ #
    def _t_security_list(self, market: Any, start: int) -> Any:
        """代码表（命令 ``0x044D``，分页 1000/页）。

        **本方法已下线**：``0x044D`` 在命令账本登记为 ``STATUS_OFFLINE``（账本对该状态的
        定义即「多主站实测无响应」），请求在发包前由
        :func:`atst.client.core._guard_offline` fail-fast 抛 :class:`CommandOffline`。
        API 面保留是为了参数校正后接回，不表示它当前可用。
        """
        market_id = _standard_market_id(market)
        offset = _require_int("start", start, minimum=0, maximum=0xFFFF)
        body = struct.pack("<HH", market_id, offset)
        return (
            yield _op_call(
                "request",
                CMD["security_list"],
                body,
                ctx={"market": market_id, "start": offset},
            )
        )

    def _t_export_security_list(self, market: Any, *, max_pages: int) -> Any:
        """全市场代码表导出（E3：0x044D 分页遍历直至耗尽，截断告警）。

        跟 ``security_list`` 同源，**本方法同样已下线**：``0x044D`` 登记为 ``STATUS_OFFLINE``
        （多主站实测无响应），首页请求即由 ``_guard_offline`` 抛 :class:`CommandOffline`，
        走不到分页循环。
        """
        market_id = _standard_market_id(market)
        page_limit = _require_int("max_pages", max_pages, minimum=1)
        out: list[dict[str, Any]] = []
        start = 0
        truncated = True  # 空页/短页 break 时置 False；耗尽 max_pages 仍满页则为 True
        for _ in range(page_limit):
            rows = yield _op_call("security_list", market_id, start)
            if not rows:
                truncated = False
                if not out:
                    record_warning(
                        WarningCode.SECURITY_LIST_EMPTY_FIRST_PAGE,
                        f"export_security_list(market={market_id}) 首页即空响应：0x044D 在 "
                        f"start=0 就声明 0 条记录，导出为空。空首页不代表该市场没有证券，"
                        f"而是主站对这个命令只回空桩",
                        stacklevel=_TPL_WARN_STACKLEVEL,
                    )
                break
            out.extend(rows)
            if len(rows) < 1000:
                truncated = False
                break
            start += len(rows)
            if start > 0xFFFF:
                break  # 16-bit 分页地址空间耗尽
        if truncated:
            record_warning(
                WarningCode.SECURITY_LIST_PAGE_LIMIT,
                f"export_security_list(market={market_id}) 在 max_pages={page_limit} 页内"
                f"未取尽（已取 {len(out)} 条，最后一页仍为满页），结果可能截断",
                stacklevel=_TPL_WARN_STACKLEVEL,
            )
        return out

    def _t_minute_history(self, symbol: str, date: int) -> Any:
        """指定日期历史分时（命令 ``0x0FB4``）。``date`` 为 YYYYMMDD 整数。

        **本方法已下线**：``0x0FB4`` 登记为 ``STATUS_OFFLINE``（多主站实测无响应），
        发包前由 ``_guard_offline`` fail-fast 抛 :class:`CommandOffline`。
        """
        mkt, code = split_symbol(symbol)
        day = _require_yyyymmdd("date", date)
        body = code.encode("ascii")[:6].ljust(6, b"\x00") + struct.pack("<HI", mkt, day)
        frame = yield _op_req(CMD["minute_history"], body, timeout=self.timeout)
        result = self._forward(
            _client_pkg.dispatch(frame, code=code, market=mkt, family=self.family),
            f"minute_history({symbol!r}, date={day}) {CMD['minute_history']:#06x} 解码",
        )
        return result.rows

    def _t_trade_today(self, symbol: str, start: int, count: int) -> Any:
        """当日逐笔成交（命令 ``0x0FC5``，✅ L1 / verified）。

        请求体（12 字节，✅ 双市场 golden 锁定）::

            <H market><6s code><H start><H count>

        同样**市场在前**：旧实现 ``<6s code><H market><H start><H count>`` 让沪市
        回 2 字节空桩（声明 0 条），深市"碰巧"有响应。修正后深/沪均正常返回
        逐笔记录。``start`` 自最新一笔往回数（``0`` = 当日最后 ``count`` 笔），
        逐笔累加价末条与实时行情价一致；全日逐笔按分钟汇总与同日 0x052D
        一分钟 K 线逐桶对账 237/240（余下 3 桶为竞价并入，算术精确相符）。
        布局与判据见
        :class:`atst.protocol.parsers.std7709_extra.TradeTodayParser`。
        """
        mkt, code = split_symbol(symbol)
        offset = _require_int("start", start, minimum=0, maximum=0xFFFF)
        page_size = _require_int("count", count, minimum=0, maximum=0xFFFF)
        code6 = code.encode("ascii")[:6].ljust(6, b"\x00")
        body = struct.pack("<H6sHH", mkt, code6, offset, page_size)
        return (
            yield _op_call("request", CMD["trade_today"], body, ctx={"code": code, "market": mkt})
        )

    def _t_block_quotes(self, block_type: int, start: int) -> Any:
        """板块行情（命令 ``0x07E5``）。block_type: 0 概念 / 1 行业 / 2 地区 / 3 指数。

        **本方法已下线**：``0x07E5`` 登记为 ``STATUS_OFFLINE``（2026-09 三主站实测无响应），
        发包前由 ``_guard_offline`` fail-fast 抛 :class:`CommandOffline`；方法保留待参数校正。
        """
        kind = _require_int("block_type", block_type, minimum=0, maximum=3)
        offset = _require_int("start", start, minimum=0, maximum=0xFFFF)
        body = struct.pack("<HH", kind, offset)
        return (
            yield _op_call(
                "request", CMD["block_quotes"], body, ctx={"block_type": kind, "start": offset}
            )
        )

    def _t_file_download(
        self,
        symbol: str,
        filename: str,
        *,
        offset: int,
        length: int,
        max_packets: int,
        strict: bool,
    ) -> Any:
        """文件分块下载（命令 ``0x06B9``；全量/截断语义见 sync 壳 docstring）。"""
        mkt, code = split_symbol(symbol)
        start_offset = _require_int("offset", offset, minimum=0, maximum=0xFFFFFFFF)
        requested_length = _require_int("length", length, minimum=0, maximum=0xFFFFFFFF)
        packet_limit = _require_int("max_packets", max_packets, minimum=1)
        strict_mode = _require_bool("strict", strict)
        # 文件名必须在进入任何 I/O 路径前校验；_file_download_once 会重复校验，
        # 因为它同时是可直接调用的私有兼容 seam。
        _encode_gbk_field("filename", filename, max_bytes=80)

        if requested_length > 0:
            rows = yield _op_call(
                "_file_download_once",
                mkt,
                code,
                filename,
                start_offset,
                requested_length,
            )
            return b"".join(self._file_row_data(row) for row in rows)

        chunks = bytearray()
        total_len: int | None = None
        for _ in range(packet_limit):
            current_offset = start_offset + len(chunks)
            _require_int("offset", current_offset, minimum=0, maximum=0xFFFFFFFF)
            rows = yield _op_call("_file_download_once", mkt, code, filename, current_offset, 0)
            if not rows:
                break  # 服务端无更多数据
            raw_total = rows[0].get("total_len", total_len)
            if raw_total is not None:
                total_len = _require_int("total_len", raw_total, minimum=0, maximum=0xFFFFFFFF)
            data = self._file_row_data(rows[0])
            if not data:
                break  # 空包：文件已取尽
            chunks.extend(data)
            if total_len is not None and start_offset + len(chunks) >= total_len:
                break
        # for-else 不需要：max_packets 耗尽时由下方 total_len 校验兜底告警

        data = bytes(chunks)
        end_offset = start_offset + len(data)
        if total_len is not None and end_offset < total_len:
            msg = (
                f"file_download({symbol!r}, {filename!r}) 从 offset={start_offset} 累计"
                f" {len(data)} 字节，到达 {end_offset} < 服务端报告 total_len={total_len}，"
                "结果可能截断"
            )
            if strict_mode:
                raise TruncatedDataError(
                    msg,
                    context={
                        "symbol": symbol,
                        "filename": filename,
                        "returned": len(data),
                        "total_len": total_len,
                        "offset": start_offset,
                        "end_offset": end_offset,
                    },
                )
            record_warning(WarningCode.FILE_DOWNLOAD_SHORT, msg, stacklevel=_TPL_WARN_STACKLEVEL)
        return data

    @staticmethod
    def _file_row_data(row: Any) -> bytes:
        """校验并提取单行 ``file_download`` 响应的 ``data`` 字段。"""
        if not isinstance(row, dict):
            raise ParseError(
                f"file_download 响应行必须是 dict，收到 {type(row).__name__}",
                context={"row_type": type(row).__name__},
            )
        data = row.get("data", b"")
        if data is None:
            return b""
        if not isinstance(data, (bytes, bytearray, memoryview)):
            raise ParseError(
                f"file_download data 必须是 bytes-like，收到 {type(data).__name__}",
                context={"data_type": type(data).__name__},
            )
        return bytes(data)

    def _t_file_download_once(
        self, mkt: int, code: str, filename: str, offset: int, length: int
    ) -> Any:
        """0x06B9 单次请求（v5 PG2 拆出，供全量模式循环复用）。"""
        market_id = _standard_market_id(mkt)
        if not isinstance(code, str) or len(code) != 6 or not code.isascii() or not code.isdigit():
            raise ParseError(
                f"file_download code 必须是 6 位 ASCII 数字: {code!r}",
                context={"code": code},
            )
        fn = _encode_gbk_field("filename", filename, max_bytes=80)
        start_offset = _require_int("offset", offset, minimum=0, maximum=0xFFFFFFFF)
        requested_length = _require_int("length", length, minimum=0, maximum=0xFFFFFFFF)
        body = (
            code.encode("ascii")
            + struct.pack("<H", market_id)
            + fn
            + struct.pack("<II", start_offset, requested_length)
        )
        # 0x06B9 is shared by the standard transport and the F10 facade.  The
        # F10 family has no separate response parser for this command, so a
        # generic F10 request would silently discard the file chunk.  Parse it
        # through the canonical standard file-download parser for both clients.
        frame = yield _op_req(CMD["file_download"], body, timeout=self.timeout)
        result = self._forward(
            _client_pkg.dispatch(frame, family=Family.STANDARD),
            f"file_download({code!r}, {filename!r}) {CMD['file_download']:#06x} 单包解码",
        )
        return result.rows

    def _t_auction_snapshot(self, symbol: str) -> Any:
        """集合竞价过程快照（命令 ``0x056A``）。

        **本方法已下线**：``0x056A`` 登记为 ``STATUS_OFFLINE``（2026-09 三主站实测无响应），
        发包前由 ``_guard_offline`` fail-fast 抛 :class:`CommandOffline`；方法保留待参数校正。
        """
        mkt, code = split_symbol(symbol)
        body = code.encode("ascii")[:6].ljust(6, b"\x00") + struct.pack("<H", mkt) + b"\x00" * 4
        return (
            yield _op_call(
                "request", CMD["auction_snapshot"], body, ctx={"code": code, "market": mkt}
            )
        )

    def _t_volume_price_dist(self, symbol: str) -> Any:
        """量价分布 / 筹码分布（命令 ``0x051A``）。

        **本方法已下线**：``0x051A`` 登记为 ``STATUS_OFFLINE``（2026-09 三主站实测无响应），
        发包前由 ``_guard_offline`` fail-fast 抛 :class:`CommandOffline`；方法保留待参数校正。
        """
        mkt, code = split_symbol(symbol)
        body = code.encode("ascii")[:6].ljust(6, b"\x00") + struct.pack("<H", mkt) + b"\x00" * 4
        return (
            yield _op_call(
                "request", CMD["volume_price_dist"], body, ctx={"code": code, "market": mkt}
            )
        )

    def _t_quotes_snapshot(self, symbols: Any, *, try_offline_snapshot: bool = False) -> Any:
        """批量行情快照（默认直接逐只 ``0x0530``；显式 opt-in 才尝试已知下线的 0x054C）。

        ``0x054C`` 登记为 ``STATUS_OFFLINE``（多主站实测无响应），但它是账本里唯一被
        ``core._OFFLINE_FALLBACK_OK`` 放行的 offline 命令：**本方法仍可用**，真实数据
        来自逐只 ``0x0530`` 回退路径。过去每次调用都先发一次已知会失败的 0x054C，
        白耗一次往返。默认 ``try_offline_snapshot=False`` 跳过它、直接走回退；确需
        探测/验证该命令时显式传 ``True``，才保留旧的"先 0x054C，失败/空帧/L3 再
        回退逐只 0x0530"路径。

        Parameters
        ----------
        try_offline_snapshot:
            默认 ``False``。``True`` 时按旧行为先尝试 0x054C 再回退。
        """
        syms = _normalize_symbols(symbols)
        # 混合容器：0x054C 批量路径收 Quote 对象，0x0530 回退路径收
        # as_format="dict" 的 dict（to_dicts 对两者都产 dict，运行时一致）。
        out: list[Any] = []
        errors: list[tuple[str, BaseException]] = []
        for i in range(0, len(syms), _QUOTES_SNAPSHOT_BATCH):
            chunk = syms[i : i + _QUOTES_SNAPSHOT_BATCH]
            if not try_offline_snapshot:
                # T-5：0x054C 已知下线，默认直接走逐只 0x0530 回退，不发必失败的批量帧。
                out.extend((yield _op_call("quotes", chunk, as_format="dict", _collect=errors)))
                continue
            try:
                body = struct.pack("<B", len(chunk))
                for sym in chunk:
                    mkt, code = split_symbol(sym)
                    body += struct.pack("<H", mkt) + code.encode("ascii")[:6].ljust(6, b"\x00")
                frame = yield _op_req(CMD["quotes_snapshot"], body, timeout=self.timeout)
                result = self._forward(
                    _client_pkg.dispatch(frame, family=self.family, price_scale=100),
                    f"quotes_snapshot {CMD['quotes_snapshot']:#06x} 解码",
                )
            except TdxError as exc:
                # 批量失败（含主站不回 0x054C）：记日志 + 计数，本片回退逐只
                logger.warning("quotes_snapshot 0x054C 批量失败，本片回退逐只 0x0530: %s", exc)
                errors.append(("0x054C", exc))
                out.extend((yield _op_call("quotes", chunk, as_format="dict", _collect=errors)))
                continue
            if not result.rows:
                # 主站对 0x054C 回空帧（已下线的典型表现）→ 本片回退逐只
                out.extend((yield _op_call("quotes", chunk, as_format="dict", _collect=errors)))
                continue
            if result.tier == TIER_L3:
                # 深审 M17：L3 = 原始透传（解析器不认识该响应）——把垃圾行
                # 经 _row_to_quote 强转成 Quote 是静默脏数据（字段全 None/0）。
                # 与批量失败同待遇：回退逐只 0x0530。
                logger.warning("quotes_snapshot 0x054C 降级 L3 透传，本片回退逐只 0x0530")
                errors.append(("0x054C", TdxError("0x054C 响应仅 L3 透传")))
                out.extend((yield _op_call("quotes", chunk, as_format="dict", _collect=errors)))
                continue
            out.extend(_row_to_quote(row) for row in result.rows)
        self._set_last_errors(errors)
        return _emit(out, "dict")

    # ------------------------------------------------------------------ #
    # 便捷：单只完整快照
    # ------------------------------------------------------------------ #
    def _t_snapshot(self, symbol: str, *, as_format: OutputFormat) -> Any:
        """实时行情 + 当日日线，合成一份快照（表驱动降级在 router 层完成）。"""
        _require_output_format(as_format)
        #: 指数位要跟着符号走：``count=1`` 时单条记录的字段偏移本身没错（尾巴在最后），
        #: 所以这一条腿历史上"碰巧是对的"；但涨跌家数会被静默丢掉，而快照是给"这一只
        #: 现在什么状态"用的，缺了涨跌家数的指数快照信息不全。统一按段表推断，
        #: 与 :meth:`_t_bars` 的口径一致，不再依赖"只取一根所以没错位"这个巧合。
        from ..universe import classify_with_index

        quote = yield _op_call("quotes", [symbol], as_format="dict")
        q = quote[0] if quote else None
        day = yield _op_call(
            "bars",
            symbol,
            period="day",
            count=1,
            as_format="dict",
            index=classify_with_index(symbol)[1],
        )
        d = day[0] if day else None
        snap = {
            "code": symbol,
            "quote": q,
            "prev_close": d,
        }
        if as_format == "dataframe":
            from ..domain.models import to_dataframe

            return to_dataframe([snap])
        return snap


# --------------------------------------------------------------------------- #
# _t_bars 分页状态机（P2-2：显式状态对象 + 纯判定函数；判据与文案逐字沿用拆分前实现）
# --------------------------------------------------------------------------- #


@dataclass
class _BarsPageCursor:
    """``_t_bars`` 分页状态机的显式状态（P2-2：原 while 循环上的散装 flag 收拢）。

    ``offset`` / ``remaining`` 是分页光标；``seen`` 是去重键（datetime 字符串）；
    ``bars`` 是已吸收的 K 线；三个判据位与 ``first_page_declared`` 承担
    「为什么停」的记账；``page_rows`` / ``offenders`` 只在越域终止那一页填写
    （终局文案要引用那一页的形状）。
    """

    offset: int
    remaining: int
    seen: set[str] = field(default_factory=set)
    bars: list[Bar] = field(default_factory=list)
    drifted: bool = False  # 整页去重后零新增（锚点漂移），非正常历史耗尽
    empty_first_page: bool = False  # 第一页就 0 条：不是耗尽，是空桩/无该标的数据
    unusable_page: bool = False  # 整页记录全部越域：不是脏数据，是这一页根本不属于该标的
    first_page_declared: int | None = None  # 首页服务端声明的记录数，用于分家二者
    page_rows: int = 0  # 终局那一页的原始行数（越域文案引用）
    offenders: int = 0  # 终局那一页的越域行数


def _absorb_bars_page(
    state: _BarsPageCursor,
    raw_rows: list[dict[str, Any]],
    page: int,
    *,
    meta: Mapping[str, Any],
) -> str | None:
    """吸收一页解码结果并给出分页判定（纯函数，P2-2 自 ``_t_bars`` while 循环提出）。

    无 I/O、无告警、无异常：输入一页原始行与本页请求大小，把结果记进 ``state``
    并返回终局原因（``None`` = 继续分页）。四条判据与光标算术**逐字**沿用拆分
    前实现，顺序即语义：越域 → 空页 → 漂移 → 吸收 → 短页 → 推进。
    """
    offenders = sum(1 for row in raw_rows if row_violations(row))
    if raw_rows and offenders * 2 > len(raw_rows):
        #: **超过半数**越域：这一页不是该标的的记录，不是"有几根可疑"。门槛取
        #: 严格多数而不是"全部"，是因为真实错位页的头一根常常恰好还对得上——
        #: 实测 ``sh999999`` 请求 5 根回 5 根，第 1 根干净、后 4 根日期是
        #: ``8047-92-78`` / ``334231-62-94``（指数位走反时也是"第 1 根对、其后
        #: 整体错位"这同一个形状）。按"全部越域"判，这一页会漏网；按"有一根越域"
        #: 判，又会把局部瑕疵整页连坐。
        #:
        #: 丢弃的理由：两种真实形状（代码不存在时主站回缓冲区里别的标的、记录
        #: 布局错位）产出的行都不可用，而"空 + 一条专门告警"至少说的是实话；
        #: 把乱码交给调用方，下游会把它当真。
        state.unusable_page = True
        state.page_rows = len(raw_rows)
        state.offenders = offenders
        return "unusable_page"
    if not raw_rows:
        # 次页空 = 历史耗尽（正常终止，真实耗尽只会表现为短页或空次页）；
        # 首页空要按服务端**声明的记录数**分家：声明 0 才是"没有这段历史"，
        # 声明 N 却回 0 个记录字节是空桩（实测主站对 0x052D 声明 800 条）。
        if not state.seen:
            state.empty_first_page = True
            state.first_page_declared = meta.get("declared_count")
        return "exhausted_empty"
    fresh = [row for row in raw_rows if str(row.get("datetime")) not in state.seen]
    state.seen.update(str(row.get("datetime")) for row in raw_rows)
    if not fresh:
        state.drifted = True
        return "drifted"  # 整页重复：盘中锚点漂移，防死循环
    state.bars.extend(_row_to_bar(row) for row in fresh)
    if len(raw_rows) < page:
        return "exhausted_short"  # 短页：历史耗尽（正常终止）
    #: 光标必须按**整页实际返回数**推进，不能按去重后的 ``len(fresh)``。
    #: 服务端返回的是「从 ``offset`` 起的连续块」，因此下一未读位置恒为
    #: ``offset + len(raw_rows)``；重复行只说明锚点漂了，并不改变这条位置算术。
    #:
    #: 反面读法（别再按这个"修"回去）：按 ``len(fresh)`` 推进会少走，
    #: 让光标**倒退重读**——某页漂移 10 行时，下一请求会落在尚未读走的位置
    #: 之前，整页命中已见行 → ``fresh == 0`` → 被误判成漂移提前 break，
    #: 本可取到的尾部反而丢了。"多发一页"只是浪费，不等于"更安全"。
    #:
    #: 终止性：``remaining`` 每轮按 ``len(fresh)`` 严格递减（≥1），叠加 3 条
    #: break 判据，不存在死循环。
    state.offset += len(raw_rows)
    state.remaining -= len(fresh)
    return None


def _bars_truncation_diagnosis(
    state: _BarsPageCursor, symbol: str, period: str, count: int
) -> tuple[WarningCode, str, dict[str, Any]] | None:
    """分页终局诊断（纯函数，P2-2 自 ``_t_bars`` 提出，同一句话的单一出口）。

    返回 ``(告警码, 消息, context)``；``None`` = 取满、无话可说。strict 抛
    :class:`TruncatedDataError` 与 ``record_warning`` 两条出口共用这里产出的
    同一句消息与 context，与拆分前两条出口的文案逐字一致。
    """
    if len(state.bars) < count and state.drifted:
        msg = (
            f"bars({symbol!r}, period={period!r}, count={count}) 分页因盘中"
            f"锚点漂移提前终止：实取 {len(state.bars)} 根 < 请求 {count} 根"
        )
        return (
            WarningCode.BARS_ANCHOR_DRIFT,
            msg,
            {"symbol": symbol, "returned": len(state.bars), "requested": count},
        )
    if state.empty_first_page:
        # 空首页此前与"历史耗尽"共用一个 break，读起来就是"请求成功、恰好 0 根"；
        # 判据分家靠服务端声明数，而不是替它编一个数字（F-60）。
        if state.first_page_declared:
            cause = (
                f"服务端声明 {state.first_page_declared} 条记录，却一个记录字节都没回——"
                f"这是空桩，不是该标的没有历史（真没有历史时声明数就是 0）"
            )
        elif state.first_page_declared == 0:
            cause = "服务端声明 0 条记录：该标的无此周期历史，或主站对这个命令只回空桩"
        else:
            cause = "解析器没在响应里读到记录数头，声明数未知"
        msg = (
            f"bars({symbol!r}, period={period!r}, count={count}) 首页即空响应："
            f"{cause}，实取 0 根。空首页不是历史耗尽（耗尽只会表现为短页）"
        )
        return (
            WarningCode.BARS_EMPTY_FIRST_PAGE,
            msg,
            {
                "symbol": symbol,
                "returned": 0,
                "requested": count,
                "declared": state.first_page_declared,
            },
        )
    if state.unusable_page:
        #: 与"空首页"分开报：空首页是服务端一个字节都没给，这里是给了一整页却不属于
        #: 这个标的。两者的处置相同（都没有可用数据），但根因不同——前者查主站/标的
        #: 是否有历史，后者查代码拼写与记录布局。
        msg = (
            f"bars({symbol!r}, period={period!r}, count={count}) 这一页 {state.offenders}/"
            f"{state.page_rows} 根记录的字段落在库自己声明的取值域之外（日期解成 "
            f"'8047-92-78' 这类非法日历日），已丢弃该页并返回 {len(state.bars)} 根：这一页不是"
            f"该标的的记录——代码不存在时主站回的是缓冲区里别的标的，记录布局错位时"
            f"也是同一个形状"
        )
        return (
            WarningCode.BARS_PAGE_UNUSABLE,
            msg,
            {
                "symbol": symbol,
                "period": period,
                "returned": len(state.bars),
                "requested": count,
            },
        )
    return None
