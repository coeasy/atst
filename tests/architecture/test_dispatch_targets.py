# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""分派目标的判据（V18 第 22 轮，G27）。

第 2 遍「核心链路连通性」查出的一类形状：绑定表里写着 ``method``，运行时用
``getattr(目标, meta.method)`` 去取实现。取不到时**没有**任何测试会先红——
``validate_call`` 原先只把 ``KeyError/TypeError/ValueError`` 收敛成
:class:`~tstdx.errors.ValidationError`，``AttributeError`` 直接穿出去，
HTTP/WS 两张面把它报成 E9000/500：一条过期的表项长得像内部故障，不像契约问题。

本文件钉住六件事：

① 后端的词表是闭合的：每张面按 ``method`` 派发的后端（``METHOD_BACKENDS``）与
   只把 ``method`` 当标签、按 capability 分岔的后端（``TAG_BACKENDS``）加起来必须
   正好等于绑定表里出现的后端集合——出现第六种"按什么派发"的后端时先红，不许被
   静默归进"标签那一桶"（口径承接
   :mod:`tests.architecture.test_offline_capability_honesty` 的 ``_NON_LEDGER_BACKENDS`` 断言）。
② ``METHOD_BACKENDS`` 每一行的 ``method`` 必须真的能从它自己的派发目标上取到。
   目标按派发方各自的解析式现算（``resolve_channel_adapter`` 等），不在这里抄一遍类名。
③ ``TAG_BACKENDS`` 的 ``method`` 必须复读 capability：标签一旦和 capability 分叉，
   读表的人就会被一个不存在的实现名骗到。唯一例外是 ``f10_client`` 的
   ``f10_catalog`` 行——它是
   :func:`tests.architecture.test_offline_capability_honesty.test_every_published_name_has_a_home`
   里点名过的「唯一的登记盲区」（``f10`` → ``download``、其余 → ``catalog``），
   要动它先去动那条断言。
④ 表真过期时，``validate_call`` 必须把它收口成 ValidationError（判据 ② 在测试里量，
   这一条管线上那一次调用的形状）。
⑤ 来源查表不许带默认值：``_SOURCE_FOR_PROVIDER`` 必须覆盖语义主里出现的每个 Provider。
⑥ web 源表与音量归一化器表双向闭合：``_get_normalizer`` 对没登记的源返回 identity，
   而 identity 就是「这列已经是 股 / 元」这个论断本身。

判据本身也要被测：植入的失效（改坏一行 ``method``、删掉一个来源登记、凭空加一个源）
必须只让对应那条红。
"""

from __future__ import annotations

import dataclasses
from typing import Any

import pytest

from tstdx.catalog import capability as cap
from tstdx.catalog.capability import MIGRATED_BINDINGS, MigratedCapabilityBinding
from tstdx.catalog.provider_bindings import resolve_channel_adapter
from tstdx.client import ExMarketClient, GoodsClient, MacClient, TdxClient
from tstdx.errors import ValidationError
from tstdx.web.normalize import _NORMALIZER_REGISTRY
from tstdx.web.session import WebQuoteSession
from tstdx.web.sources import SOURCES

#: 按 ``meta.method`` 派发的后端 → 它的派发目标（与 ``validate_call`` /
#: ``DirectProviderExecutor`` 里各自的 getattr 表达式同一口径）。
METHOD_BACKENDS: dict[str, Any] = {
    "web_session": lambda binding: WebQuoteSession,
    "tdx_client": lambda binding: TdxClient,
    "ex_client": lambda binding: ExMarketClient,
    "goods_client": lambda binding: GoodsClient,
    "mac_client": lambda binding: MacClient,
    "direct_adapter": lambda binding: resolve_channel_adapter(binding.provider, binding.channel),
}

#: ``method`` 只是标签、按 capability 分岔的后端。
TAG_BACKENDS = frozenset({"f10_client", "web_adapter", "composed"})

#: ③ 的唯一例外，连同它实际复读的实现名。理由见模块 docstring。
TAG_EXEMPTIONS: dict[tuple[str, str, str], str] = {("tdx", "f10", "f10_catalog"): "catalog"}


def unresolved_method_rows(
    bindings: tuple[MigratedCapabilityBinding, ...] = MIGRATED_BINDINGS,
) -> list[str]:
    """②：``method`` 取不到派发目标实现的行。"""
    return [
        f"{binding.key} → {binding.backend}.{binding.method}"
        for binding in bindings
        if binding.backend in METHOD_BACKENDS
        and not hasattr(METHOD_BACKENDS[binding.backend](binding), binding.method)
    ]


def mislabeled_tag_rows(
    bindings: tuple[MigratedCapabilityBinding, ...] = MIGRATED_BINDINGS,
) -> list[str]:
    """③：标签行不再复读 capability。"""
    return [
        f"{binding.key} → method={binding.method!r} capability={binding.capability!r}"
        for binding in bindings
        if binding.backend in TAG_BACKENDS
        and TAG_EXEMPTIONS.get(binding.key, binding.capability) != binding.method
    ]


def test_the_dispatch_vocabulary_is_closed() -> None:
    """①：绑定表里出现的后端，正好等于「按 method 派发」∪「按 capability 分岔」。"""
    present = {binding.backend for binding in MIGRATED_BINDINGS}
    assert present == set(METHOD_BACKENDS) | TAG_BACKENDS, (
        f"后端词表分叉：{sorted(present ^ (set(METHOD_BACKENDS) | TAG_BACKENDS))}——"
        "新后端按什么派发？先回答这个问题，再把它登记进本文件，别让它免检"
    )


def test_every_dispatched_binding_method_resolves() -> None:
    """②：绑定表里运行时 getattr 的每一个方法名都必须真的在目标上。"""
    dispatched = [b for b in MIGRATED_BINDINGS if b.backend in METHOD_BACKENDS]
    assert dispatched, "没有一行是按 method 派发的了，② 成了空判据"
    rows = unresolved_method_rows()
    assert not rows, f"{len(rows)} 行绑定的 method 在其派发目标上不存在：\n" + "\n".join(rows)


def test_every_tag_binding_still_repeats_its_capability() -> None:
    """③：标签行必须复读 capability（唯一例外按 key 点名，并写明它复读哪个实现）。"""
    tagged = [b for b in MIGRATED_BINDINGS if b.backend in TAG_BACKENDS]
    assert tagged, "标签后端一行都没了，③ 成了空判据"
    rows = mislabeled_tag_rows()
    assert not rows, f"{len(rows)} 行标签绑定的 method 与 capability 分叉：\n" + "\n".join(rows)


def test_a_stale_binding_is_a_contract_error_not_an_internal_fault(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """④：表指向不存在的实现时，``validate_call`` 必须收口成 ValidationError。

    这是判据 ② 兜不住的那一半：判据在测试里量，线上那一次调用仍然要有正确的形状。
    ``AttributeError`` 穿出去会被 HTTP/WS 记成 E9000/500，而调用方参数没错。
    """
    victim = cap.binding_for("tdx", "quotation", "quotes_concurrent")
    stale = dataclasses.replace(victim, method="no_such_impl")
    table = dict(cap._BINDINGS_BY_KEY)
    table[stale.key] = stale
    monkeypatch.setattr(cap, "_BINDINGS_BY_KEY", table)
    with pytest.raises(ValidationError) as excinfo:
        cap.validate_call("tdx", "quotation", "quotes_concurrent", (["sh600519"],), {})
    assert excinfo.value.context.get("phase") == "binding_resolution", (
        f"漂移被报成了别的东西：{excinfo.value.context}——调用方看不出是表过期"
    )


def test_the_rulers_see_a_planted_binding_drift() -> None:
    """正控：改坏一行 ``method``，② 必须只点出那一行；标签分叉由 ③ 单独管。"""
    victim = next(b for b in MIGRATED_BINDINGS if b.backend == "web_session")
    planted = dataclasses.replace(victim, method=victim.method + "_drifted")
    rows = unresolved_method_rows((*MIGRATED_BINDINGS, planted))
    assert len(rows) == 1 and rows[0].endswith(f".{victim.method}_drifted"), (
        f"改坏一行绑定却没被 ② 抓到：{rows}"
    )

    tag_victim = next(
        b for b in MIGRATED_BINDINGS if b.backend in TAG_BACKENDS and b.key not in TAG_EXEMPTIONS
    )
    relabeled = dataclasses.replace(tag_victim, method=f"{tag_victim.method}_drifted")
    tag_rows = mislabeled_tag_rows((*MIGRATED_BINDINGS, relabeled))
    assert len(tag_rows) == 1 and f"{tag_victim.capability!r}" in tag_rows[0], (
        f"标签与 capability 分叉却没被 ③ 抓到：{tag_rows}"
    )
    # ②/③ 各管一桶：标签行的漂移不该被按 method 派发的那把尺子看见，反之亦然。
    assert unresolved_method_rows((*MIGRATED_BINDINGS, relabeled)) == [], (
        "② 越界量了标签后端——它按 capability 派发，method 取不到实现不是故障"
    )


def test_the_web_source_vocabulary_has_no_default_route() -> None:
    """⑤：语义主里的每个 Provider 都必须自己登记来源，查表不许带默认值。

    ``.get(provider, "sina")`` 那种写法会让一个新 Provider 的能力以别家的量纲单位上线；
    同文件里 ``_discover_web_bindings`` 用的是严格下标，两处必须同一口径。
    """
    declared = {provider for provider, _ in cap._SEMANTIC_WEB_CHANNELS}
    assert declared, "语义主家名单空了，⑤ 变成空判据"
    assert declared <= set(cap._SOURCE_FOR_PROVIDER), (
        f"语义主里出现了没登记来源的 Provider：{sorted(declared - set(cap._SOURCE_FOR_PROVIDER))}"
    )


def test_dropping_a_source_entry_is_loud(monkeypatch: pytest.MonkeyPatch) -> None:
    """正控：删掉一个正在被使用的来源登记，构造绑定表时必须当场报错而不是退回默认值。"""
    monkeypatch.setattr(
        cap,
        "_SOURCE_FOR_PROVIDER",
        {k: v for k, v in cap._SOURCE_FOR_PROVIDER.items() if k != "eastmoney"},
    )
    with pytest.raises(KeyError) as excinfo:
        cap._semantic_web_bindings()
    assert excinfo.value.args[0] == "eastmoney", (
        f"缺来源时报的不是那个 Provider：{excinfo.value.args}"
    )


def unit_semantics_findings() -> list[str]:
    """⑥：两份注册表双向不闭合处。"""
    out: list[str] = []
    silent = sorted(set(SOURCES) - set(_NORMALIZER_REGISTRY))
    orphan = sorted(set(_NORMALIZER_REGISTRY) - set(SOURCES))
    if silent:
        out.append(f"这些源没有登记量纲系数，会被 identity 静默放行：{silent}")
    if orphan:
        out.append(f"这些归一化器没有对应的源，没人能用上：{orphan}")
    return out


def test_every_http_web_source_declares_its_unit_semantics() -> None:
    """⑥：web 源表与音量归一化器表必须双向闭合。"""
    findings = unit_semantics_findings()
    assert not findings, "\n".join(findings)


def test_the_unit_ruler_sees_a_planted_source(monkeypatch: pytest.MonkeyPatch) -> None:
    """正控：凭空注册一个没有归一化器的源，⑥ 必须只点名它。"""
    existing = SOURCES[next(iter(SOURCES))]
    fake = dataclasses.replace(existing, name="planted-source")
    monkeypatch.setitem(SOURCES, "planted-source", fake)
    findings = unit_semantics_findings()
    assert len(findings) == 1 and "planted-source" in findings[0], (
        f"注册了一个没有归一化器的源却没被 ⑥ 抓到：{findings}"
    )
