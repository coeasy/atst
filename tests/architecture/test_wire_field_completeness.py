"""G22：结果侧每一格都得走到 wire 上——出口不许悄悄漏掉新加的字段。

第 21 轮「前后端全部贯通」这一遍量到的形状：:func:`atst.integration.serialization._query_result`
是一份**手抄的键清单**（``provider``/``channel``/``capability``/``fingerprint``/
``provenance``/``warnings``，以及嵌套 ``provenance`` 里的 5 格）。请求侧早在 F-47 就收成了
"每个字段要么被读走、要么当场被拒"，结果侧却只有读取点扫描把守（``test_result_shape_gates.py``）：
它管"有字段没人读"，管不到"有人读的字段在三面出口被丢掉"。

这两件事的后果不对等：读点缺一个，调用方只是拿不到；wire 少一格，调用方**不知道自己少拿了**。
本轮把 ``Provenance.provider_timestamp`` 那类"richer than the wire"的形状往回推了一步——
但"改出口的实现做法"实测过并被否决：把 ``_query_result`` 换成 ``jsonable(meta)``（``asdict``
整张摊平）之后，出口里再没有一个字段名是**逐字写出来**的，而 :mod:`tests.support.field_readers`
那把全仓唯一的读取点尺子按字段名找人读它——同轮量到 ``Provenance`` 新掉出 4 格、
``ResultMeta`` 2 格"无人读"（``scratch_v18b21/g22_asdict_experiment.log`` 第 95 行
``2 failed, 38 passed``）。所以本轮**不改出口的形状**，改的是"漏一格要当场红"：
清单照旧手抄，判据把清单钉在 ``dataclasses.fields()`` 上。

允许缺席的只有身份三格（``provider``/``channel``/``capability``）：它们在 ``meta`` 一层已经
出现过，而 :meth:`atst.result.ResultMeta.from_plan` 在构造时就断言 ``provenance`` 与
``plan`` 的这三格逐字相等——wire 上不重复同一个值两次，是裁决，不是漏抄。
"""

from __future__ import annotations

import dataclasses
from typing import Any

import pytest

from atst.diagnostics import ResultWarning, WarningCode
from atst.integration.serialization import serialize_result
from atst.result import Provenance, ProvenanceKind, QueryResult, ResultMeta

#: ``meta.provenance`` 里允许缺席的键：值已在 ``meta`` 一层给出，且构造期即断言相等。
IDENTITY_KEYS = frozenset({"provider", "channel", "capability"})


def _field_names(cls: type) -> set[str]:
    names = {item.name for item in dataclasses.fields(cls)}
    # 反洞自查：分母空集意味着判据自己失效，而不是"这张表没有字段"。
    assert names, f"{cls.__name__} 已经没有字段了，判据自身失效"
    return names


def _absent_keys(payload: dict[str, Any], cls: type, *, allowed: frozenset[str]) -> set[str]:
    """手抄清单相对 ``dataclasses.fields()`` 漏掉的键（多出来的键也算违约，另判）。"""
    emitted = set(payload)
    missing = _field_names(cls) - allowed - emitted
    extra = emitted - _field_names(cls)
    assert not extra, f"{cls.__name__} 的出口上有表里没有的键：{sorted(extra)}"
    return missing


def _result() -> QueryResult[list[dict[str, Any]]]:
    provenance = Provenance(
        provider="tencent",
        channel="quote",
        capability="quotes",
        kind=ProvenanceKind.DIRECT,
        observed_at_ns=1_700_000_000_000_000_000,
        cache_tier=None,
        requested_provider="tencent",
        fallback=False,
    )
    meta = ResultMeta(
        provider="tencent",
        channel="quote",
        capability="quotes",
        fingerprint="fp-test",
        provenance=provenance,
        warnings=(ResultWarning(WarningCode.DECODE_CAVEAT, "解码告警"),),
    )
    return QueryResult(data=[{"symbol": "600519", "last": 0.0}], meta=meta)


def test_wire_carries_every_result_meta_field() -> None:
    payload = serialize_result(_result())["meta"]
    assert _absent_keys(payload, ResultMeta, allowed=frozenset()) == set()


def test_wire_carries_every_provenance_field_beyond_the_identity_triple() -> None:
    payload = serialize_result(_result())["meta"]["provenance"]
    assert _absent_keys(payload, Provenance, allowed=IDENTITY_KEYS) == set()


def test_wire_carries_every_result_warning_field() -> None:
    warnings = serialize_result(_result())["meta"]["warnings"]
    assert len(warnings) == 1, "告警一格都没到 wire，出口断了"
    assert _absent_keys(warnings[0], ResultWarning, allowed=frozenset()) == set()


def test_identity_triple_is_deduplicated_not_lost() -> None:
    """身份三格可以不在嵌套对象里，但必须在父层原样出现，且两侧同源。"""

    result = _result()
    payload = serialize_result(result)["meta"]
    for key in sorted(IDENTITY_KEYS):
        assert payload[key] == getattr(result.meta.provenance, key)


@pytest.mark.parametrize("dropped", ["fingerprint", "kind", "message"])
def test_a_field_lost_on_the_way_out_is_red(dropped: str) -> None:
    """正控：三条判据各造一次"出口漏一格"，都必须当场红。

    没有这组正控，``_absent_keys`` 完全可能因为清单写反而永远返回空集。
    """

    payload = serialize_result(_result())["meta"]
    target, cls, allowed = {
        "fingerprint": (payload, ResultMeta, frozenset()),
        "kind": (payload["provenance"], Provenance, IDENTITY_KEYS),
        "message": (payload["warnings"][0], ResultWarning, frozenset()),
    }[dropped]
    broken = dict(target)
    del broken[dropped]
    with pytest.raises(AssertionError, match=dropped):
        assert _absent_keys(broken, cls, allowed=allowed) == set()


def test_a_key_the_dataclass_does_not_have_is_red() -> None:
    """正控（反方向）：出口凭空多一格也要红——多出来的键同样没人声明过。"""

    payload = dict(serialize_result(_result())["meta"], invented_field=1)
    with pytest.raises(AssertionError, match="invented_field"):
        _absent_keys(payload, ResultMeta, allowed=frozenset())
