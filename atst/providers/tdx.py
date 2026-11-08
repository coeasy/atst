# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""通达信主站行情（85 命令协议族；默认 Provider，身份是信任边界）——Provider 声明（v2 注册表收敛）。

本模块是 ``tdx`` 的**唯一声明处**：channel/capability 归属（``SPEC``）、
channel→adapter 绑定（``CHANNEL_ADAPTERS``）、能力→实现的显式绑定（``BINDINGS``）。
新增能力：实现方法 + 在 ``BINDINGS`` 里登记一行；启动对账（``audit_runtime``）
与 channel 契约校验（``providers/__init__.py``）负责两侧一致。
"""

from __future__ import annotations

from .registry import CapabilityBinding, ProviderSpec, _c

SPEC = ProviderSpec(
    id="tdx",
    default=True,
    channels=(
        _c(
            "quotation",
            "auction",
            "bars",
            "block_quotes",
            "capital_changes",
            "corporate_action",
            "finance",
            "minute",
            "minute_history",
            "quotes",
            "quotes_concurrent",
            "security_count",
            "security_list",
            "security_list_all",
            "snapshot",
            "trades",
            "volume_price",
            live=True,
            periods=(
                "15min",
                "1min",
                "30min",
                "5min",
                "60min",
                "day",
                "month",
                "season",
                "week",
                "year",
            ),
            unavailable=(
                "auction",
                "block_quotes",
                "minute",
                "minute_history",
                "security_list",
                "security_list_all",
                "trades",
                "volume_price",
            ),
        ),
        _c(
            "extended",
            "bars",
            "ex_bars",
            "ex_instruments",
            "ex_market_list",
            "ex_quotes",
            "quotes",
            live=True,
        ),
        _c("goods", "bars", "goods_bars", "goods_quotes", "quotes", live=True),
        _c("f10", "f10", "f10_catalog"),
        _c("mac", "mac_quotes", "quotes", live=True),
    ),
)

CHANNEL_ADAPTERS: dict[str, tuple[str, str]] = {}
EXPLICIT_CHANNELS: frozenset[str] = frozenset({})
DIRECT_CHANNELS: frozenset[str] = frozenset({"extended", "f10", "goods", "mac", "quotation"})
CHANNEL_API_EXEMPT = False

DEFAULT_CAPABILITIES: tuple[str, ...] = ()
MARKET_DEFAULT_CAPABILITIES: dict[str, tuple[str, ...]] = {}

STATUS: dict[str, str] = {
    "auction": "offline",
    "block_quotes": "offline",
    "minute_history": "offline",
    "volume_price": "offline",
}

BINDINGS: tuple[CapabilityBinding, ...] = (
    CapabilityBinding(
        capability="ex_bars",
        provider="tdx",
        channel="extended",
        backend="ex_client",
        method="ex_bars",
    ),
    CapabilityBinding(
        capability="ex_instruments",
        provider="tdx",
        channel="extended",
        backend="ex_client",
        method="ex_instrument_list",
    ),
    CapabilityBinding(
        capability="ex_market_list",
        provider="tdx",
        channel="extended",
        backend="ex_client",
        method="ex_market_list",
    ),
    CapabilityBinding(
        capability="ex_quotes",
        provider="tdx",
        channel="extended",
        backend="ex_client",
        method="ex_quote",
    ),
    CapabilityBinding(
        capability="f10",
        provider="tdx",
        channel="f10",
        backend="f10_client",
        method="f10",
    ),
    CapabilityBinding(
        capability="f10_catalog",
        provider="tdx",
        channel="f10",
        backend="f10_client",
        method="catalog",
    ),
    CapabilityBinding(
        capability="goods_bars",
        provider="tdx",
        channel="goods",
        backend="goods_client",
        method="goods_bars",
    ),
    CapabilityBinding(
        capability="goods_quotes",
        provider="tdx",
        channel="goods",
        backend="goods_client",
        method="goods_quote",
    ),
    CapabilityBinding(
        capability="mac_quotes",
        provider="tdx",
        channel="mac",
        backend="mac_client",
        method="mac_quote",
    ),
    CapabilityBinding(
        capability="auction",
        provider="tdx",
        channel="quotation",
        backend="tdx_client",
        method="auction_snapshot",
    ),
    CapabilityBinding(
        capability="block_quotes",
        provider="tdx",
        channel="quotation",
        backend="tdx_client",
        method="block_quotes",
    ),
    CapabilityBinding(
        capability="capital_changes",
        provider="tdx",
        channel="quotation",
        backend="tdx_client",
        method="capital_changes",
    ),
    CapabilityBinding(
        capability="corporate_action",
        provider="tdx",
        channel="quotation",
        backend="tdx_client",
        method="capital_changes",
    ),
    CapabilityBinding(
        capability="finance",
        provider="tdx",
        channel="quotation",
        backend="tdx_client",
        method="finance_info",
    ),
    CapabilityBinding(
        capability="minute_history",
        provider="tdx",
        channel="quotation",
        backend="tdx_client",
        method="minute_history",
    ),
    CapabilityBinding(
        capability="quotes_concurrent",
        provider="tdx",
        channel="quotation",
        backend="tdx_client",
        method="quotes_concurrent",
    ),
    CapabilityBinding(
        capability="security_list_all",
        provider="tdx",
        channel="quotation",
        backend="tdx_client",
        method="export_security_list",
    ),
    CapabilityBinding(
        capability="volume_price",
        provider="tdx",
        channel="quotation",
        backend="tdx_client",
        method="volume_price_dist",
    ),
)
