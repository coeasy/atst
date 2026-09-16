# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""P14-A2/A3 回归：``hosts audit`` CLI 子命令 + ``--hosts-file`` 外部候选注入。

覆盖：
* CLI 子命令注册与参数解析（无网络）；
* ``load_external_hosts`` 三种格式（纯文本 / JSON list / JSON dict）；
* Family 别名映射（quotation/standard/std/7709 等）；
* 错误路径（缺字段 / 非法 family / 文件不存在 / JSON 语法错）；
* ``audit_all`` 注入外部候选后的去重与 notes 记录。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tstdx.cli import build_parser
from tstdx.protocol.commands import Family


# --------------------------------------------------------------------------- #
# 加载模块（scripts 不在包内，需动态导入）
# --------------------------------------------------------------------------- #
def _import_audit_hosts():
    import importlib
    import sys

    scripts_dir = str(Path(__file__).resolve().parents[1] / "scripts")
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    return importlib.import_module("audit_hosts")


audit_hosts_mod = _import_audit_hosts()

#: ``probe`` is dispatched from the implementation module
#: (:mod:`tstdx.tools.host_audit`); ``scripts/audit_hosts.py`` only re-exports
#: it. Patching the implementation module is what actually suppresses network
#: I/O — patching the wrapper would silently be a no-op.
from tstdx.tools import host_audit as host_audit_impl  # noqa: E402


# --------------------------------------------------------------------------- #
# CLI 子命令注册与参数解析
# --------------------------------------------------------------------------- #
@pytest.mark.unit
class TestCliHostsAudit:
    def test_audit_subcommand_registered(self):
        parser = build_parser()
        ns = parser.parse_args(["hosts", "audit"])
        assert ns.hosts_command == "audit"
        assert ns.func is not None

    def test_audit_family_choice_validation(self):
        parser = build_parser()
        ns = parser.parse_args(["hosts", "audit", "--family", "quotation"])
        assert ns.family == ["quotation"]
        ns2 = parser.parse_args(["hosts", "audit", "--family", "quotation", "--family", "f10"])
        assert ns2.family == ["quotation", "f10"]

    def test_audit_family_rejects_unknown(self):
        parser = build_parser()
        with pytest.raises(SystemExit) as excinfo:
            parser.parse_args(["hosts", "audit", "--family", "not_a_family"])
        assert excinfo.value.code == 2

    def test_audit_default_parameters(self):
        parser = build_parser()
        ns = parser.parse_args(["hosts", "audit"])
        assert ns.timeout == 5.0
        assert ns.workers == 16
        assert ns.ranking_file == "~/.tstdx/server_ranking.json"
        assert ns.report == "./host_audit_report.json"
        assert ns.strict is False
        assert ns.quiet is False
        assert ns.no_save_ranking is False
        assert ns.hosts_file is None

    def test_audit_flags_toggle(self):
        parser = build_parser()
        ns = parser.parse_args(
            [
                "hosts",
                "audit",
                "--strict",
                "--quiet",
                "--no-save-ranking",
                "--timeout",
                "1.5",
                "--workers",
                "8",
            ]
        )
        assert ns.strict is True
        assert ns.quiet is True
        assert ns.no_save_ranking is True
        assert ns.timeout == 1.5
        assert ns.workers == 8

    def test_hosts_subcommand_still_includes_list_and_scan(self):
        """回归：既有 hosts list/scan 未被 audit 覆盖。"""
        parser = build_parser()
        for cmd in ("hosts list", "hosts scan", "hosts audit"):
            assert parser.parse_args(cmd.split()).func is not None


# --------------------------------------------------------------------------- #
# load_external_hosts：三种格式 + 别名 + 错误路径
# --------------------------------------------------------------------------- #
@pytest.mark.unit
class TestLoadExternalHosts:
    def test_plain_text_single_line(self, tmp_path: Path):
        p = tmp_path / "h.txt"
        p.write_text("1.2.3.4:7709 quotation 我的主机\n", encoding="utf-8")
        out = audit_hosts_mod.load_external_hosts(p)
        assert Family.STANDARD in out
        assert len(out[Family.STANDARD]) == 1
        e = out[Family.STANDARD][0]
        assert e.host == "1.2.3.4"
        assert e.port == 7709
        assert e.name == "我的主机"

    def test_plain_text_with_comments_and_blank_lines(self, tmp_path: Path):
        p = tmp_path / "h.txt"
        p.write_text(
            "# 注释行\n\n"
            "1.2.3.4:7709 quotation 甲\n"
            "5.6.7.8:7727 ex_quotation 乙 # 行内注释被剥除\n"
            "\n",
            encoding="utf-8",
        )
        out = audit_hosts_mod.load_external_hosts(p)
        assert len(out[Family.STANDARD]) == 1
        assert len(out[Family.EXTENDED]) == 1

    def test_plain_text_default_family_is_standard(self, tmp_path: Path):
        p = tmp_path / "h.txt"
        p.write_text("1.2.3.4:7709\n", encoding="utf-8")
        out = audit_hosts_mod.load_external_hosts(p)
        assert Family.STANDARD in out

    def test_json_list_format(self, tmp_path: Path):
        p = tmp_path / "h.json"
        p.write_text(
            json.dumps(
                [
                    {"host": "1.1.1.1", "port": 7709, "family": "quotation", "name": "A"},
                    {"host": "2.2.2.2", "port": 7727, "family": "ex_quotation", "name": "B"},
                ],
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        out = audit_hosts_mod.load_external_hosts(p)
        assert len(out[Family.STANDARD]) == 1
        assert len(out[Family.EXTENDED]) == 1
        assert out[Family.STANDARD][0].name == "A"

    def test_json_dict_format(self, tmp_path: Path):
        p = tmp_path / "h.json"
        p.write_text(
            json.dumps(
                {
                    "quotation": [
                        {"host": "1.1.1.1", "port": 7709, "name": "A"},
                        "3.3.3.3:7709",
                    ],
                    "f10": [{"host": "4.4.4.4", "port": 7709, "name": "F"}],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        out = audit_hosts_mod.load_external_hosts(p)
        assert len(out[Family.STANDARD]) == 2
        assert len(out[Family.F10]) == 1

    def test_family_aliases_all_resolve(self, tmp_path: Path):
        aliases = {
            "quotation": Family.STANDARD,
            "standard": Family.STANDARD,
            "std": Family.STANDARD,
            "7709": Family.STANDARD,
            "ex_quotation": Family.EXTENDED,
            "extended": Family.EXTENDED,
            "ex": Family.EXTENDED,
            "7727": Family.EXTENDED,
            "mac_quotation": Family.MAC,
            "mac": Family.MAC,
            "goods": Family.GOODS,
            "f10": Family.F10,
        }
        for alias, expected in aliases.items():
            assert audit_hosts_mod._norm_family(alias) == expected

    def test_family_case_insensitive(self):
        # 别名与常量本身均大小写不敏感
        assert audit_hosts_mod._norm_family("Quotation") == Family.STANDARD
        assert audit_hosts_mod._norm_family("QUOTATION") == Family.STANDARD

    def test_unknown_family_raises(self, tmp_path: Path):
        p = tmp_path / "h.txt"
        p.write_text("1.2.3.4:7709 nope_family\n", encoding="utf-8")
        with pytest.raises(ValueError, match="未知协议族"):
            audit_hosts_mod.load_external_hosts(p)

    def test_json_list_missing_host_field_raises(self, tmp_path: Path):
        p = tmp_path / "h.json"
        p.write_text(json.dumps([{"port": 7709}]), encoding="utf-8")
        with pytest.raises(ValueError, match="缺少 host"):
            audit_hosts_mod.load_external_hosts(p)

    def test_json_dict_non_list_value_raises(self, tmp_path: Path):
        p = tmp_path / "h.json"
        p.write_text(json.dumps({"quotation": "not-a-list"}), encoding="utf-8")
        with pytest.raises(ValueError, match="必须为 list"):
            audit_hosts_mod.load_external_hosts(p)

    def test_malformed_json_raises(self, tmp_path: Path):
        p = tmp_path / "h.json"
        p.write_text("[{not json}", encoding="utf-8")
        with pytest.raises(ValueError, match="JSON 解析失败"):
            audit_hosts_mod.load_external_hosts(p)

    def test_plain_text_bad_host_format_raises(self, tmp_path: Path):
        # dict 格式里字符串 host 项缺 ':' → 抛 ValueError
        p = tmp_path / "h.json"
        p.write_text(json.dumps({"quotation": ["badhost"]}), encoding="utf-8")
        with pytest.raises(ValueError, match="host:port"):
            audit_hosts_mod.load_external_hosts(p)

    def test_file_not_found_raises(self, tmp_path: Path):
        with pytest.raises(FileNotFoundError):
            audit_hosts_mod.load_external_hosts(tmp_path / "nope.txt")

    def test_empty_file_returns_empty_dict(self, tmp_path: Path):
        p = tmp_path / "h.txt"
        p.write_text("   \n  # only comment\n", encoding="utf-8")
        out = audit_hosts_mod.load_external_hosts(p)
        # 无有效条目时不返回任何 family key
        assert out == {}


# --------------------------------------------------------------------------- #
# audit_family / audit_all：外部候选注入后的去重与 notes
# --------------------------------------------------------------------------- #
@pytest.mark.unit
class TestAuditFamilyWithExternalHosts:
    def test_additional_hosts_are_deduplicated(self, tmp_path: Path):
        """内置池已有的主站 + additional_hosts 中重复条目 → 只算一次。"""
        from tstdx.transport.hosts import HostEntry

        # 从内置 STANDARD 池取第一台主站，故意重复注入
        built_in = next(e for e in audit_hosts_mod.DEFAULT_HOST_POOL if e.family == Family.STANDARD)
        dup = HostEntry(host=built_in.host, port=built_in.port, family=Family.STANDARD)

        # monkeypatch probe：不发真实网络请求
        def _fake_probe(*args, **kwargs):
            return audit_hosts_mod.ProbeResult(
                host=args[0] if args else kwargs.get("host", ""),
                port=args[1] if len(args) > 1 else kwargs.get("port", 7709),
                ok=False,
                error="no network (unit test)",
            )

        orig_probe = host_audit_impl.probe
        host_audit_impl.probe = _fake_probe
        try:
            audit = audit_hosts_mod.audit_family(
                Family.STANDARD,
                timeout=0.1,
                progress=False,
                additional_hosts=[dup, dup, dup],
            )
        finally:
            host_audit_impl.probe = orig_probe

        # 去重：additional 里的重复条目只算一次
        # 总条数应等于内置池长度（additional 3 个重复被 seen_keys 过滤掉）
        assert audit.total == len(audit_hosts_mod.POOL_BY_FAMILY[Family.STANDARD])

        # 更直接的断言：dup 只出现一次
        dup_key = f"{built_in.host}:{built_in.port}"
        dup_count = sum(1 for r in audit.results if f"{r['host']}:{r['port']}" == dup_key)
        assert dup_count == 1, f"expected dup to appear once, got {dup_count}"

    def test_no_additional_hosts_matches_baseline(self, tmp_path: Path):
        """未提供 additional_hosts 时行为不变（回归）。"""

        def _fake_probe(*args, **kwargs):
            return audit_hosts_mod.ProbeResult(
                host=args[0] if args else "",
                port=args[1] if len(args) > 1 else 7709,
                ok=False,
                error="unit",
            )

        orig_probe = host_audit_impl.probe
        host_audit_impl.probe = _fake_probe
        try:
            audit = audit_hosts_mod.audit_family(Family.STANDARD, timeout=0.1, progress=False)
        finally:
            host_audit_impl.probe = orig_probe

        assert audit.total == len(audit_hosts_mod.POOL_BY_FAMILY[Family.STANDARD])
        assert audit.family == Family.STANDARD
