# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""``hosts discover`` 回归：候选解析容错、族过滤、实测采纳与 CLI 失败路径。

为什么这些判据必须存在（本轮实测依据）：

* 官方 ``connect.cfg`` 真实含 ``Port=0`` 的坏条目——整文件校验会让"发现"在第一步
  就整体失败，所以解析必须逐条跳过；
* 同一份 ``connect.cfg`` 同时带 7709 行情与 7727 扩展，混着测会把 18 台扩展主机记成
  "行情不可达"，所以候选必须按端口归族；
* 采纳是写 ``~/.atst/server_ranking.json`` 的副作用，``--dry-run`` / ``--no-verify``
  必须一格都不写，否则"看一眼"会改变运行行为。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from atst.cli import build_parser, main
from atst.errors import ConfigError
from atst.transport import discover as discover_mod
from atst.transport.hosts import Family, resolve_hosts
from atst.transport.speedtest import ProbeResult

# --------------------------------------------------------------------------- #
# connect.cfg
# --------------------------------------------------------------------------- #
_CFG_TEXT = """[USER]
UserName=

[HQHOST]
HostNum=6
PrimaryHost=1
HostName01=深圳双线主站1
IPAddress01=110.41.147.114
Port01=7709
HostName02=上海双线主站1
IPAddress02=124.223.163.242
Port02=7709
HostName03=坏端口
IPAddress03=1.2.3.4
Port03=0
HostName04=重复条目
IPAddress04=110.41.147.114
Port04=7709
HostName05=空地址
IPAddress05=
Port05=7709
HostName06=扩展市场深圳双线1
IPAddress06=112.74.214.43
Port06=7727
"""


@pytest.mark.unit
class TestConnectCfgParsing:
    def test_bad_entries_are_skipped_not_fatal(self) -> None:
        candidates = discover_mod.parse_connect_cfg_text(_CFG_TEXT)
        assert [(c.host, c.port) for c in candidates] == [
            ("110.41.147.114", 7709),
            ("124.223.163.242", 7709),
            ("112.74.214.43", 7727),
        ]

    def test_family_is_derived_from_port(self) -> None:
        by_port = {c.port: c.family for c in discover_mod.parse_connect_cfg_text(_CFG_TEXT)}
        assert by_port[7709] == Family.STANDARD
        assert by_port[7727] == Family.EXTENDED

    def test_names_survive_gbk_decoding(self, tmp_path: Path) -> None:
        config = tmp_path / "connect.cfg"
        config.write_bytes(_CFG_TEXT.encode("gbk"))
        candidates = discover_mod.read_connect_cfg(config)
        assert candidates[0].name == "深圳双线主站1"

    def test_crlf_line_endings_are_parsed(self, tmp_path: Path) -> None:
        """官方 ``connect.cfg`` 是 CRLF：``$`` 只匹配到 ``\\n`` 之前，行尾必须吃掉 ``\\r``。"""

        config = tmp_path / "connect.cfg"
        config.write_bytes(_CFG_TEXT.replace("\n", "\r\n").encode("gbk"))
        candidates = discover_mod.read_connect_cfg(config)
        assert len(candidates) == 3
        assert candidates[0].name == "深圳双线主站1"

    def test_unreachable_ports_are_dropped(self) -> None:
        text = "IPAddress01=1.1.1.1\nPort01=80\nIPAddress02=2.2.2.2\nPort02=7709\n"
        candidates = discover_mod.parse_connect_cfg_text(text)
        assert [c.host for c in candidates] == ["2.2.2.2"]


# --------------------------------------------------------------------------- #
# 开源清单
# --------------------------------------------------------------------------- #
@pytest.mark.unit
class TestOpenSourceTables:
    def test_python_tuple_table(self) -> None:
        text = (
            "HQ_HOSTS = [\n"
            "    ('深圳双线主站1', '110.41.147.114', 7709),\n"
            "    ('上海主站Z80', '180.153.18.172', 80),\n"
            "    ('扩展1', '112.74.214.43', 7727),\n"
            "]\n"
        )
        candidates = discover_mod.parse_python_host_table(text, source="mootdx")
        assert [(c.host, c.port, c.family) for c in candidates] == [
            ("110.41.147.114", 7709, Family.STANDARD),
            ("112.74.214.43", 7727, Family.EXTENDED),
        ]
        assert {c.source for c in candidates} == {"mootdx"}

    def test_go_struct_table(self) -> None:
        text = (
            "var mainHostList = []HostInfo{\n"
            '    {Name: "通达信深圳双线主站1", IP: "110.41.147.114", Port: 7709},\n'
            '    {Name: "扩展市场深圳双线1", IP: "112.74.214.43", Port: 7727},\n'
            "}\n"
        )
        candidates = discover_mod.parse_go_host_table(text, source="gotdx")
        assert [(c.host, c.name) for c in candidates] == [
            ("110.41.147.114", "通达信深圳双线主站1"),
            ("112.74.214.43", "扩展市场深圳双线1"),
        ]

    def test_json_keeps_custom_ports_but_honours_declared_family(self) -> None:
        text = json.dumps(
            {
                "standard": [
                    "1.2.3.4:7709",
                    {"host": "5.6.7.8", "port": 9000, "name": "自定义"},
                    {"host": "9.9.9.9", "port": 7709, "family": "ex_quotation"},
                ]
            }
        )
        candidates = discover_mod.parse_json_hosts(text, source="file")
        assert [(c.host, c.port, c.family) for c in candidates] == [
            ("1.2.3.4", 7709, Family.STANDARD),
            ("5.6.7.8", 9000, ""),
            ("9.9.9.9", 7709, Family.EXTENDED),
        ]

    def test_merge_keeps_first_source_for_duplicate_endpoint(self) -> None:
        a = [discover_mod.Candidate("1.1.1.1", 7709, source="gotdx")]
        b = [discover_mod.Candidate("1.1.1.1", 7709, source="pytdx")]
        merged = discover_mod.merge_candidates(a, b)
        assert len(merged) == 1
        assert merged[0].source == "gotdx"


# --------------------------------------------------------------------------- #
# 安装目录发现
# --------------------------------------------------------------------------- #
@pytest.mark.unit
class TestFindInstalls:
    def test_finds_nested_stock_layout_and_skips_unrelated_dirs(self, tmp_path: Path) -> None:
        nested = tmp_path / "stock" / "new_tdx64"
        nested.mkdir(parents=True)
        (nested / "connect.cfg").write_bytes(b"[HQHOST]\n")
        direct = tmp_path / "new_tdx"
        direct.mkdir()
        (direct / "connect.cfg").write_bytes(b"[HQHOST]\n")
        unrelated = tmp_path / "photos"
        unrelated.mkdir()

        found = discover_mod.find_tdx_installs([tmp_path])
        assert {path.parent.name for path in found} == {"new_tdx64", "new_tdx"}


# --------------------------------------------------------------------------- #
# 实测与采纳
# --------------------------------------------------------------------------- #
def _probe(host: str, port: int, *, ok: bool) -> ProbeResult:
    return ProbeResult(
        host=host,
        port=port,
        family=Family.STANDARD,
        ok=ok,
        connect_ms=1.0 if ok else None,
        rtt_ms=5.0 if ok else None,
        error="" if ok else "ConnectionFailed",
    )


def _fake_speedtest(hosts, **kwargs):  # noqa: ANN001, ARG001
    return [_probe(entry.host, entry.port, ok=entry.host.endswith(".1")) for entry in hosts]


@pytest.mark.unit
class TestAdoption:
    def test_adopted_hosts_become_ranking_extras(self, tmp_path: Path, monkeypatch) -> None:
        ranking = tmp_path / "ranking.json"
        candidates = tmp_path / "hosts.json"
        candidates.write_text(
            json.dumps({"standard": ["9.9.9.1:7709", "9.9.9.2:7709"]}), encoding="utf-8"
        )
        monkeypatch.setattr(discover_mod, "speedtest", _fake_speedtest)

        result = discover_mod.discover(
            ["file"], path=candidates, ranking_file=str(ranking), timeout=0.01
        )

        assert result.adopted == 1
        assert ranking.exists()
        resolved = resolve_hosts(None, ranking_file=str(ranking), max_hosts=40)
        assert "9.9.9.1:7709" in {entry.key for entry in resolved}

    def test_dry_run_never_touches_the_ranking_file(self, tmp_path: Path, monkeypatch) -> None:
        ranking = tmp_path / "ranking.json"
        candidates = tmp_path / "hosts.json"
        candidates.write_text(json.dumps({"standard": ["9.9.9.1:7709"]}), encoding="utf-8")
        monkeypatch.setattr(discover_mod, "speedtest", _fake_speedtest)

        result = discover_mod.discover(
            ["file"], path=candidates, ranking_file=str(ranking), timeout=0.01, adopt=False
        )

        assert result.reachable == 1
        assert result.adopted == 0
        assert not ranking.exists()

    def test_non_standard_family_is_never_persisted(self, tmp_path: Path) -> None:
        results = [_probe("9.9.9.1", 7727, ok=True)]
        written = discover_mod.adopt_results(
            results, family=Family.EXTENDED, ranking_file=str(tmp_path / "ranking.json")
        )
        assert written == 0
        assert not (tmp_path / "ranking.json").exists()

    def test_fetch_text_rejects_non_http_scheme(self) -> None:
        with pytest.raises(ConfigError):
            discover_mod.fetch_text("ftp://example.com/hosts.txt")


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
@pytest.mark.unit
class TestDiscoverCli:
    def test_subcommand_registered(self) -> None:
        parser = build_parser()
        ns = parser.parse_args(["hosts", "discover", "--no-verify"])
        assert ns.func is not None
        assert ns.family == Family.STANDARD

    def test_no_verify_lists_candidates_without_network(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        candidates = tmp_path / "hosts.json"
        candidates.write_text(json.dumps({"standard": ["1.2.3.4:7709"]}), encoding="utf-8")
        report = tmp_path / "report.json"

        code = main(
            [
                "hosts",
                "discover",
                "--source",
                "file",
                "--path",
                str(candidates),
                "--no-verify",
                "--report",
                str(report),
            ]
        )

        assert code == 0
        assert "未实测" in capsys.readouterr().out
        assert json.loads(report.read_text(encoding="utf-8"))["total"] == 1

    def test_missing_candidate_file_exits_two(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        code = main(
            [
                "hosts",
                "discover",
                "--source",
                "file",
                "--path",
                str(tmp_path / "absent.json"),
                "--report",
                str(tmp_path / "report.json"),
            ]
        )
        # P2-3（M7）：_cmd_* 不再裸 print，错误统一走 main() 信封出口——退出码
        # 语义不变（TdxError 家族 → 2），stderr 变为规范化 JSON 信封。
        assert code == 2
        err = capsys.readouterr().err
        assert '"code": "E1000"' in err
        assert "候选文件不存在" in err

    def test_empty_candidate_set_is_reported_as_such(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """0 台候选要说"没发现"，不能退化成"全部不可达"（两者排查方向相反）。"""

        empty = tmp_path / "empty.json"
        empty.write_text('{"standard": []}', encoding="utf-8")

        code = main(
            [
                "hosts",
                "discover",
                "--source",
                "file",
                "--path",
                str(empty),
                "--report",
                str(tmp_path / "report.json"),
            ]
        )

        assert code == 1
        assert "未发现任何候选" in capsys.readouterr().out
