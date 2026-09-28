from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = _ROOT / "scripts" / "check_pypi_release.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("atst_pypi_release_contract", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _dist(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    dist = tmp_path / "dist"
    dist.mkdir()
    wheel = dist / "atst-1.2.3-py3-none-any.whl"
    sdist = dist / "atst-1.2.3.tar.gz"
    wheel.write_bytes(b"wheel")
    sdist.write_bytes(b"sdist")
    hashes = {
        wheel.name: hashlib.sha256(wheel.read_bytes()).hexdigest(),
        sdist.name: hashlib.sha256(sdist.read_bytes()).hexdigest(),
    }
    return dist, hashes


def _payload(hashes: dict[str, str]) -> dict[str, object]:
    return {
        "urls": [
            {"filename": name, "digests": {"sha256": digest}}
            for name, digest in hashes.items()
        ]
    }


def test_absent_release_is_publishable(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    checker = _load()
    dist, _ = _dist(tmp_path)
    monkeypatch.setattr(checker, "_fetch_release", lambda version: None)

    assert checker.check("1.2.3", dist) is False


def test_identical_existing_release_is_safe_retry(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    checker = _load()
    dist, hashes = _dist(tmp_path)
    monkeypatch.setattr(checker, "_fetch_release", lambda version: _payload(hashes))

    assert checker.check("1.2.3", dist) is True


def test_existing_version_with_mismatched_hash_fails_closed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    checker = _load()
    dist, hashes = _dist(tmp_path)
    remote = dict(hashes)
    remote["atst-1.2.3-py3-none-any.whl"] = "0" * 64
    monkeypatch.setattr(checker, "_fetch_release", lambda version: _payload(remote))

    with pytest.raises(SystemExit, match="制品不一致"):
        checker.check("1.2.3", dist)


def test_existing_version_with_missing_canonical_file_fails_closed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    checker = _load()
    dist, hashes = _dist(tmp_path)
    hashes.pop("atst-1.2.3.tar.gz")
    monkeypatch.setattr(checker, "_fetch_release", lambda version: _payload(hashes))

    with pytest.raises(SystemExit, match="制品不一致"):
        checker.check("1.2.3", dist)


def test_verifier_requires_exactly_one_wheel_and_sdist(tmp_path: Path) -> None:
    checker = _load()
    dist, _ = _dist(tmp_path)
    (dist / "atst-1.2.3-2-py3-none-any.whl").write_bytes(b"duplicate")

    with pytest.raises(SystemExit, match="恰好包含一个"):
        checker._canonical_artifacts(dist)


def test_existing_version_with_extra_remote_artifact_fails_closed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    checker = _load()
    dist, hashes = _dist(tmp_path)
    remote = dict(hashes)
    remote["atst-1.2.3-py2.py3-none-any.whl"] = "1" * 64
    monkeypatch.setattr(checker, "_fetch_release", lambda version: _payload(remote))

    with pytest.raises(SystemExit, match="制品集合/摘要不一致"):
        checker.check("1.2.3", dist)
