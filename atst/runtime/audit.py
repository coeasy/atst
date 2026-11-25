# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""Unified startup audit entrypoint for the provider-first runtime."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache


@dataclass(frozen=True, slots=True)
class RuntimeAuditReport:
    providers: int
    capabilities: int
    bindings: int


@lru_cache(maxsize=1)
def audit_runtime() -> RuntimeAuditReport:
    """Run all structural runtime audits once per process.

    R-6：内核与执行器初始化都会调用本入口，审计结果是纯声明对账（同一进程内
    不会变化），记忆化后两份调用共享同一份报告。``functools.lru_cache`` 不记忆化异常，
    因此审计失败时每次调用仍会重新执行并原样上抛，行为不变。

    This function validates declarations only. It never changes Provider
    selection and never introduces fallback behaviour.

    P0-9：纳入 ``audit_direct_bindings``（绑定表自查，重复/统一可达缺元数据即红）
    与 executor 方法存在性校验——过去这两道闸只在测试里被调用，``executor_name``
    拼错要到运行期才炸裸 ``AttributeError``，三方对账的 unbound/ghost 两向又是
    同源生成的恒空集，形同虚设。
    """

    from .bindings import audit_direct_bindings
    from .capability_audit import audit_capability_bindings
    from .executor import DirectProviderExecutor
    from .provider_audit import audit_provider_registry

    provider_report = audit_provider_registry()
    capability_report = audit_capability_bindings()
    bindings = audit_direct_bindings()

    #: executor 方法存在性：DIRECT_BINDINGS 的 executor_name 必须在
    #: DirectProviderExecutor 上真实可 getattr，拼错名字启动即红。
    missing = sorted(
        {
            binding.executor_name
            for binding in bindings
            if not hasattr(DirectProviderExecutor, binding.executor_name)
        }
    )
    if missing:
        raise RuntimeError(
            f"DIRECT_BINDINGS 引用了 DirectProviderExecutor 上不存在的方法: {missing!r}；"
            f"绑定表与执行体必须同步，运行期裸 AttributeError 不允许发生"
        )

    return RuntimeAuditReport(
        providers=provider_report.providers,
        capabilities=capability_report.migrated_capabilities,
        bindings=capability_report.executable_bindings,
    )
