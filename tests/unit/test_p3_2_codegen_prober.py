# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""P3-2 低覆盖模块补离线单测：spec 脚手架生成器（tools/codegen.py）与
未知命令探测器（protocol/prober.py）。

全部离线：codegen 走 tmp 目录 spec；prober 用假 client（dry-run / 罐头帧 /
异常注入）与 monkeypatch 的交易时段状态，归档落到 tmp_path。
"""

from __future__ import annotations

import struct
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from atst.errors import TdxError
from atst.protocol.prober import Prober, ProbeResult, _byte_stats, _plausible_record_sizes
from atst.tools import codegen

pytestmark = pytest.mark.unit


# --------------------------------------------------------------------------- #
# codegen：辅助函数
# --------------------------------------------------------------------------- #
class TestCodegenHelpers:
    def test_pascal_and_parser_class(self) -> None:
        """#1 snake_case → PascalCase → Parser 类名。"""
        assert codegen._pascal_case("security_bars") == "SecurityBars"
        assert codegen._parser_class_name("security_bars") == "SecurityBarsParser"
        assert codegen._parser_class_name("") == "Parser"

    def test_family_constant_mapping(self) -> None:
        """#2 family → Family 常量；未知值回落 STANDARD。"""
        assert codegen._family_constant("7709") == "Family.STANDARD"
        assert codegen._family_constant("7727") == "Family.EXTENDED"
        assert codegen._family_constant("MAC") == "Family.MAC"
        assert codegen._family_constant("F10") == "Family.F10"
        assert codegen._family_constant("GOODS") == "Family.GOODS"
        assert codegen._family_constant("whatever") == "Family.STANDARD"

    def test_tier_constant_mapping(self) -> None:
        """#3 status → Tier 常量；未知值回落 TIER_DECLARED。"""
        assert codegen._tier_constant("stable") == "TIER_L1"
        assert codegen._tier_constant("verified") == "TIER_L1"
        assert codegen._tier_constant("inferred") == "TIER_L2"
        assert codegen._tier_constant("draft") == "TIER_L2"
        assert codegen._tier_constant("declared") == "TIER_DECLARED"

    def test_read_method_all_types(self) -> None:
        """#4 YAML 类型 → BinaryReader 调用；未知类型 → TODO 注释。"""
        read = codegen._read_method
        assert read("uint8") == "reader.uint8()"
        assert read("int8") == "reader.int8()"
        assert read("uint16") == "reader.uint16()"
        assert read("int16") == "reader.int16()"
        assert read("uint32") == "reader.uint32()"
        assert read("int32") == "reader.int32()"
        assert read("float32") == "reader.float32()"
        assert read("float64") == "reader.float64()"
        assert read("leb128") == "reader.leb128()"
        assert read("tdx_float") == "reader.tdx_float()"
        assert read("varint") == "reader.varint()"
        assert read("bool") == "bool(reader.uint8())"
        assert read("string[6]") == "decode_gbk(reader.bytes(6))"
        assert read("raw[8]") == "reader.bytes(8)"
        assert read("custom_xyz") == "# TODO: read custom_xyz"

    def test_header_size(self) -> None:
        """#5 header 字节数累加；非法/缺失 length 跳过；无 header 为 0。"""
        assert codegen._header_size({"response": {}}) == 0
        spec = {
            "response": {
                "header": [
                    {"name": "a", "length": 2},
                    {"name": "b", "length": "4"},
                    {"name": "c"},  # 无 length → 跳过
                    {"name": "d", "length": "x"},  # 非数值 → 跳过
                    "not-a-dict",  # 非字典 → 跳过
                ]
            }
        }
        assert codegen._header_size(spec) == 6

    def test_parse_spec_id_guard(self) -> None:
        """#6 非法 spec_id 前置 raise。"""
        assert codegen._parse_spec_id("0x0530") == 0x0530
        for bad in ("zzz", None, 3.5):
            with pytest.raises(ValueError, match="spec_id"):
                codegen._parse_spec_id(bad)


# --------------------------------------------------------------------------- #
# codegen：生成器
# --------------------------------------------------------------------------- #
_SPEC = {
    "spec_id": "0x0530",
    "name": "security_bars",
    "family": "7709",
    "description": "测试解析器",
    "status": "stable",
    "response": {
        "record_size": 32,
        "encoding": "gbk",
        "header": [{"name": "record_count", "type": "uint16", "length": 2}],
        "fields": [
            {"name": "price", "type": "uint32"},
            {"name": "code", "type": "string[6]"},
        ],
    },
}


class TestCodegenGenerate:
    def test_generate_parser_code(self) -> None:
        """#7 Parser 骨架：类名/装饰器/RECORD_SIZE/ENCODING/count 循环/字段读取。"""
        code = codegen.generate_parser_code(_SPEC)
        assert "class SecurityBarsParser(BaseParser):" in code
        assert (
            '@register_parser(0x0530, family=Family.STANDARD, name="SECURITY_BARS", head=2, tier="TIER_L1")'
            in code
        )
        assert "RECORD_SIZE = 32" in code
        assert 'ENCODING = "gbk"' in code
        assert "count = reader.uint16()" in code
        assert "for _ in range(count):" in code
        assert "row['price'] = reader.uint32()" in code
        assert "row['code'] = decode_gbk(reader.bytes(6))" in code
        assert "return rows" in code

    def test_generate_parser_code_no_record_count(self) -> None:
        """#8 无 record_count header：字段读取退回 8 空格缩进、无 count 循环。"""
        spec = {
            "spec_id": "0x0001",
            "name": "flat",
            "response": {"fields": [{"name": "v", "type": "uint16"}]},
        }
        code = codegen.generate_parser_code(spec)
        assert "count = reader.uint16()" not in code
        assert "        row['v'] = reader.uint16()" in code

    def test_generate_parser_code_no_fields(self) -> None:
        """#9 无响应字段：生成 TODO 占位。"""
        spec = {"spec_id": "0x0002", "name": "empty"}
        code = codegen.generate_parser_code(spec)
        assert "# TODO: 无响应字段定义" in code
        assert "rows.append({})" in code

    def test_generate_parser_code_bad_spec_id(self) -> None:
        """#10 spec_id 非法：生成器前置报错（不静默吞掉）。"""
        with pytest.raises(ValueError, match="spec_id"):
            codegen.generate_parser_code({**_SPEC, "spec_id": "nothex"})

    def test_generate_command_entry(self) -> None:
        """#11 ledger 条目：命令号十六进制大写、tier/verified 由 status 推导。"""
        assert codegen.generate_command_entry(_SPEC) == (
            '_c(0x0530, "SECURITY_BARS", "测试解析器", tier=TIER_L1, verified=True),'
        )
        draft = {**_SPEC, "status": "draft", "spec_id": "0x2a"}
        assert codegen.generate_command_entry(draft) == (
            '_c(0x002A, "SECURITY_BARS", "测试解析器", tier=TIER_L2, verified=False),'
        )

    def test_load_spec(self, tmp_path: Path) -> None:
        """#12 load_spec：YAML → dict；缺失文件 / 非 dict → 前置报错。"""
        p = tmp_path / "spec.yaml"
        p.write_text(
            'spec_id: "0x0530"\nname: security_bars\nresponse:\n  record_size: 32\n',
            encoding="utf-8",
        )
        spec = codegen.load_spec(str(p))
        assert spec["spec_id"] == "0x0530" and spec["response"]["record_size"] == 32
        with pytest.raises(FileNotFoundError):
            codegen.load_spec(str(tmp_path / "missing.yaml"))
        bad = tmp_path / "list.yaml"
        bad.write_text("- a\n- b\n", encoding="utf-8")
        with pytest.raises(ValueError, match="did not parse to a dict"):
            codegen.load_spec(str(bad))

    def test_load_all_specs_and_generate_output(self, tmp_path: Path) -> None:
        """#13 load_all_specs：批量加载 + 非法/无 id 跳过；_generate_output 去重 import。"""
        good = tmp_path / "7709" / "a.yaml"
        good.parent.mkdir()
        good.write_text(
            'spec_id: "0x0530"\nname: security_bars\nresponse:\n'
            "  header:\n    - name: record_count\n      length: 2\n"
            "  fields:\n    - name: price\n      type: uint32\n",
            encoding="utf-8",
        )
        (tmp_path / "bad.yaml").write_text("- x\n", encoding="utf-8")
        (tmp_path / "noid.yaml").write_text("name: only\n", encoding="utf-8")
        specs = codegen.load_all_specs(str(tmp_path))
        assert set(specs) == {"0x0530"}
        assert specs["0x0530"]["_file"].endswith("7709/a.yaml")

        output = codegen._generate_output(None, str(tmp_path))
        assert output.count("from __future__ import annotations") == 1  # 合并块只保留一份前导
        assert "# _c(0x0530," in output

        single = codegen._generate_output([str(good)], str(tmp_path))
        assert "_c(0x0530," in single

    def test_main_cli(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """#14 CLI：--list 列表、单 spec 打印产物、缺失 spec 报错，退出码 0。"""
        good = tmp_path / "a.yaml"
        good.write_text('spec_id: "0x0c13"\nname: minute_data\n', encoding="utf-8")
        assert codegen.main(["--list", "--spec-dir", str(tmp_path)]) == 0
        assert "0x0c13" in capsys.readouterr().out
        assert codegen.main([str(good)]) == 0
        out = capsys.readouterr().out
        assert "class MinuteDataParser" in out
        with pytest.raises(FileNotFoundError):
            codegen.main([str(tmp_path / "missing.yaml")])
        assert codegen.main(["--list", "--spec-dir", str(tmp_path / "empty")]) == 0
        assert "No specs found" in capsys.readouterr().out


# --------------------------------------------------------------------------- #
# prober：结构分析辅助
# --------------------------------------------------------------------------- #
class TestProbeAnalysis:
    def test_plausible_record_sizes(self) -> None:
        """#15 记录长度候选：整除 + 常见集命中；空 body → 空。"""
        assert _plausible_record_sizes(0) == []
        hits = _plausible_record_sizes(64)
        assert 32 in hits and 16 in hits
        assert _plausible_record_sizes(7) == [1]  # 整除 1 恒命中
        assert 48 in _plausible_record_sizes(48)

    def test_byte_stats(self) -> None:
        """#16 字节统计：熵、可打印比、直方图；空输入全零。"""
        assert _byte_stats(b"") == (0.0, 0.0, {})
        entropy, printable, hist = _byte_stats(b"abab")
        assert entropy == pytest.approx(1.0)
        assert printable == 1.0
        assert hist[ord("a")] == 2 and hist[ord("b")] == 2
        e2, p2, _ = _byte_stats(bytes(range(256)))
        assert e2 == pytest.approx(8.0, abs=1e-4)
        assert p2 == pytest.approx(95 / 256, abs=1e-4)

    def test_probe_result_to_dict(self) -> None:
        """#17 to_dict：命令号十六进制化、raw 头部截断 64 字节。"""
        r = ProbeResult(
            cmd_id=0x053E,
            raw_response=bytes(range(80)),
            frame_size=80,
            plausible_record_sizes=[16, 32],
            ok=True,
        )
        d = r.to_dict()
        assert d["cmd_id"] == "0x053e"
        assert d["raw_response_hex_head"] == bytes(range(64)).hex()
        assert d["plausible_record_sizes"] == [16, 32]
        assert d["ok"] is True


# --------------------------------------------------------------------------- #
# prober：探测主流程
# --------------------------------------------------------------------------- #
class _FakeFrame:
    """ResponseFrame 假形态：header_raw + payload。"""

    def __init__(self, header: bytes, body: bytes) -> None:
        self.header_raw = header
        self.body = body
        self.payload = body


def _header(magic: int = 0x0074CBB1) -> bytes:
    return struct.pack("<I", magic) + b"\x00" * 12  # 16 字节头


class _FakeClient:
    def __init__(self, frame: Any = None, exc: Exception | None = None) -> None:
        self.frame = frame
        self.exc = exc
        self.calls: list[tuple[int, bytes, float | None]] = []

    def request(self, cmd_id: int, body: bytes, **kwargs: Any) -> Any:
        self.calls.append((cmd_id, body, kwargs.get("timeout")))
        if self.exc is not None:
            raise self.exc
        return self.frame


class TestProberGuards:
    def test_rate_limit_guard(self) -> None:
        """#18 rate_limit<=0 前置报错（条件性无限阻塞）。"""
        with pytest.raises(ValueError, match="rate_limit"):
            Prober(rate_limit=0)
        with pytest.raises(ValueError, match="rate_limit"):
            Prober(rate_limit=-1)

    def test_only_offline_hours_by_explicit_now(self) -> None:
        """#19 离线时段守卫：显式时刻判定，盘中拒绝、午休/周末放行。"""
        prober = Prober(client=None)
        assert prober.only_offline_hours(now=datetime(2026, 11, 23, 10, 0)) is False  # 周一盘中
        assert prober.only_offline_hours(now=datetime(2026, 11, 23, 9, 0)) is True  # 早盘前
        assert prober.only_offline_hours(now=datetime(2026, 11, 23, 12, 0)) is True  # 午休
        assert prober.only_offline_hours(now=datetime(2026, 11, 21, 10, 0)) is True  # 周六

    def test_blocked_during_trading_hours_graceful(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """#20 盘中拦截（默认非 strict）：优雅失败、带合规说明、不发请求。"""
        from atst.transport.ratelimit import SessionState

        monkeypatch.setattr(
            "atst.protocol.prober.session_state", lambda now=None: SessionState.CONTINUOUS
        )
        prober = Prober(client=_FakeClient(), rate_limit=1000.0)
        result = prober.probe_command(0x053E)
        assert result.ok is False and result.frame_size == 0
        assert any("blocked during trading hours" in n for n in result.notes)
        assert any("clean-room" in n for n in result.notes)
        assert prober.total_probes == 0  # 请求根本没发

    def test_blocked_strict_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """#21 盘中拦截（strict=True）：保留旧行为抛 TdxError。"""
        from atst.transport.ratelimit import SessionState

        monkeypatch.setattr(
            "atst.protocol.prober.session_state", lambda now=None: SessionState.CONTINUOUS
        )
        prober = Prober(client=_FakeClient(), rate_limit=1000.0, strict=True)
        with pytest.raises(TdxError, match="blocked during trading hours"):
            prober.probe_command(0x053E)


class TestProberProbeCommand:
    def test_dry_run_no_client(self) -> None:
        """#22 无 client dry-run：一致的结果对象 + 说明，不发请求。"""
        prober = Prober(client=None, rate_limit=1000.0)
        result = prober.probe_command(0x053E, market=1, code="600519")
        assert result.ok is False and result.frame_size == 0
        assert any("no client configured" in n for n in result.notes)
        assert result.cmd_id == 0x053E and result.market == 1
        assert prober.total_probes == 1
        assert result.plausible_record_sizes == []
        assert result.byte_histogram == {}

    def test_frame_response_with_header(self) -> None:
        """#23 含头帧：剥 16B 头分析 body，magic 校验通过、默认请求体记录。"""
        body = b"\x01" * 64
        prober = Prober(client=_FakeClient(_FakeFrame(_header(), body)), rate_limit=1000.0)
        result = prober.probe_command(0x053E, market=1, code="600519")
        assert result.ok is True
        assert result.frame_size == 80 and result.body_size == 64
        assert result.plausible_record_size > 0
        assert result.entropy == pytest.approx(0.0)  # 全同字节 → 熵 0
        assert not any("magic mismatch" in n for n in result.notes)
        assert result.request_body == struct.pack("<H", 1) + b"600519"
        assert prober.total_probes == 1

    def test_frame_magic_mismatch_noted(self) -> None:
        """#24 magic 不匹配：写入 notes，不失败。"""
        body = b"\x02" * 40
        prober = Prober(
            client=_FakeClient(_FakeFrame(_header(magic=0xDEADBEEF), body)), rate_limit=1000.0
        )
        result = prober.probe_command(0x053E)
        assert result.ok is True
        assert any("magic mismatch" in n for n in result.notes)

    def test_header_only_frame_notes_empty_payload(self) -> None:
        """#25 仅 16B 头无 body：记 "response has no body"。"""
        prober = Prober(client=_FakeClient(_FakeFrame(_header(), b"")), rate_limit=1000.0)
        result = prober.probe_command(0x053E)
        assert any("no body" in n for n in result.notes)

    def test_headless_payload_not_restripped(self) -> None:
        """#26 无头形态（shim 直接给 payload）：不误剥 16 字节，body_size 全长。"""

        class _Headless:
            payload = b"\x03" * 32
            header_raw = b""

        prober = Prober(client=_FakeClient(_Headless()), rate_limit=1000.0)
        result = prober.probe_command(0x053E)
        assert result.ok is True
        assert result.body_size == 32  # 未被误剥成 16
        assert 32 in result.plausible_record_sizes

    def test_raw_bytes_client(self) -> None:
        """#27 原始字节客户端：request 返回裸 bytes 视为完整帧（含 16B 头）。"""

        class _RawShim:
            def request(self, cmd_id: int, body: bytes):  # noqa: ANN202 - 测试桩
                return _header() + b"\x04" * 16

        prober = Prober(client=_RawShim(), rate_limit=1000.0)
        result = prober.probe_command(0x053E)
        assert result.ok is True and result.body_size == 16
        assert result.frame_size == 32

    def test_client_exception_graceful(self) -> None:
        """#28 客户端异常：notes 记录异常类型 + 失败计数，不抛。"""
        prober = Prober(client=_FakeClient(exc=TimeoutError("t")), rate_limit=1000.0)
        result = prober.probe_command(0x053E)
        assert result.ok is False
        assert any(n.startswith("TimeoutError") for n in result.notes)
        assert prober.total_failures == 1

    def test_send_timeout_contract(self) -> None:
        """#29 _send：签名支持 timeout 才透传；否则省略（不靠 TypeError 重试）。"""
        client = _FakeClient(_FakeFrame(_header(), b"x"))
        prober = Prober(client=client, rate_limit=1000.0)
        prober.probe_command(0x053E)
        assert client.calls[0][2] == prober.timeout

        class _NoTimeoutClient:
            def __init__(self) -> None:
                self.seen_kwargs: dict[str, Any] | None = None

            def request(self, cmd_id: int, body: bytes) -> bytes:
                self.seen_kwargs = {}
                return _header() + b"\x05" * 8

        strict_client = _NoTimeoutClient()
        result = Prober(client=strict_client, rate_limit=1000.0).probe_command(0x053E)
        assert result.ok is True  # TypeError 重试路径未触发（签名预判直接省略 timeout）

    def test_probe_range_clamps(self) -> None:
        """#30 probe_range：命令号个数受 max_count 钳制；max_count<=0 报错。"""
        prober = Prober(client=None, rate_limit=1000.0)
        with pytest.raises(ValueError, match="max_count"):
            prober.probe_range(0x0500, 0x05FF, max_count=0)
        results = prober.probe_range(0x0500, 0x05FF, markets=(0,), max_count=3)
        assert [r.cmd_id for r in results] == [0x0500, 0x0501, 0x0502]
        assert all(r.ok is False for r in results)  # dry-run

    def test_rate_limit_enforced_locally(self) -> None:
        """#31 本地限速：第二次探测间隔至少 1/rate（此处 1/rate=0.05s）。"""
        prober = Prober(client=None, rate_limit=10.0)
        t0 = time.monotonic()
        prober.probe_command(0x053E)
        prober.probe_command(0x053F)
        # 1/10 s 间隔；留 20% 余量吸收 Windows sleep 粒度
        assert time.monotonic() - t0 >= 0.08


class TestProberArchive:
    def test_archive_writes_draft_yaml(self, tmp_path: Path) -> None:
        """#32 archive：DRAFT yaml 落盘，含合规声明与实际请求体 hex。"""
        prober = Prober(client=None, rate_limit=1000.0, archive_dir=tmp_path)
        result = prober.probe_command(0x053E, market=1, code="600519")
        result.notes = ["unit-test note"]
        path = prober.archive(result)
        assert path.exists() and path.name == "053e_DRAFT.yaml"
        text = path.read_text(encoding="utf-8")
        assert 'spec_id: "0x053e"' in text
        assert "clean-room" in text
        assert f'body_hex: "{result.request_body.hex()}"' in text
        assert result.draft_path == str(path)

    def test_archive_no_overwrite_by_default(self, tmp_path: Path) -> None:
        """#33 已有 DRAFT 默认不覆盖（保护人工评审内容）；overwrite=True 才重写。"""
        prober = Prober(client=None, rate_limit=1000.0, archive_dir=tmp_path)
        r1 = prober.probe_command(0x053E)
        path = prober.archive(r1)
        first_text = path.read_text(encoding="utf-8")
        r2 = ProbeResult(cmd_id=0x053E, notes=["changed"], request_body=b"\x01\x02")
        path2 = prober.archive(r2)
        assert path2 == path
        assert path.read_text(encoding="utf-8") == first_text  # 内容未被覆盖
        prober.archive(r2, overwrite=True)
        assert "changed" in path.read_text(encoding="utf-8")
