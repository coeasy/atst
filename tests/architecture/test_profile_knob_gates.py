"""档案/预设层的"声明了没人读"门禁：`DataProfile` 与 `MarketPreset`。

这一族判据此前量过 `QueryPlan`（F-50）、`Provenance`/`ResultMeta`（F-52）、
`ProviderSpec`/`ChannelSpec`（F-54）、`StreamPlan`（F-55）、`ProvenanceKind`（F-57）、
`Command`（F-64），漏了 `atst/reader/profile.py` 与 `atst/profile/presets.py`：
本地文件解码链的规格档案从来没进过分母。第一次上分母量出的就是两格——

* **G10 `DataProfile.timezone`**：探测链声明了"时区"，运行期零读取点，却被
  ``DetectionResult.to_dict()['profile']['timezone']`` 原样递给用户，等于在诊断输出里
  断言"这份数据属于 Asia/Shanghai"。而 `DataProfile` 的字段按它自己模块的定义全是
  **解码器真正按它行动的差异维度**，时区不是：本库对时间戳的对外口径是"服务端按
  交易所本地时间给字符串，库不换算、不提供 tz 出参"（`docs/FAQ.md`），所以这个旋钮
  既没人读、读到了也没人能用。已删除。
* **G11 `MarketPreset` 的整列编码先验**：``typical_categories``/``quote_scale``/
  ``volume_unit``/``price_encoding``/``time_encoding``/``default_period`` 六列加
  ``data_profile_kwargs()``/``to_dict()`` 两个方法，生产读取点 **0**、调用点 **0**、
  用户文档 **0**。模块 docstring 却写着"为 detect 提供先验（先验越强，探测置信度
  越高）"——真实先验只有 ``market_id`` 一处（``_match_preset_name`` 命中给 +0.1
  加权）。更要紧的是那座桥把**行情快照**口径的 ``quote_scale`` 原样填进 K 线解码器
  的 ``price_scale``，两套口径之间没有换算：黄金/期货快照记 1000，日线档案
  ``future_day`` 记 100，接线即十分之一价。于是本表只剩身份三列。

尺子还是全仓唯一那一把（`tests/support/field_readers.unread_field_sites`），本族为它
添了两件此前没有的保险：

1. **读取者白名单**：owner 名单里的 ``p`` 会同时命中别的类的 ``p.name``——实测
   ``atst/trade/simulator.py`` 就给 `DataProfile.name` 记过一块不存在的水牌。于是只认
   白名单模块里的站点，而白名单每一条都必须在磁盘上存在：名单自身腐烂当场红。
2. **``holders`` 持有点**：``self.profile.amount_unit`` 的链条塌到 ``self``，而
   ``self`` 按名单纪律不能进 owners——不补这一维就会把有人读的 `amount_unit` 量成孤儿。
   这条由 `test_the_holder_path_is_what_buys_amount_unit_a_reader` 把守：名单一旦失效它
   当众红，而不是静默少量一个字段。
"""

from __future__ import annotations

import dataclasses

from atst.profile.presets import PRESETS, MarketPreset
from atst.reader.profile import BUILTIN_PROFILES, DataProfile
from tests.support.field_readers import REPO_ROOT, unread_field_sites

#: 手工核对过：这些名字在生产代码里确实绑定到 DataProfile 实例
#: （``reader/formats.py`` 的 ``profile``/``p``/``prof``/``detected``、
#: ``ProfileDetector.detect`` 的 ``hint``/``base``、``profile/detect.py`` 的
#: ``best_profile``）。不放 ``self``——见 ``tests/support/field_readers.py`` 的名单纪律。
_DATA_PROFILE_OWNERS = {"profile", "p", "prof", "detected", "hint", "base", "best_profile"}

#: DataProfile 实例挂在谁身上的属性名（``self.profile.<字段>``）。
_DATA_PROFILE_HOLDERS = {"profile"}

#: 允许按 DataProfile 字段行动的模块：定义它的解码层 + 两个真实消费者。
_DATA_PROFILE_READERS = {
    "atst/reader/profile.py",
    "atst/reader/formats.py",
    "atst/sink/local_day.py",
    "atst/profile/detect.py",
}

_PRESET_OWNERS = {"p", "preset"}

#: 允许按 MarketPreset 字段行动的模块：表自身（``match_preset``）与探测层的加权点。
_PRESET_READERS = {"atst/profile/presets.py", "atst/profile/detect.py"}

#: 只登记身份、不参与解码的字段。豁免不是"允许空白"：它们必须仍然存在于档案形状里
#: （``test_identity_exemptions_still_name_real_fields``），必须真的出现在序列化产物里
#: （``test_identity_labels_reach_the_user``——这是豁免唯一的理由），也不许把被删的那格
#: 换个名字收进来（``test_profile_timezone_knob_stays_deleted``）。
_IDENTITY_LABELS = {
    "market": "档案服务于哪个市场目录段；档案表键自述 + 探测报告透出",
    "asset_class": "股票/指数/期货/期权之分；差异本身由 record_size+extra_fields 兑现",
    "period": "这份档案描述的周期；实际布局由 time_encoding+record_size 兑现",
}


def _credited(sites: dict[str, set[str]], readers: set[str]) -> set[str]:
    """只认读取者白名单里的站点；白名单每一条必须还在磁盘上，否则当场红。"""

    for relative in sorted(readers):
        assert (REPO_ROOT / relative).is_file(), f"读取者白名单里的模块已不存在：{relative}"
    return {field for rel, fields in sites.items() if rel in readers for field in fields}


def _measure(
    cls: type, owners: set[str], readers: set[str], *, holders: set[str] | None = None
) -> tuple[set[str], set[str]]:
    """跑一遍尺子并做完三把防盲检查，返回 (全部字段, 白名单内的读取字段)。"""

    fields, scanned, sites = unread_field_sites(cls, owners, holders=holders)
    assert fields, f"{cls.__name__} 已经没有字段了，判据自身失效"
    assert scanned > 30, f"只扫到 {scanned} 个模块，读取扫描自身失效"
    credited = _credited(sites, readers)
    assert credited, f"{cls.__name__}：一个读取点都没命中，是 owner/holder 名单与代码脱节"
    return fields, credited


# --------------------------------------------------------------------------- #
# DataProfile
# --------------------------------------------------------------------------- #
def test_every_data_profile_field_is_acted_on_or_user_visible() -> None:
    fields, credited = _measure(
        DataProfile, _DATA_PROFILE_OWNERS, _DATA_PROFILE_READERS, holders=_DATA_PROFILE_HOLDERS
    )
    orphans = sorted(fields - credited - set(_IDENTITY_LABELS))
    assert orphans == [], f"DataProfile 字段无人按它行动、也不在身份豁免里：{orphans}"


def test_data_profile_field_order_is_pinned() -> None:
    """顺序敏感形状清单：补读取点扫描的另一类洞（同名字段能蒙过扫描，蒙不过清单）。"""

    names = [item.name for item in dataclasses.fields(DataProfile)]
    assert names == [
        "name",
        "market",
        "asset_class",
        "period",
        "price_scale",
        "price_encoding",
        "volume_unit",
        "amount_unit",
        "time_encoding",
        "charset",
        "record_size",
        "extra_fields",
        "confidence",
        "notes",
    ], f"DataProfile 形状变了：{names}"


def test_profile_timezone_knob_stays_deleted() -> None:
    """G10 的正控：时区既不是解码维度，也不许作为豁免悄悄回到形状里。"""

    names = {item.name for item in dataclasses.fields(DataProfile)}
    assert "timezone" not in names, (
        "DataProfile.timezone 回来了：它零读取点，还会被写进 DetectionResult 的报告"
    )
    assert "timezone" not in _IDENTITY_LABELS, "不许把时区塞进豁免清单蒙过上一条"


def test_identity_exemptions_still_name_real_fields() -> None:
    """豁免清单不许活成孤儿：豁免字段的删除必须同时改这里。"""

    names = {item.name for item in dataclasses.fields(DataProfile)}
    stale = sorted(set(_IDENTITY_LABELS) - names)
    assert stale == [], f"豁免清单里有 DataProfile 已不存在的字段：{stale}"


def test_identity_labels_reach_the_user() -> None:
    """豁免的唯一理由是"用户看得到"——那就把这句话变成断言。"""

    payload = DataProfile().to_dict()
    missing = sorted(set(_IDENTITY_LABELS) - set(payload))
    assert missing == [], f"豁免字段没有出现在序列化产物里，豁免理由不成立：{missing}"


def test_the_holder_path_is_what_buys_amount_unit_a_reader() -> None:
    """正控：``amount_unit`` 的读取点 ``self.profile.amount_unit`` 只经 holders 可见。

    谁把持有者属性 ``self.profile`` 改名，这条就当众红——而不是让 `amount_unit` 静默
    变成一个"没人读"的字段、再被下一次清理顺手删掉。
    """

    _, plain = _measure(DataProfile, _DATA_PROFILE_OWNERS, _DATA_PROFILE_READERS)
    assert "amount_unit" not in plain, "不靠 holders 也扫得到 amount_unit，名单纪律该重查"
    _, held = _measure(
        DataProfile, _DATA_PROFILE_OWNERS, _DATA_PROFILE_READERS, holders=_DATA_PROFILE_HOLDERS
    )
    assert "amount_unit" in held, "holders 名单失效：self.profile.<字段> 的读取点扫不到了"


def test_builtin_profiles_are_self_consistent() -> None:
    """档案表每行都得自洽：键名与自述一致、身份列非空、三个换算口都能跑。"""

    assert BUILTIN_PROFILES, "内置档案表空了，判据自身失效"
    for name, profile in BUILTIN_PROFILES.items():
        assert profile.name == name, f"档案表键与自述 name 不一致：{name} != {profile.name}"
        for label, reason in _IDENTITY_LABELS.items():
            assert getattr(profile, label), f"{name} 的身份列 {label} 是空的（{reason}）"
        assert profile.to_price(100) > 0
        assert profile.to_volume(1) >= 0
        assert profile.to_amount(1) >= 0


# --------------------------------------------------------------------------- #
# MarketPreset
# --------------------------------------------------------------------------- #
def test_every_market_preset_field_has_a_reader() -> None:
    """预设表零豁免：一列没人读的编码先验就是下一次"预设能解码"的谎。"""

    fields, credited = _measure(MarketPreset, _PRESET_OWNERS, _PRESET_READERS)
    orphans = sorted(fields - credited)
    assert orphans == [], f"MarketPreset 字段没有任何读取点（无人兑现的声称）：{orphans}"


def test_market_preset_shape_is_pinned() -> None:
    names = [item.name for item in dataclasses.fields(MarketPreset)]
    assert names == ["name", "market_id", "code_prefixes"], f"预设表形状变了：{names}"


def test_preset_table_has_no_road_into_the_decoder() -> None:
    """G11 的正控：预设不得再长出通往 ``DataProfile`` 的出口。"""

    bridge = sorted(
        name
        for name in vars(MarketPreset)
        if "data_profile" in name or name in {"to_dict", "to_profile", "as_profile"}
    )
    assert bridge == [], f"预设又长出解码口径出口：{bridge}"


def test_preset_table_still_covers_the_documented_nine() -> None:
    """形状收缩不许把表本身缩小：用户文档承诺 9 个预设。"""

    assert len(PRESETS) == 9, f"预设表不再是 9 行：{len(PRESETS)}"
    assert {p.name for p in PRESETS.values()} == set(PRESETS), "预设自述名字与表键不一致"
    assert all(p.code_prefixes for p in PRESETS.values()), "有预设没有代码段，match_preset 永不可达"
