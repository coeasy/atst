# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""Goods / ExMarket / Mac / F10 扩展族协议模板（P2-2：自 ``_mixin.py`` 拆出）。

四个子客户端（``GoodsClient`` / ``ExMarketClient`` / ``MacClient`` /
``F10Client`` 及其 async 镜像）共用的 ``_t_*`` 模板本体：0x0200/0x0201（Goods）、
0x0100-0x0103（扩展市场）、0x1400 族统一行情（Mac）、0x120F/0x1210（板块）、
0x0001（F10 栏目目录）。机制总述与 fail-closed 校验约束见
:mod:`atst.client._mixin` 顶层 docstring。
"""

from __future__ import annotations

import struct
from typing import Any

import atst.client as _client_pkg  # 包级符号经此转发（见 sync.py 说明）

from ..protocol.commands import CMD
from ._trampoline import (
    OutputFormat,
    _op_call,
    _op_req,
    _TrampolineMixin,
)
from .core import (  # B1：共享核心（纯协议构造，无 I/O）
    _bars_body,
    _emit,
    _guard_unverified_family,
    _quote_body,
    _require_int,
    _require_output_format,
    _row_to_bar,
    _row_to_quote,
    period_to_category,
    split_symbol,
)


class _FamilyExtensionTemplates(_TrampolineMixin):
    """Goods / Ex / Mac / F10 扩展族 ``_t_*`` 模板（P2-2 自 ``_ClientMixin`` 拆出）。

    继承 :class:`_TrampolineMixin` 只为让 mypy 在单类上下文里校验机制成员；
    运行期组合与既有 import 见 :mod:`atst.client._mixin`。
    """

    # ------------------------------------------------------------------ #
    # 多协议族子客户端模板（Goods / ExMarket / Mac / F10）
    # ------------------------------------------------------------------ #
    def _t_goods_bars(
        self, symbol: str, *, period: str, count: int, start: int, as_format: OutputFormat
    ) -> Any:
        _require_output_format(as_format)
        category = period_to_category(period)
        mkt, code = split_symbol(symbol)
        #: P1-10（B5）：experimental 统一闸——布局未 golden 锁定，发包前 fail-fast（E9010）。
        _guard_unverified_family("goods_bars")
        frame = yield _op_req(
            CMD["goods_bars"],
            _bars_body(mkt, code, category, start, count),
            timeout=self.timeout,
        )
        result = self._forward(
            _client_pkg.dispatch(frame, category=category, family=self.family),
            f"goods_bars({symbol!r}) {CMD['goods_bars']:#06x} 解码",
        )
        return _emit([_row_to_bar(row) for row in result.rows], as_format)

    def _t_goods_quote(self, symbol: str, as_format: OutputFormat) -> Any:
        _require_output_format(as_format)
        mkt, code = split_symbol(symbol)
        #: P1-10（B5）：experimental 统一闸——布局未 golden 锁定，发包前 fail-fast（E9010）。
        _guard_unverified_family("goods_quote")
        frame = yield _op_req(CMD["goods_quote"], _quote_body(code, mkt), timeout=self.timeout)
        result = self._forward(
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
        #: P1-10（B5）：experimental 统一闸——布局未 golden 锁定，发包前 fail-fast（E9010）。
        _guard_unverified_family("ex_bars")
        frame = yield _op_req(
            CMD["ex_instrument_bars"],
            _bars_body(mkt, code, category, start, count),
            timeout=self.timeout,
        )
        result = self._forward(
            _client_pkg.dispatch(frame, category=category, family=self.family),
            f"ex_bars({symbol!r}) {CMD['ex_instrument_bars']:#06x} 解码",
        )
        return _emit([_row_to_bar(row) for row in result.rows], as_format)

    def _t_ex_quote(self, symbol: str, as_format: OutputFormat) -> Any:
        _require_output_format(as_format)
        mkt, code = split_symbol(symbol)
        #: P1-10（B5）：experimental 统一闸——布局未 golden 锁定，发包前 fail-fast（E9010）。
        _guard_unverified_family("ex_quote")
        frame = yield _op_req(
            CMD["ex_instrument_quote"], _quote_body(code, mkt), timeout=self.timeout
        )
        result = self._forward(
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
        #: P1-10（B5）：experimental 统一闸——布局未 golden 锁定，发包前 fail-fast（E9010）。
        _guard_unverified_family("mac_quote")
        frame = yield _op_req(
            CMD["mac_unified_quote"], _quote_body(code, mkt), timeout=self.timeout
        )
        result = self._forward(
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
