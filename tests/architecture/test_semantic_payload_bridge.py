# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""语义路径的入参必须真的抵达实现（第 30 轮 30-B，G45）。

第 2 遍实测暴露的断链形状是：注册表声明 ``tencent`` / ``baidu`` / ``eastmoney`` 提供
``minute``，接口文档照此推荐「分时改用声明该能力的 Web Provider」，而
``Client.minute("000001", provider="tencent")`` 在规划通过之后、任何 I/O 之前撞上
``[E1010] capability 'minute' 参数不符合 v13 contract：missing a required argument:
'symbol'``。原因不是参数写错，而是**根本没人生成参数**：核心能力的便捷方法把请求写在
:attr:`tstdx.query.QuerySpec.symbols` 这类语义字段里，而 ``_migrated_capability`` 只认
``options["args"] / options["kwargs"]`` 那套 raw payload 约定。两套约定各自成立，中间
那一格没人接。

判据因此不看"实现看起来写了什么"，只看一件事：**每个从语义路径可达的执行格，按
QuerySpec 的语义字段派生出的入参，能不能绑定到它自己的实现签名上**。绑定不上就是断链，
无论断在哪一侧。

变异台账（第 30 轮 30-B 在同一个 0.2s 的跑批里逐条演示，改前必须红）：
- **M1** 从 :data:`tstdx.runtime.executor._SEMANTIC_CALL_FIELDS` 去掉 ``symbols`` →
  5 个格子的绑定判据 + 字段表判据 + 批量代码判据全红；
- **M2** 把 ``_SEMANTIC_FIELD_RENAMES`` 的目标从 ``symbol`` 改成 ``code`` → 绑定判据红；
- **M3** 让 ``MinuteSource.fetch_minute`` 多收一个必填形参 ``date`` →
  ``[tencent-minute-minute]`` 红（报 ``missing a required argument: 'date'``）；
- **M4** 把"无落脚点字段"的闸放宽成静默丢掉 → 幻影旋钮判据红；
- **M5** 让批量代码静默取第一只 → 批量判据红；
- **M6** 让 :func:`~tstdx.catalog.capability.implementation_for` 不认识 ``direct_adapter``
  （即宿主类再被抄第二份的形状）→ 绑定与交叉核对判据红。

"""

from __future__ import annotations

from dataclasses import fields
from typing import Any

import pytest

from tstdx.catalog.capability import binding_for, implementation_for, validate_call
from tstdx.client.api import _CORE_CAPABILITIES
from tstdx.errors import ValidationError
from tstdx.query import QueryPlanner, QuerySpec
from tstdx.runtime.executor import (
    _SEMANTIC_CALL_FIELDS,
    _SEMANTIC_FIELD_RENAMES,
    DIRECT_BINDINGS,
    _semantic_call_payload,
    audit_direct_bindings,
)

#: 语义路径上一条最小查询的形状：核心能力的便捷方法都只写 symbols + 各自缺省。
_PROBE_SYMBOL = "000001"


def _compile(capability: str, provider: str, **kwargs: Any) -> Any:
    return QueryPlanner().compile(
        QuerySpec.build(capability, symbols=_PROBE_SYMBOL, provider=provider, **kwargs)
    )


def _semantic_cells() -> list[tuple[str, str, str]]:
    """注册表里「执行体是 ``_migrated_capability``、能力却归核心集」的三元组。

    这些格子正是两套约定交界的地方。``quotes`` / ``bars`` 的非 canonical channel 会被
    :func:`tstdx.query._reject_core_channel_mismatch` 在规划期挡掉（它们由 v14 Direct
    API 面服务），所以这里按**真实编译**筛，而不是抄一份可达名单——抄来的名单本身就是
    本文件所指控的那种第二份表。
    """
    cells: list[tuple[str, str, str]] = []
    for binding in audit_direct_bindings():
        if binding.executor_name != "_migrated_capability":
            continue
        if binding.capability not in _CORE_CAPABILITIES:
            continue
        try:
            plan = _compile(binding.capability, binding.provider, channel=binding.channel)
        except ValidationError:
            continue
        if (plan.provider, plan.channel, plan.spec.capability) == binding.key:
            cells.append(binding.key)
    return cells


CELLS = _semantic_cells()


def test_the_bridge_carries_the_cells_the_docs_promise() -> None:
    """分母不许安静收缩到零。

    ``docs/api/interfaces.md`` 的降级表给 ``minute`` 声明了 ``baidu`` / ``eastmoney`` /
    ``tencent`` 三个可用 Provider。若哪天这些格子从注册表消失，本判据会先在这里红——
    那时该改的是文档，而不是把断链跳过去。
    """
    assert CELLS, "语义路径与 migrated 执行体的交界格子为空：注册表或执行器分派表变了形"
    keyed = {(capability, provider) for provider, _channel, capability in CELLS}
    assert ("minute", "tencent") in keyed
    assert ("minute", "baidu") in keyed
    assert ("minute", "eastmoney") in keyed


@pytest.mark.parametrize("provider,channel,capability", CELLS)
def test_every_semantic_cell_binds_its_own_implementation(
    provider: str, channel: str, capability: str
) -> None:
    """派生出的入参必须过实现自己的签名闸——断链的定义就是过不了。"""
    plan = _compile(capability, provider)
    assert plan.channel == channel
    meta = binding_for(provider, channel, capability)
    args, kwargs = _semantic_call_payload(plan, meta)
    validate_call(provider, plan.channel, capability, tuple(args), dict(kwargs))
    assert callable(implementation_for(provider, channel, capability))


def test_the_bridge_only_names_fields_the_spec_actually_carries() -> None:
    """字段表与改名表的每一格都必须是 :class:`QuerySpec` 的真成员。

    一条指向不存在字段的桥就是一句关于 spec 的谎话，而它比缺一条桥更糟：读代码的人会
    以为那个语义字段有人接。
    """
    spec_fields = {item.name for item in fields(QuerySpec)}
    unknown = sorted(set(_SEMANTIC_CALL_FIELDS) - spec_fields)
    assert not unknown, f"{unknown} 不是 QuerySpec 的字段"
    assert set(_SEMANTIC_FIELD_RENAMES) <= set(_SEMANTIC_CALL_FIELDS)
    assert all(name.isidentifier() for name in _SEMANTIC_FIELD_RENAMES.values())


def test_a_semantic_field_with_no_home_on_the_implementation_is_rejected() -> None:
    """设了却没人收的字段必须当场失败，不能被静默丢掉（幻影旋钮）。

    ``tencent`` 的逐笔实现收 ``max_pages``，不收 ``count``。调用方写了 ``count=50`` 就
    以为自己拿到了 50 条，这比拿不到更糟。
    """
    plan = _compile("trades", "tencent", count=50)
    meta = binding_for("tencent", plan.channel, "trades")
    with pytest.raises(ValidationError, match="count"):
        _semantic_call_payload(plan, meta)


def test_a_batch_query_never_reaches_a_single_symbol_implementation() -> None:
    """一批代码不许挑第一只去跑：静默缩水比报错严重。"""
    plan = QueryPlanner().compile(
        QuerySpec.build("minute", symbols=[_PROBE_SYMBOL, "600519"], provider="tencent")
    )
    meta = binding_for("tencent", plan.channel, "minute")
    with pytest.raises(ValidationError, match="单只"):
        _semantic_call_payload(plan, meta)


def test_implementation_home_matches_the_binding_method() -> None:
    """``implementation_for`` 必须就是绑定所声明的那个成员。

    这条判据把"两处各抄一份宿主类"关在门外：宿主类改名或方法搬家时，这里与
    ``_migrated_capability`` 会同时失去落点，而不是只有一侧红。
    """
    checked = 0
    for binding in DIRECT_BINDINGS:
        if binding.executor_name != "_migrated_capability":
            continue
        try:
            meta = binding_for(*binding.key)
        except KeyError:
            continue
        impl = implementation_for(*binding.key)
        if impl is None:
            continue
        assert getattr(impl, "__name__", "") == meta.method or meta.backend == "f10_client", (
            f"{binding.key} 的绑定声明 {meta.method!r}，实现宿主却是 {impl!r}"
        )
        checked += 1
    assert checked > 20, f"交叉核对的实现数只有 {checked}，绑定表或本判据走样了"
