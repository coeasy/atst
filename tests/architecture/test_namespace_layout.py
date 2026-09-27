"""V17 Phase 3C guard: the flat root namespace stays flat-capped.

Phase 3C re-rooted every non-contract module into its domain package
(``client/``, ``runtime/``, ``catalog/``). Only the protocol-neutral contract
layer may sit at ``atst/`` root, and the old module paths must not come back
as shims.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

#: Exhaustive allow-list of modules allowed to live directly in ``atst/``.
ROOT_WHITELIST = {
    "__init__.py",
    "__main__.py",
    "query.py",
    "result.py",
    "batch.py",
    "typed_query.py",
    "stream_contract.py",
    "errors.py",
    "error_envelope.py",
    "diagnostics.py",
}

#: old dotted module path -> new dotted module path (clean-break table).
MOVED: dict[str, str] = {
    "atst.client_api": "atst.client.api",
    "atst.client_core": "atst.client.core",
    "atst.direct_provider": "atst.runtime.executor",
    "atst.orchestration": "atst.runtime.orchestration",
    "atst.runtime_audit": "atst.runtime.audit",
    "atst.runtime_identity": "atst.runtime.identity",
    "atst.runtime_provenance": "atst.runtime.provenance",
    "atst.capability_catalog": "atst.catalog.capability",
    "atst.capability_audit": "atst.catalog.capability_audit",
    "atst.provider_api": "atst.catalog.provider_bindings",
    "atst.provider_contract": "atst.catalog.provider_contract",
    "atst.provider_guard": "atst.catalog.provider_guard",
    "atst.provider_audit": "atst.catalog.provider_audit",
    "atst.freshness": "",
    "atst.health": "",
    "atst.failure": "",
}


def test_root_namespace_matches_whitelist() -> None:
    present = {p.name for p in (ROOT / "atst").glob("*.py")}
    assert present == ROOT_WHITELIST, f"unexpected root modules: {sorted(present - ROOT_WHITELIST)}"


def test_moved_modules_are_gone_from_disk() -> None:
    offenders = []
    for old in MOVED:
        path = ROOT.joinpath(*old.split("."))
        if path.with_suffix(".py").exists() or path.is_dir():
            offenders.append(old)
    assert offenders == []


def test_moved_modules_cannot_be_imported() -> None:
    for old in MOVED:
        sys.modules.pop(old, None)
        try:
            importlib.import_module(old)
        except ImportError:
            continue
        raise AssertionError(f"old module path {old!r} is still importable")


def test_new_module_paths_are_importable() -> None:
    for new in MOVED.values():
        if new:
            importlib.import_module(new)
