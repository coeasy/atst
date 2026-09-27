from __future__ import annotations

import re
from pathlib import Path

import pytest

from atst.client import TdxClient
from atst.errors import NotImplementedFeature
from atst.protocol.commands import CMD, COMMANDS, Family

_ROOT = Path(__file__).resolve().parents[2]
_FAMILY_BY_SPEC_DIR = {
    "7709": Family.STANDARD,
    "7727": Family.EXTENDED,
    "GOODS": Family.GOODS,
    "MAC": Family.MAC,
    "F10": Family.F10,
}
_SPEC_ID_RE = re.compile(r'^spec_id:\s*["\']?(0x[0-9a-fA-F]+)', re.MULTILINE)
_STATUS_RE = re.compile(r'^status:\s*["\']?([a-zA-Z_]+)', re.MULTILINE)


class _NoIoPool:
    def request(self, *_args, **_kwargs):
        raise AssertionError("inferred structured command must fail before transport I/O")


def test_inferred_protocol_specs_never_claim_verified_registry_status() -> None:
    spec_root = _ROOT / "PROTOCOL_SPEC"
    checked = 0
    for directory, family in _FAMILY_BY_SPEC_DIR.items():
        family_root = spec_root / directory
        if not family_root.is_dir():
            continue
        for path in family_root.glob("*.yaml"):
            text = path.read_text(encoding="utf-8")
            spec_match = _SPEC_ID_RE.search(text)
            status_match = _STATUS_RE.search(text)
            if spec_match is None or status_match is None:
                continue
            command_id = int(spec_match.group(1), 16)
            command = COMMANDS.get((family, command_id))
            if command is None:
                continue
            checked += 1
            if status_match.group(1).lower() == "inferred":
                assert command.verified is False, (
                    f"{path.relative_to(_ROOT)} is inferred but registry marks "
                    f"{command.name} verified"
                )
    assert checked > 0


def test_known_inferred_structured_clients_are_blocked_before_io() -> None:
    client = TdxClient(pool=_NoIoPool())

    with pytest.raises(NotImplementedFeature, match="inferred"):
        client.minute_today("600000")
    with pytest.raises(NotImplementedFeature, match="inferred"):
        client.trade_today("600000", count=10)


def test_registry_marks_known_inferred_parsers_unverified() -> None:
    assert COMMANDS[(Family.STANDARD, CMD["minute_today"])].verified is False
    assert COMMANDS[(Family.STANDARD, CMD["trade_today"])].verified is False
