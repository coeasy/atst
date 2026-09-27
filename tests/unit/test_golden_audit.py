"""atst.tools.golden_audit 单元测试（origin 分级 + L1 真实样本门禁）。

全部离线：仓库内 ``_tmp_golden_test/`` 下构造微型语料（不用 pytest
tmp_path——本环境沙箱禁止扫描点前缀临时目录，惯例同 test_golden_expand）。
L1 门禁用两种方式验证：

* 注入式假账本（``audit(ledger_commands=...)``）—— 精确控制命令集合；
* 真实账本 + 动态构造（先读 ``l1_verified`` 再为每条命令造 real 样本）——
  自适应账本演进，测试不因新增 L1 命令而脆断。
"""

from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

import pytest

from atst.tools.golden_audit import (
    ORIGIN_REAL,
    ORIGIN_SYNTHETIC,
    ORIGIN_UNKNOWN,
    audit,
    classify_origin,
    main,
    scan_corpus,
)

pytestmark = pytest.mark.unit

_TMP_ROOT = Path(__file__).resolve().parent.parent / "_tmp_golden_test"


@dataclass
class FakeCommand:
    """audit(ledger_commands=...) 注入用的最小账本命令。"""

    cmd: int
    name: str
    tier: str = "L1"
    verified: bool = True


@pytest.fixture()
def corpus_root() -> Path:
    _TMP_ROOT.mkdir(exist_ok=True)
    case_dir = _TMP_ROOT / f"audit_{uuid4().hex[:12]}"
    case_dir.mkdir(exist_ok=True)
    yield case_dir
    shutil.rmtree(case_dir, ignore_errors=True)


def _sample(
    root: Path,
    cmd: str,
    name: str,
    *,
    source: str,
    category: int | None = None,
    market: int | None = None,
    code: str = "",
    payload: bytes = b"\x01" * 8,
) -> None:
    ctx: dict[str, object] = {}
    if category is not None:
        ctx["category"] = category
    if market is not None:
        ctx["market"] = market
    if code:
        ctx["code"] = code
    # 目录名含 uuid 片段：同命令多样本不冲突（timestamp 段不参与审计逻辑）
    d = root / f"{cmd}_{uuid4().hex[:8]}" / f"20260101-{uuid4().hex[:6]}"
    d.mkdir(parents=True)
    (d / "payload.bin").write_bytes(payload)
    meta = {
        "schema": 1,
        "source": source,
        "family": "quotation",
        "command": cmd,
        "command_name": name,
        "tag": name,
        "parse_ctx": ctx,
        "response": {
            "payload_len": len(payload),
            "sha256": hashlib.sha256(payload).hexdigest(),
        },
    }
    (d / "meta.json").write_text(json.dumps(meta), encoding="utf-8")


def _l1_keys(root: Path) -> list[str]:
    return list(audit(root)["l1_verified"])


# --------------------------------------------------------------------------- #
# classify_origin
# --------------------------------------------------------------------------- #
class TestClassifyOrigin:
    @pytest.mark.parametrize("src", ["self-captured", "self_captured", "real", "captured"])
    def test_real(self, src: str) -> None:
        assert classify_origin(src) == ORIGIN_REAL

    @pytest.mark.parametrize("src", ["synthetic", "synthetic-v2", "SYNTHETIC"])
    def test_synthetic(self, src: str) -> None:
        assert classify_origin(src) == ORIGIN_SYNTHETIC

    @pytest.mark.parametrize("src", [None, "", "unknown", "external", 123])
    def test_unknown(self, src: object) -> None:
        assert classify_origin(src) == ORIGIN_UNKNOWN  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# scan / audit 聚合
# --------------------------------------------------------------------------- #
class TestScanAndAudit:
    def test_scan_skips_payloadless(self, corpus_root: Path) -> None:
        d = corpus_root / "0x530_x" / "t1"
        d.mkdir(parents=True)
        (d / "meta.json").write_text("{}", encoding="utf-8")  # 无 payload.bin
        assert scan_corpus(corpus_root) == []

    def test_broken_meta_counts_unknown(self, corpus_root: Path) -> None:
        d = corpus_root / "0x530_x" / "t1"
        d.mkdir(parents=True)
        (d / "payload.bin").write_bytes(b"x")
        (d / "meta.json").write_text("{not-json", encoding="utf-8")
        a = audit(corpus_root)
        assert a["summary"]["unknown"] == 1
        assert a["by_command"]["<unparsed>"]["unknown"] == 1

    def test_aggregation_and_missing_real(self, corpus_root: Path) -> None:
        _sample(
            corpus_root,
            "0x52d",
            "SECURITY_BARS",
            source="self-captured",
            category=4,
            market=1,
            code="600000",
        )
        _sample(
            corpus_root,
            "0x52d",
            "SECURITY_BARS",
            source="synthetic",
            category=9,
            market=0,
            code="000001",
        )
        _sample(corpus_root, "0x44e", "SECURITY_COUNT", source="self-captured", market=1)
        _sample(corpus_root, "0x530", "REALTIME_QUOTE", source="synthetic", market=0, code="600000")
        a = audit(
            corpus_root,
            ledger_commands=[
                FakeCommand(0x52D, "SECURITY_BARS"),
                FakeCommand(0x44E, "SECURITY_COUNT"),
                FakeCommand(0x530, "REALTIME_QUOTE"),
            ],
        )
        assert a["summary"] == {"total": 4, "real": 2, "synthetic": 2, "unknown": 0}
        assert a["by_command"]["0x52d"]["real"] == 1
        assert a["by_command"]["0x52d"]["synthetic"] == 1
        # 维度只统计 real 样本：synthetic 的 cat=9/mkt=0 不得混入
        assert a["by_command"]["0x52d"]["categories"] == [4]
        assert a["by_command"]["0x52d"]["markets"] == [1]
        assert a["by_command"]["0x52d"]["n_codes"] == 1
        # 0x530 只有 synthetic → 缺 real
        assert [m["command"] for m in a["missing_real"]] == ["0x530"]
        assert "--plan quotes" in a["hints"]["0x530"]

    def test_capture_hint_for_core_commands(self, corpus_root: Path) -> None:
        _sample(corpus_root, "0x44e", "SECURITY_COUNT", source="synthetic", market=0)
        a = audit(
            corpus_root,
            ledger_commands=[
                FakeCommand(0x44E, "SECURITY_COUNT"),
                FakeCommand(0x000F, "CAPITAL_CHANGES"),
            ],
        )
        assert "--plan core" in a["hints"]["0x44e"]
        assert "--plan core" in a["hints"]["0xf"]  # 0x000F 完全无样本

    def test_market_gaps(self, corpus_root: Path) -> None:
        _sample(
            corpus_root,
            "0x52d",
            "SECURITY_BARS",
            source="self-captured",
            category=4,
            market=1,
            code="600000",
        )
        a = audit(corpus_root, ledger_commands=[FakeCommand(0x52D, "SECURITY_BARS")])
        assert a["market_gaps"] == {"0x52d": [0]}

    def test_kline_categories_real_only(self, corpus_root: Path) -> None:
        for cat in (0, 4, 9):
            _sample(
                corpus_root,
                "0x52d",
                "SECURITY_BARS",
                source="self-captured",
                category=cat,
                market=0,
                code="000001",
            )
        _sample(corpus_root, "0x52d", "SECURITY_BARS", source="synthetic", category=11, market=0)
        a = audit(corpus_root, ledger_commands=[FakeCommand(0x52D, "SECURITY_BARS")])
        assert a["kline_categories_real"] == [0, 4, 9]


# --------------------------------------------------------------------------- #
# CLI / 门禁（真实账本 + 动态构造语料）
# --------------------------------------------------------------------------- #
class TestGateCli:
    def test_gate_passes_when_all_l1_have_real(self, corpus_root: Path) -> None:
        """为账本中每条 L1 verified 命令造一个 real 样本 → 门禁通过。"""
        for key in _l1_keys(corpus_root):
            _sample(corpus_root, key, "FAKE", source="self-captured", market=0, code="600000")
        assert main(["--root", str(corpus_root), "--gate"]) == 0

    def test_gate_fails_on_missing_real(self, corpus_root: Path) -> None:
        """任一 L1 verified 命令只有 synthetic → 门禁退出码 1。"""
        keys = _l1_keys(corpus_root)
        for i, key in enumerate(keys):
            src = "synthetic" if i == 0 else "self-captured"
            _sample(corpus_root, key, "FAKE", source=src, market=0, code="600000")
        assert main(["--root", str(corpus_root), "--gate"]) == 1

    def test_gate_fails_when_command_absent(self, corpus_root: Path) -> None:
        keys = _l1_keys(corpus_root)
        for key in keys[1:]:
            _sample(corpus_root, key, "FAKE", source="self-captured", market=0, code="600000")
        assert main(["--root", str(corpus_root), "--gate"]) == 1

    def test_gate_require_markets(self, corpus_root: Path) -> None:
        """硬门禁过（各 L1 都有 real），但 0x52d 缺 market 0 → 追加门禁拦下。"""
        for key in _l1_keys(corpus_root):
            _sample(corpus_root, key, "FAKE", source="self-captured", market=1, code="600000")
        assert main(["--root", str(corpus_root), "--gate"]) == 0
        assert main(["--root", str(corpus_root), "--gate", "--require-markets"]) == 1

    def test_gate_require_kline_categories(self, corpus_root: Path) -> None:
        for key in _l1_keys(corpus_root):
            _sample(corpus_root, key, "FAKE", source="self-captured", market=0, code="600000")
        # 0x52d 的 real 样本 market=0 无 category → 类别集合为空
        assert (
            main(["--root", str(corpus_root), "--gate", "--require-kline-categories", "0,4"]) == 1
        )

    def test_json_output(self, corpus_root: Path, capsys: pytest.CaptureFixture) -> None:
        _sample(
            corpus_root, "0x530", "REALTIME_QUOTE", source="self-captured", market=0, code="600000"
        )
        assert main(["--root", str(corpus_root), "--json"]) == 0
        data = json.loads(capsys.readouterr().out)
        assert data["summary"]["real"] == 1
        assert "0x530" in data["by_command"]


# --------------------------------------------------------------------------- #
# payload 克隆普查：份数与"证明了几件事"必须分开报
# --------------------------------------------------------------------------- #
class TestPayloadClones:
    def test_identical_payloads_group_and_split_by_origin(self, corpus_root: Path) -> None:
        same = bytes(range(40))
        _sample(corpus_root, "0x530", "REALTIME_QUOTE", source="self-captured", payload=same)
        _sample(corpus_root, "0x530", "REALTIME_QUOTE", source="synthetic", payload=same)
        _sample(corpus_root, "0x530", "REALTIME_QUOTE", source="synthetic", payload=same)
        _sample(corpus_root, "0x530", "REALTIME_QUOTE", source="synthetic", payload=b"\x00" * 40)
        clones = audit(corpus_root)["payload_clones"]
        assert len(clones) == 1, "只有前 3 份字节相同，应恰好一组"
        group = clones[0]
        assert (len(group["samples"]), group["real"], group["synthetic"]) == (3, 1, 2)
        assert group["size"] == 40 and group["commands"] == ["0x530"]

    def test_clone_census_is_report_only_and_leaves_the_gate_alone(self, corpus_root: Path) -> None:
        """克隆再多也不改判：L1 门禁只看"有没有有效 real 样本"，不看份数。"""
        same = bytes(range(200))
        l1 = _l1_keys(corpus_root)
        for key in l1:
            _sample(
                corpus_root,
                key,
                "FAKE",
                source="self-captured",
                market=0,
                code="600000",
                payload=same,
            )
        for _ in range(9):
            _sample(
                corpus_root,
                "0x530",
                "FAKE",
                source="synthetic",
                market=0,
                code="600000",
                payload=same,
            )
        clones = audit(corpus_root)["payload_clones"]
        assert len(clones) == 1 and len(clones[0]["samples"]) == len(l1) + 9
        assert main(["--root", str(corpus_root), "--gate"]) == 0

    def test_distinct_payloads_produce_no_group(self, corpus_root: Path) -> None:
        _sample(corpus_root, "0x530", "A", source="self-captured", payload=b"\x01" * 40)
        _sample(corpus_root, "0x530", "B", source="self-captured", payload=b"\x02" * 40)
        assert audit(corpus_root)["payload_clones"] == []
