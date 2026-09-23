"""档案层词表门禁：`tstdx/reader/profile.py` 的七个常量类只登记有人按它行动的词。

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
  ``"int32"`` 的两处字面量属于 :mod:`tstdx.tools.codegen` 的**字段类型**词表，全仓没有任何
  地方把 ``"epoch"`` 写进档案或按它分支。

**本轮真正的收获是那格假绿**：按名统计 ``Market.ALL`` 时量到 8 个读取点，逐条解析后全部
落在 :class:`tstdx.domain.symbol.Market`（与本类的 ``Market`` **重名而不同定义**）。于是尺子
升级成先解析导入再记账（`tests/support/field_readers.constant_class_vocabulary`），而
``test_the_name_collision_is_what_the_by_name_ruler_cannot_see`` 把这格陷阱钉成正控：尺子
一旦退回按名扫描，它当众红。

三档周期 ``QUARTER``/``YEAR``/``SEASON`` 留下来了，因为它们的**取值**确实有人按：
``tstdx.client.core._PERIOD_TO_CATEGORY`` 用手写字符串键收它们。"同一份周期词表在
``Period``、``_PERIOD_TO_CATEGORY``、``KlineCategory`` 三处各自声明、互不派生"已登记为
**G13**，由 ``test_g13_period_vocabulary_is_still_declared_three_times`` 量着现场。
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

from tests.support.field_readers import (
    REPO_ROOT,
    ConstantVocabulary,
    constant_class_vocabulary,
    member_reference_sites,
)
from tstdx.reader.profile import (
    AmountUnit,
    AssetClass,
    Market,
    Period,
    PriceEncoding,
    TimeEncoding,
    VolumeUnit,
)

TARGET = "tstdx/reader/profile.py"
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
    "Period": ["ALL", "FILE_EXT", "CMD_CATEGORY"],
    "PriceEncoding": ["INT32"],
    "TimeEncoding": ["EPOCH"],
}

#: 零 ``Class.MEMBER`` 读取点、只以**取值字符串**被消费的成员：``类.成员`` → 兑现它的键表。
_BY_VALUE_CONSUMERS = {
    "Period.QUARTER": ("tstdx/client/core.py", "_PERIOD_TO_CATEGORY"),
    "Period.YEAR": ("tstdx/client/core.py", "_PERIOD_TO_CATEGORY"),
    "Period.SEASON": ("tstdx/client/core.py", "_PERIOD_TO_CATEGORY"),
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


def _string_keys_of_table(relative: str, table: str) -> set[str]:
    """读某模块里那张 ``NAME = {...}`` 的字符串键——按值消费的兑现处必须现量。"""

    tree = ast.parse((REPO_ROOT / relative).read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign):
            targets, value = node.targets, node.value
        elif isinstance(node, ast.AnnAssign):
            targets, value = [node.target], node.value
        else:
            continue
        if not any(isinstance(t, ast.Name) and t.id == table for t in targets):
            continue
        if isinstance(value, ast.Dict):
            return {key.value for key in value.keys if isinstance(key, ast.Constant)}
    raise AssertionError(f"{relative} 里已经没有字符串键表 {table}：按值消费的口径变了")


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
            "QUARTER",
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
            if value not in _string_keys_of_table(relative, table):
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
    """``tstdx/domain/symbol.py`` 与本文件**都有** ``class Market``：命中要记对人家。

    本轮真实踩过的格子——按名统计给档案层的 ``Market.ALL`` 记了 8 个读取点，解析导入后
    全部属于符号引擎。这条同时按住两种退化：尺子退回按名扫描，或符号层不再暴露
    ``Market.ALL``（那时也要重新确认这格证据还在，而不是把判据删掉了事）。
    """

    vocab = _vocab()
    assert "ALL" not in vocab.members["Market"], "档案层的 ALL 回来了，本判据的前提已变"
    assert vocab.sites("Market", "ALL") == set(), "档案层 Market.ALL 竟有读取点，解析逻辑待复核"
    assert any(
        item.startswith("tstdx/domain/symbol.py") for item in vocab.foreign.get("Market.ALL", set())
    ), "符号层 Market.ALL 的命中没被记成『他定义』：尺子不再区分同名两处定义"
    _, by_name = member_reference_sites("Market")
    assert "ALL" in by_name, "按名尺子已看不到 Market.ALL——它是否还会张冠李戴需要重新评估"


def test_g13_period_vocabulary_is_still_declared_three_times() -> None:
    """G13 的现场：``Period`` 的三档周期只以取值字符串活在 ``_PERIOD_TO_CATEGORY`` 里。

    接线（让那张键表由 ``Period`` 派生）后这条会红——那时应当关闭 G13 并把本判据改成
    派生关系检查，而不是把三档删掉。
    """

    vocab = _vocab()
    keys = _string_keys_of_table("tstdx/client/core.py", "_PERIOD_TO_CATEGORY")
    by_value = {
        member: vocab.values["Period"][member]
        for member in vocab.members["Period"]
        if not vocab.sites("Period", member)
    }
    assert {f"Period.{name}" for name in by_value} == set(_BY_VALUE_CONSUMERS), (
        f"只按值消费的周期成员已经变了：{sorted(by_value)}"
    )
    assert set(by_value.values()) <= keys, (
        f"这些取值已不是线上键表的键：{sorted(by_value.values())}"
    )
    # 键表还收着 ``Period`` 没有的别名（``d``/``1m``/``daily``…）——这就是"两处声明互不派生"
    aliases = sorted(keys - set(by_value.values()))
    assert len(aliases) > 5, f"周期别名比预想少，G13 的账要重算：{aliases}"


def test_market_codes_table_is_the_single_source_for_market_numbers() -> None:
    """档案层的市场号只有一处声明：预设从 ``Market.CODES`` 取，扩展号另行登记在册。"""

    vocab = _vocab()
    assert vocab.sites("Market", "CODES") >= {
        "tstdx/profile/presets.py",
        "tstdx/profile/detect.py",
    }, f"市场号消费者已变：{sorted(vocab.sites('Market', 'CODES'))}"
    source = Path(REPO_ROOT / "tstdx/profile/presets.py").read_text(encoding="utf-8")
    hardcoded = {int(num) for num in re.findall(r"market_id=(\d+)", source)}
    undeclared = sorted(hardcoded - _TDX_EXTENSION_MARKET_IDS)
    assert not undeclared, f"预设里出现了未登记的市场号：{undeclared}"
