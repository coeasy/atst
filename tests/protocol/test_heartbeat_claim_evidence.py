"""心跳主张对账（台账 F-20 收口 · 第 28 轮）。

2026-09-26 真机探针（标准族默认主站池 10 台，7 台可达、3 台 TCP 超时）实测，
7 台答得完全一致：

* 账本里登记为 HEARTBEAT 的 ``0x0004``：各回一帧 **10 字节** 载荷
  （``0000000000003c283501``，末 4 字节按小端 u32 读为 ``20260924``，布局未锁定）。
* 传输层探活真正发出的 :data:`DEFAULT_HEARTBEAT_CMD`（``0x0002``）：各回 **50 字节**
  （含 GBK 文本「上交所公告」）——所以它**不是**"服务端对未知命令回一个短帧"意义上
  的中性探测码，旧注释那样写过，是假的。
* 备用号 ``0x0015``（账本 PING）：各回 127–136 字节压缩帧。

两条都答 ⇒ 换码不是修复，把主张写诚实才是（默认决定：不翻转 ``DEFAULT_HEARTBEAT_CMD``，
不把 0x0002 塞进标准族账本）。本判据钉住三件事，改动任意一边都会在这里要求重新裁决：

1. 探活码只有一个真相源（两张池与两条连接的默认值都从 ``DEFAULT_HEARTBEAT_CMD`` 派生）。
2. 线上码与账本 HEARTBEAT 行**是两条不同的命令**，前者在标准族没有登记行。
3. ``0x0004`` 那条 ``verified=True`` 由 spec 自带的实测块兑现，而不是由"响应为空"这句
   无法兑现的话兑现；它没有解析器，免注册的依据是显式 ``parse: false``。
"""

from __future__ import annotations

import inspect

import pytest

from tstdx.protocol.commands import TIER_L2, Family, by_family, get_command
from tstdx.protocol.registry import PARSERS
from tstdx.tools.codegen import load_spec
from tstdx.transport.async_ import AsyncConnectionPool, AsyncTcpConnection
from tstdx.transport.base import DEFAULT_HEARTBEAT_CMD, TcpConnection
from tstdx.transport.pool import ConnectionPool

pytestmark = pytest.mark.unit

_LEDGER_HEARTBEAT = 0x0004
_SPEC_FILE = "PROTOCOL_SPEC/7709/0x0004_HEARTBEAT.yaml"


def _default_of(target: object, parameter: str) -> object:
    """取某个可调用对象某参数的默认值——"派生自同一真相源"的机械读法。"""
    signature = inspect.signature(target)
    return signature.parameters[parameter].default


def test_probe_code_has_a_single_source_of_truth() -> None:
    """四处默认值全部引用同一个常量，没有第二处把心跳码写成字面量。"""
    for target, parameter in (
        (ConnectionPool.__init__, "heartbeat_cmd"),
        (AsyncConnectionPool.__init__, "heartbeat_cmd"),
        (TcpConnection.ping, "cmd"),
        (AsyncTcpConnection.ping, "cmd"),
    ):
        assert _default_of(target, parameter) is DEFAULT_HEARTBEAT_CMD, target


def test_wire_code_and_ledger_heartbeat_are_two_different_commands() -> None:
    """F-20 记录的是**刻意分叉**，不是遗漏：探活码 0x0002 在标准族无登记行，
    账本里的 HEARTBEAT 行 0x0004 没有默认发送方（只有 ``tstdx probe 0x0004`` 会显式发出）。
    """
    heartbeat = get_command(_LEDGER_HEARTBEAT, Family.STANDARD)
    assert heartbeat is not None
    assert heartbeat.name == "HEARTBEAT"
    assert heartbeat.tier == TIER_L2
    assert heartbeat.cmd != DEFAULT_HEARTBEAT_CMD
    assert get_command(DEFAULT_HEARTBEAT_CMD, Family.STANDARD) is None

    # 标准族里"心跳"只登记一条：探活码不靠同名下行事，账本也不靠它冒充线上码。
    assert [c for c in by_family(Family.STANDARD) if c.name == "HEARTBEAT"] == [heartbeat]


def test_ledger_heartbeat_verified_claim_is_backed_by_measurement() -> None:
    """``verified=True`` 的唯一兑现物是 spec 里的实测块 + 显式不解析声明。"""
    heartbeat = get_command(_LEDGER_HEARTBEAT, Family.STANDARD)
    assert heartbeat is not None
    spec = load_spec(_SPEC_FILE)
    assert (spec.get("status") == "verified") is heartbeat.verified
    assert spec["response"]["parse"] is False
    measured = spec.get("measured")
    assert isinstance(measured, dict), "verified 主张没有实测块背书"
    assert measured["body_length"] > 0, "实测块不得退回「响应体为空」那句假主张"
    answered, _, total = str(measured["hosts_answered"]).partition("/")
    assert 0 < int(answered) <= int(total)


def test_ledger_heartbeat_has_no_parser_registered() -> None:
    """免注册是这条命令唯一的实现证据来源，因此 ``parse: false`` 必须存在。"""
    assert (Family.STANDARD, _LEDGER_HEARTBEAT) not in PARSERS
