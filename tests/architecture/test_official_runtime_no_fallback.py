from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]

OFFICIAL_RUNTIME = [
    ROOT / "tstdx" / "service.py",
    ROOT / "tstdx" / "planned_service.py",
    ROOT / "tstdx" / "async_service.py",
    ROOT / "tstdx" / "provider_api.py",
    ROOT / "tstdx" / "providers" / "__init__.py",
    ROOT / "tstdx" / "providers" / "http.py",
    ROOT / "tstdx" / "sources" / "__init__.py",
    ROOT / "tstdx" / "facade" / "planned.py",
    ROOT / "tstdx" / "facade" / "strict.py",
    ROOT / "tstdx" / "facade" / "strict_async.py",
    ROOT / "tstdx" / "integration" / "http_app.py",
    ROOT / "tstdx" / "integration" / "http_runtime.py",
    ROOT / "tstdx" / "integration" / "tasks.py",
]

FORBIDDEN_NAMES = {
    "AllSourcesExhausted",
    "DEFAULT_FALLBACK_ORDER",
    "WebQuoteClient",
    "WebQuoteSession",
    "SourceManager",
    "SourceRegistry",
}


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
        for token in ("WebQuoteClient(", "web_session(", "AllSourcesExhausted("):
            if token in text:
                suspicious.append(f"{path.relative_to(ROOT)}:{token}")
    assert suspicious == []
