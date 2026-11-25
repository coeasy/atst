# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""canonical ``quotes`` 通道解析 HK/US 时不得套用 A 股 ×100/×10000(真机回归)。

腾讯 A 股布局 [6]=手、[37]=万元；HK/US 布局 [6]=股、[37]=原生币种元。
2026-11-24 本地真机实测：canonical 路由把 hk00700 的成交额 101 亿港元放大成
101 万亿，成交量 2939 万股放大成 29 亿股。修复后两种入口（canonical quotes
与 hk_quotes/us_quotes 迁移能力）口径必须一致。
"""

from __future__ import annotations

from atst.sources.tencent.adapters import HkSource, TencentSource, UsSource


def _payload(code: str, *, price: str, volume: str, amount: str) -> str:
    fields = [""] * 60
    fields[1] = "测试标的"
    fields[3] = price
    fields[4] = price
    fields[5] = price
    fields[6] = volume
    fields[30] = "2026/11/24 16:08:15"
    fields[32] = "0.5"
    fields[33] = price
    fields[34] = price
    fields[37] = amount
    return f'v_{code}="{"~".join(fields)}";'


def test_canonical_tencent_source_keeps_foreign_units() -> None:
    src = TencentSource()
    try:
        text = _payload("hk00700", price="341.6", volume="29393105", amount="10123426303.24")
        rows = src.parse(text, ["hk00700"])
        assert len(rows) == 1
        row = rows[0]
        assert row.volume == 29_393_105
        assert row.amount == 10_123_426_303.24
        assert row.extra["currency"] == "HKD"
        assert row.datetime == "2026-11-24 16:08:15"

        text = _payload("usAAPL", price="352.11", volume="4154009", amount="1450019006")
        row = src.parse(text, ["usAAPL"])[0]
        assert row.volume == 4_154_009
        assert row.amount == 1_450_019_006.0
        assert row.extra["currency"] == "USD"
    finally:
        src.close()


def test_canonical_and_migrated_entries_agree() -> None:
    text = _payload("hk00700", price="341.6", volume="29393105", amount="10123426303.24")
    canonical = TencentSource().parse(text, ["hk00700"])[0]
    migrated = HkSource().parse(text, ["hk00700"])[0]
    assert (canonical.volume, canonical.amount) == (migrated.volume, migrated.amount)

    text = _payload("usAAPL", price="352.11", volume="4154009", amount="1450019006")
    canonical = TencentSource().parse(text, ["usAAPL"])[0]
    migrated = UsSource().parse(text, ["usAAPL"])[0]
    assert (canonical.volume, canonical.amount) == (migrated.volume, migrated.amount)


def test_a_share_scaling_unchanged() -> None:
    """A 股仍按 [6]手 ×100、[37]万元 ×10000。"""
    src = TencentSource()
    try:
        text = _payload("sh600519", price="1460.00", volume="42924", amount="621419")
        row = src.parse(text, ["sh600519"])[0]
        assert row.volume == 4_292_400
        assert row.amount == 6_214_190_000.0
    finally:
        src.close()
