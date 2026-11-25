# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""P-4 回归：账本是解析器 tier 的唯一事实源，且修复前后有效值逐条不变。"""

from __future__ import annotations

from atst.protocol import parsers  # noqa: F401  触发全部 @register_parser
from atst.protocol.commands import (
    COMMANDS,
    TIER_DECLARED,
    TIER_L1,
    TIER_L2,
    Command,
    Family,
    get_command,
)
from atst.protocol.registry import PARSERS, BaseParser, register_parser

#: 修复前 61 条注册解析器的有效 TIER 快照。新注册允许增加键，但这里每条的值
#: 必须保持原样——"账本成为唯一事实源"不许悄悄改变对外行为。
_PINNED_TIERS: dict[tuple[str, int], str] = {
    (Family.STANDARD, 0x000F): TIER_L2,
    (Family.STANDARD, 0x0010): TIER_L2,
    (Family.STANDARD, 0x044D): TIER_L2,
    (Family.STANDARD, 0x044E): TIER_L1,
    (Family.STANDARD, 0x051A): TIER_L2,
    (Family.STANDARD, 0x051C): TIER_L2,
    (Family.STANDARD, 0x052D): TIER_L1,
    (Family.STANDARD, 0x0530): TIER_L1,
    (Family.STANDARD, 0x0537): TIER_L1,
    (Family.STANDARD, 0x053E): TIER_L2,
    (Family.STANDARD, 0x0547): TIER_L2,
    (Family.STANDARD, 0x054C): TIER_L2,
    (Family.STANDARD, 0x056A): TIER_L2,
    (Family.STANDARD, 0x06B9): TIER_L2,
    (Family.STANDARD, 0x07E5): TIER_L2,
    (Family.STANDARD, 0x0FB4): TIER_L2,
    (Family.STANDARD, 0x0FC5): TIER_L1,
    (Family.STANDARD, 0x0FC6): TIER_L2,
    (Family.F10, 0x0001): TIER_L2,
}
_PINNED_TIERS.update({(Family.EXTENDED, c): TIER_L2 for c in range(0x0100, 0x010F)})
_PINNED_TIERS.update({(Family.GOODS, c): TIER_L2 for c in range(0x0200, 0x020B)})
_PINNED_TIERS.update(
    {(Family.MAC, c): TIER_L2 for c in (0x120F, 0x1210, 0x1300, 0x1301, 0x1400, 0x1500)}
)
_PINNED_TIERS.update(
    {(Family.MAC, c): TIER_L2 for c in (0x1600, 0x1700, 0x2000, 0x2100, 0x2200, 0x2300)}
)
_PINNED_TIERS.update({(Family.MAC, c): TIER_L2 for c in (0x2400, 0x2500, 0x2560, 0x2562)})


def test_effective_tiers_match_ledger_and_keep_baseline_values() -> None:
    """每条已登记命令的 parser.TIER 必须等于账本，且逐条等于修复前的值。"""
    assert len(_PINNED_TIERS) == 61
    assert len(PARSERS) >= len(_PINNED_TIERS)

    for key, expected in _PINNED_TIERS.items():
        parser = PARSERS.get(key)
        assert parser is not None, f"修复前已注册的 {key} 消失了"
        assert expected == parser.TIER, f"{key}: 有效 TIER 从 {expected} 漂移到 {parser.TIER}"

    for (family, cmd), parser in PARSERS.items():
        row = get_command(cmd, family)
        assert row is not None, f"{family} {hex(cmd)} 的解析器没有账本行"
        assert row.tier == parser.TIER, (
            f"{family} {hex(cmd)}: 解析器 {parser.TIER} 与账本 {row.tier} 漂移"
        )


def test_new_ledger_command_tier_propagates_to_new_parser() -> None:
    """新命令入账后，注册解析器的 TIER 跟着账本走，而不是跟着装饰器参数走。"""
    family = "p4-ledger-test"
    cmd = 0x7F41
    key = (family, cmd)
    try:
        COMMANDS[key] = Command(cmd=cmd, name="P4_LEDGER_PROBE", family=family, tier=TIER_DECLARED)

        @register_parser(cmd, family=family, name="P4_LEDGER_PROBE", tier=TIER_L1)
        class _DeclaredProbe(BaseParser):
            pass

        assert _DeclaredProbe.TIER == TIER_DECLARED, "账本 D 被装饰器的 L1 覆盖了"
        assert PARSERS[key] is _DeclaredProbe

        PARSERS.pop(key)
        COMMANDS[key] = Command(cmd=cmd, name="P4_LEDGER_PROBE", family=family, tier=TIER_L2)

        @register_parser(cmd, family=family, name="P4_LEDGER_PROBE", tier=TIER_L1)
        class _L2Probe(BaseParser):
            pass

        assert _L2Probe.TIER == TIER_L2, "账本改成 L2 后解析器没跟着变"
        assert PARSERS[key] is _L2Probe
    finally:
        COMMANDS.pop(key, None)
        PARSERS.pop(key, None)


def test_unregistered_command_keeps_decorator_tier_as_fallback() -> None:
    """未入账的命令仍走兼容回退，公开注册 API 不因 P-4 变窄。"""
    family = "p4-ledger-test"
    cmd = 0x7F42
    key = (family, cmd)
    assert get_command(cmd, family) is None
    try:

        @register_parser(cmd, family=family, tier=TIER_L2)
        class _FallbackProbe(BaseParser):
            pass

        assert _FallbackProbe.TIER == TIER_L2
    finally:
        PARSERS.pop(key, None)
