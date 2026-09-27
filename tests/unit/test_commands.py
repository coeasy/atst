# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""命令账本校准测试：tier/verified 状态、status 字段与查询助手、字段形状（F-64）。"""

from __future__ import annotations

from dataclasses import fields

import pytest

from atst.protocol.commands import (
    CMD,
    COMMANDS,
    STATUS_OFFLINE,
    STATUS_ONLINE,
    Command,
    Family,
    by_family,
    by_status,
    cmd,
    get_command,
    unknown_command_ids,
)

pytestmark = pytest.mark.unit

#: 账本形状。F-64 量过：``spec_file``/``request_fields``/``aliases`` 三个字段在 ``atst/``
#: 里零读取点（``spec_file`` 唯一的同名命中属于 ``spec_audit.AuditResult``，另一个类），
#: ``summary`` 曾同样无人读——它因此不是"留下的幸存者"，而是本步才被接进
#: ``_guard_offline`` 报错文案的（到达判据见 ``tests/client/test_offline_failfast.py``）。
#: 刻意不套 :func:`tests.support.field_readers.unread_fields`：``name``/``status``/``tier``
#: 这类名字在 ``atst/`` 里到处都是，按 owner 变量名扫只会量出假读取点。
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


class TestLedgerQuerySurface:
    """F-65 裁决 (b)：保留的公开查询面必须逐条有用例，而不是"挂在 ``__all__`` 上就算存在"。

    裁决前 ``by_family``/``unknown_command_ids`` 在 ``atst/`` 与 ``tests/`` 里的调用点
    都是 0（``by_family`` 只被 ``unknown_command_ids`` 自己调一次）。一个既没人调、用例
    也不调的名字挂在对外名单上，读者只能靠猜它做什么。
    """

    @pytest.mark.parametrize(
        ("family", "size"),
        [
            (Family.STANDARD, 39),
            (Family.EXTENDED, 17),
            (Family.MAC, 16),
            (Family.GOODS, 11),
            (Family.F10, 2),
        ],
    )
    def test_by_family_yields_exactly_its_own_rows(self, family: str, size: int) -> None:
        rows = list(by_family(family))
        assert len(rows) == size == sum(1 for key in COMMANDS if key[0] == family)
        assert {c.family for c in rows} == {family}
        assert [c.cmd for c in rows] == sorted(c.cmd for c in rows)

    def test_by_family_partitions_the_whole_ledger(self) -> None:
        """五个族常量的并集 = 账本全集：新增一族却忘了写进这里，分母立刻对不上。"""
        families = (
            Family.STANDARD,
            Family.EXTENDED,
            Family.MAC,
            Family.GOODS,
            Family.F10,
        )
        assert sum(len(list(by_family(f))) for f in families) == len(COMMANDS)

    def test_unknown_family_is_empty_not_an_error(self) -> None:
        assert list(by_family("no_such_family")) == []
        assert unknown_command_ids("no_such_family") == []

    def test_unknown_command_ids_are_the_unverified_rows(self) -> None:
        """``unknown`` 的口径是"语义未经 golden 校正"，且返回的是行不是号（文档同口径）。

        2026-09-21（V18 第 9 轮）30 → 32：``0x000F``/``0x0010`` 的 ``verified`` 撤回后落进
        这一格。这一数是"未经校正"的规模，只会长不会缩——账本诚实度的读数不许反向调小。
        """
        rows = unknown_command_ids()
        assert len(rows) == 32
        assert all(isinstance(c, Command) and not c.verified for c in rows)
        assert [c.cmd for c in rows] == sorted(c.cmd for c in rows)
        assert {c.cmd for c in rows} == {
            c.cmd for c in COMMANDS.values() if not c.verified and c.family == Family.STANDARD
        }
        # 尚未有实采样本的四个族，其全部行都是 unknown
        assert set(unknown_command_ids(Family.GOODS)) == set(by_family(Family.GOODS))

    def test_name_lookup_composes_instead_of_scanning(self) -> None:
        """删掉 ``get_command_by_name()`` 不削能力：名字 → 行 = ``get_command(cmd(name), family)``。

        被删的那个是全包唯一对 85 行做线性名字扫描的入口，而它给出的东西这条组合
        已经给得出，且给的是同一个对象。逐行验证，不抽样。
        """
        for (family, number), row in COMMANDS.items():
            assert cmd(row.name.lower()) == number
            assert get_command(number, family) is row

    def test_cmd_names_are_unique_across_families(self) -> None:
        """``cmd()`` 不需要族上下文的前提是名字全局唯一；一旦撞名，这条先红。"""
        names = [c.name.lower() for c in COMMANDS.values()]
        assert len(set(names)) == len(names) == len(CMD) == len(COMMANDS)

    def test_unregistered_name_raises_with_the_ledger_size(self) -> None:
        with pytest.raises(KeyError) as excinfo:
            cmd("no_such_command")
        assert "85" in str(excinfo.value)


class TestLedgerCalibration:
    """2026-09 账本校准批次（docs/archive/OPTIMIZATION_PLAN.md 批次 A2）。"""

    @pytest.mark.parametrize("cmd", [0x052D, 0x0530, 0x0547])
    def test_golden_backed_commands_verified(self, cmd: int) -> None:
        """账本自报 verified 的命令：这一格必须与它声称的证据同时成立。

        升级规则不是"有精确解析器就算"，也不是"``register_parser`` 的 ``tier``
        缺省值就是 L1"。``0x000F`` 当初正是吃了这个缺省：没人写过判断，账本却拿它
        换了 ``tier=L1, verified=True`` 两句声明（V18 第 9 轮撤回，见
        ``tests/unit/test_golden.py`` 的域内合法判据）。今天留在这里的三条，
        ``0x052D``/``0x0530`` 另有缓冲区耗尽 + 区间交叉校验两道实采判据背书。

        注意：``0x0537``(MINUTE_TODAY) / ``0x0FC5``(TRADE_TODAY) 的真实记录布局
        尚未由 golden 锁定，账本刻意保持 ``verified=False``（见 da655d5
        "align inferred command registry with specs"）。
        """
        c = get_command(cmd)
        assert c is not None and c.verified

    @pytest.mark.parametrize("cmd", [0x0537, 0x0FC5, 0x000F, 0x0010])
    def test_inferred_commands_not_verified(self, cmd: int) -> None:
        """inferred（真实布局待 golden 锁定）的命令必须 verified=False（da655d5 校准）。

        ``0x000F``/``0x0010`` 于 V18 第 9 轮加入：F-37② 的裁决一向是"条数可用、
        字段语义不保证"，而账本那半边还写着已验证——两条命令的实采样本重放后
        分别有 1587/581 个字段值落在域外，写回 ``verified=True`` 即红。
        """
        c = get_command(cmd)
        assert c is not None and not c.verified

    @pytest.mark.parametrize("cmd", [0x0FB4, 0x06B9])
    def test_no_sample_commands_not_verified(self, cmd: int) -> None:
        """语料无实采样本的命令不得虚标 verified。"""
        c = get_command(cmd)
        assert c is not None and not c.verified

    def test_offline_facts_recorded(self) -> None:
        """实测下线命令（0x054C/0x053E/0x0450）status=offline。"""
        for number in (0x054C, 0x053E, 0x0450, 0x051A, 0x056A, 0x07E5):
            c = get_command(number)
            assert c is not None and c.status == STATUS_OFFLINE, hex(number)

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

    def test_status_and_verified_counts_read_off_the_ledger(self) -> None:
        """账本三类计数的事实。F-65 删掉了 ``stats()``：那张按族聚合的字典在 ``atst/``
        里零读取点，只有本测试是它的读者——于是这些数字改由**执行面真在用的读法**给出
        （``by_status`` 是 fail-fast 的依据、``by_family`` 是保留的公开查询面），
        而不是由一个只为测试存在的聚合器代读。
        """
        assert len(by_status(STATUS_OFFLINE)) == 9
        assert len(by_status("degraded")) == 2
        assert len(by_status(STATUS_ONLINE)) == len(COMMANDS) - 11
        # 7 条 verified：0x0004/0x000d/0x044d/0x044e/0x052d/0x0530/0x0547
        # （0x0537/0x0FC5 为 inferred，布局待 golden 锁定，不计入 verified；
        #   0x000f/0x0010 于 V18 第 9 轮按 F-37② 撤回——实采样本重放后字段值仍落在域外）
        assert sum(1 for c in by_family(Family.STANDARD) if c.verified) == 7

    def test_facade_docstring_matches_client_commands(self) -> None:
        """防回归：client 注释命令号必须与实际请求一致（批次 A1）。

        v15 拆包后，请求实现与命令号 docstring 位于 :class:`_ClientMixin`
        （``TdxClient`` 的基类，``atst/client/_mixin.py``），故合并其 MRO 上
        全部 ``atst.client`` 类的源码一起校验。
        """
        import inspect

        from atst.client import TdxClient

        src = "\n".join(
            inspect.getsource(klass)
            for klass in reversed(TdxClient.__mro__)
            if klass.__module__.startswith("atst.client")
        )
        assert "0x07E5" in src and "0x051A" in src and "0x056A" in src
        # 漂移命令号不得回潜（0x02CF/0x02EE 为其它实现的习惯号）
        assert "0x02CF" not in src and "0x02EE" not in src
