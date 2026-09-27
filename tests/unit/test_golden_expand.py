"""atst.tools.golden_expand 单元测试（D7 工具自身）。

全部离线：在本目录下构造微型语料后调用 manifest/expand/verify。
不用 pytest 的 tmp_path（本环境沙箱禁止扫描点前缀临时目录），
改用仓库内 ``_tmp_golden_test/`` 前缀目录，测试后清理。
"""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from uuid import uuid4

import pytest

from atst.tools.golden_expand import _synthetic_payload, expand, manifest, verify

pytestmark = pytest.mark.unit

_TMP_ROOT = Path(__file__).resolve().parent.parent / "_tmp_golden_test"


@pytest.fixture()
def corpus_root() -> Path:
    """为每个用例建**唯一**子目录。

    .. note::
       旧实现用 ``id(corpus_root) % 100000`` —— ``corpus_root`` 是模块级
       fixture 函数对象，其 ``id()`` 在进程内恒定，于是**所有用例共用同一个
       目录名**，后一个用例撞上前一个未清理的目录 → ``FileExistsError``
       级联。改用 UUID 保证唯一，残留也不会互相干扰。
    """
    _TMP_ROOT.mkdir(exist_ok=True)
    case_dir = _TMP_ROOT / f"case_{uuid4().hex[:12]}"
    case_dir.mkdir(exist_ok=True)
    yield case_dir
    shutil.rmtree(case_dir, ignore_errors=True)


def _make_seed(root: Path, cmd: str, name: str, payload: bytes) -> None:
    d = root / f"{cmd}_{name}" / "20260101-000000"
    d.mkdir(parents=True)
    (d / "payload.bin").write_bytes(payload)
    meta = {
        "schema": 1,
        "source": "self-captured",
        "family": "quotation",
        "command": cmd,
        "command_name": name,
        "tag": name,
        "response": {"payload_len": len(payload), "sha256": hashlib.sha256(payload).hexdigest()},
    }
    (d / "meta.json").write_text(json.dumps(meta), encoding="utf-8")


def test_manifest_counts_real_and_synthetic(corpus_root: Path) -> None:
    _make_seed(corpus_root, "0x530", "REALTIME_QUOTE", b"\x01" * 56)
    m = manifest(corpus_root)
    assert m["total_cases"] == 1
    assert m["self_captured"] == 1
    assert m["synthetic"] == 0


def test_expand_creates_unique_cases(corpus_root: Path) -> None:
    _make_seed(corpus_root, "0x530", "REALTIME_QUOTE", b"\x01" * 56)
    _make_seed(corpus_root, "0x44e", "SECURITY_COUNT", b"\x10\x27")
    expand(per_seed=16, root=corpus_root)
    m = manifest(corpus_root)
    # 2 seeds × 前 16 个 (market, code) 组合 = 32 合成 + 2 实采
    assert m["synthetic"] == 32
    assert m["total_cases"] == 34
    dirs = [p.name for p in corpus_root.iterdir() if p.is_dir()]
    assert len(dirs) == len(set(dirs)) == 34


def test_expand_idempotent_and_extensible(corpus_root: Path) -> None:
    _make_seed(corpus_root, "0x530", "REALTIME_QUOTE", b"\x01" * 56)
    r1 = expand(per_seed=8, root=corpus_root)  # 前 8 个组合
    assert r1["created"] == 8
    r2 = expand(per_seed=8, root=corpus_root)  # 同 8 个组合 → 全部幂等跳过
    assert r2["created"] == 0
    r3 = expand(per_seed=16, root=corpus_root)  # 扩到前 16 个组合 → 新增 8
    assert r3["created"] == 8
    m = manifest(corpus_root)
    assert m["total_cases"] == 17  # 1 实采 + 16 合成
    dirs = [p.name for p in corpus_root.iterdir() if p.is_dir()]
    assert len(dirs) == len(set(dirs)) == 17


def test_verify_detects_corruption(corpus_root: Path) -> None:
    _make_seed(corpus_root, "0x530", "REALTIME_QUOTE", b"\x01" * 56)
    ok, bad = verify(corpus_root)
    assert ok == 1 and bad == []
    payload = next(corpus_root.rglob("payload.bin"))
    payload.write_bytes(b"\x02" * 56)
    ok, bad = verify(corpus_root)
    assert ok == 0 and len(bad) == 1


def test_synthetic_payload_0530_rewrites_echo() -> None:
    # 0x0530 响应布局：offset0=market，offset1..6=回声 code，offset7..8 为不透明区
    seed = bytearray(56)
    seed[0] = 1
    seed[1:7] = b"600000"
    out = _synthetic_payload(bytes(seed), "0x530", 0, "000001")
    assert out[0] == 0  # market 改写
    assert out[1:7] == b"000001"  # 回声 code 改写（offset 1..6）
    assert len(out) == 56  # 长度不变


def teardown_module(module: object) -> None:
    shutil.rmtree(_TMP_ROOT, ignore_errors=True)
