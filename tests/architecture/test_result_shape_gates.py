"""结果面形状门禁：`Provenance` / `ResultMeta` 的每个字段都必须有人按它行动。

尺子取自 F-50/F-52/F-54 那一份实现（`tests/support/field_readers.py`，全仓唯一一把），
分母取 `dataclasses.fields()`。两层判据各补对方的洞：读取点扫描管"有字段没人读"，
顺序敏感的形状清单管"扫描会假绿的那一类"——变异 M4 实测：给 `ResultMeta` 加一个没人读的
`notes`，扫描仍然绿，因为 `atst/cli/runtime_commands.py` 里另一对象的 `result.notes` 被算成
了读取点；形状清单当场红。

本文件登记的正是 F-52 剩下的那一半：`Provenance.provider_timestamp` 被 `direct()` 的形参一路
带着走，却没有任何调用方传过它——全包读取点 **0**、不进 wire 的显式键、没有任何 Provider 的
解码器往里写过值。一个从未被填过也从未被读的字段，只是让"直连结果的出处"看起来比实际更丰富。

同一族里还有第 31 步（F-57）那一条：`ProvenanceKind` 曾声明 `REPLAY`/`SYNTHETIC`，而零缓存
内核唯一的构造点 `Provenance.direct()` 恒为 `DIRECT`——配上三个没有任何消费者的判定属性，
"出处可能有三种"就成了纸面声称。这里把它收成一个成员、零个属性，并由枚举对构造点的判据把守。
"""

from __future__ import annotations

import dataclasses
import inspect

import pytest

from tests.support.field_readers import members_referenced, unread_fields
from atst.result import Provenance, ProvenanceKind, ResultMeta

# 手工核对过的 owner：只认调用方真把这两个数据面对象绑定到的变量名。
# 不放 `self`——`self.capability` 属于 catalog/records 里的别的类，放进去就是假绿。
PROVENANCE_OWNERS = {"provenance", "p", "prov"}
RESULT_META_OWNERS = {"meta", "result"}


def _assert_no_unread(cls: type, owners: set[str]) -> None:
    fields, scanned, reads = unread_fields(cls, owners)
    name = cls.__name__
    assert fields, f"{name} 已经没有字段了，判据自身失效"
    assert scanned > 30, f"只扫到 {scanned} 个模块，读取扫描自身失效"
    assert reads, f"{name} 字段读取扫描一条都没命中，说明它自身失效了"
    orphans = sorted(fields - reads)
    assert orphans == [], f"{name} 字段没有任何读取点（无人兑现的声称）：{orphans}"


def test_every_provenance_field_has_a_reader() -> None:
    _assert_no_unread(Provenance, PROVENANCE_OWNERS)


def test_every_result_meta_field_has_a_reader() -> None:
    _assert_no_unread(ResultMeta, RESULT_META_OWNERS)


def test_result_meta_field_order_is_pinned() -> None:
    """`ResultMeta` 的形状逐位钉死。

    读取点扫描有一类已实测的假绿：字段名与别的类同名时，`result.notes` 这种命中也算数
    （`atst/cli/runtime_commands.py:406` 的 `result` 是探针结果，不是 `ResultMeta`）。
    位置敏感的形状清单补上这个洞——新字段必须先把形状改一次，改动就落到纸面上。
    """

    names = [item.name for item in dataclasses.fields(ResultMeta)]
    assert names == [
        "provider",
        "channel",
        "capability",
        "fingerprint",
        "provenance",
        "warnings",
    ], f"结果 meta 形状（顺序敏感）变了：{names}"


def test_provenance_field_order_is_pinned() -> None:
    """字段顺序钉死：`Provenance` 是 frozen dataclass，删中间一个会挪动所有位置式构造的语义。"""

    names = [item.name for item in dataclasses.fields(Provenance)]
    assert names == [
        "provider",
        "channel",
        "capability",
        "kind",
        "observed_at_ns",
        "cache_tier",
        "requested_provider",
        "fallback",
    ], f"出处形状（顺序敏感）变了：{names}"


def test_deleted_provenance_field_is_not_a_back_door() -> None:
    """按 clean break 删除的字段不留兼容位：构造面当场拒绝，而不是静默收下再丢掉。"""

    kwargs: dict[str, object] = {
        "provider": "tdx",
        "channel": "bars",
        "capability": "bars",
        "kind": ProvenanceKind.DIRECT,
        "observed_at_ns": 1,
    }
    with pytest.raises(TypeError, match="provider_timestamp"):
        Provenance(**kwargs, provider_timestamp="2026-09-19T00:00:00Z")
    # 唯一的公开构造入口也不留这个形参位
    assert "provider_timestamp" not in inspect.signature(Provenance.direct).parameters


def test_every_declared_provenance_kind_has_a_producer() -> None:
    """`ProvenanceKind` 的每个成员都必须真的被生产出来。

    与第 26 步那条 `WarningCode` 判据同形，且共用同一个遍历
    （:func:`tests.support.field_readers.members_referenced`）：声明一个类别而不生产它，
    等于在 wire 的 `meta.provenance.kind` 上放一个永远不会出现的取值。零缓存内核里没有
    "重放"也没有"合成"——那两个成员是缓存时代的身份词，删除后"直连之外无出处"由这条判据
    保证，而不是由注释保证。
    """

    scanned, produced = members_referenced("ProvenanceKind")
    assert scanned > 30, f"只扫到 {scanned} 个模块，扫描自身失效"
    assert produced, "全仓没有一处 ProvenanceKind 引用，说明扫描自身失效了"
    declared = {item.name for item in ProvenanceKind}
    assert declared == {"DIRECT"}, f"直连-only 运行时无从声称别的出处类别：{sorted(declared)}"
    unproduced = sorted(declared - produced)
    bogus = sorted(produced - declared)
    assert unproduced == [], f"声明了却没有任何构造点的出处类别：{unproduced}"
    assert bogus == [], f"引用了不存在的出处类别：{bogus}"


def test_provenance_exposes_no_judgement_property() -> None:
    """出处的判定只能是字段本身，不能是回长出来的 bool 属性。

    `real`/`replay`/`synthetic` 三个属性已删除：前两个连抄本读取都没有，`real` 的唯一读者
    是一条测试断言——那是规则的抄件而不是规则（F-55 同形）。分母取 `Provenance.__dict__`，
    与文件扫描无关，属性一旦长回来即红。
    """

    props = sorted(name for name, value in vars(Provenance).items() if isinstance(value, property))
    assert props == [], f"`Provenance` 上又长出判定属性，出处应由字段直接说明：{props}"
