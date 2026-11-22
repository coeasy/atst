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
    def test_hosts_list_rejects_timeout_because_it_never_connects(self) -> None:
        """``hosts list`` 只读排名文件，不该有 ``--timeout``。

        留一个被忽略的 flag 比不给更糟：用户会以为它在探活，而实际什么都没测。
        （``hosts --timeout N list`` 仍然可解析——那是 ``hosts`` 组的公共参数，
        ``audit`` 真的会用它。）
        """
        parser = build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["hosts", "list", "--timeout", "3"])
        assert parser.parse_args(["hosts", "--timeout", "3", "list"]).func is not None

    def test_hosts_scan_forwards_workers_and_samples(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path
    ) -> None:
        """候选池 70+ 台时并发度必须可调，否则弱网/CI 上跑不完就被当成不可达。"""
        from argparse import Namespace
        from importlib import import_module

        from atst.cli.runtime_commands import _cmd_hosts_scan
        from atst.transport.speedtest import ProbeResult

        captured: dict[str, object] = {}

        def fake_speedtest_and_save(hosts, **kwargs):
            captured.update(kwargs)
            return [ProbeResult(host=hosts[0].host, port=hosts[0].port, ok=True, rtt_ms=1.0)]

        monkeypatch.setattr(
            import_module("atst.transport.speedtest"), "speedtest_and_save", fake_speedtest_and_save
        )
        rc = _cmd_hosts_scan(
            Namespace(
                timeout=2.0,
                workers=32,
                samples=3,
                ranking_file=str(tmp_path / "ranking.json"),
            )
        )
        assert rc == 0
        assert captured["max_workers"] == 32
        assert captured["samples"] == 3
        capsys.readouterr()

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

    @staticmethod
    def _run_scan(monkeypatch: pytest.MonkeyPatch, ranking_file: str, timeout: float = 1.0) -> list:
        """跑一次 ``hosts scan`` 并截获真正被送去测速的候选名单。

        ``ranking_file`` 必须指向临时目录：默认值 ``~/.atst/server_ranking.json`` 是
        本机真实状态，测试读它就会随机器漂移（开发机上有 extras 时通过、CI 上失败）。
        """
        from argparse import Namespace
        from importlib import import_module

        from atst.cli.runtime_commands import _cmd_hosts_scan
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
        assert _cmd_hosts_scan(Namespace(timeout=timeout, ranking_file=ranking_file)) == 0
        return captured["hosts"]

    def test_hosts_scan_probes_the_whole_candidate_pool_not_the_top_eight(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path
    ) -> None:
        """``hosts scan`` 是"重新发现"，必须扫整个内置候选池。

        排名是证据：只扫 ``resolve_hosts()`` 的 top-8 会让掉出前八的主站永远拿不到
        新证据、再也回不来（自我锁死）。这条判据把"扫全池"钉成行为而不是注释。
        """
        from atst.transport.hosts import POOL_BY_FAMILY

        probed = self._run_scan(monkeypatch, str(tmp_path / "ranking.json"))
        pool = list(POOL_BY_FAMILY["quotation"])
        assert len(pool) > 8, "内置池不足 8 台时这条判据分不清 top-8 与全池"
        # 用「包含」而非「相等」：候选池 = 内置池 ∪ 排名 extras，内置池是最小保证。
        # 相等断言会把"多扫了发现进来的主机"误判成回归，而后者正是要的行为。
        assert {entry.key for entry in pool} <= {entry.key for entry in probed}
        assert "已写入" in capsys.readouterr().out

    def test_hosts_scan_also_refreshes_discovered_extras(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path
    ) -> None:
        """``hosts discover`` 写进排名的 extras，必须被 ``scan`` 一起重测。

        否则这些主机只有"被采纳那一刻"的 RTT：掉线了也不会被淘汰，成为冷启动超时
        的来源。这条判据钉住 ``_cmd_hosts_scan`` 里那段 union 逻辑。
        """
        from atst.transport.hosts import HostEntry, RankingStore

        ranking_file = tmp_path / "ranking.json"
        store = RankingStore(str(ranking_file))
        store.save([HostEntry(host="192.0.2.10", port=7709, family="quotation", rtt_ms=12.0)])

        probed = self._run_scan(monkeypatch, str(ranking_file))
        assert "192.0.2.10:7709" in {entry.key for entry in probed}
        # 去重：extras 若与内置池重叠不能出现两次（重复探测会平白多耗一轮超时）。
        keys = [entry.key for entry in probed]
        assert len(keys) == len(set(keys))
        capsys.readouterr()

    def test_hosts_scan_ignores_extras_when_hosts_env_overrides(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path
    ) -> None:
        """``ATST_HOSTS`` 显式覆盖时只扫覆盖名单——用户点名的主站优先于任何自动发现。"""
        from atst.transport.hosts import HostEntry, RankingStore

        ranking_file = tmp_path / "ranking.json"
        RankingStore(str(ranking_file)).save(
            [HostEntry(host="192.0.2.10", port=7709, family="quotation", rtt_ms=12.0)]
        )
        monkeypatch.setenv("ATST_HOSTS", "203.0.113.7:7709")

        probed = self._run_scan(monkeypatch, str(ranking_file))
        keys = {entry.key for entry in probed}
        assert keys == {"203.0.113.7:7709"}
        capsys.readouterr()
