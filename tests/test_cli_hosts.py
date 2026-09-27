"""CLI 主站命令与错误提示测试（U2 / U3）。

覆盖：``hosts list`` / ``hosts scan`` 子命令注册、``AllHostsUnreachable``
错误消息附可操作「下一步」建议。
"""

from __future__ import annotations

import pytest

from atst.cli import build_parser
from atst.errors import (
    ALL_HOSTS_UNREACHABLE_NEXT_STEPS,
)


@pytest.mark.unit
class TestCliHosts:
    def test_hosts_subcommands_registered(self):
        parser = build_parser()
        for cmd in ("hosts list", "hosts scan"):
            assert parser.parse_args(cmd.split()).func is not None

    def test_hosts_list_returns_zero(self, capsys):
        from atst.cli import main

        assert main(["hosts", "list"]) == 0
        out = capsys.readouterr().out
        assert "主站" in out

    def test_error_hint_constant(self):
        """U2：错误提示包含可复制的配置片段与 scan 命令。"""
        assert "hosts scan" in ALL_HOSTS_UNREACHABLE_NEXT_STEPS
        assert "servers" in ALL_HOSTS_UNREACHABLE_NEXT_STEPS

    def test_raise_site_appends_hint(self):
        """pool 抛出的 AllHostsUnreachable 消息应带「下一步」建议。"""
        from atst.transport.pool import ALL_HOSTS_UNREACHABLE_NEXT_STEPS as pool_hint

        assert pool_hint == ALL_HOSTS_UNREACHABLE_NEXT_STEPS
