# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""命令账本校准测试：tier/verified 状态、status 字段与查询助手。"""

from __future__ import annotations

import pytest

from tstdx.protocol.commands import (
    STATUS_OFFLINE,
    STATUS_ONLINE,
    by_status,
    get_command,
    stats,
)

pytestmark = pytest.mark.unit


class TestLedgerCalibration:
    """2026-09 账本校准批次（docs/archive/OPTIMIZATION_PLAN.md 批次 A2）。"""

    @pytest.mark.parametrize("cmd", [0x0010, 0x0537, 0x0FC5])
    def test_golden_backed_commands_verified(self, cmd: int) -> None:
        """有 golden 实采 + 精确解析器的命令必须 verified（账本自有升级规则）。"""
        c = get_command(cmd)
        assert c is not None and c.verified

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
        """0x0537 沪市空布局事实 → degraded（1.1.0 午休实测）。"""
        c = get_command(0x0537)
        assert c is not None and c.status == "degraded" and c.verified

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
        assert (
            st["quotation.verified"] == 11
        )  # 8 原有 + 0x0010/0x0537/0x0FC5（0x044D verified 保留为历史实测标记）

    def test_facade_docstring_matches_client_commands(self) -> None:
        """防回归：facade 注释命令号必须与 client 实际请求一致（批次 A1）。"""
        import inspect

        from tstdx.client import TdxClient

        src = inspect.getsource(TdxClient)
        assert "0x07E5" in src and "0x051A" in src and "0x056A" in src
        # 漂移命令号不得回潜（0x02CF/0x02EE 为其它实现的习惯号）
        assert "0x02CF" not in src and "0x02EE" not in src

    def test_facade_api_docstrings_no_drift(self) -> None:
        """防回归：UnifiedQuoteAPI docstring 命令号与 client 实际一致（批次 E）。"""
        import inspect

        from tstdx.facade.api import UnifiedQuoteAPI

        src = inspect.getsource(UnifiedQuoteAPI)
        # 旧习惯号漂移（security_list 0x0514 / finance 0x0223 / capital 0x0A03）
        assert "0x0514" not in src
        assert "0x0223" not in src
        assert "0x0A03" not in src
        assert "0x044D" in src and "0x0010" in src and "0x000F" in src
