# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""迁移能力执行体（``migrated_capability``）。

后端分派、参数绑定校验、客户端生命周期都在这里；不再与显式通道执行体混在
同一个 1400 行类里。
"""

from __future__ import annotations

from typing import Any

from ..errors import ValidationError
from ..providers import resolve_channel_adapter
from ..query import QueryPlan
from .capability_contract import binding_for, call_kwargs_for, validate_call


def adapter_class_for(provider_id: str, capability: str) -> type:
    """按绑定声明的 factory 字符串解析 web adapter 类（factory 字段的唯一读取口）。

    P2-4(b)/F3：sources/_session_market 的按能力路由需要拿到 Provider 绑定的
    adapter 类；读取点收在这里（AST 判据
    ``tests/architecture/test_dispatch_targets.py::factory_field_findings`` 钉
    「全包只有执行器的 web_adapter 分支读 factory」），会话层经本函数解析，
    不再自己拆 factory 字符串。
    """
    from importlib import import_module

    from ..errors import CompatibilityError
    from ..providers import bindings_for_provider

    for item in bindings_for_provider(provider_id):
        if item.capability == capability and item.factory:
            module_name, class_name = item.factory.split(":")
            return getattr(import_module(module_name), class_name)
    raise CompatibilityError(
        f"providers 声明中 {provider_id!r} 未绑定能力 {capability!r} 的 web adapter factory；"
        "路由以 providers 声明为准，不做静默回退",
        context={"provider": provider_id, "capability": capability},
    )


def upstream_for(meta: Any) -> str:
    """``web_session`` 后端的真实出数源（P1-4：provider 身份修复）。

    ``provider`` 是路由事实（选了 derived），绑定表里的 ``source`` 才是量纲/口径
    事实（数据其实是 sina 给的）。绑定缺 source 时回退 ``"sina"``，与执行分派
    的同一处缺省一致；非 ``web_session`` 后端没有聚合转手，返回空串——调用方
    以 ``or None`` 折成 ``Provenance.upstream=None``（出处与 provider 同源）。
    执行（``WebQuoteSession`` 构造）与溯源（``executor.execute`` →
    ``Provenance.upstream``）读同一份规则，不再各抄一遍。
    """

    if meta.backend != "web_session":
        return ""
    return str(getattr(meta, "source", "") or "sina")


def _migrated_capability(executor, plan: QueryPlan) -> Any:
    meta = binding_for(plan.provider, plan.channel, plan.spec.capability)
    args, kwargs = executor._call_payload(plan, meta)
    validate_call(
        plan.provider,
        plan.channel,
        plan.spec.capability,
        tuple(args),
        kwargs,
    )
    #: 一次逻辑请求只有一份预算读数：下面每个后端的 socket 超时都取它。
    hop = executor._hop_timeout(plan)

    if meta.backend == "web_session":
        from ..sources.session import WebQuoteSession

        with executor._client_session(WebQuoteSession(upstream_for(meta), timeout=hop)) as session:
            return getattr(session, meta.method)(
                *args, **call_kwargs_for(meta, plan.provider, kwargs)
            )

    if meta.backend == "tdx_client":
        with executor._client_session(executor._tdx_client(hop)) as client:
            if meta.capability == "quotes_concurrent":
                kwargs.setdefault("as_format", "obj")
            return getattr(client, meta.method)(*args, **kwargs)

    if meta.backend == "f10_client":
        from ..client import F10Client

        with executor._client_session(executor._pool_client(F10Client, hop)) as client:
            if meta.capability == "f10":
                return client.parse_text(client.download(*args, **kwargs))
            return list(client.catalog(*args, **kwargs))

    if meta.backend in {"ex_client", "goods_client", "mac_client"}:
        from ..client import ExMarketClient, GoodsClient, MacClient

        family_cls = {
            "ex_client": ExMarketClient,
            "goods_client": GoodsClient,
            "mac_client": MacClient,
        }[meta.backend]
        with executor._client_session(executor._pool_client(family_cls, hop)) as client:
            if meta.capability in {
                "ex_bars",
                "ex_quotes",
                "goods_bars",
                "goods_quotes",
                "mac_quotes",
            }:
                kwargs.setdefault("as_format", "dict")
            return getattr(client, meta.method)(*args, **kwargs)

    if meta.backend == "direct_adapter":
        # adapter 类由 ``atst.providers.CHANNEL_ADAPTERS`` 这一处解析
        adapter_cls = resolve_channel_adapter(plan.provider, meta.channel)
        adapter = adapter_cls(timeout=hop)
        try:
            return getattr(adapter, meta.method)(*args, **kwargs)
        finally:
            close = getattr(adapter, "close", None)
            if callable(close):
                close()

    if meta.backend == "web_adapter":
        return executor._web_adapter_call(meta, args, kwargs, timeout=hop)
    if meta.backend == "composed":
        return executor._composed_call(plan, meta.capability, args, kwargs, timeout=hop)

    raise ValidationError(
        "unknown migrated backend",
        context={"backend": meta.backend},
    )


def _web_adapter_call(
    executor, meta: Any, args: list[Any], kwargs: dict[str, Any], *, timeout: float
) -> Any:
    if not getattr(meta, "factory", ""):
        raise ValidationError(
            "web_adapter binding missing factory metadata",
            context={
                "provider": getattr(meta, "provider", None),
                "capability": getattr(meta, "capability", None),
            },
        )

    import importlib

    module_path, _, class_name = meta.factory.partition(":")
    adapter_cls = getattr(importlib.import_module(module_path), class_name)

    # 分派口径：``MigratedCapabilityBinding.method`` 对 ``web_adapter`` 后端
    # 就是**要调用的适配器方法名**（与 ``web_session`` / ``tdx_client`` 后端
    # 同一语义）。旧实现在此硬编码 ``fetch_bars``，导致任何非 K 线语义的
    # web_adapter 绑定（公告 / 快讯 / 人气榜）一调就 AttributeError——
    # 绑定表里明明写了入口，执行面却不去读它，是典型的「声明与执行分叉」。
    method_name = getattr(meta, "method", "") or "fetch_bars"
    adapter = adapter_cls(timeout=timeout)
    try:
        entry = getattr(adapter, method_name, None)
        if entry is None or not callable(entry):
            raise ValidationError(
                "web_adapter 绑定指向的适配器方法不存在",
                context={
                    "provider": getattr(meta, "provider", None),
                    "capability": getattr(meta, "capability", None),
                    "factory": getattr(meta, "factory", None),
                    "method": method_name,
                },
            )
        #: 全市场类能力（快讯等）不传标的；旧实现裸取 ``args[0]`` 在无参调用时
        #: IndexError。``symbol`` 为空时整个位置参数都不传——那些方法的签名上
        #: 根本没有 ``symbol`` 形参（快讯无从按标的过滤），传 None 会 TypeError。
        symbol = args[0] if args else None
        if symbol is None:
            return entry(**kwargs)
        return entry(symbol, **kwargs)
    finally:
        close = getattr(adapter, "close", None)
        if callable(close):
            close()
