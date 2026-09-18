"""V17 Phase 3C guard: the flat root namespace stays flat-capped.

Phase 3C re-rooted every non-contract module into its domain package
(``client/``, ``runtime/``, ``catalog/``). Only the protocol-neutral contract
layer may sit at ``tstdx/`` root, and the old module paths must not come back
as shims.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

#: Exhaustive allow-list of modules allowed to live directly in ``tstdx/``.
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
    "deprecation.py",
}

#: old dotted module path -> new dotted module path (clean-break table).
MOVED: dict[str, str] = {
    "tstdx.client_api": "tstdx.client.api",
    "tstdx.client_core": "tstdx.client.core",
    "tstdx.direct_provider": "tstdx.runtime.executor",
    "tstdx.orchestration": "tstdx.runtime.orchestration",
    "tstdx.runtime_audit": "tstdx.runtime.audit",
    "tstdx.runtime_identity": "tstdx.runtime.identity",
    "tstdx.runtime_provenance": "tstdx.runtime.provenance",
    "tstdx.capability_catalog": "tstdx.catalog.capability",
    "tstdx.capability_audit": "tstdx.catalog.capability_audit",
    "tstdx.provider_api": "tstdx.catalog.provider_bindings",
    "tstdx.provider_contract": "tstdx.catalog.provider_contract",
    "tstdx.provider_guard": "tstdx.catalog.provider_guard",
    "tstdx.provider_audit": "tstdx.catalog.provider_audit",
    "tstdx.freshness": "",
    "tstdx.health": "",
    "tstdx.failure": "",
}


def test_root_namespace_matches_whitelist() -> None:
    present = {p.name for p in (ROOT / "tstdx").glob("*.py")}
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
