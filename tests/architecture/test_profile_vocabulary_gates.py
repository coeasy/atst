"""档案层词表门禁：`atst/reader/profile.py` 的七个常量类只登记有人按它行动的词。

本族判据此前量过 `QueryPlan`（F-50）、`Provenance`（F-52）、`ProviderSpec`/`ChannelSpec`
（F-54）、`StreamPlan`（F-55）、`ProvenanceKind`（F-57）、`Command`（F-64）、
`DataProfile`/`MarketPreset`（G10/G11），漏了它们共同依赖的那七座"枚举常量类"。
第 17 轮第一次上分母，逐格量出的是一张没人查的二手词表：

* ``Market`` 登记 12 个市场，符号引擎只认 5 个（``parse_symbol(market="cffex")`` 当场
  ``SymbolError``），另外 6 个既无读取点也无取值落点；``Market.DIRS`` 是
  ``resolve_vipdoc_path`` 的第二份手抄件；``Market.ALL`` 一个读取点都没有。
* ``Period.CMD_CATEGORY`` 不仅没人查，还**与线上口径矛盾**：它把 ``quarter`` 记成 10，
  而 ``KlineCategory.NAMES[10]`` 是 ``season``；它拿 ``"day_alt"`` 当键，那根本不是
  ``Period`` 的成员。``Period.FILE_EXT`` 是 ``resolve_vipdoc_path`` 缺了一半（只有扩展名、
  没有目录名）的第二份手抄件。
* ``AssetClass`` 的 6 个成员、``PriceEncoding.INT32``、``TimeEncoding.EPOCH`` 同样零兑现：
  ``"int32"`` 的两处字面量属于 :mod:`atst.tools.codegen` 的**字段类型**词表，全仓没有任何
  地方把 ``"epoch"`` 写进档案或按它分支。

**本轮真正的收获是那格假绿**：按名统计 ``Market.ALL`` 时量到 8 个读取点，逐条解析后全部
落在 :class:`atst.domain.symbol.Market`（与本类的 ``Market`` **重名而不同定义**）。于是尺子
升级成先解析导入再记账（`tests/support/field_readers.constant_class_vocabulary`），而
``test_the_name_collision_is_what_the_by_name_ruler_cannot_see`` 把这格陷阱钉成正控：尺子
一旦退回按名扫描，它当众红。

三档周期 ``QUARTER``/``YEAR``/``SEASON`` 第 17 轮留下来了，因为它们的**取值**确实有人按。
第 18 轮把那句话落实成派生：周期词表只有 :mod:`atst.domain.period` 一处声明，
``atst/client/core.py`` 的 category 表由它派生，``QUARTER`` 则被量出是 :attr:`Period.SEASON`
的**别名**（``"quarter" -> "season"``）却被登记成平级成员，已删。**G13 随之关闭**，
跨面一致性由 `tests/architecture/test_period_vocabulary_gates.py` 守着。
"""

from __future__ import annotations

import re
from pathlib import Path

from tests.support.field_readers import (
    REPO_ROOT,
    ConstantVocabulary,
    constant_class_vocabulary,
    member_reference_sites,
    string_keys_of_table,
)
from atst.domain.period import CANONICAL_PERIODS
from atst.reader.profile import (
    AmountUnit,
    AssetClass,
    Market,
    Period,
    PriceEncoding,
    TimeEncoding,
    VolumeUnit,
)

TARGET = "atst/reader/profile.py"
CLASSES = (
    "Market",
    "AssetClass",
    "Period",
    "PriceEncoding",
    "VolumeUnit",
    "AmountUnit",
    "TimeEncoding",
)

#: 本轮删掉的词：一座"备着但链路没人走"的词表比缺词更危险，复活它必须过判据。
DELETED: dict[str, list[str]] = {
    "Market": ["ALL", "DIRS", "CFFEX", "DCE", "CZCE", "INE", "GFEX", "FX"],
    "AssetClass": ["ALL", "ETF", "LOF", "BOND", "WARRANT", "FX", "OTHER"],
    "Period": ["ALL", "FILE_EXT", "CMD_CATEGORY", "QUARTER"],
    "PriceEncoding": ["INT32"],
    "TimeEncoding": ["EPOCH"],
}

#: 零 ``Class.MEMBER`` 读取点、只以**取值字符串**被消费的成员：``类.成员`` → 兑现它的键表。
#: 第 18 轮起那张表是"规范拼写 → 协议 category"的十行手写表，别名一律派生。
_BY_VALUE_CONSUMERS = {
    "Period.YEAR": ("atst/client/core.py", "_CANONICAL_TO_CATEGORY"),
    "Period.SEASON": ("atst/client/core.py", "_CANONICAL_TO_CATEGORY"),
}

#: ``presets.py`` 允许直接写数字的市场号：TDX 扩展市场号，确实不在 ``Market.CODES`` 五段内
#: （该模块 docstring 明说了这点）。名单之外的硬编码就是第二份手抄口径。
_TDX_EXTENSION_MARKET_IDS = {73, 75}


def _vocab() -> ConstantVocabulary:
    return constant_class_vocabulary(TARGET, CLASSES)


def _target_class(cls: str) -> type:
    """按类名拿到运行时的那个类——防"删了类里的名字、又在别处补一个同名常量"。"""

    return {
        "Market": Market,
        "AssetClass": AssetClass,
        "Period": Period,
        "PriceEncoding": PriceEncoding,
        "VolumeUnit": VolumeUnit,
        "AmountUnit": AmountUnit,
        "TimeEncoding": TimeEncoding,
    }[cls]


# --------------------------------------------------------------------------- #
# 尺子自身：先证明它看得见，再让它去裁决别人
# --------------------------------------------------------------------------- #
def test_the_scan_is_not_blind() -> None:
    vocab = _vocab()
    assert vocab.scanned > 150, f"只解析到 {vocab.scanned} 个模块，扫描自身失效"
    empty = sorted(cls for cls in CLASSES if not vocab.members[cls])
    assert empty == [], f"这些类已经没有成员了，判据自身失效：{empty}"
    assert vocab.reads, "一个读取点都没解析到本定义，是尺子与代码脱节"
    # 解析不出的命中必须为 0：那说明有人用了本尺子不认的绑定形态（如 ``import a.b as m``
    # 再写 ``m.Market.SH``），那种写法会让读取点静默消失，宁可当场红也不悄悄记成孤儿。
    assert not vocab.unresolved, f"出现解析不出的绑定形态：{vocab.unresolved}"


def test_class_bodies_hold_only_constants_and_tables() -> None:
    """类体只允许 ``NAME = "value"`` 与 ``TABLE = {...}``：新形态不给尺子加分支就会隐身。"""

    stray = {cls: names for cls, names in _vocab().other.items() if names}
    assert stray == {}, f"这些常量类长出了尺子不认的形态：{stray}"


# --------------------------------------------------------------------------- #
# 形状：名字与顺序都要人签字
# --------------------------------------------------------------------------- #
def test_member_names_and_order_are_pinned() -> None:
    actual = {cls: tuple(_vocab().members[cls]) for cls in CLASSES}
    assert actual == {
        "Market": ("SH", "SZ", "BJ", "HK", "US", "SHFE"),
        "AssetClass": ("STOCK", "INDEX", "FUTURE", "OPTION"),
        "Period": (
            "TICK",
            "M1",
            "M5",
            "M15",
            "M30",
            "M60",
            "DAY",
            "WEEK",
            "MONTH",
            "YEAR",
            "SEASON",
        ),
        "PriceEncoding": ("UINT32", "FLOAT32", "VARINT"),
        "VolumeUnit": ("SHARE", "LOT", "CONTRACT"),
        "AmountUnit": ("YUAN", "WAN", "YI"),
        "TimeEncoding": ("YYYYMMDD", "LC16", "DATETIME32"),
    }, f"档案层词表形状变了：{actual}"


def test_no_member_is_a_second_name_for_one_value() -> None:
    """一名两值会让「按名查表」与「按值查表」给出不同答案——一座词表不许有两种写法。"""

    vocab = _vocab()
    for cls in CLASSES:
        values = list(vocab.values[cls].values())
        dupes = sorted({value for value in values if values.count(value) > 1})
        assert not dupes, f"{cls} 有成员共用同一取值：{dupes}"


def test_deleted_vocabularies_stay_deleted() -> None:
    """本轮删掉的成员/表不许悄悄回来：复活它要同时改判据与文档，那得是一次有意识的动作。"""

    vocab = _vocab()
    for cls, names in DELETED.items():
        live = {name for name in names if vocab.is_member(cls, name) or vocab.is_table(cls, name)}
        assert not live, f"{cls} 又登记了零兑现的词：{sorted(live)}"
        runtime = sorted(name for name in names if hasattr(_target_class(cls), name))
        assert not runtime, f"{cls} 的属性复活了：{runtime}"


# --------------------------------------------------------------------------- #
# 核心裁决：每个词都要有人按它行动
# --------------------------------------------------------------------------- #
def test_every_member_is_acted_on() -> None:
    """成员合法 ⟺ 有人 ``Class.MEMBER`` 读它，或它是**有人查的表**的键，或它的取值有人按。"""

    vocab = _vocab()
    unacted: list[str] = []
    for cls in CLASSES:
        queried = {table for table in vocab.tables[cls] if vocab.sites(cls, table)}
        table_keys = {
            member for table in queried for member in vocab.members_in_tables(cls).get(table, [])
        }
        for member in vocab.members[cls]:
            if vocab.sites(cls, member) or member in table_keys:
                continue
            consumer = _BY_VALUE_CONSUMERS.get(f"{cls}.{member}")
            if consumer is None:
                unacted.append(f"{cls}.{member}（零读取点、不是有人查的表的键、无按值消费者）")
                continue
            relative, table = consumer
            value = vocab.values[cls][member]
            if value not in string_keys_of_table(relative, table):
                unacted.append(f"{cls}.{member} 的取值 {value!r} 已不在 {table} 的键里")
    assert unacted == [], f"档案层词表里没人按它行动的词：{unacted}"


def test_every_table_is_queried() -> None:
    """类级表必须有人查：一张没人查的表就是下一份手抄口径的温床（``CMD_CATEGORY`` 案）。"""

    vocab = _vocab()
    dead = [
        f"{cls}.{table}"
        for cls in CLASSES
        for table in vocab.tables[cls]
        if not vocab.sites(cls, table)
    ]
    assert dead == [], f"这些表没有任何读取点解析到本定义：{dead}"


def test_docstring_declares_the_derived_member_count() -> None:
    """类 docstring 首行的份数由成员数现推——手抄份数正是本轮删掉的那类腐烂。"""

    vocab = _vocab()
    for cls in CLASSES:
        doc = (_target_class(cls).__doc__ or "").strip()
        assert doc, f"{cls} 没有 docstring：本轮的删除理由要写在词表旁边"
        first = doc.splitlines()[0]
        match = re.search(r"（(\d+)\s*[类档种]", first)
        assert match, f"{cls} 的 docstring 首行没写「（N 类/档）」：{first!r}"
        declared = int(match.group(1))
        assert declared == len(vocab.members[cls]), (
            f"{cls} docstring 声称 {declared} 档，实有 {len(vocab.members[cls])} 档"
        )


# --------------------------------------------------------------------------- #
# 正控：重名陷阱、G13 现场、市场号的单一来源
# --------------------------------------------------------------------------- #
def test_the_name_collision_is_what_the_by_name_ruler_cannot_see() -> None:
    """``atst/domain/symbol.py`` 与本文件**都有** ``class Market``：命中要记对人家。

    本轮真实踩过的格子——按名统计给档案层的 ``Market.ALL`` 记了 8 个读取点，解析导入后
    全部属于符号引擎。这条同时按住两种退化：尺子退回按名扫描，或符号层不再暴露
    ``Market.ALL``（那时也要重新确认这格证据还在，而不是把判据删掉了事）。
    """

    vocab = _vocab()
    assert "ALL" not in vocab.members["Market"], "档案层的 ALL 回来了，本判据的前提已变"
    assert vocab.sites("Market", "ALL") == set(), "档案层 Market.ALL 竟有读取点，解析逻辑待复核"
    assert any(
        item.startswith("atst/domain/symbol.py") for item in vocab.foreign.get("Market.ALL", set())
    ), "符号层 Market.ALL 的命中没被记成『他定义』：尺子不再区分同名两处定义"
    _, by_name = member_reference_sites("Market")
    assert "ALL" in by_name, "按名尺子已看不到 Market.ALL——它是否还会张冠李戴需要重新评估"


def test_g13_is_closed_the_period_category_table_is_derived() -> None:
    """G13 已关闭的现场：``Period`` 只按值消费两档，而那张 category 表**不许再手抄别名**。

    第 17 轮登记 G13 时，``_PERIOD_TO_CATEGORY`` 是 24 个手写字面量键，与域内规范表各抄
    一份别名；第 18 轮它变成派生表，手写部分只剩"规范拼写 → 协议号"。本判据按第 17 轮
    判据 docstring 自己指的路改写：接线后要检查的是**派生关系**，而不是把成员删掉。
    一旦有人把别名重新写成字面量键（第 5 份手抄件复活），``_table_is_derived`` 那条当场红。
    """

    vocab = _vocab()
    keys = string_keys_of_table("atst/client/core.py", "_CANONICAL_TO_CATEGORY")
    by_value = {
        member: vocab.values["Period"][member]
        for member in vocab.members["Period"]
        if not vocab.sites("Period", member)
    }
    assert {f"Period.{name}" for name in by_value} == set(_BY_VALUE_CONSUMERS), (
        f"只按值消费的周期成员已经变了：{sorted(by_value)}"
    )
    assert set(by_value.values()) <= keys, (
        f"这些取值已不是 category 表的键：{sorted(by_value.values())}"
    )
    # 手写那张表的键必须仍是规范拼写：别名回到这里就是 G13 复活。
    assert keys <= set(CANONICAL_PERIODS), f"category 表里出现了非规范周期拼写：{sorted(keys)}"


def test_market_codes_table_is_the_single_source_for_market_numbers() -> None:
    """档案层的市场号只有一处声明：预设从 ``Market.CODES`` 取，扩展号另行登记在册。"""

    vocab = _vocab()
    assert vocab.sites("Market", "CODES") >= {
        "atst/profile/presets.py",
        "atst/profile/detect.py",
    }, f"市场号消费者已变：{sorted(vocab.sites('Market', 'CODES'))}"
    source = Path(REPO_ROOT / "atst/profile/presets.py").read_text(encoding="utf-8")
    hardcoded = {int(num) for num in re.findall(r"market_id=(\d+)", source)}
    undeclared = sorted(hardcoded - _TDX_EXTENSION_MARKET_IDS)
    assert not undeclared, f"预设里出现了未登记的市场号：{undeclared}"
