"""F-84：Web 源登记表必须逐格等于运行期 ``KNOWN_SOURCES``，因为它是那张登记表的唯一读者。

第 26 轮第 1 遍把"声明了却没人行动"这条尺子（G39 家族）从协议面泛化到文档面时，量到的最大一块
缺口不在代码里，而在文档里：``tstdx.web.sources.KNOWN_SOURCES`` 登记了 31 个 HTTP 适配器，
每个 ``SourceSpec`` 写着 ``summary`` / ``capabilities`` / ``notes``，而**全仓没有一个读者读这三格**
（普查读数见 ``scratch_v18b26/probe26/census26a_afterfix.log``，``SourceSpec`` 一行把三者列为
NO-READ）。更直接的用户侧后果是 ``docs/configuration.md`` 那两行——
``[web] enabled_sources`` 与 ``[web] rate_limit`` 都写着"须属于 ``KNOWN_SOURCES``"，
却从没列出合法取值到底是哪些名字。

处置不是给三个字段找一个虚构的内部读取方，而是把这张登记表交给它的读者：
``docs/api/interfaces.md``「Web 源登记」一节逐名列出 31 个源名、摘要、能力与接口告诫，
本判据再把它逐格钉回运行期登记表。三列因此各自有了代价：改代码里的 ``notes`` 不改文档，红；
文档凭空写一条上游坑，红。

防"尺子自己瞎"的三处正控在 :func:`test_the_ruler_itself_sees_a_planted_drift`
（删一行 / 改一格告诫 / 换一种能力拼写），另有一处管分母（标题里的 31）。
"""

from __future__ import annotations

import re

import pytest

from tests.support.field_readers import REPO_ROOT
from tstdx.web.sources import KNOWN_SOURCES

pytestmark = pytest.mark.unit

DOC = REPO_ROOT / "docs" / "api" / "interfaces.md"

HEADING = re.compile(
    r"^### Web 源登记（`tstdx\.web\.sources\.KNOWN_SOURCES`，(?P<count>\d+) 个）$", re.M
)
TABLE_HEADER = "| 源名 | 摘要 | 能力 | 接口告诫（`SourceSpec.notes`） |"

#: 表头四列对应的登记表字段。列名不进判据，字段进：加一列就得在这里登记它读谁。
COLUMNS = ("name", "summary", "capabilities", "notes")

_TICKED = re.compile(r"`([^`\n]+)`")


# ---------------------------------------------------------------------------
# 运行期那一侧
# ---------------------------------------------------------------------------


def runtime_rows() -> dict[str, dict[str, object]]:
    return {
        name: {
            "summary": spec.summary,
            "capabilities": list(spec.capabilities),
            "notes": spec.notes,
        }
        for name, spec in KNOWN_SOURCES.items()
    }


# ---------------------------------------------------------------------------
# 文档那一侧
# ---------------------------------------------------------------------------


def doc_section(text: str) -> str:
    matched = HEADING.search(text)
    if matched is None:
        raise AssertionError(
            "接口文档里没有「Web 源登记」一节（或标题形状变了）——这一节是 31 个源名的唯一读者面"
        )
    tail = text[matched.end() :]
    cut = re.search(r"^(?:## |---$)", tail, re.M)
    return tail[: cut.start()] if cut else tail


def doc_rows(text: str) -> dict[str, dict[str, object]]:
    """从表头那一行往下读，直到第一个非表格行——节内、节外都吃得下。"""
    lines = text.splitlines()
    if TABLE_HEADER not in lines:
        raise AssertionError("Web 源登记表的表头形状变了，本判据读不到任何行")
    rows: dict[str, dict[str, object]] = {}
    for line in lines[lines.index(TABLE_HEADER) + 1 :]:
        if not line.lstrip().startswith("|"):
            break
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        assert len(cells) == len(COLUMNS), f"这一行有 {len(cells)} 格，表头声明 {COLUMNS}：{line}"
        if set("".join(cells)) <= {"-"}:
            continue
        name, summary, capabilities, notes = cells
        key = name.strip("`")
        assert key not in rows, f"源名 `{key}` 在表里出现两次"
        rows[key] = {
            "summary": summary,
            "capabilities": _TICKED.findall(capabilities),
            "notes": notes,
        }
    return rows


@pytest.fixture(scope="module")
def doc_text() -> str:
    text = DOC.read_text(encoding="utf-8")
    assert TABLE_HEADER in text, "Web 源登记表被整段删掉了：那是 31 个源名唯一的读者可见清单"
    return text


# ---------------------------------------------------------------------------
# 判据
# ---------------------------------------------------------------------------


def test_the_registry_is_big_enough_for_a_shared_denominator() -> None:
    """分母下限：登记表退化成个位数时，下面的逐格对账会一起变空，先拦住。"""
    assert len(KNOWN_SOURCES) >= 30, f"KNOWN_SOURCES 只剩 {len(KNOWN_SOURCES)} 个源，判据失去对象"


def test_the_documented_source_set_is_the_registered_one(doc_text: str) -> None:
    documented, runtime = set(doc_rows(doc_text)), set(KNOWN_SOURCES)
    assert documented == runtime, (
        f"文档多出未登记的源名 {sorted(documented - runtime)}；"
        f"文档漏掉已登记的源名 {sorted(runtime - documented)}"
    )


def test_the_heading_count_is_the_registered_one(doc_text: str) -> None:
    matched = HEADING.search(doc_text)
    assert matched, "标题行是这张表的分母，不能改形状"
    assert int(matched["count"]) == len(KNOWN_SOURCES), (
        f"标题写 {matched['count']} 个，登记表有 {len(KNOWN_SOURCES)} 个"
    )
    assert len(doc_rows(doc_text)) == len(KNOWN_SOURCES), "表体行数与分母不符"


def test_every_summary_cell_is_the_registered_one(doc_text: str) -> None:
    rows, runtime = doc_rows(doc_text), runtime_rows()
    drift = [
        f"{name}: 文档「{rows[name]['summary']}」vs 登记「{runtime[name]['summary']}」"
        for name in runtime.keys() & rows.keys()
        if rows[name]["summary"] != runtime[name]["summary"]
    ]
    assert not drift, "摘要漂移：" + "；".join(drift)


def test_every_capability_cell_is_the_adapters_own_tuple_in_its_own_order(doc_text: str) -> None:
    """顺序也管：这一列就是 ``SourceSpec.capabilities``，插一档漏写一格都红。"""
    rows, runtime = doc_rows(doc_text), runtime_rows()
    drift = [
        f"{name}: 文档 {rows[name]['capabilities']} vs 登记 {runtime[name]['capabilities']}"
        for name in runtime.keys() & rows.keys()
        if rows[name]["capabilities"] != runtime[name]["capabilities"]
    ]
    assert not drift, "能力漂移：" + "；".join(drift)


def test_every_warning_cell_is_the_adapters_own_note(doc_text: str) -> None:
    rows, runtime = doc_rows(doc_text), runtime_rows()
    drift = [
        f"{name}: 文档「{rows[name]['notes']}」vs 登记「{runtime[name]['notes']}」"
        for name in runtime.keys() & rows.keys()
        if rows[name]["notes"] != runtime[name]["notes"]
    ]
    assert not drift, "接口告诫漂移：" + "；".join(drift)


def test_both_narrative_columns_are_filled_for_every_row(doc_text: str) -> None:
    """三列没有一格是空的：空的 ``summary`` / ``notes`` 让这一列整体失去读者。"""
    blank = sorted(name for name, spec in KNOWN_SOURCES.items() if not spec.summary.strip())
    assert not blank, f"这些源的 summary 是空的：{blank}"
    blank = sorted(name for name, spec in KNOWN_SOURCES.items() if not spec.notes.strip())
    assert not blank, f"这些源的 notes 是空的：{blank}"
    rows = doc_rows(doc_text)
    assert all(str(rows[name]["summary"]).strip() for name in rows), "表里有空的摘要格"
    assert all(str(rows[name]["notes"]).strip() for name in rows), "表里有空的告诫格"


def test_no_registered_text_can_break_the_table_shape() -> None:
    """``|`` 是格界：任何字段带竖线都会把 4 列撑成 5 列，而 Markdown 不会报错，只会静默错格。"""
    offenders = sorted(
        f"{name}.{field}"
        for name, spec in KNOWN_SOURCES.items()
        for field in ("summary", "notes")
        if "|" in getattr(spec, field)
    )
    assert not offenders, f"登记表文本含竖线，表格会错格：{offenders}"


def test_every_source_name_the_config_docs_accept_is_registered() -> None:
    """``docs/configuration.md`` 用 ``KNOWN_SOURCES`` 约束 ``enabled_sources`` / ``rate_limit``：
    那份约束的默认值必须真在这张表里，否则文档给的示例配置会被运行期拒绝。"""
    from tstdx.config.loader import DEFAULT_CONFIG

    defaults = list(DEFAULT_CONFIG.web.enabled_sources)
    assert set(defaults) <= set(KNOWN_SOURCES), f"缺省源顺序点了未登记的名字：{defaults}"
    tabled = set(doc_rows(DOC.read_text(encoding="utf-8")))
    assert set(defaults) <= tabled, f"缺省源顺序在读者那份表里查不到：{defaults}"


# ---------------------------------------------------------------------------
# 正控：四处单点篡改，尺子必须各抓出自己那一格
# ---------------------------------------------------------------------------


def test_the_ruler_itself_sees_a_planted_drift(doc_text: str) -> None:
    section = doc_section(doc_text)
    rows, runtime = doc_rows(doc_text), runtime_rows()
    assert len(rows) == len(runtime), "基准行数就不对，后面的对比全无效"

    victim = sorted(rows)[0]

    dropped = "\n".join(
        line for line in section.splitlines() if not line.startswith(f"| `{victim}` |")
    )
    assert set(doc_rows(dropped)) == set(rows) - {victim}, f"删掉 `{victim}` 那一行却看不出来"

    renamed = section.replace(f"| `{victim}` |", "| `renamed-source` |", 1)
    assert set(doc_rows(renamed)) ^ set(runtime) == {victim, "renamed-source"}, "改个源名没人看见"

    note_line = next(line for line in section.splitlines() if line.startswith(f"| `{victim}` |"))
    old_note = str(rows[victim]["notes"])
    blinded = note_line.replace(old_note, "这条告诫是编的", 1)
    mutated = section.replace(note_line, blinded, 1)
    assert doc_rows(mutated)[victim]["notes"] == "这条告诫是编的", "告诫格篡不动，判据读错了列"
    assert any(
        row["notes"] != runtime[name]["notes"]
        for name, row in doc_rows(mutated).items()
        if name in runtime
    ), "改了告诫格却不红"

    body = [line for line in section.splitlines() if line.startswith("| `")]
    assert len(body) == len(rows), "表体行数读不齐，正控失效"
    ticked = _TICKED.findall
    multi_line = next(line for line in body if len(ticked(line.split("|")[3])) > 1)
    caps = ticked(multi_line.split("|")[3])
    name_of_multi = ticked(multi_line.split("|")[1])[0]
    swapped = multi_line.replace(f"`{caps[0]}` / `{caps[1]}`", f"`{caps[1]}` / `{caps[0]}`", 1)
    assert swapped != multi_line, "能力格拼写形状与预期不符，正控失效"
    reordered = section.replace(multi_line, swapped, 1)
    assert (
        doc_rows(reordered)[name_of_multi]["capabilities"] != runtime[name_of_multi]["capabilities"]
    ), "调换两档能力的顺序却不红"
