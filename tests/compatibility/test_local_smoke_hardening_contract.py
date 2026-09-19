from __future__ import annotations

import importlib
import importlib.util
import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]

_FROM_IMPORT = re.compile(r"from\s+(tstdx[\w.]*)\s+import\s+([A-Za-z_][\w, ]*)")
_PLAIN_IMPORT = re.compile(r"import\s+(tstdx[\w.]*)")

#: 发布链上会执行 import 的两处冒烟（本地 wheel 冒烟 + CI artifact 冒烟）
_SMOKE_SOURCES = (
    _ROOT / "scripts" / "build_package.py",
    _ROOT / ".github" / "workflows" / "wheels.yml",
)


def test_local_wheel_smoke_is_source_isolated_and_checks_all_runtime_hardening() -> None:
    script = (_ROOT / "scripts" / "build_package.py").read_text(encoding="utf-8")

    assert 'work_dir = temp_root / "work"' in script
    assert '[str(python), "-I", "-c", probe]' in script
    assert "package_file.is_relative_to(venv_root)" in script
    assert "TdxClient.__init__.__module__ == 'tstdx.client._pool_binding_hardening'" in script
    assert "AsyncTdxClient.__init__.__module__ == 'tstdx.client._pool_binding_hardening'" in script
    assert "TdxClient.bestip.__module__ == 'tstdx.client._bestip_hardening'" in script
    assert "AsyncTdxClient.bestip.__module__ == 'tstdx.client._bestip_hardening'" in script
    assert (
        "AsyncTdxClient.quotes_concurrent.__module__ == 'tstdx.client._async_concurrency_hardening'"
    ) in script
    assert (
        "ConnectionPool.__init__.__module__ == 'tstdx.transport._pool_family_hardening'" in script
    )
    assert (
        "AsyncConnectionPool.__init__.__module__ == 'tstdx.transport._pool_family_hardening'"
    ) in script
    # 桩层解散后，冒烟断言改判实现住在池模块自身。
    assert "ConnectionPool.request.__module__ == 'tstdx.transport.pool'" in script
    assert "ConnectionPool.update_hosts.__module__ == 'tstdx.transport.pool'" in script
    assert "AsyncConnectionPool.request.__module__ == 'tstdx.transport.async_'" in script
    assert ("AsyncConnectionPool.update_hosts.__module__ == 'tstdx.transport.async_'") in script
    assert "RankingStore.load.__module__ == 'tstdx.transport._ranking_hardening'" in script
    assert "resolve_hosts.__module__ == 'tstdx.transport._host_selector_hardening'" in script


def test_release_smoke_imports_only_symbols_that_still_exist() -> None:
    """发布冒烟里的 import 写在字符串里，路径类守卫看不见它指向已删除的模块。

    `tstdx.facade.UnifiedQuoteAPI` 就是靠这条缝隙在 wheel 冒烟里活了整个 v16：
    磁盘路径守卫只查 `tests/…py` 之类的文件路径，不查 `python -c` 里的模块名。
    """

    from_symbols: dict[str, set[str]] = {}
    plain_modules: set[str] = set()
    for source in _SMOKE_SOURCES:
        text = source.read_text(encoding="utf-8")
        for module, names in _FROM_IMPORT.findall(text):
            from_symbols.setdefault(module, set()).update(
                name.strip() for name in names.split(",") if name.strip()
            )
        plain_modules.update(_PLAIN_IMPORT.findall(text))

    assert from_symbols and plain_modules, "冒烟脚本里的 tstdx import 解析为空，守卫本身失效"
    unresolved = sorted(
        module
        for module in {*from_symbols, *plain_modules}
        if importlib.util.find_spec(module) is None
    )
    assert not unresolved, f"发布冒烟 import 了不存在的模块：{unresolved}"

    missing = []
    for module, names in sorted(from_symbols.items()):
        loaded = importlib.import_module(module)
        missing += [f"{module}.{name}" for name in sorted(names) if not hasattr(loaded, name)]
    assert not missing, f"发布冒烟 import 了不存在的符号：{missing}"
