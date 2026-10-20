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

    def test_hosts_scan_probes_the_whole_candidate_pool_not_the_top_eight(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """``hosts scan`` 是"重新发现"，必须扫整个候选池。

        排名是证据：只扫 ``resolve_hosts()`` 的 top-8 会让掉出前八的主站永远拿不到
        新证据、再也回不来（自我锁死）。这条判据把"扫全池"钉成行为而不是注释。
        """
        from argparse import Namespace
        from importlib import import_module

        from atst.cli.runtime_commands import _cmd_hosts_scan
        from atst.transport.hosts import POOL_BY_FAMILY
        from atst.transport.speedtest import ProbeResult

        captured: dict[str, object] = {}

        def fake_speedtest_and_save(hosts, **kwargs):  # noqa: ARG001
            captured["hosts"] = list(hosts)
            return [ProbeResult(host=hosts[0].host, port=hosts[0].port, ok=True, rtt_ms=1.0)]

        #: ``atst.transport.speedtest`` 这个名字在包上是**函数**（遮蔽了同名子模块），
        #: 所以替身必须挂在真正的模块对象上。
        monkeypatch.setattr(
            import_module("atst.transport.speedtest"), "speedtest_and_save", fake_speedtest_and_save
        )
        assert _cmd_hosts_scan(Namespace(timeout=1.0)) == 0

        probed = captured["hosts"]
        pool = list(POOL_BY_FAMILY["quotation"])
        assert len(pool) > 8, "内置池不足 8 台时这条判据分不清 top-8 与全池"
        assert {entry.key for entry in probed} == {entry.key for entry in pool}
        assert "已写入" in capsys.readouterr().out
