from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

#: v15 canonical runtime: the Client -> QueryPlan -> UnifiedRuntime path plus the
#: canonical integration surfaces. The legacy v12 integration surfaces
#: (git-ignored, deleted in v15 Phase 2) and the legacy facade compatibility
#: layer are intentionally excluded — the invariant is that the *canonical*
#: runtime never routes through the aggregate web fallback engine.
OFFICIAL_RUNTIME = [
    ROOT / "tstdx" / "client_api.py",
    ROOT / "tstdx" / "runtime_v13.py",
    ROOT / "tstdx" / "query.py",
    ROOT / "tstdx" / "direct_provider.py",
    ROOT / "tstdx" / "orchestration.py",
    ROOT / "tstdx" / "provider_api.py",
    ROOT / "tstdx" / "capability_catalog.py",
    ROOT / "tstdx" / "batch.py",
    ROOT / "tstdx" / "planned_service.py",
    ROOT / "tstdx" / "async_service.py",
    ROOT / "tstdx" / "service.py",
    ROOT / "tstdx" / "providers" / "__init__.py",
    ROOT / "tstdx" / "providers" / "http.py",
    ROOT / "tstdx" / "integration" / "__init__.py",
    ROOT / "tstdx" / "integration" / "runtime_http.py",
    ROOT / "tstdx" / "integration" / "runtime_ws.py",
    ROOT / "tstdx" / "integration" / "runtime_ws_server.py",
    ROOT / "tstdx" / "integration" / "runtime_tasks.py",
    ROOT / "tstdx" / "integration" / "serialization.py",
    ROOT / "tstdx" / "integration" / "mcp" / "_server.py",
    ROOT / "tstdx" / "cli" / "__init__.py",
    ROOT / "tstdx" / "cli" / "runtime_commands.py",
]

#: The aggregate web router and its fallback-order literal must never be reachable
#: from the canonical runtime. ``WebQuoteSession`` (an exact Provider adapter) and
#: ``AllSourcesExhausted`` (an error type) are deliberately *not* forbidden: v15
#: reuses both, and only *aggregate routing* is the architecture violation.
FORBIDDEN_NAMES = {
    "WebQuoteClient",
    "DEFAULT_FALLBACK_ORDER",
    "SourceManager",
    "SourceRegistry",
}

#: Call-shaped tokens that only an aggregate web router would emit.
FORBIDDEN_CALLS = ("WebQuoteClient(", "web_session(")


def _imported_names(tree: ast.AST) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add((alias.asname or alias.name).split(".")[-1])
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                names.add(alias.asname or alias.name)
    return names


def test_official_runtime_does_not_import_legacy_fallback_engine() -> None:
    offenders: list[str] = []
    for path in OFFICIAL_RUNTIME:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        bad = sorted(_imported_names(tree) & FORBIDDEN_NAMES)
        if bad:
            offenders.append(f"{path.relative_to(ROOT)} -> {bad}")
    assert offenders == []


def test_official_runtime_has_no_cross_provider_fallback_literal() -> None:
    """Catch accidental reintroduction of the old fallback list/pipeline."""
    suspicious: list[str] = []
    patterns = (
        "tdx, web, reader, cache",
        "tdx -> web",
        "tdx->web",
        "fallback_to_web=true",
        "default_fallback_order",
    )
    for path in OFFICIAL_RUNTIME:
        text = path.read_text(encoding="utf-8").lower()
        for pattern in patterns:
            if pattern in text:
                suspicious.append(f"{path.relative_to(ROOT)}:{pattern}")
    assert suspicious == []


def test_official_runtime_never_calls_legacy_aggregate_web_client() -> None:
    """Exact Provider adapters may live in tstdx.web; aggregate routing may not."""
    suspicious: list[str] = []
    for path in OFFICIAL_RUNTIME:
        text = path.read_text(encoding="utf-8")
        for token in FORBIDDEN_CALLS:
            if token in text:
                suspicious.append(f"{path.relative_to(ROOT)}:{token}")
    assert suspicious == []
