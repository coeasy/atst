# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""命令账本校准测试：tier/verified 状态、status 字段与查询助手、字段形状（F-64）。"""

from __future__ import annotations

from dataclasses import fields

import pytest

from tstdx.protocol.commands import (
    COMMANDS,
    STATUS_OFFLINE,
    STATUS_ONLINE,
    Command,
    by_status,
    get_command,
    stats,
)

pytestmark = pytest.mark.unit

#: 账本形状。F-64 量过：``spec_file``/``request_fields``/``aliases`` 三个字段在 ``tstdx/``
#: 里零读取点（``spec_file`` 唯一的同名命中属于 ``spec_audit.AuditResult``，另一个类），
#: ``summary`` 曾同样无人读——它因此不是"留下的幸存者"，而是本步才被接进
#: ``_guard_offline`` 报错文案的（到达判据见 ``tests/client/test_offline_failfast.py``）。
#: 刻意不套 :func:`tests.support.field_readers.unread_fields`：``name``/``status``/``tier``
#: 这类名字在 ``tstdx/`` 里到处都是，按 owner 变量名扫只会量出假读取点。
#: 于是这里的判据是"形状逐字相等 + 读取点由行为判据证明"。
LEDGER_SHAPE = ("cmd", "name", "family", "tier", "verified", "status", "summary")


class TestLedgerFieldShape:
    """F-64：账本里不得再出现"登记了但没人读"的字段。"""

    def test_command_shape_is_exactly_the_read_fields(self) -> None:
        assert tuple(f.name for f in fields(Command)) == LEDGER_SHAPE

    def test_every_command_carries_a_summary(self) -> None:
        """``summary`` 是 46 条无 PROTOCOL_SPEC 条目命令的唯一描述，且直接进报错文案。"""
        blank = sorted(c.hex for c in COMMANDS.values() if not c.summary.strip())
        assert blank == []


class TestLedgerCalibration:
    """2026-09 账本校准批次（docs/archive/OPTIMIZATION_PLAN.md 批次 A2）。"""

    @pytest.mark.parametrize("cmd", [0x0010, 0x000F, 0x052D, 0x0530, 0x0547])
    def test_golden_backed_commands_verified(self, cmd: int) -> None:
        """有精确解析器/实采样本的命令必须 verified（账本自有升级规则）。

        注意：``0x0537``(MINUTE_TODAY) / ``0x0FC5``(TRADE_TODAY) 的真实记录布局
        尚未由 golden 锁定，账本刻意保持 ``verified=False``（见 da655d5
        “align inferred command registry with specs”）。
        """
        c = get_command(cmd)
        assert c is not None and c.verified

    @pytest.mark.parametrize("cmd", [0x0537, 0x0FC5])
    def test_inferred_commands_not_verified(self, cmd: int) -> None:
        """inferred（真实布局待 golden 锁定）的命令必须 verified=False（da655d5 校准）。"""
        c = get_command(cmd)
        assert c is not None and not c.verified

    @pytest.mark.parametrize("cmd", [0x0FB4, 0x06B9])
    def test_no_sample_commands_not_verified(self, cmd: int) -> None:
        """语料无实采样本的命令不得虚标 verified。"""
        c = get_command(cmd)
        assert c is not None and not c.verified

    def test_offline_facts_recorded(self) -> None:
        """实测下线命令（0x054C/0x053E/0x0450）status=offline。"""
        for cmd in (0x054C, 0x053E, 0x0450, 0x051A, 0x056A, 0x07E5):
            c = get_command(cmd)
            assert c is not None and c.status == STATUS_OFFLINE, hex(cmd)

    def test_degraded_facts_recorded(self) -> None:
        """0x0537 沪市空布局事实 → degraded，但真实布局未由 golden 锁定，故 verified=False。"""
        c = get_command(0x0537)
        assert c is not None and c.status == "degraded" and not c.verified

    def test_default_status_online(self) -> None:
        assert get_command(0x0530).status == STATUS_ONLINE
        assert get_command(0x052D).status == STATUS_ONLINE

    def test_by_status_offline(self) -> None:
        off = by_status(STATUS_OFFLINE)
        assert {c.cmd for c in off} == {
            0x0450,
            0x053E,
            0x054C,
            0x051A,
            0x056A,
            0x07E5,
            0x0FEB,
            0x044D,  # 2026-09-06 实测停答（代码表，东财 clist 兜底）
            0x0FB4,  # 2026-09-06 实测停答（历史分时）
        }
        # family 过滤
        assert all(c.family == "quotation" for c in by_status(STATUS_OFFLINE, "quotation"))

    def test_stats_status_counts(self) -> None:
        st = stats()
        assert st["all.status_offline"] == 9
        assert st["all.status_degraded"] == 2
        assert st["all.status_online"] == st["all.total"] - 11
        # 9 条 verified：0x0004/0x000d/0x000f/0x0010/0x044d/0x044e/0x052d/0x0530/0x0547
        # （0x0537/0x0FC5 为 inferred，布局待 golden 锁定，不计入 verified）
        assert st["quotation.verified"] == 9

    def test_facade_docstring_matches_client_commands(self) -> None:
        """防回归：client 注释命令号必须与实际请求一致（批次 A1）。

        v15 拆包后，请求实现与命令号 docstring 位于 :class:`_ClientMixin`
        （``TdxClient`` 的基类，``tstdx/client/_mixin.py``），故合并其 MRO 上
        全部 ``tstdx.client`` 类的源码一起校验。
        """
        import inspect

        from tstdx.client import TdxClient

        src = "\n".join(
            inspect.getsource(klass)
            for klass in reversed(TdxClient.__mro__)
            if klass.__module__.startswith("tstdx.client")
        )
        assert "0x07E5" in src and "0x051A" in src and "0x056A" in src
        # 漂移命令号不得回潜（0x02CF/0x02EE 为其它实现的习惯号）
        assert "0x02CF" not in src and "0x02EE" not in src
