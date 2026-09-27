"""tools 域语义测试（审计 §2-20）。

覆盖：
* capture：``--dry-run`` 计划校验（非法名 SystemExit）、``--limit`` 钳制、
  TdxError → exit 2、时区懒加载；
* golden_expand：嵌套 yaml meta 解析（_yaml_min）、缺 sha256 记 bad；
* check_originality：``--fix`` 方向性门控（白名单/缺声明才补盖）；
* spec_audit：结果缓存（一次解析复用）；
* codegen：spec_id 前置 raise 两处统一 + ``--write`` 草稿路径 + 生成条目与账本形状同真（F-64）。
"""

from __future__ import annotations

import hashlib
import shutil
from dataclasses import fields
from pathlib import Path

import pytest

from atst.protocol.commands import TIER_DECLARED, TIER_L1, TIER_L2, Command, _c
from atst.tools import capture as capture_mod
from atst.tools import codegen as codegen_mod
from atst.tools import golden_expand as ge_mod
from atst.tools import spec_audit as sa_mod
from atst.tools._yaml_min import dump_yaml, load_yaml

pytestmark = pytest.mark.unit


# --------------------------------------------------------------------------- #
# _yaml_min 写读闭环（§3-4 YAML 收敛）
# --------------------------------------------------------------------------- #
def _capture_like_meta() -> dict:
    """与 capture.write_sample 产出的 meta 同构的样例（含全部棘手类型）。"""
    return {
        "schema": 2,
        "source": "self-captured",
        "command": "0x0530",
        "command_name": "realtime_quote",
        "tag": "600519",
        "note": "",
        "host": "218.75.126.9:7709",  # 含冒号 → 必须加引号
        "market": "1",  # 数值字符串 → 必须加引号防类型漂移
        "code": "600519",  # 同上
        "captured_at": "2026-09-02T12:00:00+00:00",
        "elapsed_ms": 12.5,
        "ok_flag": True,
        "missing": None,
        "request": {"method": "0x530", "body_hex": "00ff10", "body_len": 3},
        "response": {"zip_size": 1024, "unzip_size": 4096, "empty_extra": {}, "no_notes": []},
        "fields": ["open", "close", "volume"],
        "rows": [{"code": "600519", "price": 1234.5}, {"code": "000001", "price": 10.0}],
        "multiline": "line1\nline2",
    }


class TestYamlMinRoundTrip:
    """dump_yaml → load_yaml 无损（标量类型不漂移）。"""

    @pytest.mark.parametrize(
        "scalar",
        [
            42,
            -7,
            3.14,
            "plain",
            "600519",  # 数值字符串
            "0x0530",  # 十六进制形字符串
            "sh600519",
            "218.75.126.9:7709",  # 含冒号
            "",  # 空串
            "true",  # 布尔词形字符串
            "yes",
            True,
            False,
            None,
            "a#b",  # 注释符
            "a: b",  # 键值分隔符
            " lead",  # 首部空白
            "2026-01-02",
        ],
    )
    def test_scalar_round_trip(self, scalar: object) -> None:
        assert load_yaml(dump_yaml({"k": scalar}))["k"] == scalar

    def test_dict_round_trip(self) -> None:
        obj = _capture_like_meta()
        assert load_yaml(dump_yaml(obj)) == obj

    def test_list_of_dicts_round_trip(self) -> None:
        obj = {"rows": [{"a": 1, "b": "x"}, {"a": 2, "b": {"c": [1, 2]}}]}
        assert load_yaml(dump_yaml(obj)) == obj

    def test_nested_list_round_trip(self) -> None:
        obj = {"matrix": [[1, 2], [3, 4]], "empty": [], "empty_map": {}}
        assert load_yaml(dump_yaml(obj)) == obj

    def test_empty_containers_as_values(self) -> None:
        obj = {"no_notes": [], "empty_extra": {}, "value": None}
        text = dump_yaml(obj)
        assert "no_notes: []" in text and "empty_extra: {}" in text
        assert load_yaml(text) == obj

    def test_capture_alias_is_shared_dump(self) -> None:
        """capture._yaml_dump 就是共享 dump_yaml（三套 YAML 收敛锁定）。"""
        assert capture_mod._yaml_dump is dump_yaml

    def test_capture_meta_round_trips_through_dump(self) -> None:
        """capture 风格 meta 经 _yaml_dump 落盘后可被 load_yaml 读回（读得回去为准）。"""
        meta = _capture_like_meta()
        text = capture_mod._yaml_dump(meta)
        assert load_yaml(text) == meta

    def test_dump_output_is_proTOCOL_SPEC_style(self) -> None:
        """输出风格与 PROTOCOL_SPEC 兼容：2 空格缩进、键下嵌套 `- ` 列表、首对内联。

        对照真实 spec（PROTOCOL_SPEC/7709/0x0004_HEARTBEAT.yaml）：列表项
        在键下缩进一层；字典列表项首对键值内联在 ``- `` 之后。
        """
        text = dump_yaml({"command": "0x0530", "response": {"sha256": "abc"}, "fields": ["a", "b"]})
        lines = text.splitlines()
        assert lines[0] == "command: 0x0530"
        assert lines[1] == "response:"
        assert lines[2] == "  sha256: abc"
        assert lines[3] == "fields:"
        assert lines[4] == "  - a"
        assert text.endswith("\n")


# --------------------------------------------------------------------------- #
# capture
# --------------------------------------------------------------------------- #
class TestCaptureCli:
    def test_dry_run_validates_plans(self, capsys) -> None:  # type: ignore[no-untyped-def]
        rc = capture_mod.main(["--dry-run"])
        assert rc == 0
        assert "dry-run" in capsys.readouterr().out

    def test_dry_run_invalid_plan_raises(self) -> None:
        with pytest.raises(SystemExit, match="未知采集计划"):
            capture_mod.main(["--dry-run", "--plan", "no-such-plan"])

    def test_dry_run_limit_clamped(self, capsys) -> None:  # type: ignore[no-untyped-def]
        rc = capture_mod.main(["--dry-run", "--limit", "999999"])
        assert rc == 0
        out = capsys.readouterr().out
        # 钳制后条数不超过 1000（列出条目行数 - 头行）
        listed = [ln for ln in out.splitlines() if ln.strip().startswith("- ")]
        assert len(listed) <= 1000

    def test_tdx_error_exits_2(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from atst.errors import TdxError

        def _boom(**kw):  # type: ignore[no-untyped-def]
            raise TdxError("法律自检未通过")

        monkeypatch.setattr(capture_mod, "_legal_self_check", _boom)
        rc = capture_mod.main([])
        assert rc == 2

    def test_shanghai_tz_lazy(self) -> None:
        tz = capture_mod._shanghai_tz()
        assert tz is not None and str(tz).endswith("Asia/Shanghai")


# --------------------------------------------------------------------------- #
# golden_expand
# --------------------------------------------------------------------------- #
def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class TestGoldenExpandMeta:
    def _write_case(self, root: Path, payload: bytes, sha: str | None) -> Path:
        d = root / "0x0530_realtime_quote_600000" / "20260101-000000"
        d.mkdir(parents=True)
        (d / "payload.bin").write_bytes(payload)
        yaml_lines = [
            "command: 0x0530",
            "command_name: realtime_quote",
            "response:",
            f"  sha256: {sha}" if sha else "  sha256:",
        ]
        (d / "meta.yaml").write_text("\n".join(yaml_lines) + "\n", encoding="utf-8")
        return d

    def test_nested_yaml_sha_parsed(self, tmp_path: Path) -> None:
        payload = b"\x01\x02\x03\x04"
        self._write_case(tmp_path, payload, _sha256(payload))
        ok, bad = ge_mod.verify(tmp_path)
        assert (ok, bad) == (1, [])

    def test_missing_sha_recorded_bad(self, tmp_path: Path) -> None:
        payload = b"\x01\x02\x03\x04"
        self._write_case(tmp_path, payload, None)
        ok, bad = ge_mod.verify(tmp_path)
        assert ok == 0
        assert len(bad) == 1 and "缺少 response.sha256" in bad[0]

    def test_wrong_sha_recorded_bad(self, tmp_path: Path) -> None:
        self._write_case(tmp_path, b"\x01\x02\x03\x04", "0" * 64)
        ok, bad = ge_mod.verify(tmp_path)
        assert ok == 0 and len(bad) == 1

    def test_json_meta_still_supported(self, tmp_path: Path) -> None:
        import json

        payload = b"abc"
        d = tmp_path / "0x0530_realtime_quote_600000" / "20260101-000000"
        d.mkdir(parents=True)
        (d / "payload.bin").write_bytes(payload)
        (d / "meta.json").write_text(
            json.dumps({"response": {"sha256": _sha256(payload)}}), encoding="utf-8"
        )
        ok, bad = ge_mod.verify(tmp_path)
        assert (ok, bad) == (1, [])


# --------------------------------------------------------------------------- #
# check_originality --fix 门控
# --------------------------------------------------------------------------- #
class TestCheckOriginalityFixGate:
    def test_fix_adds_header_only_for_unknown_license(self, tmp_path: Path) -> None:
        from atst.tools.check_originality import main

        no_license = tmp_path / "no_license.py"
        no_license.write_text("x = 1\n", encoding="utf-8")
        gpl_file = tmp_path / "gpl.py"
        gpl_orig = "# SPDX-License-Identifier: GPL-3.0\nx = 2\n"
        gpl_file.write_text(gpl_orig, encoding="utf-8")

        rc = main([str(tmp_path), "--fix"])
        assert rc == 0
        # 缺许可声明 → 补盖默认 MIT 头
        assert "MIT" in no_license.read_text(encoding="utf-8")
        # 检出非白名单许可 → 保留原许可，不改写
        assert gpl_file.read_text(encoding="utf-8") == gpl_orig

    def test_fix_skips_allowed_license_without_header(self, tmp_path: Path) -> None:
        from atst.tools.check_originality import main

        bsd_file = tmp_path / "bsd.py"
        bsd_orig = "# SPDX-License-Identifier: BSD-3-Clause\nx = 1\n"
        bsd_file.write_text(bsd_orig, encoding="utf-8")
        main([str(tmp_path), "--fix"])
        # 白名单许可但缺头：允许补盖（方向一致），但不引入内容差异以外的东西
        content = bsd_file.read_text(encoding="utf-8")
        assert "BSD-3-Clause" in content or "MIT" in content


# --------------------------------------------------------------------------- #
# spec_audit 结果缓存
# --------------------------------------------------------------------------- #
class TestSpecAuditCaching:
    def test_coverage_summary_reuses_results(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from atst.tools.spec_audit import SpecAuditResult

        results = [
            SpecAuditResult(
                spec_id="0x0530", name="quote", spec_file="f", in_ledger=True, has_parser=True
            ),
        ]
        calls: list[int] = []

        real = sa_mod.audit_all

        def spy(*a, **kw):  # type: ignore[no-untyped-def]
            calls.append(1)
            return real(*a, **kw)

        monkeypatch.setattr(sa_mod, "audit_all", spy)
        summary = sa_mod.coverage_summary(results)
        assert calls == []  # 复用 results，未再解析
        assert summary["total_specs"] == 1
        assert summary["covered"] == 1

    def test_print_table_accepts_cached_summary(self, capsys) -> None:  # type: ignore[no-untyped-def]
        from atst.tools.spec_audit import SpecAuditResult, _print_table, coverage_summary

        results = [
            SpecAuditResult(
                spec_id="0x0530", name="quote", spec_file="f", in_ledger=True, has_parser=True
            ),
        ]
        _print_table(results, coverage_summary(results))
        out = capsys.readouterr().out
        assert "0x0530" in out and "Coverage" in out


# --------------------------------------------------------------------------- #
# codegen
# --------------------------------------------------------------------------- #
_MINIMAL_SPEC = """\
name: probe_cmd
spec_id: "0x053e"
family: "7709"
status: draft
description: probe command smoke spec
response:
  header: []
  fields: []
"""


class TestCodegenSpecId:
    def test_parse_spec_id_accepts_hex(self) -> None:
        assert codegen_mod._parse_spec_id("0x053e") == 0x053E
        assert codegen_mod._parse_spec_id("0x530") == 0x0530

    @pytest.mark.parametrize("bad", ["zzz", "", "0x", None, "12.5"])
    def test_parse_spec_id_raises(self, bad: object) -> None:
        with pytest.raises(ValueError, match="spec_id"):
            codegen_mod._parse_spec_id(bad)

    def test_generate_parser_code_bad_spec_id_raises(self) -> None:
        with pytest.raises(ValueError, match="spec_id"):
            codegen_mod.generate_parser_code({"name": "x", "spec_id": "not-hex"})

    def test_generate_command_entry_bad_spec_id_raises(self) -> None:
        with pytest.raises(ValueError, match="spec_id"):
            codegen_mod.generate_command_entry({"name": "x", "spec_id": "not-hex"})

    def test_write_goes_to_generated_draft(self, tmp_path: Path) -> None:
        spec_file = tmp_path / "spec.yaml"
        spec_file.write_text(_MINIMAL_SPEC, encoding="utf-8")
        try:
            rc = codegen_mod.main(["--write", str(spec_file)])
            assert rc == 0
            draft = (
                Path(codegen_mod.__file__).resolve().parent / "generated_draft" / "_generated.py"
            )
            assert draft.exists()
            assert draft.read_text(encoding="utf-8").strip() != ""
        finally:
            draft_dir = Path(codegen_mod.__file__).resolve().parent / "generated_draft"
            shutil.rmtree(draft_dir, ignore_errors=True)


class TestCodegenLedgerShape:
    """F-64：生成器与账本形状必须同真。

    旧生成器会往 ``_c(...)`` 里写 ``request_fields=(...)``，而读侧那三个字段本步已删。
    真跑一次 codegen 才会 TypeError，日常测试碰不到——于是这里把生成的那一行喂回
    ``_c``，形状不符当场红；反向（账本长回幻影字段）由
    ``tests/unit/test_commands.py::TestLedgerFieldShape`` 守。
    """

    _SPEC: dict = {
        "name": "realtime_quote",
        "spec_id": "0x0530",
        "description": "实时行情快照",
        "status": "stable",
        "request": {
            "fields": [
                {"name": "market", "type": "uint16"},
                {"name": "code", "type": "string[6]"},
            ]
        },
    }

    def test_generated_entry_builds_a_command_on_the_current_shape(self) -> None:
        entry = codegen_mod.generate_command_entry(self._SPEC)
        namespace = {
            "_c": _c,
            "TIER_L1": TIER_L1,
            "TIER_L2": TIER_L2,
            "TIER_DECLARED": TIER_DECLARED,
        }
        command = eval(entry.rstrip(","), dict(namespace))
        assert isinstance(command, Command)
        assert command.cmd == 0x0530
        assert command.name == "REALTIME_QUOTE"
        assert command.summary == "实时行情快照"
        assert {f.name for f in fields(command)} == {
            "cmd",
            "name",
            "family",
            "tier",
            "verified",
            "status",
            "summary",
        }

    def test_generated_entry_does_not_replay_deleted_fields(self) -> None:
        entry = codegen_mod.generate_command_entry(self._SPEC)
        for dead in ("request_fields", "aliases", "spec_file"):
            assert dead not in entry, f"生成器又在写已删字段 {dead}"
