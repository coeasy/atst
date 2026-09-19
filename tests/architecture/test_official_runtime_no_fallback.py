from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

#: v16 canonical runtime: Client -> QueryPlan -> zero-cache kernel plus the
#: canonical integration surfaces. Deleted legacy layers (v12 facade / service /
#: sources / cache) cannot appear here; the invariant is that the canonical
#: runtime never routes through an aggregate web fallback engine.
OFFICIAL_RUNTIME = [
    ROOT / "tstdx" / "client" / "api.py",
    ROOT / "tstdx" / "runtime" / "kernel.py",
    ROOT / "tstdx" / "query.py",
    ROOT / "tstdx" / "runtime" / "executor.py",
    ROOT / "tstdx" / "runtime" / "orchestration.py",
    ROOT / "tstdx" / "catalog" / "provider_bindings.py",
    ROOT / "tstdx" / "catalog" / "capability.py",
    ROOT / "tstdx" / "batch.py",
    ROOT / "tstdx" / "streaming" / "base.py",
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


def test_package_defines_no_data_cache_layer() -> None:
    """「零缓存」是运行期事实，所以它必须可门禁，而不是只写在 README 与 docstring 里。

    ``CapitalChangeCache`` 是 Phase 2 删缓存层后留下的孤儿：它自带"命中即跳过
    0x0010 网络与解析"的 TTL + 落盘语义，却在 ``tstdx/`` 里没有任何调用方，
    只有它自己的单测在测它——一个能跳过数据源的形状留在包里，下次接线只需一行。
    纯函数记忆化（``functools.lru_cache``）不在此列：它不省掉任何一次网络请求。
    """
    offenders: list[str] = []
    for path in sorted((ROOT / "tstdx").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            name = getattr(node, "name", None) or ""
            if "cache" not in name.lower():
                continue
            relative = str(path.relative_to(ROOT))
            is_function = isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
            if isinstance(node, ast.ClassDef):
                offenders.append(f"{relative}:class {name}")
            elif is_function and name.startswith("get_"):
                offenders.append(f"{relative}:def {name}()")
    assert offenders == []


def test_pypi_description_claims_no_caching() -> None:
    """发布元数据是对外承诺：删掉缓存层后它仍写着 "semantic caching"。"""
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r"^description\s*=\s*\"([^\"]*)\"", text, flags=re.M)
    assert match is not None, "pyproject 里没有可解析的 description，守卫自身失效"
    description = match.group(1)
    assert "cach" not in description.lower(), f"PyPI 描述仍在宣称缓存：{description}"
