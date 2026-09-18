"""Single-kernel anti-regression guards (v16 Phase 3A/3B).

The v14 execution envelope (``Runtime`` / ``RuntimeGateway`` / ``QueryRequest``
/ DAG planner / ``tstdx.provider`` router) and the never-populated executor
registry trio were physically deleted. These guards pin that deletion so a
second execution seam can never silently reappear.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

DELETED_MODULES = (
    "tstdx.execution",
    "tstdx.execution.semantic",
    "tstdx.execution.planner",
    "tstdx.provider",
    "tstdx.provider.router",
    "tstdx.runtime.gateway",
    "tstdx.runtime.runtime",
    "tstdx.runtime.request",
    "tstdx.runtime.response",
    "tstdx.runtime.bootstrap",
    "tstdx.runtime.stream",
    "tstdx.runtime.typed",
    "tstdx.runtime.context",
    "tstdx.executor_registry",
    "tstdx.executor_bindings",
    "tstdx.executor_binding_registry",
)

DELETED_SYMBOLS = (
    "Runtime",
    "RuntimeGateway",
    "QueryRequest",
    "QueryResponse",
    "create_runtime",
    "request_from_typed",
    "runtime_subscribe",
    "StreamHandle",
    "ExecutionPlanner",
    "SemanticExecutionAdapter",
    "ProviderRouter",
    "resolve_executor",
    "ExecutorBindingRegistry",
)


@pytest.mark.parametrize("module_name", DELETED_MODULES)
def test_deleted_modules_stay_unimportable(module_name: str) -> None:
    sys.modules.pop(module_name, None)
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module(module_name)


@pytest.mark.parametrize("module_name", DELETED_MODULES)
def test_deleted_module_files_are_gone_from_disk(module_name: str) -> None:
    relative = module_name.removeprefix("tstdx.").replace(".", "/")
    assert not (ROOT / "tstdx" / f"{relative}.py").exists()
    assert not (ROOT / "tstdx" / relative).is_dir()


#: F-11 是一次纯改名（clean-break，不留别名）：`facade` 命名指向的 ``UnifiedQuoteAPI``
#: 门面已随 v16 Phase 2 删除，改名后任何旧路径复现都意味着有人重新引入了那层语义。
WEB_MODULE_RENAMES: tuple[tuple[str, str], ...] = (
    ("tstdx.web.facade", "tstdx.web.session"),
    *(
        (f"tstdx.web._facade_mixin_{domain}", f"tstdx.web._session_{domain}")
        for domain in (
            "astock",
            "baidu",
            "efinance",
            "fund_v2",
            "fundamental",
            "info",
            "market",
            "news",
            "p1",
            "p2",
            "p3",
        )
    ),
)


@pytest.mark.parametrize(("old_module", "new_module"), WEB_MODULE_RENAMES)
def test_renamed_web_modules_point_at_the_session_layout(old_module: str, new_module: str) -> None:
    sys.modules.pop(old_module, None)
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module(old_module)
    for relative in (old_module, new_module):
        path = ROOT / "tstdx" / f"{relative.removeprefix('tstdx.').replace('.', '/')}.py"
        if relative == old_module:
            assert not path.exists(), f"{path} 仍在磁盘上，改名未落实"
        else:
            assert path.exists(), f"缺少 {path}"
    module = importlib.import_module(new_module)
    if new_module == "tstdx.web.session":
        # 只有组合模块承载 WebQuoteSession；`_session_*` 是按域的方法分组。
        assert hasattr(module, "WebQuoteSession")


@pytest.mark.parametrize("symbol", DELETED_SYMBOLS)
def test_envelope_symbols_are_not_exported(symbol: str) -> None:
    import tstdx

    runtime_pkg = importlib.import_module("tstdx.runtime")
    assert not hasattr(tstdx, symbol), f"tstdx re-exports deleted symbol {symbol}"
    assert not hasattr(runtime_pkg, symbol), f"tstdx.runtime re-exports deleted symbol {symbol}"


def test_runtime_package_exports_only_the_kernel() -> None:
    runtime_pkg = importlib.import_module("tstdx.runtime")
    assert set(runtime_pkg.__all__) == {"KernelExecutor", "UnifiedRuntime"}


def test_runtime_package_never_reimports_deleted_layers() -> None:
    import ast

    banned = ("tstdx.execution", "tstdx.provider")

    def is_banned(module: str) -> bool:
        # package-boundary match: the live `tstdx.providers` registry is not `tstdx.provider`
        return any(module == name or module.startswith(f"{name}.") for name in banned)

    offenders: list[str] = []
    for path in (ROOT / "tstdx" / "runtime").glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom):
                continue
            pkg = "tstdx.runtime"
            for _ in range(node.level - 1):
                pkg = pkg.rpartition(".")[0]
            module = f"{pkg}.{node.module}" if node.module else pkg
            if is_banned(module):
                offenders.append(f"{path.name}: import {module}")
    assert offenders == []


def test_kernel_is_the_only_execution_seam_of_client() -> None:
    """Client 只经 UnifiedRuntime 执行；kernel 之外不存在第二条 plan 消费路径。"""
    from tstdx.client_api import Client
    from tstdx.runtime.kernel import UnifiedRuntime

    client = Client(runtime=UnifiedRuntime(default_provider="tencent"))
    try:
        assert isinstance(client.runtime, UnifiedRuntime)
        assert type(client.runtime.executor).__name__ == "DirectProviderExecutor"
    finally:
        client.close()
