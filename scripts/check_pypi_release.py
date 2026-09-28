#!/usr/bin/env python3
# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""Verify whether an atst version already exists on PyPI with identical artifacts.

This helper makes release retries safe after an irreversible PyPI publication.
It never treats "version exists" as sufficient: every canonical wheel/sdist
filename must be present on PyPI with the exact local SHA-256 digest.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import sys
import urllib.error
import urllib.request

PROJECT = "atst"
PYPI_JSON = "https://pypi.org/pypi/{project}/{version}/json"
EXPECTED_SUFFIXES = (".whl", ".tar.gz")


def _sha256(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_artifacts(dist: pathlib.Path) -> dict[str, str]:
    artifacts: dict[str, str] = {}
    for path in sorted(dist.iterdir()):
        if not path.is_file():
            continue
        if path.name == "SHA256SUMS.txt":
            continue
        if not path.name.startswith(f"{PROJECT}-"):
            continue
        if not path.name.endswith(EXPECTED_SUFFIXES):
            continue
        artifacts[path.name] = _sha256(path)

    wheels = [name for name in artifacts if name.endswith(".whl")]
    sdists = [name for name in artifacts if name.endswith(".tar.gz")]
    if len(wheels) != 1 or len(sdists) != 1 or len(artifacts) != 2:
        raise SystemExit(
            "[PyPI校验失败] dist 必须恰好包含一个 canonical wheel 与一个 canonical sdist"
        )
    return artifacts


def _fetch_release(version: str) -> dict[str, object] | None:
    url = PYPI_JSON.format(project=PROJECT, version=version)
    request = urllib.request.Request(
        url,
        headers={"Accept": "application/json", "User-Agent": "atst-release-verifier/1"},
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise SystemExit(f"[PyPI校验失败] PyPI HTTP {exc.code}: {url}") from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise SystemExit(f"[PyPI校验失败] 无法确认 PyPI 发布状态: {exc}") from exc


def _remote_hashes(payload: dict[str, object]) -> dict[str, str]:
    urls = payload.get("urls")
    if not isinstance(urls, list):
        raise SystemExit("[PyPI校验失败] PyPI JSON 缺少 urls")

    hashes: dict[str, str] = {}
    for item in urls:
        if not isinstance(item, dict):
            continue
        filename = item.get("filename")
        digests = item.get("digests")
        if not isinstance(filename, str) or not isinstance(digests, dict):
            continue
        sha256 = digests.get("sha256")
        if isinstance(sha256, str):
            hashes[filename] = sha256
    return hashes


def check(version: str, dist: pathlib.Path) -> bool:
    local = _canonical_artifacts(dist)
    payload = _fetch_release(version)
    if payload is None:
        print(f"[PyPI] {PROJECT} {version} 尚未发布，将执行 trusted publishing")
        return False

    remote = _remote_hashes(payload)
    missing = sorted(set(local) - set(remote))
    mismatched = sorted(
        name for name, digest in local.items() if remote.get(name) not in (None, digest)
    )
    if missing or mismatched:
        raise SystemExit(
            "[PyPI校验失败] 同版本已存在但制品不一致；禁止覆盖/伪装成功: "
            f"missing={missing}, mismatched={mismatched}"
        )

    print(f"[PyPI] {PROJECT} {version} 已存在，且 canonical 制品 SHA256 完全一致")
    return True


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--version", required=True)
    parser.add_argument("--dist", type=pathlib.Path, required=True)
    parser.add_argument("--github-output", type=pathlib.Path)
    return parser


def main() -> int:
    args = _parser().parse_args()
    exists = check(args.version, args.dist)
    if args.github_output is not None:
        with args.github_output.open("a", encoding="utf-8") as stream:
            stream.write(f"exists={str(exists).lower()}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
