"""周期词表的派生门禁：``atst/domain/period.py`` 是唯一声明处，其余各面只许派生。

第 18 轮第一次把"用户打字的那串周期拼写"上分母，量到的是**同一份库、同一个旋钮、
两张面给不同答案**：

* ``atst/client/core.py::_PERIOD_TO_CATEGORY`` 曾是 24 个手写字面量键，与
  ``atst/domain/period.py::PERIOD_ALIASES``（29 键）各抄一份别名，两份的交集只有 14 格；
* 于是 15 个拼写经运行期规范路径能取到数据、经 ``Client.bars(period=...)`` 直接报
  ``ParseError``：``1d`` ``1mo`` ``1w`` ``1y`` ``m1`` ``m5`` ``m15`` ``m30`` ``m60``
  ``monthly`` ``q`` ``quarterly`` ``weekly`` ``y`` ``yearly``（本轮实测，见行动记录）；
* ``atst/web/_session_market.py::KLINES_PERIOD_ALIASES`` 是第三份手抄的 19 键，
  与上面两份又不相同；
* ``atst/query.py::_MINUTE_PERIODS`` 是第四份（决定 ``minute_kline`` 还是 ``kline`` 通道）；
* ``Period.QUARTER = "quarter"`` 则是把 :attr:`Period.SEASON` 的一个**别名**登记成了
  平级成员——域内规范表明写 ``"quarter" -> "season"``。

本轮把这些收成一条不变式：**每张面的接受集 = 域内词表按"这一面服务得起哪几档规范周期"
过滤后的派生**。差异只允许出现在"能不能服务"这一维，不许出现在"怎么打字"这一维。

判据分三层，缺一层就有一条退化路径：
① 词表自身合法（别名不许指向别名、规范集合与档案层 ``Period`` 逐格相同）；
② 各面**行为**一致（同一拼写在各面要么给出同一个协议号，要么被"这一面不服务"显式拒）；
③ 派生关系可证（AST 现读那两张表的赋值右侧，手抄字面量键一回来就当众红）。
"""

from __future__ import annotations

import ast
import re

from tests.support.field_readers import REPO_ROOT, module_assignment, string_keys_of_table
from atst.client.core import _CANONICAL_TO_CATEGORY, _PERIOD_TO_CATEGORY, period_to_category
from atst.domain.period import (
    CANONICAL_PERIODS,
    MINUTE_PERIODS,
    PERIOD_ALIASES,
    normalize_bar_period,
)
from atst.query import _canonical_unified_channel
from atst.reader.profile import Period
from atst.web._session_market import _KLINE_SERVABLE, KLINES_PERIOD_ALIASES
from atst.web.tencent.adapters import KlineSource

#: 第 18 轮实测那 15 个"一面能查、另一面报错"的拼写。它们是本轮的来路，
#: 留着是为了让"派生"这一步不许悄悄退回去。
FORMERLY_DIVERGENT = (
    "1d",
    "1mo",
    "1w",
    "1y",
    "m1",
    "m5",
    "m15",
    "m30",
    "m60",
    "monthly",
    "q",
    "quarterly",
    "weekly",
    "y",
    "yearly",
)


def _literal_alias_keys(node: ast.expr) -> set[str]:
    """赋值右侧那些**手抄成字面量**的别名键——派生表应当一个都没有。"""

    return {
        key.value
        for key in ast.walk(node)
        if isinstance(key, ast.Constant)
        and isinstance(key.value, str)
        and key.value in PERIOD_ALIASES
    }


# --------------------------------------------------------------------------- #
# ① 唯一声明处自身
# --------------------------------------------------------------------------- #
def test_the_vocabulary_is_not_blind() -> None:
    """防盲：任何一张表空了都是尺子失效，不是"很干净"。"""

    assert CANONICAL_PERIODS, "规范周期表空了，判据自身失效"
    assert PERIOD_ALIASES, "别名表空了：用户只剩一种打字方式，本族的账无从谈起"
    assert _PERIOD_TO_CATEGORY, "category 表空了：库面的周期入口已断"
    assert _KLINE_SERVABLE, "web 面服务集为空：本文件关于『按服务能力过滤』的判据失去对象"


def test_no_alias_points_at_another_alias() -> None:
    """别名必须一步到规范拼写：``a->b->c`` 意味着词表里还藏着一份非规范成员。"""

    canonical = set(CANONICAL_PERIODS)
    bad = {
        alias: target
        for alias, target in PERIOD_ALIASES.items()
        if target not in canonical or alias in canonical
    }
    assert not bad, f"别名表里有指向非规范拼写（或键本身是规范拼写）的格：{sorted(bad.items())}"


def test_quarter_spelling_stays_an_alias_and_never_a_member() -> None:
    """G13 那格的正控：``quarter`` 是 ``season`` 的别名，档案层不许再登记它为成员。"""

    assert PERIOD_ALIASES["quarter"] == "season"
    assert normalize_bar_period("quarter") == "season"
    assert period_to_category("quarter") == period_to_category("season")
    values = {getattr(Period, name) for name in dir(Period) if name.isupper()}
    assert "quarter" not in values, "Period 又把一个别名当平级周期登记了：季线的规范拼写是 season"


# --------------------------------------------------------------------------- #
# ② 各面行为一致
# --------------------------------------------------------------------------- #
def test_every_public_spelling_gets_one_answer_on_both_library_paths() -> None:
    """本轮的裁决：同一拼写在「直查 category 表」与「先规范再查」两条路径上必须同解。

    第 18 轮之前这条量出 15 格不一致（``1y`` 等：一条给 11，另一条 ``ParseError``）。
    """

    spellings = set(PERIOD_ALIASES) | set(CANONICAL_PERIODS) | set(_PERIOD_TO_CATEGORY)
    divergent = []
    for spelling in sorted(spellings):
        direct = _PERIOD_TO_CATEGORY.get(spelling)
        via_normalizer = _PERIOD_TO_CATEGORY.get(normalize_bar_period(spelling))
        if direct != via_normalizer:
            divergent.append((spelling, direct, via_normalizer))
    assert not divergent, f"两条路径给出不同答案的拼写：{divergent}"


def test_the_fifteen_formerly_divergent_spellings_now_reach_the_kernel() -> None:
    """来路账：那 15 个曾被 ``Client.bars`` 拒掉的写法现在必须真能解开。"""

    unaccepted = [
        spelling for spelling in FORMERLY_DIVERGENT if spelling not in _PERIOD_TO_CATEGORY
    ]
    assert not unaccepted, f"这些拼写又走不回库面了：{unaccepted}"
    assert period_to_category("1y") == period_to_category("year")
    assert period_to_category("q") == period_to_category("season")


def test_the_web_face_accepts_exactly_its_servable_canonical_periods() -> None:
    """web 面只许声明「服务得起哪几档」，别名一律由域内词表派生。

    它服务不了 ``tick``/``season``/``year``（上游 ifzq 没有对应参数），所以那几档仍显式
    报错——但**报错的理由必须是"这一面不服务"，而不是"这串拼写我们不认识"**。
    """

    expected = {
        **{alias: canon for alias, canon in PERIOD_ALIASES.items() if canon in _KLINE_SERVABLE},
        **{canon: canon for canon in _KLINE_SERVABLE},
    }
    assert expected == KLINES_PERIOD_ALIASES, (
        "web 面的周期接受集不再等于「域内词表 ∩ 本面服务集」："
        f"{sorted(set(KLINES_PERIOD_ALIASES) ^ set(expected))}"
    )
    assert set(_KLINE_SERVABLE) <= set(CANONICAL_PERIODS), "web 面服务了非规范周期拼写"
    assert set(_KLINE_SERVABLE) <= set(KlineSource.PERIODS), (
        "web 面声称服务的周期在 K 线源那里没有参数落点"
    )


def test_minute_channel_routing_follows_the_canonical_minutes() -> None:
    """``minute_kline`` / ``kline`` 的分叉点只认规范分钟档：别名先规范再分叉。"""

    assert frozenset(p for p in CANONICAL_PERIODS if p.endswith("min")) == MINUTE_PERIODS, (
        f"分钟档集合与规范周期表不再一致：{sorted(MINUTE_PERIODS)}"
    )
    wrong = [
        alias
        for alias, canon in PERIOD_ALIASES.items()
        if (canon in MINUTE_PERIODS) != (normalize_bar_period(alias) in MINUTE_PERIODS)
    ]
    assert not wrong, f"这些别名会被派到错误的通道：{wrong}"
    for alias in ("1m", "m5", "60m", "1hour"):
        assert _canonical_unified_channel("tencent", "bars", normalize_bar_period(alias)) == (
            "minute_kline"
        ), f"{alias} 规范化后没走分钟通道"
    assert _canonical_unified_channel("tencent", "bars", normalize_bar_period("daily")) == "kline"


# --------------------------------------------------------------------------- #
# ③ 派生关系可证：手抄回来要当场红
# --------------------------------------------------------------------------- #
def test_the_category_and_web_tables_are_derived_not_recopied() -> None:
    """AST 现读：这两张表的赋值右侧必须引用域内那份词表，字面量别名键一回来就红。"""

    for relative, table in (
        ("atst/client/core.py", "_PERIOD_TO_CATEGORY"),
        ("atst/web/_session_market.py", "KLINES_PERIOD_ALIASES"),
    ):
        node = module_assignment(relative, table)
        assert any(
            isinstance(item, ast.Name) and item.id == "PERIOD_ALIASES" for item in ast.walk(node)
        ), f"{table} 不再从 atst.domain.period.PERIOD_ALIASES 派生：又成了手抄件"
        recopied = _literal_alias_keys(node)
        assert not recopied, f"{table} 里出现了手抄的别名字面量键：{sorted(recopied)}"


def test_the_hand_written_part_holds_only_protocol_numbers() -> None:
    """手写的只剩「规范拼写 → 协议号」这一张表，且除 ``tick`` 外每档都要有编号。"""

    assert string_keys_of_table("atst/client/core.py", "_CANONICAL_TO_CATEGORY") == set(
        _CANONICAL_TO_CATEGORY
    ), "category 表混进了非字面量键：手写部分与派生部分的边界变了"
    assert string_keys_of_table("atst/client/core.py", "_PERIOD_TO_CATEGORY") == set(), (
        "派生表里又出现了字面量键：那张表只许由域内词表派生"
    )
    missing = sorted(set(CANONICAL_PERIODS) - {"tick"} - set(_CANONICAL_TO_CATEGORY))
    assert not missing, f"这些规范周期没有协议落点：{missing}"
    assert "tick" not in _CANONICAL_TO_CATEGORY, (
        "tick 有了 K 线 category 落点：分笔不是一条 K 线，本判据的前提要重查"
    )
    assert set(_PERIOD_TO_CATEGORY.values()) == set(_CANONICAL_TO_CATEGORY.values()), (
        "派生表引入了新的 category：别名与规范拼写解开了不同的档"
    )


def test_profile_layer_period_members_are_the_canonical_spellings() -> None:
    """档案层 ``Period`` 的取值集合 == 域内规范集合：两边不许各自长词。"""

    declared = {
        name: value
        for name, value in vars(Period).items()
        if name.isupper() and isinstance(value, str)
    }
    assert set(declared.values()) == set(CANONICAL_PERIODS), (
        f"Period 与 CANONICAL_PERIODS 分叉：{sorted(set(declared.values()) ^ set(CANONICAL_PERIODS))}"
    )


# --------------------------------------------------------------------------- #
# ④ 读者文档那张表钉回运行期：``docs/api/interfaces.md`` §「K 线周期拼写」
# --------------------------------------------------------------------------- #
#: 第 19 轮把周期词表写进了读者文档；写进去的那一刻起，它就是一张第二手词表。
#: 这一节的判据把它钉回运行期真相源：改一张面的服务集而忘了改文档，红。
#:
#: ``120min``/``1200min`` 是新浪专属档（tdx 协议没有对应 category，不进规范集合），
#: 但它们是公开写法，所以要算进"接受写法数"这一列。
PROVIDER_SCOPED = frozenset({"120min", "1200min"})
_SPELLINGS = sorted(set(CANONICAL_PERIODS) | set(PERIOD_ALIASES) | PROVIDER_SCOPED)


def _accepted_by_normalizer(served: set[str]) -> set[str]:
    """会话面 / 内核口径：公开写法先规范再交裸源，故写法集是"规范化后落在服务集里"的那些。"""

    return {s for s in _SPELLINGS if normalize_bar_period(s) in served}


def _runtime_surface() -> dict[str, tuple[int, int]]:
    """每一行文档 → ``(服务档数, 接受写法数)``，全部现读运行期表。"""

    from atst.web._session_market import _KLINE_SERVABLE, KLINES_PERIOD_ALIASES
    from atst.web.baidu.adapters import _KLINE_KTYPES
    from atst.web.eastmoney.adapters import EastmoneyHistoryKlineSource
    from atst.web.sina.adapters import SinaHistoryKlineSource
    from atst.web.tencent.adapters import _MKLINE_PERIODS, KlineSource

    def row(served: set[str], accepted: set[str]) -> tuple[int, int]:
        return (len(served), len(accepted))

    sina = set(SinaHistoryKlineSource.SCALES)
    eastmoney = set(EastmoneyHistoryKlineSource.KLTS)
    baidu = set(_KLINE_KTYPES)
    tencent = set(KlineSource.PERIODS)
    mkline = set(_MKLINE_PERIODS)
    return {
        "`Client.bars` / 统一 `bars` 查询（CLI `bars --period`、HTTP、WS、MCP 同此）": row(
            set(_CANONICAL_TO_CATEGORY), set(_PERIOD_TO_CATEGORY)
        ),
        "`WebQuoteSession.klines`（ifzq 会话面）": row(
            set(_KLINE_SERVABLE), set(KLINES_PERIOD_ALIASES)
        ),
        '`WebQuoteSession.history(source="sina")`': row(sina, _accepted_by_normalizer(sina)),
        '`WebQuoteSession.history(source="eastmoney")`': row(
            eastmoney, _accepted_by_normalizer(eastmoney)
        ),
        "`baidu_kline`": row(baidu, _accepted_by_normalizer(baidu)),
        "`SinaHistoryKlineSource`（裸源，只收规范拼写）": row(sina, sina),
        "`EastmoneyHistoryKlineSource`（裸源）": row(eastmoney, eastmoney),
        "`KlineSource`（腾讯 fqkline 裸源）": row(tencent, tencent),
        "腾讯 mkline 分钟裸源": row(mkline, mkline),
    }


def _documented_surface(markdown: str) -> dict[str, tuple[int, int]]:
    """读文档那张表：行首单元格是入口，``（N）`` 是服务档数，第二列是接受写法数。"""

    start = markdown.index("### K 线周期拼写")
    body = markdown[start + 4 :].split("\n### ")[0]
    rows = [
        line for line in body.splitlines() if line.startswith("| ") and not line.startswith("|---")
    ]
    out: dict[str, tuple[int, int]] = {}
    for line in rows[1:]:  # rows[0] 是表头
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        assert len(cells) == 4, f"文档表不是四列形状：{cells}"
        served_text, accepted_text = cells[1], cells[2]
        assert served_text.endswith("）"), f"服务档一列没写出「（N）」档数：{served_text!r}"
        out[cells[0]] = (
            int(re.search(r"（(\d+)）$", served_text).group(1)),
            int(accepted_text),
        )
    return out


def _mismatches(
    documented: dict[str, tuple[int, int]], runtime: dict[str, tuple[int, int]]
) -> list[str]:
    """把两份账比出人话：改行名、改档数、改写法数都要单独叫出来。"""

    out: list[str] = []
    if set(documented) - set(runtime):
        out.append(f"文档有这一节没有的入口行：{sorted(set(documented) - set(runtime))}")
    if set(runtime) - set(documented):
        out.append(f"运行期有、文档漏了的入口行：{sorted(set(runtime) - set(documented))}")
    out += [
        f"{label} 文档写着 服务 {documented[label][0]} 档 / 接受 {documented[label][1]} 写法，"
        f"运行期现读是 {runtime[label][0]} / {runtime[label][1]}"
        for label in set(documented) & set(runtime)
        if documented[label] != runtime[label]
    ]
    return out


def test_the_documented_period_surface_is_the_runtime_one() -> None:
    """读者文档里的周期表必须逐行等于运行期现读的那份。"""

    markdown = (REPO_ROOT / "docs" / "api" / "interfaces.md").read_text(encoding="utf-8")
    documented = _documented_surface(markdown)
    runtime = _runtime_surface()
    assert len(documented) >= 9, f"文档表只读出 {len(documented)} 行：本判据失去对象"
    assert not _mismatches(documented, runtime), "\n".join(_mismatches(documented, runtime))


def test_the_doc_ruler_is_not_blind() -> None:
    """正控：把某一行的档数改错一格，上面那条尺子必须只点出那一行。"""

    runtime = _runtime_surface()
    planted = dict(runtime)
    label = "`baidu_kline`"
    planted[label] = (runtime[label][0] + 1, runtime[label][1] - 3)
    hits = _mismatches(planted, runtime)
    assert len(hits) == 1 and label in hits[0], f"文档尺子对错位行无感：{hits}"
    assert _mismatches(runtime, runtime) == []
