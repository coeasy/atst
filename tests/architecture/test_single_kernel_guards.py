"""Single-kernel anti-regression guards (v16 Phase 3A/3B).

The v14 execution envelope (``Runtime`` / ``RuntimeGateway`` / ``QueryRequest``
/ DAG planner / ``tstdx.provider`` router) and the never-populated executor
registry trio were physically deleted. These guards pin that deletion so a
second execution seam can never silently reappear.
"""

from __future__ import annotations

import ast
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
    "BatchSpec",
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
    from tstdx.client.api import Client
    from tstdx.runtime.kernel import UnifiedRuntime

    client = Client(runtime=UnifiedRuntime(default_provider="tencent"))
    try:
        assert isinstance(client.runtime, UnifiedRuntime)
        assert type(client.runtime.executor).__name__ == "DirectProviderExecutor"
    finally:
        client.close()


#: 绑定审计里不许出现业务事实（R-2）。字符串字面量是唯一的抓手：`tencent`/`sina`
#: 这类 Provider 名一旦出现在这两个函数体内，就说明"谁能被派发到"这条判断开始
#: 在第二个地方被抄写——注册表与 canonical 表才是唯一事实源。
_AUDITED_FUNCTIONS = ("_is_unified_reachable", "audit_direct_bindings")
_EXECUTOR_SOURCE = ROOT / "tstdx" / "runtime" / "executor.py"


def _string_constants_in(function_name: str) -> set[str]:
    tree = ast.parse(_EXECUTOR_SOURCE.read_text(encoding="utf-8"), filename=str(_EXECUTOR_SOURCE))
    body = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == function_name
    )
    doc = ast.get_docstring(body)
    return {
        node.value
        for node in ast.walk(body)
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value != doc
    }


def test_binding_audit_holds_no_provider_business_facts() -> None:
    from tstdx.providers import PROVIDERS

    providers = set(PROVIDERS.ids()) | {"bars", "quotes"}
    offenders: dict[str, set[str]] = {}
    for name in _AUDITED_FUNCTIONS:
        hits = _string_constants_in(name) & providers
        if hits:
            offenders[name] = hits
    assert offenders == {}, f"绑定审计里出现了业务事实，注册表才是唯一事实源：{offenders}"


def test_the_provider_fact_ruler_sees_a_reintroduced_special_case() -> None:
    """正控：判据必须真的能抓住"把 Provider 名写回审计函数"这一变异。"""
    sample = 'def f():\n    return "tencent" == "tencent" and "bars"\n'
    tree = ast.parse(sample)
    found = {
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }
    assert {"tencent", "bars"} <= found
