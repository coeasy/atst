from __future__ import annotations

import pytest


def test_top_level_v13_public_api_is_client_first() -> None:
    import atst

    assert atst.Client.__name__ == "Client"
    assert atst.AsyncClient.__name__ == "AsyncClient"
    assert atst.QuerySpec.__name__ == "QuerySpec"
    assert atst.StreamSpec.__name__ == "StreamSpec"
    assert atst.BatchResult.__name__ == "BatchResult"
    assert atst.UnifiedRuntime.__name__ == "UnifiedRuntime"
    assert atst.FallbackPolicy.__name__ == "FallbackPolicy"
    assert atst.ProviderOrchestrator.__name__ == "ProviderOrchestrator"
    assert atst.ErrorEnvelope.__name__ == "ErrorEnvelope"


def test_legacy_business_api_is_not_top_level_public_surface() -> None:
    """历史业务 API 不是顶层官方公开面（v13 §5.3）。

    官方公开面 = ``atst.__all__`` ∪ 惰性导出表 ``atst._LAZY``。
    这里刻意不检查 ``hasattr(atst, "facade")``：任何模块只要 import 过
    ``atst.facade`` 子模块，Python 就会把该属性挂到包上，断言会随测试收集
    顺序漂移（非契约）。
    """
    import atst

    for name in (
        "UnifiedQuoteAPI",
        "quote_api",
        "TdxClient",
        "AsyncTdxClient",
        "WebQuoteClient",
        "DirectProviderExecutor",
        "facade",
    ):
        assert name not in atst.__all__
        assert name not in atst._LAZY

    # 非子模块名还必须真的不可从包命名空间直达。
    for name in ("UnifiedQuoteAPI", "quote_api", "TdxClient", "WebQuoteClient"):
        assert not hasattr(atst, name)


def test_legacy_compat_namespaces_are_physically_removed() -> None:
    """v16 clean-break：`atst/runtime/gateway.py` 等信封层已删除，兼容层物理删除，只留 Client 一个业务入口。"""
    import importlib

    for module in ("atst.facade", "atst.service", "atst.planned_service", "atst.sources"):
        with pytest.raises(ModuleNotFoundError):
            importlib.import_module(module)
