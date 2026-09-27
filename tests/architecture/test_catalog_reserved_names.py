# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""自动发现的跳过名单得说清它在为哪件事负责（V18-C1 后半第一段，第 12 轮）。

第 10 轮量过一次就把话押在 §19 的待裁决里："catalog 引 `DEDICATED_CAPABILITIES` 会成导入环，
要接先得定那份名单归谁"。本轮先把环和名单分别量清楚，再决定改什么。

**环是真的**：`runtime/executor.py` 在模块顶层就 `from ..catalog.capability import ...`，而目录在
**模块导入期**就要跑完 `_build_bindings()`（`MIGRATED_BINDINGS = _build_bindings()` 那一行）。所以方向
反过来不管写成顶层 import 还是函数内延迟 import，都是在一个只初始化了一半的模块上取属性——这份名单
不可能靠 import 派生，只能靠判据钉住。

**名单在做什么**（离线探针，读数记在方案 §21）：跳过名单九条里
只有三条真挡在自动发现那一圈上——`close`、`quotes`、`minute`；`mro` **在任何一种成员口径下都不
是成员**（`dir()` 无、`vars()` 无、`getmembers(callable)` 无，只有 `getattr` 拿得到，而发现环用的
不是 `getattr` 口径）；其余五条（`bars`/`snapshot`/`trades`/`security_count`/`security_list`）是
"类上今天没有、将来可能有"的**保留名**。一条关于类的陈述混着三种说法，读的人分不出哪条在挡什么
——第 10 轮的记录就把它读成了"六条根本不是成员"，连数量都数错（是 `mro` 加五条，不是六条）。

于是本轮把那张表拆成两份各管一种说法，再上三条判据（含两格口径自检）：

八 **保留名 = 派生集**：`_RESERVED_CORE_CAPABILITIES` 与 `DEDICATED_CAPABILITIES` 双向差为 0。
    权威在执行器那张实现表，目录这份是**声明**——从此不靠"恰好抄对了"。
八b **成员黑名单必须真的是成员**：`_NOT_A_QUERY_MEMBER` 每一条都要命中发现环看见的可调用成员；
    整份跳过名单里也不许出现"既不是成员、又不是保留名"的条目。`mro` 就是这一格在改动前抓出来的。
八c **这张名单确实在做事**：摘掉它，目录立刻给核心能力开出第二个家；留着它，目录里任何一条
    `backend="web_session"` 的绑定都不许占用核心能力名——那五格"今天什么都没挡住"的保留名由同
    一格给出代价：现场给 `WebQuoteSession` 装一个同名方法，摘掉就长家、留着就不长。
"""

from __future__ import annotations

import inspect

import pytest

from atst.catalog import capability as catalog
from atst.runtime.executor import DEDICATED_CAPABILITIES

#: 低于这个规模就说明读的是空集合，"零违例"就没意义了。
_MIN_MEMBERS = 100
_MIN_RESERVED = 7


def _discovery_members() -> set[str]:
    """`_discover_web_bindings` 实际遍历的那一圈：公开、可调用。

    口径必须由 :func:`test_the_discovery_loop_and_the_ruler_share_one_lens` 自己核对，
    否则八b 量的是另一个类。
    """
    from atst.web.session import WebQuoteSession

    return {
        name
        for name, _member in inspect.getmembers(WebQuoteSession, predicate=callable)
        if not name.startswith("_")
    }


def test_reserved_names_are_the_derived_core_set() -> None:
    """八：目录那份保留名与执行器派生出来的核心集，双向差为 0。"""
    reserved = catalog._RESERVED_CORE_CAPABILITIES
    assert len(reserved) >= _MIN_RESERVED, f"保留名单只剩 {len(reserved)} 格，判据被读空了"
    problems = [
        f"目录说 {name!r} 归核心分派，执行体表里却没有它的专属执行体"
        for name in sorted(reserved - DEDICATED_CAPABILITIES)
    ] + [
        f"执行器给了 {name!r} 一个专属执行体，目录却允许 web 面再开一个家"
        for name in sorted(DEDICATED_CAPABILITIES - reserved)
    ]
    assert not problems, "保留名与派生核心集各说各话：\n" + "\n".join(problems)


def test_member_blacklist_only_names_real_members() -> None:
    """八b：成员黑名单里不许躺着一个不是成员的名字，跳过名单里不许夹第三种说法。"""
    members = _discovery_members()
    calibration = {
        "close": "close" in members,  # 真成员：`_SessionBase.close`
        "mro": "mro" in members,  # 不是成员：只有 `getattr` 口径拿得到
        "bars": "bars" in members,  # 不是成员：保留名为将来留的位置
    }
    assert calibration == {"close": True, "mro": False, "bars": False}, (
        f"判据自己的口径漂了：{calibration}。`close` 在成员圈里、`mro` 与 `bars` 不在——"
        "这三格对不上，下面两条断言就是在量一个变了形的集合"
    )

    phantom = sorted(catalog._NOT_A_QUERY_MEMBER - members)
    unexplained = sorted(catalog._SKIP_WEB_METHODS - members - catalog._RESERVED_CORE_CAPABILITIES)
    assert not phantom, (
        f"成员黑名单里的 {phantom} 不是 WebQuoteSession 的可调用成员：它在解释一个不存在的类结构，"
        "读名单的人会照着它以为类上真有这么个东西"
    )
    assert not unexplained, (
        f"跳过名单里的 {unexplained} 既不是成员也不是保留名：两种说法又混回一张表了"
    )


def test_no_core_capability_has_a_web_session_home() -> None:
    """八c 前半：目录里没有任何一条 web_session 绑定占用核心能力名。"""
    offenders = [
        item.key
        for item in catalog.MIGRATED_BINDINGS
        if item.capability in DEDICATED_CAPABILITIES and item.backend == "web_session"
    ]
    assert offenders == [], f"核心能力在目录里长出了第二个家：{offenders}"
    named = sorted(
        {
            item.capability
            for item in catalog.MIGRATED_BINDINGS
            if item.capability in DEDICATED_CAPABILITIES
        }
    )
    assert named, (
        "目录里一条同名于核心集的迁移绑定都没有了：这一格从头到尾没看过任何候选，"
        "红绿都不说明问题——核心能力在别的面上有了新归宿，判据要跟着搬家"
    )


def test_the_skip_list_is_what_keeps_those_homes_out(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """八c 后半（正控）：名单一摘就长家，留着就一颗都不长——五格保留名也一样。"""
    from atst.web.session import WebQuoteSession

    full = catalog._SKIP_WEB_METHODS

    def discover_with(value: frozenset[str]) -> set[tuple[str, str, str]]:
        monkeypatch.setattr(catalog, "_SKIP_WEB_METHODS", value)
        return {item.key for item in catalog._discover_web_bindings()}

    def probe() -> None:
        raise AssertionError("探针方法不该被执行")

    # 今天真的挡着的东西：`minute`/`quotes` 是类上真实存在的同名方法，`close` 是真实存在的
    # 非查询成员。两份名单各挡各的，摘错那一半就不会红——所以两半都要单独验。
    without_reserved = discover_with(catalog._NOT_A_QUERY_MEMBER)
    assert {
        ("derived", "catalog", "minute"),
        ("derived", "catalog", "quotes"),
    } <= without_reserved, (
        "摘掉保留名单却没开出第二个家：类上 `minute`/`quotes` 这两个同名成员已经不归发现环管了，"
        "这一格守的东西不在了，判据该搬家而不是留着"
    )
    without_blacklist = discover_with(catalog._RESERVED_CORE_CAPABILITIES)
    assert ("derived", "catalog", "close") in without_blacklist, (
        "摘掉成员黑名单却没把 `close` 变成一条能力：这一格守的东西不在了"
    )
    kept = discover_with(full)
    assert kept.isdisjoint(
        {
            ("derived", "catalog", "minute"),
            ("derived", "catalog", "quotes"),
            ("derived", "catalog", "close"),
        }
    ), "完整名单下目录仍然给核心能力或非查询成员开了绑定：它没在做事"

    # 五格"今天什么都没挡住"的保留名：现场造一个同名成员，看名单到底值不值。
    for name in sorted(catalog._RESERVED_CORE_CAPABILITIES):
        monkeypatch.setattr(WebQuoteSession, name, staticmethod(probe), raising=False)
        without = discover_with(catalog._NOT_A_QUERY_MEMBER)
        assert ("derived", "catalog", name) in without, (
            f"类上凭空长出同名方法 {name!r} 时，摘掉保留名单并不会开出第二个家——"
            f"那 {name} 这一格是空话，该从名单里删掉"
        )
        kept = discover_with(full)
        assert ("derived", "catalog", name) not in kept, (
            f"保留名单挡不住 {name!r}：同名一出现就多一个家，核心分派与 web 家各说各话"
        )


def test_the_discovery_loop_and_the_ruler_share_one_lens() -> None:
    """口径自检：判据量的那一圈必须就是发现环遍历的那一圈。

    发现环换了 ``predicate``（例如把属性也收进来）时，成员黑名单会从"真的挡东西"变成"解释一个
    看不见的类"，而八b 照样全绿——这一格不让它静默地换尺子。
    """
    members = _discovery_members()
    assert len(members) >= _MIN_MEMBERS, f"成员集合只有 {len(members)} 个，判据在读空"

    saved = catalog._SKIP_WEB_METHODS
    catalog._SKIP_WEB_METHODS = frozenset()
    try:
        everything = {item.method for item in catalog._discover_web_bindings()}
    finally:
        catalog._SKIP_WEB_METHODS = saved
    assert everything == members, (
        f"发现环遍历的圈子与判据量的圈子不是同一把尺子：绑定侧多 {sorted(everything - members)}、"
        f"少 {sorted(members - everything)}"
    )
    skipped_today = sorted(members - {item.method for item in catalog._discover_web_bindings()})
    assert skipped_today == sorted(members & saved), (
        f"跳过名单实际挡掉的是 {skipped_today}，与名单和成员圈的交集不符——它被别的东西代劳了"
    )
