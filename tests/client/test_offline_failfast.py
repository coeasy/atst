"""B5 · offline 命令 fail-fast 测试。

覆盖：
* 账本标记 ``STATUS_OFFLINE`` 且无回退豁免的命令 → ``_req`` 立即抛
  :class:`~tstdx.errors.CommandOffline`（不发请求、不吃超时链）；
* 豁免命令（0x054C quotes_snapshot，方法内有逐只 0x0530 回退）不触发
  fail-fast，仍正常进连接池；
* ``CommandOffline`` 为不可重试错误（``default_advice.retryable is False``）；
* 每条真被拦下的命令（offline 与 inferred-block 两类）都把**自己那行** ``summary``
  带进报错文案与 ``context``——账本字段有人读的证据（F-64），不是装饰。
"""

from __future__ import annotations

import pytest

from tstdx.client import TdxClient
from tstdx.client.core import (
    _OFFLINE_FALLBACK_OK,
    _UNVERIFIED_STRUCTURED_BLOCK,
)
from tstdx.errors import CommandOffline, NotImplementedFeature
from tstdx.protocol.commands import (
    CMD,
    STATUS_OFFLINE,
    Family,
    by_status,
    get_command,
)


def _offline_cmd() -> int:
    """取一条无回退豁免的 offline 命令（0x07E5 BLOCK_QUOTES）。"""
    return CMD["block_quotes"]


def _blocked_offline_cmds() -> list[int]:
    """所有会被 ``_guard_offline`` 以 CommandOffline 拦下的标准族命令。"""
    return sorted(
        c.cmd
        for c in by_status(STATUS_OFFLINE, Family.STANDARD)
        if c.cmd not in _OFFLINE_FALLBACK_OK
    )


class _NoRequestPool:
    """哨兵 pool：任何请求到达即测试失败（fail-fast 应在 pool 之前拦截）。"""

    def request(self, cmd: int, body: bytes, *, timeout: float = 3.0) -> bytes:
        raise AssertionError(f"offline 命令 0x{cmd:04X} 不应到达连接池")


class _RecordPool:
    """记录型 pool：豁免命令应能到达这里。"""

    def __init__(self) -> None:
        self.seen: list[int] = []

    def request(self, cmd: int, body: bytes, *, timeout: float = 3.0) -> bytes:
        self.seen.append(cmd)
        return b"\x00" * 16


def _make_client(pool: object) -> TdxClient:
    c = TdxClient.__new__(TdxClient)
    c._pool = pool  # type: ignore[attr-defined]
    c.timeout = 3.0
    c.family = Family.STANDARD
    return c


class TestOfflineFailFast:
    def test_offline_command_raises_immediately(self) -> None:
        c = _make_client(_NoRequestPool())
        with pytest.raises(CommandOffline) as ei:
            c._req(_offline_cmd(), b"", timeout=c.timeout)
        assert "0x07E5" in str(ei.value) or "BLOCK_QUOTES" in str(ei.value)
        assert ei.value.context is not None
        assert ei.value.context["cmd"] == _offline_cmd()

    def test_quotes_snapshot_exempt_from_fail_fast(self) -> None:
        """0x054C 有方法内逐只回退，豁免 fail-fast，正常到达 pool。"""
        pool = _RecordPool()
        c = _make_client(pool)
        c._req(CMD["quotes_snapshot"], b"", timeout=c.timeout)
        assert pool.seen == [CMD["quotes_snapshot"]]

    def test_exactly_nine_offline_commands(self) -> None:
        """账本当前实测下线 9 条（含 0x044D/0x0FB4，2026-09-06 实测）；若增减请同步审视豁免集。"""
        offline = by_status(STATUS_OFFLINE, Family.STANDARD)
        assert len(offline) == 9

    def test_command_not_in_ledger_passes_guard(self) -> None:
        """未登记命令不拦截（交给 UnknownCommand / L2/L3 兜底链）。"""
        pool = _RecordPool()
        c = _make_client(pool)
        assert get_command(0x7FFF) is None
        c._req(0x7FFF, b"", timeout=c.timeout)
        assert pool.seen == [0x7FFF]

    def test_error_not_retryable(self) -> None:
        assert CommandOffline.default_advice.retryable is False


class TestBlockedCommandsSpeakTheirOwnLedgerLine:
    """F-64：账本 ``summary`` 必须出现在拦下这条命令的那句话里。

    分母取账本自身（offline 全集减豁免、inferred-block 全集），不抄命令号清单——
    新增一条下线命令而忘了给它写描述，这里当场红；把 ``summary`` 从报错文案里摘掉，
    每一条拦下它的用例一起红。

    判据刻意读 ``ei.value.message`` 而不是 ``str(ei.value)``：``TdxError.__str__``
    会把 ``context`` 的前六个键拼进字符串（``tstdx/errors.py:135-140``），于是
    ``summary in str(exc)`` 会被 ``context`` 单独满足——变异 M1（只摘文案、留 context）
    实测以 RC=0 溜过。两句分开断言，才是"人读的那句话"与"机器读的那个键"两件事。
    """

    @pytest.mark.parametrize("cmd", _blocked_offline_cmds())
    def test_offline_error_carries_its_summary(self, cmd: int) -> None:
        command = get_command(cmd)
        assert command is not None and command.summary
        c = _make_client(_NoRequestPool())
        with pytest.raises(CommandOffline) as ei:
            c._req(cmd, b"", timeout=c.timeout)
        assert command.summary in ei.value.message
        assert command.name in ei.value.message
        assert ei.value.context is not None
        assert ei.value.context["summary"] == command.summary

    @pytest.mark.parametrize("cmd", sorted(_UNVERIFIED_STRUCTURED_BLOCK))
    def test_structured_block_error_carries_its_summary(self, cmd: int) -> None:
        command = get_command(cmd)
        assert command is not None and command.summary
        c = _make_client(_NoRequestPool())
        with pytest.raises(NotImplementedFeature) as ei:
            c._req(cmd, b"", timeout=c.timeout)
        assert command.summary in ei.value.message
        assert ei.value.context is not None
        assert ei.value.context["summary"] == command.summary

    def test_blocked_denominators_are_not_empty(self) -> None:
        """三把防盲保险之一：扫描自身失明（分母为 0）时必须自曝。"""
        assert len(_blocked_offline_cmds()) == 8
        assert len(_UNVERIFIED_STRUCTURED_BLOCK) == 2
