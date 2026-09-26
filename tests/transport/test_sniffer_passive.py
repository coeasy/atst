from __future__ import annotations

from typing import Any

import pytest

from tstdx.protocol.commands import Family
from tstdx.transport.sniff import Sniffer, attach, detach

#: 账本里只登记在 ``mac_quotation`` 族的命令号：它在 ``quotation`` 族查不到。
#: 用作"判定域是否真被 families 左右"的探针。
MAC_ONLY = 0x120F
#: 任何族都没登记的命令号。
NEVER_REGISTERED = 0x9FFF


def test_default_families_cover_every_ledger_family() -> None:
    """类 docstring 写着"默认全部家族"——这句必须是真的。

    旧实现把 ``self.families`` 存下来却从不读它，判定域实际写死在 ``quotation``
    一族上，于是这句承诺是一个没人能反驳的声明。
    """
    assert Sniffer().families == [
        Family.STANDARD,
        Family.EXTENDED,
        Family.MAC,
        Family.GOODS,
        Family.F10,
    ]


def test_a_mac_command_is_known_only_when_its_family_is_in_scope() -> None:
    assert Sniffer().known(MAC_ONLY) is True
    assert Sniffer(families=[Family.STANDARD]).known(MAC_ONLY) is False


def test_explicit_family_narrows_the_judgement_to_that_family() -> None:
    """显式点名一族时不得偷偷并入 ``self.families``。"""
    sniffer = Sniffer()  # 默认含 mac_quotation
    sniffer.observe_response(MAC_ONLY, b"\x00")
    assert sniffer.known(MAC_ONLY) is True
    assert sniffer.known(MAC_ONLY, Family.STANDARD) is False
    assert sniffer.unknown_commands() == []
    assert sniffer.unknown_commands(family=Family.STANDARD) == [MAC_ONLY]


def test_unknown_commands_uses_the_sniffer_scope_not_one_hardcoded_family() -> None:
    sniffer = Sniffer()
    sniffer.observe_response(MAC_ONLY, b"\x01\x02")
    sniffer.observe_response(NEVER_REGISTERED, b"\xff")
    assert sniffer.unknown_commands() == [NEVER_REGISTERED]
    assert sniffer.unknown_commands(family=Family.STANDARD) == [MAC_ONLY, NEVER_REGISTERED]
    assert sniffer.observed_commands() == sorted([MAC_ONLY, NEVER_REGISTERED])


def test_attach_records_each_response_once() -> None:
    sniffer = Sniffer(ring_size=4)
    target = _FakeClient()
    assert attach(sniffer, target) is target
    target.request(0x052D, b"payload")
    target.request(0x052D, b"payload2")
    assert sniffer.samples(0x052D) == [b"payload", b"payload2"]
    assert sniffer.stats()[0x052D]["count"] == 2


def test_attach_is_idempotent_and_does_not_stack_wrappers() -> None:
    sniffer = Sniffer()
    target = _FakeClient()
    attach(sniffer, target)
    attach(sniffer, target)
    target.request(0x052D, b"x")
    assert sniffer.samples(0x052D) == [b"x"]  # 双层包裹会记两条


def test_attach_overrides_the_sniffer_family_scope() -> None:
    """``attach(families=...)`` 曾被文档承诺却从不生效——这条钉住它现在生效。"""
    sniffer = Sniffer()
    assert sniffer.known(MAC_ONLY) is True
    attach(sniffer, _FakeClient(), families=[Family.STANDARD])
    assert sniffer.families == [Family.STANDARD]
    assert sniffer.known(MAC_ONLY) is False


def test_attach_on_an_unhookable_object_is_a_no_op() -> None:
    sniffer = Sniffer()
    plain = object()
    assert attach(sniffer, plain) is plain
    assert sniffer.stats() == {}


def test_ring_size_and_payload_cap_are_enforced() -> None:
    sniffer = Sniffer(ring_size=2, max_payload_bytes=4)
    for i in range(5):
        sniffer.observe_response(0x0050, bytes([i]) * 9)
    assert sniffer.samples(0x0050) == [b"\x03" * 4, b"\x04" * 4]
    stats = sniffer.stats()[0x0050]
    assert stats["count"] == 5
    # 记的是原始长度，不是截断后长度；窗口与 ring_size 同宽（第 26 轮：曾经是
    # 一条 append 到天的 list，长期挂载无界增长）。总次数在 count 里，不掩盖截断。
    assert stats["sizes"] == [9] * 2
    assert stats["last_payload_size"] == 9


def test_sizes_history_is_bounded_by_ring_size() -> None:
    """``CommandStats.sizes`` 有界：观察 1000 条也只留 ``ring_size`` 份。"""
    sniffer = Sniffer(ring_size=4)
    for i in range(1000):
        sniffer.observe_response(0x0050, bytes([i % 251]) * (i % 7 + 1))
    stats = sniffer.stats()[0x0050]
    assert stats["count"] == 1000  # 精确总次数不因窗口而失真
    assert len(stats["sizes"]) == 4


def test_attach_detach_round_trip() -> None:
    """``detach`` 卸下 attach 装上的钩子；不匹配的观察器不动别人的钩子。"""

    class _Resp:
        method = 0x0050
        payload = b"abc"

    class _Target:
        def request(self, method: int, body: bytes = b"") -> _Resp:
            return _Resp()

    target = _Target()
    first, second = Sniffer(), Sniffer()
    # 每次属性访问都造一个新的绑定方法对象，所以这里比的是 ==（同函数 + 同实例）
    original = target.request
    attach(first, target)
    assert target.request != original
    # 另一个观察器来 detach：不许卸下别人的钩子
    assert detach(second, target) is False
    assert target.request != original
    assert detach(first, target) is True
    assert target.request == original
    # 幂等：已卸下再 detach 只返回 False
    assert detach(first, target) is False
    # 卸下之后，钩子记录不再发生
    target.request(0x0050, b"")
    assert first.stats() == {}
    attach(first, target)
    target.request(0x0050, b"")
    assert set(first.stats()) == {0x0050}
    assert detach(first, target) is True


def test_invalid_sizes_are_rejected() -> None:
    with pytest.raises(ValueError):
        Sniffer(ring_size=0)
    with pytest.raises(ValueError):
        Sniffer(max_payload_bytes=0)


class _FakeResponse:
    def __init__(self, method: int, payload: bytes) -> None:
        self.method = method
        self.payload = payload


class _FakeClient:
    """只暴露 ``request`` 的最小传输层对象（attach 的第二种命中形状）。"""

    def __init__(self) -> None:
        self.calls: list[tuple[int, bytes]] = []

    def request(self, method: int, payload: bytes) -> Any:
        self.calls.append((method, payload))
        return _FakeResponse(method, payload)
