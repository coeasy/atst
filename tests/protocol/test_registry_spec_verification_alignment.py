from __future__ import annotations

import re
from pathlib import Path

import pytest

from atst.client import TdxClient
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
    """仍然 inferred 的结构化命令必须在发包前被拦下。

    2026-10-21 深夜：``minute_today`` / ``trade_today`` 从这里毕业——两条命令的
    请求体字节序（市场在前）与 leb128 记录布局都被双市场 golden 样本 + 0x052D
    K 线交叉验证锁定，账本升到 L1 / verified，拦截表
    :data:`atst.client.core._UNVERIFIED_STRUCTURED_BLOCK` 相应清空。

    判据因此改成**双向**的：

    * 拦截表现在是空的——再往里加命令必须先补 golden 证据，并在本文件留一条
      「发包前即抛」的用例；
    * 毕业那两条真的不再抛 :class:`NotImplementedFeature`：用一个必然炸的池驱动，
      能走到「发包」这一步本身就证明守卫放行了（断言的是池被调用，而不是超时）。
    """
    from atst.client.core import _UNVERIFIED_STRUCTURED_BLOCK

    assert frozenset() == _UNVERIFIED_STRUCTURED_BLOCK, (
        "拦截表又变非空：新入表的命令必须在 PROTOCOL_SPEC 里写明 inferred，"
        "并在这里补一条『发包前即抛』的用例"
    )

    class _RecordingPool:
        def __init__(self) -> None:
            self.calls: list[int] = []

        def request(self, cmd, body, *, timeout=None):  # noqa: ANN001
            self.calls.append(cmd)
            raise AssertionError("探测池不应被真正发包——只用来证明守卫已放行")

    pool = _RecordingPool()
    client = TdxClient(pool=pool)
    for call in (
        lambda: client.minute_today("600000"),
        lambda: client.trade_today("600000", count=10),
    ):
        with pytest.raises(AssertionError, match="只用来证明守卫已放行"):
            call()
    assert pool.calls == [CMD["minute_today"], CMD["trade_today"]]


def test_registry_marks_known_inferred_parsers_unverified() -> None:
    """inferred spec 与账本必须一致；2026-10-21 毕业的那两条反向也钉住。

    正向（原有）：spec 写 ``status: inferred`` ⇒ 账本 ``verified=False``。
    反向（新增）：spec 写 ``verified`` ⇒ 账本 ``verified=True`` 且 ``tier=L1``——
    否则「golden 已锁定」这句话只活在 spec 里，账本那半边还在说布局未验证，
    与 F-37 当年「两个文件互相矛盾而无人报红」是同一个坑。
    """
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
            command = COMMANDS.get((family, int(spec_match.group(1), 16)))
            if command is None:
                continue
            checked += 1
            if status_match.group(1).lower() == "inferred":
                assert command.verified is False, (
                    f"{path.relative_to(_ROOT)} is inferred but registry marks "
                    f"{command.name} verified"
                )
            else:
                assert command.verified is True, (
                    f"{path.relative_to(_ROOT)} claims {status_match.group(1)} but registry "
                    f"marks {command.name} unverified"
                )
    assert checked >= 8, f"只核对到 {checked} 份 spec，判据自身失效"

    # 0x0537 / 0x0FC5：2026-10-21 双市场 golden 锁定后的账本现状
    for name in ("minute_today", "trade_today"):
        row = COMMANDS[(Family.STANDARD, CMD[name])]
        assert row.verified is True and row.tier == "L1", (
            f"{name} 已用正确请求体重采 golden 并通过 0x052D 交叉验证，"
            f"账本却仍是 verified={row.verified} tier={row.tier}"
        )
