# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""同步 / 异步客户端共享方法骨架（REFACTOR_PLAN_v9 Q2）。

承载 ``TdxClient`` / ``AsyncTdxClient`` 及四个子客户端镜像方法对的**唯一**
方法体：协议体构造（复用 :mod:`atst.client.core` 纯函数）+ ``dispatch`` 解析
+ ``as_format`` 分派（``_emit``）+ ``last_errors`` 收尾。

异步差异的消除方式——**模板 + trampoline**：

* 每个镜像方法写成**同步生成器模板**（``_t_xxx``），凡原 ``await`` /
  ``self._req`` / ``self.<其他公开方法>`` 的位置改为
  ``yield _op_req(...)`` / ``yield _op_call(...)`` 挂起；
* 同步端 :meth:`_ClientMixin._drive` 逐条执行 op（直接调用传输 seam
  ``_req``），异步端 :meth:`_ClientMixin._adrive` 逐条 ``await``；
* op 执行抛出的异常经 ``gen.throw`` 在**原挂起点**重入模板，模板内的
  ``try/except TdxError``（坏标的隔离、0x054C 回退）语义逐字保留；
* ``warnings.warn`` 的 ``stacklevel`` 相应 +2（gen 帧 + drive 帧），
  告警归属仍为客户端调用方。

对外行为不变：公开方法壳留在 sync.py / async_.py（签名、overload、
docstring 摘要与迁移前一致）；``dispatch`` 经 :mod:`atst.client` 包级
符号在**调用时**解析（monkeypatch 语义不变，见 sync.py 说明）。

**fail-closed 参数校验是本模板的硬约束**：协议参数（市场标识、页偏移、
日期、文件名、批量容器）必须在**任何 pool I/O 之前**校验完毕并抛
:class:`~atst.errors.ParseError`；同步 / 异步两端不得各维护一套，
校验只能落在本共享模板或 :mod:`atst.client.core` 的 SSOT 纯函数里。
"""

from __future__ import annotations

import logging
import struct
import sys
from collections.abc import Mapping
from typing import Any

import atst.client as _client_pkg  # 包级符号经此转发（见 sync.py 说明）

from ..diagnostics import WarningCode, record_warning
from ..domain.finance import FINANCE_INFO_FIELDS, map_finance_values
from ..domain.integrity import row_violations
from ..domain.models import Bar, CapitalChange, Quote
from ..errors import (
    ParseError,
    TdxError,
    TruncatedDataError,
    ValidationError,
)
from ..protocol.commands import CMD, Family
from ..protocol.parsers.std7709 import (
    SecurityBarsParser,
    build_realtime_quote_body,
)
from ..protocol.registry import TIER_L3, ParseResult
from .core import (  # B1：共享核心（纯协议构造，无 I/O）
    _bars_body,
    _emit,
    _encode_gbk_field,
    _normalize_symbols,
    _quote_body,
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

OutputFormat = str  # "dict" | "tuple" | "dataframe"

#: 模板内 ``warnings.warn`` 的 stacklevel：原实现（方法体直呼）为 2 =
#: 调用方；模板化后调用链多出 2 帧（生成器帧 + drive 帧），故 +2。
#: 只对**单跳**模板成立——嵌套 ``_op_call`` 的路径靠 :func:`_caller_stacklevel` 实测。
_TPL_WARN_STACKLEVEL = 4

#: 一条越域告警最多带几条实例。910 行的错位页能产出上千条理由，全塞进 wire 等于
#: 用噪声换信号；计数始终是全文，例子只截前几条。
_DOMAIN_CAVEAT_SAMPLES = 3


def _op_req(cmd: int, body: bytes, **kw: Any) -> tuple[str, int, bytes, dict[str, Any]]:
    """op：发送单命令帧（sync ``self._req`` / async ``await self._req``）。"""
    return ("req", cmd, body, kw)


def _op_call(name: str, *args: Any, **kwargs: Any) -> tuple[str, str, tuple, dict]:
    """op：调用本客户端另一方法（sync 直呼 / async ``await``），保持
    monkeypatch 可见性与拆分前一致。"""
    return ("call", name, args, kwargs)


def _forward_decode_caveats(result: ParseResult, label: str) -> ParseResult:
    """把"这一页数据有问题"的判断接进结果侧的告警通道——唯一的那个转发口。

    两路判断都从这里过，因为它们在 wire 上是同一个缺陷的两半：

    * 解码层每一页记下的"声明 N 实收 M""钳制""降级为 L3 透传"。袋本身没人读就是
      wire 上的静默（F-51 的第 26 步接线只覆盖了 ``bars`` 一条命令，其余 14 个分派点
      把判断整族丢弃 —— F-63①）。
    * 解出来的行落在库自己声明的值域之外。布局未经真机 golden 锁定的命令（G3 的
      ``0x000F``/``0x0010``）页内字节数对得上，解码层因此一个字都不记，而值已经错位
      ——调用方此前只能靠读源码 docstring 知道（G7）。
    """
    for caveat in result.warnings:
        record_warning(
            WarningCode.DECODE_CAVEAT,
            f"{label}：{caveat}",
            stacklevel=_caller_stacklevel(),
        )
    offenders = 0
    samples: list[str] = []
    for index, row in enumerate(result.rows):
        if violations := row_violations(row, f"row[{index}]"):
            offenders += 1
            if len(samples) < _DOMAIN_CAVEAT_SAMPLES:
                samples.extend(violations[: _DOMAIN_CAVEAT_SAMPLES - len(samples)])
    if samples:
        record_warning(
            WarningCode.FIELD_OUT_OF_DOMAIN,
            f"{label}：{offenders} 行的字段值落在库自己声明的取值域之外"
            f"（例：{'；'.join(samples)}）——这些字段不可当作可信输入；对记录布局尚未"
            "由真机 golden 锁定的命令（见命令账本的 tier/verified），这正是错位的形状",
            stacklevel=_caller_stacklevel(),
        )
    return result


def _caller_stacklevel() -> int:
    """取"离开 atst 向上的第一帧"在 ``record_warning`` 语义下的 stacklevel。

    模板之间用 ``_op_call`` 互相调用，一次公共 API 可以嵌套好几层 trampoline
    （实测 ``block_list`` → ``request`` → ``request_result`` 三跳）。固定常量只对
    单跳成立，多跳会把告警的归属行留在库里——归属错位的告警等于把缺陷指给一个
    没做错的人，所以这里按当前调用栈实测，而不是猜一个数。
    """
    frame = sys._getframe(1)  # 调用方：_forward_decode_caveats
    depth = 0
    while frame.f_globals.get("__name__", "").startswith("atst"):
        depth += 1
        if frame.f_back is None:
            break
        frame = frame.f_back
    return depth + 1


class _ClientMixin:
    """同步 / 异步客户端共享骨架（模板 + trampoline）。

    传输 seam 由子类实现：sync 为 ``_req(cmd, body, *, timeout)``，
    async 为同名 ``async _req``。``last_errors`` 收尾统一走
    :meth:`_set_last_errors` 钩子（sync 加锁、async 直赋——镜像既有差异）。
    """

    # The sync/async concrete clients provide these members.  They are
    # declared on the shared template so mypy can validate the trampoline
    # without weakening the concrete client contracts.
    timeout: float
    family: str
    _errors_lock: Any
    last_errors: list[tuple[str, BaseException]]
    _req: Any

    # ------------------------------------------------------------------ #
    # trampoline 驱动器
    # ------------------------------------------------------------------ #
    def _drive(self, gen: Any) -> Any:
        """同步驱动：执行模板挂起的 op，直到模板 return。"""
        try:
            op = next(gen)
            while True:
                try:
                    if op[0] == "req":
                        out = self._req(op[1], op[2], **op[3])
                    else:
                        _, name, args, kwargs = op
                        out = getattr(self, name)(*args, **kwargs)
                except BaseException as exc:  # noqa: BLE001 - 原样在挂起点重入模板
                    op = gen.throw(exc)
                else:
                    op = gen.send(out)
        except StopIteration as stop:
            return stop.value

    async def _adrive(self, gen: Any) -> Any:
        """异步驱动：逐条 ``await`` 模板挂起的 op（镜像 :meth:`_drive`）。"""
        try:
            op = next(gen)
            while True:
                try:
                    if op[0] == "req":
                        out = await self._req(op[1], op[2], **op[3])
                    else:
                        _, name, args, kwargs = op
                        out = await getattr(self, name)(*args, **kwargs)
                except BaseException as exc:  # noqa: BLE001
                    op = gen.throw(exc)
                else:
                    op = gen.send(out)
        except StopIteration as stop:
            return stop.value

    def _set_last_errors(self, errors: list[tuple[str, BaseException]]) -> None:
        """``last_errors`` 收尾钩子（sync：与并发路径互斥加锁，C3）。"""
        with self._errors_lock:
            self.last_errors = errors

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
        index: bool,
        as_format: OutputFormat,
        strict: bool,
    ) -> Any:
        """K 线 / 分钟线（命令 ``0x052D``，自动分页 + 截断语义）。

        空桩首页与锚点漂移截断的判据就在本模板里：瑕疵逐条发 ``UserWarning``（经
        ``Client`` 内核时同时进 ``ResultMeta.warnings``），``strict=True`` 时同一条判据
        直接抛 :class:`TruncatedDataError` 而不是返回后靠调用方自查。
        """
        _require_output_format(as_format)
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

        bars: list[Bar] = []
        seen: set[str] = set()
        drifted = False  # 整页去重后零新增（锚点漂移），非正常历史耗尽
        empty_first_page = False  # 第一页就 0 条：不是耗尽，是空桩/无该标的数据
        first_page_declared: int | None = None  # 首页服务端声明的记录数，用于分家二者

        while remaining > 0:
            page = min(remaining, MAX_BARS_PER_REQUEST)
            body = struct.pack(
                "<H6sHHHHIIH",
                mkt,
                code.encode("ascii")[:6].ljust(6, b"\x00"),
                category,
                1,
                offset,
                page,
                0,
                0,
                0,
            )
            frame = yield _op_req(CMD["security_bars"], body, timeout=self.timeout)
            #: 逐页的解码判断（"声明 N 实收 M"）必须上 wire，转发口见
            #: :func:`_forward_decode_caveats`（F-51 的原始症状就长在这里）。
            result = _forward_decode_caveats(
                _client_pkg.dispatch(frame, category=category, family=self.family, index=index),
                f"bars({symbol!r}) 分页解码",
            )
            raw_rows = result.rows  # 原始 dict 行（去重以 datetime 字符串为键）
            if not raw_rows:
                # 次页空 = 历史耗尽（正常终止，真实耗尽只会表现为短页或空次页）；
                # 首页空要按服务端**声明的记录数**分家：声明 0 才是"没有这段历史"，
                # 声明 N 却回 0 个记录字节是空桩（实测主站对 0x052D 声明 800 条）。
                if not seen:
                    empty_first_page = True
                    first_page_declared = result.meta.get("declared_count")
                break
            fresh = [row for row in raw_rows if str(row.get("datetime")) not in seen]
            seen.update(str(row.get("datetime")) for row in raw_rows)
            if not fresh:
                drifted = True
                break  # 整页重复：盘中锚点漂移，防死循环
            bars.extend(_row_to_bar(row) for row in fresh)
            if len(raw_rows) < page:
                break  # 短页：历史耗尽（正常终止）
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
            offset += len(raw_rows)
            remaining -= len(fresh)

        if len(bars) < count and drifted:
            msg = (
                f"bars({symbol!r}, period={period!r}, count={count}) 分页因盘中"
                f"锚点漂移提前终止：实取 {len(bars)} 根 < 请求 {count} 根"
            )
            if strict_mode:
                raise TruncatedDataError(
                    msg, context={"symbol": symbol, "returned": len(bars), "requested": count}
                )
            record_warning(WarningCode.BARS_ANCHOR_DRIFT, msg, stacklevel=_TPL_WARN_STACKLEVEL)
        elif empty_first_page:
            # 空首页此前与"历史耗尽"共用一个 break，读起来就是"请求成功、恰好 0 根"；
            # 判据分家靠服务端声明数，而不是替它编一个数字（F-60）。
            if first_page_declared:
                cause = (
                    f"服务端声明 {first_page_declared} 条记录，却一个记录字节都没回——"
                    f"这是空桩，不是该标的没有历史（真没有历史时声明数就是 0）"
                )
            elif first_page_declared == 0:
                cause = "服务端声明 0 条记录：该标的无此周期历史，或主站对这个命令只回空桩"
            else:
                cause = "解析器没在响应里读到记录数头，声明数未知"
            msg = (
                f"bars({symbol!r}, period={period!r}, count={count}) 首页即空响应："
                f"{cause}，实取 0 根。空首页不是历史耗尽（耗尽只会表现为短页）"
            )
            if strict_mode:
                raise TruncatedDataError(
                    msg,
                    context={
                        "symbol": symbol,
                        "returned": 0,
                        "requested": count,
                        "declared": first_page_declared,
                    },
                )
            record_warning(WarningCode.BARS_EMPTY_FIRST_PAGE, msg, stacklevel=_TPL_WARN_STACKLEVEL)
        #: 出门前统一按时间**升序**：分页拼出来的原始序列是"锯齿"的——页内升序、
        #: 页间倒退（每页都比上一页旧），所以 ``bars[0]`` 并非最早、``bars[-1]`` 并非
        #: 最新。（线路实测 sz000001/16000 根：``bars[0]``=2023-06-15 而 ``bars[-1]``
        #: =1993-02-16，两端都不是极值。）回归测试用的 ``_PagePool`` 恰好是页内降序，
        #: 拼出来整体单调，把这个形状遮住了——所以此前既无判据也无文档。
        #: 升序后契约唯一且可用：``bars[0]`` = 最早一根，``bars[-1]`` = 最新一根。
        #: 稳定性保证同一时间戳的相对次序不变；``_emit`` 对 rows/tuple/dataframe
        #: 三种出口都保留顺序。
        bars.sort(key=lambda bar: bar.datetime)
        return _emit(bars, as_format)

    # ------------------------------------------------------------------ #
    # 实时行情（逐只 0x0530）
    # ------------------------------------------------------------------ #
    def _t_quotes(
        self,
        symbols: Any,
        *,
        as_format: OutputFormat,
        _collect: list[tuple[str, BaseException]] | None,
    ) -> Any:
        """实时行情快照（命令 ``0x0530``，逐只请求；坏标的隔离见 sync 壳）。"""
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
            result = _forward_decode_caveats(
                _client_pkg.dispatch(
                    frame, code=code, market=mkt, price_scale=100, family=self.family
                ),
                f"quotes({sym!r}) {CMD['realtime_quote']:#06x} 解码",
            )
            if not result.rows:
                errors.append((sym, ParseError(f"0x0530 无解析结果: {result.warnings}")))
                continue
            out.append(_row_to_quote(result.rows[0]))
        if _collect is None:
            self._set_last_errors(errors)
        return _emit(out, as_format)

    # ------------------------------------------------------------------ #
    # 元数据类
    # ------------------------------------------------------------------ #
    def _t_security_count(self, market: Any) -> int:  # type: ignore[misc]
        """某市场的证券总数（命令 ``0x044E``）。"""
        market_id = _standard_market_id(market)
        body = struct.pack("<H", market_id) + b"\x00" * 4
        frame = yield _op_req(CMD["security_count"], body, timeout=self.timeout)
        result = _forward_decode_caveats(
            _client_pkg.dispatch(frame, family=self.family),
            f"security_count(market={market_id}) {CMD['security_count']:#06x} 解码",
        )
        if not result.rows:
            raise ParseError("0x044E 无解析结果", context={"market": market_id})
        return int(result.rows[0].get("count", 0))

    def _t_capital_changes(self, symbol: str) -> list[CapitalChange]:  # type: ignore[misc]
        """除权除息 / 股本变迁（命令 ``0x000F``）。

        .. warning:: **字段口径未闭合（docs/archive/plans/REFACTOR_PLAN_V17_CLOSURE.md §0.3 F-37）**：2026-09-19 在
           可达主站上实测本命令能回整批记录，但解出的 ``market``/``code``/``date`` 逐字段
           错位（如 ``market`` 读到 ASCII ``'0'``），且不带任何解码告警；离线 golden 只钉住
           长度与条数、不校验字段值，所以全绿门禁看不见它。当前口径是**条数可用、字段语义
           不保证**；修它要新的真机布局判据，不按猜测改解析器。
        """
        mkt, code = split_symbol(symbol)
        body = code.encode("ascii")[:6].ljust(6, b"\x00") + struct.pack("<H", mkt)
        frame = yield _op_req(CMD["capital_changes"], body, timeout=self.timeout)
        result = _forward_decode_caveats(
            _client_pkg.dispatch(frame, code=code, market=mkt, family=self.family),
            f"capital_changes({symbol!r}) {CMD['capital_changes']:#06x} 解码",
        )
        return [_row_to_capital(row) for row in result.rows]

    def _t_finance_info(self, symbol: str) -> dict[str, Any]:  # type: ignore[misc]
        """财务基础信息（命令 ``0x0010``，F1 语义化字段）。

        .. warning:: 与 :meth:`capital_changes` 同一条未闭合口径（docs/archive/plans/REFACTOR_PLAN_V17_CLOSURE.md §0.3
           F-37）：真机回包长度与 golden 同形，实测解出的 ``market``/``code``/``values``
           却是空值/错位，且不触发解码告警 ⇒ 字段语义不保证，条数与结构可用。
        """
        mkt, code = split_symbol(symbol)
        body = code.encode("ascii")[:6].ljust(6, b"\x00") + struct.pack("<H", mkt)
        frame = yield _op_req(CMD["finance_info"], body, timeout=self.timeout)
        result = _forward_decode_caveats(
            _client_pkg.dispatch(frame, code=code, market=mkt, family=self.family),
            f"finance_info({symbol!r}) {CMD['finance_info']:#06x} 解码",
        )
        if not result.rows:
            return {}
        row = dict(result.rows[0])
        values = list(row.get("values", []) or [])
        out: dict[str, Any] = {"market": row.get("market", mkt), "code": row.get("code", code)}
        out.update(map_finance_values(values, FINANCE_INFO_FIELDS))
        out["values"] = values
        return out

    def _t_minute_today(self, symbol: str) -> list[Any]:  # type: ignore[misc]
        """当日分时数据（命令 ``0x0537``）。

        **本方法当前不发泡**：``0x0537`` 的 parser/request 仍是 inferred，
        ``core._UNVERIFIED_STRUCTURED_BLOCK`` 在发包前抛 ``NotImplementedFeature``
        （真机 golden 锁定前不通过结构化 API 发包，原始线路面仍可用）。
        """
        mkt, code = split_symbol(symbol)
        body = code.encode("ascii")[:6].ljust(6, b"\x00") + struct.pack("<H", mkt) + b"\x00" * 4
        frame = yield _op_req(CMD["minute_today"], body, timeout=self.timeout)
        result = _forward_decode_caveats(
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
        return _forward_decode_caveats(
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
        result = _forward_decode_caveats(
            _client_pkg.dispatch(frame, code=code, market=mkt, family=self.family),
            f"minute_history({symbol!r}, date={day}) {CMD['minute_history']:#06x} 解码",
        )
        return result.rows

    def _t_trade_today(self, symbol: str, start: int, count: int) -> Any:
        """当日逐笔成交（命令 ``0x0FC5``）。

        与 ``minute_today`` 同一条拦截：request/parser 仍为 inferred，
        ``core._UNVERIFIED_STRUCTURED_BLOCK`` 在发包前抛 ``NotImplementedFeature``。
        """
        mkt, code = split_symbol(symbol)
        offset = _require_int("start", start, minimum=0, maximum=0xFFFF)
        page_size = _require_int("count", count, minimum=0, maximum=0xFFFF)
        body = code.encode("ascii")[:6].ljust(6, b"\x00") + struct.pack(
            "<HHH", mkt, offset, page_size
        )
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
        result = _forward_decode_caveats(
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

    def _t_quotes_snapshot(self, symbols: Any) -> Any:
        """批量行情快照（优先 0x054C，失败/空帧/L3 降级回退逐只 0x0530）。

        ``0x054C`` 登记为 ``STATUS_OFFLINE``（多主站实测无响应），但它是账本里唯一被
        ``core._OFFLINE_FALLBACK_OK`` 放行的 offline 命令：**本方法仍可用**，代价是每次
        批量尝试都按已知会失败处理，真实数据来自逐只 ``0x0530`` 回退路径。
        """
        syms = _normalize_symbols(symbols)
        # 混合容器：0x054C 批量路径收 Quote 对象，0x0530 回退路径收
        # as_format="dict" 的 dict（to_dicts 对两者都产 dict，运行时一致）。
        out: list[Any] = []
        errors: list[tuple[str, BaseException]] = []
        for i in range(0, len(syms), _QUOTES_SNAPSHOT_BATCH):
            chunk = syms[i : i + _QUOTES_SNAPSHOT_BATCH]
            try:
                body = struct.pack("<B", len(chunk))
                for sym in chunk:
                    mkt, code = split_symbol(sym)
                    body += struct.pack("<H", mkt) + code.encode("ascii")[:6].ljust(6, b"\x00")
                frame = yield _op_req(CMD["quotes_snapshot"], body, timeout=self.timeout)
                result = _forward_decode_caveats(
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
        quote = yield _op_call("quotes", [symbol], as_format="dict")
        q = quote[0] if quote else None
        day = yield _op_call("bars", symbol, period="day", count=1, as_format="dict")
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

    # ------------------------------------------------------------------ #
    # 多协议族子客户端模板（Goods / ExMarket / Mac / F10）
    # ------------------------------------------------------------------ #
    def _t_goods_bars(
        self, symbol: str, *, period: str, count: int, start: int, as_format: OutputFormat
    ) -> Any:
        _require_output_format(as_format)
        category = period_to_category(period)
        mkt, code = split_symbol(symbol)
        frame = yield _op_req(
            CMD["goods_bars"],
            _bars_body(mkt, code, category, start, count),
            timeout=self.timeout,
        )
        result = _forward_decode_caveats(
            _client_pkg.dispatch(frame, category=category, family=self.family),
            f"goods_bars({symbol!r}) {CMD['goods_bars']:#06x} 解码",
        )
        return _emit([_row_to_bar(row) for row in result.rows], as_format)

    def _t_goods_quote(self, symbol: str, as_format: OutputFormat) -> Any:
        _require_output_format(as_format)
        mkt, code = split_symbol(symbol)
        frame = yield _op_req(CMD["goods_quote"], _quote_body(code, mkt), timeout=self.timeout)
        result = _forward_decode_caveats(
            _client_pkg.dispatch(frame, code=code, market=mkt, price_scale=100, family=self.family),
            f"goods_quote({symbol!r}) {CMD['goods_quote']:#06x} 解码",
        )
        return _emit([_row_to_quote(row) for row in result.rows], as_format)

    def _t_goods_count(self, market: int) -> Any:
        """商品数量（命令 ``0x0200``，family=GOODS）。返回 ``{"count"}`` 行。"""
        market_id = _require_int("market", market, minimum=0, maximum=0xFFFF)
        body = struct.pack("<H", market_id)
        return (yield _op_call("request", CMD["goods_count"], body, ctx={"market": market_id}))

    def _t_goods_list(self, market: int, start: int) -> Any:
        """商品列表（命令 ``0x0201``，family=GOODS）。返回 ``{"code", "name", "category"}`` 行。"""
        market_id = _require_int("market", market, minimum=0, maximum=0xFFFF)
        offset = _require_int("start", start, minimum=0, maximum=0xFFFF)
        body = struct.pack("<HH", market_id, offset)
        return (
            yield _op_call(
                "request", CMD["goods_list"], body, ctx={"market": market_id, "start": offset}
            )
        )

    def _t_ex_bars(
        self, symbol: str, *, period: str, count: int, start: int, as_format: OutputFormat
    ) -> Any:
        _require_output_format(as_format)
        category = period_to_category(period)
        mkt, code = split_symbol(symbol)
        frame = yield _op_req(
            CMD["ex_instrument_bars"],
            _bars_body(mkt, code, category, start, count),
            timeout=self.timeout,
        )
        result = _forward_decode_caveats(
            _client_pkg.dispatch(frame, category=category, family=self.family),
            f"ex_bars({symbol!r}) {CMD['ex_instrument_bars']:#06x} 解码",
        )
        return _emit([_row_to_bar(row) for row in result.rows], as_format)

    def _t_ex_quote(self, symbol: str, as_format: OutputFormat) -> Any:
        _require_output_format(as_format)
        mkt, code = split_symbol(symbol)
        frame = yield _op_req(
            CMD["ex_instrument_quote"], _quote_body(code, mkt), timeout=self.timeout
        )
        result = _forward_decode_caveats(
            _client_pkg.dispatch(frame, code=code, market=mkt, price_scale=100, family=self.family),
            f"ex_quote({symbol!r}) {CMD['ex_instrument_quote']:#06x} 解码",
        )
        return _emit([_row_to_quote(row) for row in result.rows], as_format)

    def _t_ex_market_count(self) -> Any:
        """扩展市场数量（命令 ``0x0100``，family=EXTENDED）。返回 ``{"count"}`` 行。"""
        return (yield _op_call("request", CMD["ex_market_count"], b"\x00\x00", ctx={}))

    def _t_ex_market_list(self) -> Any:
        """扩展市场列表（命令 ``0x0101``，family=EXTENDED）。返回 ``{"market_id", "name"}`` 行。"""
        return (yield _op_call("request", CMD["ex_market_list"], b"\x00\x00", ctx={}))

    def _t_ex_instrument_count(self, market: int) -> Any:
        """扩展市场品种数量（命令 ``0x0102``，family=EXTENDED）。返回 ``{"count"}`` 行。"""
        market_id = _require_int("market", market, minimum=0, maximum=0xFFFF)
        body = struct.pack("<H", market_id)
        return (
            yield _op_call("request", CMD["ex_instrument_count"], body, ctx={"market": market_id})
        )

    def _t_ex_instrument_list(self, market: int, start: int) -> Any:
        """扩展市场品种列表（命令 ``0x0103``，family=EXTENDED）。返回 ``{"market", "code", "name"}`` 行。"""
        market_id = _require_int("market", market, minimum=0, maximum=0xFFFF)
        offset = _require_int("start", start, minimum=0, maximum=0xFFFF)
        body = struct.pack("<HH", market_id, offset)
        return (
            yield _op_call(
                "request",
                CMD["ex_instrument_list"],
                body,
                ctx={"market": market_id, "start": offset},
            )
        )

    def _t_mac_quote(self, symbol: str, as_format: OutputFormat) -> Any:
        _require_output_format(as_format)
        mkt, code = split_symbol(symbol)
        frame = yield _op_req(
            CMD["mac_unified_quote"], _quote_body(code, mkt), timeout=self.timeout
        )
        result = _forward_decode_caveats(
            _client_pkg.dispatch(frame, code=code, market=mkt, price_scale=100, family=self.family),
            f"mac_quote({symbol!r}) {CMD['mac_unified_quote']:#06x} 解码",
        )
        return _emit([_row_to_quote(row) for row in result.rows], as_format)

    def _t_block_list(self, block_type: int, start: int) -> Any:
        """板块列表（命令 ``0x120F``，family=MAC）。返回 ``{"name", "block_id"}`` 行。"""
        kind = _require_int("block_type", block_type, minimum=0, maximum=0xFFFF)
        offset = _require_int("start", start, minimum=0, maximum=0xFFFF)
        body = struct.pack("<HH", kind, offset)
        return (
            yield _op_call(
                "request",
                CMD["mac_block_list"],
                body,
                ctx={"block_type": kind, "start": offset},
            )
        )

    def _t_block_members(self, block_id: int, start: int) -> Any:
        """板块成分股（命令 ``0x1210``，family=MAC）。返回 ``{"code"}`` 行。"""
        identifier = _require_int("block_id", block_id, minimum=0, maximum=0xFFFF)
        offset = _require_int("start", start, minimum=0, maximum=0xFFFF)
        body = struct.pack("<HH", identifier, offset)
        return (
            yield _op_call(
                "request",
                CMD["mac_block_members"],
                body,
                ctx={"block_id": identifier, "start": offset},
            )
        )

    def _t_catalog(self, symbol: str) -> Any:
        """F10 栏目目录清单（命令 ``0x0001``，family=F10；布局待真机定标）。"""
        mkt, code = split_symbol(symbol)
        body = code.encode("ascii")[:6].ljust(6, b"\x00") + struct.pack("<H", mkt)
        return (
            yield _op_call("request", CMD["f10_catalog"], body, ctx={"code": code, "market": mkt})
        )
