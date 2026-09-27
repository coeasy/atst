# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""Provider 声明面 ↔ 执行器绑定面的贯通审计。

能力目录由绑定表生成，所以"目录 ⊆ 绑定"这一方向看不见"某项能力悄悄失去执行路径"
——两侧会同时缩小，审计照绿。Provider 注册表是独立书写的声明，把它与执行器绑定
双向对账，才真正钉住"每个对外声明都有唯一执行路径、每条执行路径都受对外约束"。
"""

from __future__ import annotations

import pytest

from atst.catalog.capability_audit import CapabilityAuditError, audit_capability_bindings


def _declared() -> set[tuple[str, str, str]]:
    from atst.providers import PROVIDERS

    return {
        (provider, channel.id, capability)
        for provider in PROVIDERS.ids()
        for channel in PROVIDERS.get(provider).channels
        for capability in channel.capabilities
    }


def test_report_counts_three_surfaces() -> None:
    from atst.runtime.executor import DIRECT_BINDINGS

    report = audit_capability_bindings()

    assert report.migrated_capabilities > 0
    assert report.executable_bindings == len(DIRECT_BINDINGS)
    assert report.declared_bindings == len(_declared())


def test_registry_declaration_matches_public_surface() -> None:
    """注册表声明的能力集合必须正好等于 Client 对外承诺的能力集合。"""
    from atst.client.api import Client

    assert {key[2] for key in _declared()} == set(Client.capabilities())


def test_audit_rejects_empty_catalog(monkeypatch: pytest.MonkeyPatch) -> None:
    import atst.catalog.capability as capability_module

    monkeypatch.setattr(capability_module, "MIGRATED_BINDINGS", ())

    with pytest.raises(CapabilityAuditError, match="catalog is empty"):
        audit_capability_bindings()


@pytest.mark.parametrize(
    ("mutate", "expected"),
    [
        ("drop_migrated", "capabilities without executor bindings"),
        ("drop_unlisted", "with no executor binding"),
        ("add_ghost", "outside the Provider registry"),
    ],
)
def test_audit_has_teeth_on_both_directions(
    monkeypatch: pytest.MonkeyPatch, mutate: str, expected: str
) -> None:
    """向绑定表注入三处漂移（丢一条目录内绑定、丢一条目录外绑定、加一条幽灵绑定），
    审计必须逐一失败——否则上面的绿灯只是巧合。"""
    from types import SimpleNamespace

    import atst.runtime.executor as executor_module
    from atst.catalog.capability import MIGRATED_BINDINGS

    real = executor_module.DIRECT_BINDINGS
    keys = {binding.key for binding in real}
    migrated = {item.key for item in MIGRATED_BINDINGS}
    if mutate == "drop_migrated":
        victim = sorted(keys & migrated)[0]
        mutated = tuple(binding for binding in real if binding.key != victim)
    elif mutate == "drop_unlisted":
        victim = sorted(keys - migrated)[0]
        mutated = tuple(binding for binding in real if binding.key != victim)
    else:
        mutated = (*real, SimpleNamespace(key=("tdx", "quotation", "capability_nobody_declares")))
    monkeypatch.setattr(executor_module, "DIRECT_BINDINGS", mutated)

    with pytest.raises(CapabilityAuditError, match=expected):
        audit_capability_bindings()
