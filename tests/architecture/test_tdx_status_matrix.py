# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""连通性矩阵（:file:`docs/tdx_status.md`）的每个格子都要由代码现推。

这张表是"哪些接口真能用"的对外口径，而它此前恰好是仓里最容易过期的那份抄本：命令账本把
一条命令判成 offline、或客户端在发包前把它拦下之后，表里的 ✅ 不会自己变。本轮实测到的
三处失真都在这一类——批量快照（账本 offline）标着 ✅、两条被 `NotImplementedFeature`
拦在结构化 API 外面的能力标着 ✅、还有一行写着"自动降级东财 clist"（那是 pre-v17 门面的
行为，随门面删除）。更糟的是有一行配了一个**账本里根本不存在的命令号**。

所以这里不校对措辞，只校对判据：命令号必须存在；标记档位必须与账本的
`status`/`verified` 以及客户端的发包拦截表一致；账本里每一条 offline/`degraded` 的
quotation 命令都必须在表里有一行（漏登记等于把一条停答命令从对外矩阵里抹掉）；而 7709
实时面上恒空的三个公开字段（G8）必须写在调用方读得到的地方。
"""

from __future__ import annotations

import dataclasses
import re
from pathlib import Path

from tstdx.client import core as client_core
from tstdx.domain.models import Quote
from tstdx.protocol.commands import COMMANDS, STATUS_DEGRADED, STATUS_OFFLINE

ROOT = Path(__file__).resolve().parents[2]
DOC = ROOT / "docs" / "tdx_status.md"
API_DOC = ROOT / "docs" / "api" / "interfaces.md"

_HEX = re.compile(r"^0x[0-9A-Fa-f]{4}$")
_ANY_HEX = re.compile(r"0x[0-9A-Fa-f]{4}")
_LEDGER_IDS = {cmd for (_family, cmd) in COMMANDS}
#: 三个档位：给数 / 内容与可用性存疑 / 结构化入口一调即抛。
_MARKS = ("✅", "⚠️", "⛔")

OFFLINE_IDS = {entry.cmd for entry in COMMANDS.values() if entry.status == STATUS_OFFLINE}
DEGRADED_IDS = {entry.cmd for entry in COMMANDS.values() if entry.status == STATUS_DEGRADED}
BLOCKED_IDS = set(client_core._UNVERIFIED_STRUCTURED_BLOCK)  # noqa: SLF001
FALLBACK_OK_IDS = set(client_core._OFFLINE_FALLBACK_OK)  # noqa: SLF001
#: 账本判"真机 golden 锁过"的命令号（任一 family）。
VERIFIED_IDS = {entry.cmd for entry in COMMANDS.values() if entry.verified}


def _text() -> str:
    return DOC.read_text(encoding="utf-8").replace("\r\n", "\n")


def _section(heading_prefix: str) -> str:
    """取某个小节（`## ` 或 `### ` 起头）的内容，到下一个同级或更高级标题为止。"""
    text = _text()
    start = -1
    for m in re.finditer(r"^#{2,3} .*$", text, re.MULTILINE):
        if m.group(0).startswith(heading_prefix):
            start = m.end()
            break
    assert start != -1, f"找不到小节 {heading_prefix!r}——本判据自身失效"
    rest = text[start:]
    nxt = re.search(r"^#{2,3} ", rest, re.MULTILINE)
    return rest if nxt is None else rest[: nxt.start()]


def _matrix_rows(heading_prefix: str) -> list[tuple[str, int, int, str]]:
    """取该小节里带命令号 + 档位的行：``(接口, 命令号, 档位下标, 档位原文)``。

    按形状识别而不是按行号：某一格恰好是 `0xNNNN`、紧跟一格恰好以档位开头。这样 §二
    那种"0x120F 族"写在括号里的行、以及表头/分隔行都自然不进判据。
    """
    rows: list[tuple[str, int, int, str]] = []
    for line in _section(heading_prefix).splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        cells = [c.strip() for c in stripped.strip("|").split("|")]
        hex_indexes = [i for i, cell in enumerate(cells) if _HEX.match(cell)]
        if len(hex_indexes) != 1:
            continue
        at = hex_indexes[0]
        if at + 1 >= len(cells):
            continue
        mark = cells[at + 1]
        mark_index = next((i for i, m in enumerate(_MARKS) if mark.startswith(m)), -1)
        if mark_index == -1:
            continue
        name = cells[0] or cells[1]
        rows.append((name, int(cells[at], 16), mark_index, mark))
    return rows


def test_every_command_id_written_in_the_matrix_exists_in_the_ledger() -> None:
    """表里出现过的每个命令号都必须在命令账本里——幻影命令号是这张表最坏的失真。

    全篇扫（不止带档位的那些行）：一个只出现在散文里的 `0xNNNN` 同样会被读者当成事实。
    本表没有任何"退役命令号登记"的豁免节，所以这条判据不需要例外名单——写不进去的号就
    不该出现在这里。
    """
    cited = {int(token, 16) for token in _ANY_HEX.findall(_text())}
    assert len(cited) >= 15, f"全篇只扫到 {len(cited)} 个命令号，扫描自身失效"
    phantom = sorted(f"0x{cmd:04X}" for cmd in cited - _LEDGER_IDS)
    assert phantom == [], f"docs/tdx_status.md 点了账本里没有的命令号：{phantom}"


def test_matrix_marks_agree_with_the_ledger_and_the_dispatch_block() -> None:
    """档位与代码同源：✅ 要求账本真给数，⚠️/⛔ 必须说得出是哪条判据把它降下来的。

    两个方向都判。只判"不许把死命令标 ✅"会漏掉反向的失真——把一条 `online` + `verified`
    的命令标成 ⛔，等于对外少承诺一格能力，与虚报同样糟。
    """
    rows = _matrix_rows("## 一、") + _matrix_rows("## 三、")
    assert len(rows) >= 15, f"只解析到 {len(rows)} 行带命令号的格子，判据自身失效"
    problems: list[str] = []
    for name, cmd, mark_index, mark in rows:
        dead = cmd in OFFLINE_IDS or cmd in BLOCKED_IDS
        shaky = cmd in DEGRADED_IDS or cmd not in VERIFIED_IDS
        if mark_index == 0:  # ✅
            reasons = []
            if cmd in OFFLINE_IDS:
                reasons.append("账本 offline")
            if cmd in BLOCKED_IDS:
                reasons.append("结构化 API 发包前拦截")
            if cmd in DEGRADED_IDS:
                reasons.append("账本 degraded")
            if cmd not in VERIFIED_IDS:
                reasons.append("账本 verified=False（布局未经真机 golden 锁定）")
            if reasons:
                problems.append(f"{name} 0x{cmd:04X} 标 ✅ 而账本说：{'、'.join(reasons)}")
        elif mark_index == 2 and not dead:  # ⛔
            problems.append(
                f"{name} 0x{cmd:04X} 标 ⛔（一调即抛），但它既不在 offline 也不在发包拦截表里"
            )
        elif cmd in FALLBACK_OK_IDS and mark_index != 1:
            #: 唯一被 `_OFFLINE_FALLBACK_OK` 放行的 offline 命令：它真的会发包并给出数据（逐只
            #: 回退），所以既不许写 ✅（账本 offline）也不许写 ⛔（并不是一调即抛）。
            problems.append(
                f"{name} 0x{cmd:04X} 是 _OFFLINE_FALLBACK_OK 里放行的一条，档位应为 ⚠️，实为 {mark!r}"
            )
        elif mark_index == 1 and not (dead or shaky):  # ⚠️
            problems.append(
                f"{name} 0x{cmd:04X} 标 ⚠️（内容存疑），而账本 online+verified 且不在拦截表里：{mark}"
            )
    assert problems == [], "docs/tdx_status.md 的实测档位与代码不符：" + "；".join(problems)


def test_every_dead_quotation_command_has_a_row_in_the_matrix() -> None:
    """账本判死（offline / degraded）的 quotation 命令必须逐条出现在矩阵里。

    漏一行等于把一条停答命令从对外口径里抹掉——读者只会看到"剩下的全部打通"。
    分母取账本自身，不抄清单。
    """
    dead_quotation = {
        cmd
        for (_family, cmd), entry in COMMANDS.items()
        if entry.family == "quotation" and entry.status in (STATUS_OFFLINE, STATUS_DEGRADED)
    }
    assert len(dead_quotation) >= 8, (
        f"账本里只有 {len(dead_quotation)} 条死掉的 quotation 命令，取分母自身失效"
    )
    listed = {cmd for (_n, cmd, _i, _m) in _matrix_rows("## 一、")}
    missing = sorted(f"0x{cmd:04X}" for cmd in dead_quotation - listed)
    assert missing == [], f"这些已停答/降级的 7709 命令在矩阵里没有行：{missing}"


def test_the_composed_snapshot_row_stays_out_of_the_command_ledger() -> None:
    """`snapshot` 那一格是两跳拼出来的，不许长出命令号。

    它此前写着"0x0535 五档全量"，等于把一个不存在的命令号 + 一个给不了的字段一起写进对外
    矩阵。这一格只许以"组合面"出现在命令号列，且必须与 §一之二 那句"不含盘口深度"同读。
    """
    rows = [line for line in _section("## 一、").splitlines() if "snapshot 快照合成" in line]
    assert len(rows) == 1, "§一 里 `snapshot` 行应当恰好一条"
    cells = [c.strip() for c in rows[0].strip().strip("|").split("|")]
    assert cells[1] == "组合面", f"组合面的命令号列不该是命令号：{cells[1]!r}"
    assert not _ANY_HEX.search(rows[0]), "组合面那一行不许引用任何命令号"
    assert "§一之二" in rows[0], "组合面行没把盘口深度的去向指到 §一之二"


def test_the_always_empty_quote_fields_are_written_where_a_caller_reads_them() -> None:
    """G8：`Quote` 在 7709 实时面上恒空的字段，必须写在读者会读的那两份文档里。

    分母取 `Quote` 的字段名而不是抄"datetime/bid/ask"三个词：解析器哪天真的填了这些格，
    §一之二 那句话就成了谎——那时本判据会红，逼着改文档的人去确认是哪一半变了。
    """
    section = _section("### 一之二")
    fields = {item.name for item in dataclasses.fields(Quote)}
    empty = {name for name in ("datetime", "bid", "ask") if name in fields}
    assert empty == {"datetime", "bid", "ask"}, "Quote 的字段名变了，本判据的分母要跟着改"
    assert section, "找不到 §一之二"
    for name in sorted(empty):
        assert name in section, f"§一之二 没写 `{name}`"
        assert name in _section("## 一、"), f"§一 的表格里看不到 `{name}` 这一格恒空"
    #: 接口文档是调用方查字段的那一份；连通性矩阵只算第二处。两处都要点名。
    api = API_DOC.read_text(encoding="utf-8").replace("\r\n", "\n")
    for name in sorted(empty):
        assert name in api, f"docs/api/interfaces.md 没写 `Quote.{name}` 在 7709 实时面上的形状"
    assert "不臆造" in section and "不臆造" in api, (
        "§一之二 与接口文档都要说清这是『不臆造未锁定布局』的主动留空，而不是丢字段"
    )


def test_every_dead_cell_names_the_exception_the_caller_actually_gets() -> None:
    """⛔ 那一档的全部含义是"调用方拿到的是异常"，所以行里必须写出异常类名，且写对。

    类名由代码现推（`_guard_offline` 对 inferred 拦截抛 `NotImplementedFeature`、对账本
    offline 抛 `CommandOffline`），不靠文档作者记得是哪一个——写错异常名的行与没写一样有害，
    因为调用方会去 `except` 一个永不发生的类。
    """
    problems: list[str] = []
    rows = _matrix_rows("## 一、") + _matrix_rows("## 三、")
    for line in _section("## 一、").splitlines() + _section("## 三、").splitlines():
        stripped = line.strip()
        if not stripped.startswith("|") or "⛔" not in stripped:
            continue
        cells = [c.strip() for c in stripped.strip("|").split("|")]
        at = next((i for i, cell in enumerate(cells) if _HEX.match(cell)), None)
        if at is None or at + 1 >= len(cells) or not cells[at + 1].startswith("⛔"):
            continue
        cmd = int(cells[at], 16)
        expected = "NotImplementedFeature" if cmd in BLOCKED_IDS else "CommandOffline"
        body = "".join(cells)
        if expected not in body:
            problems.append(f"{cells[0]} 0x{cmd:04X}：⛔ 行没写调用方拿到的 {expected}")
        wrong = {
            name
            for name in ("CommandOffline", "NotImplementedFeature")
            if name != expected and name in body
        }
        if wrong:
            problems.append(
                f"{cells[0]} 0x{cmd:04X}：写成了 {sorted(wrong)}，代码抛的是 {expected}"
            )
    assert len(rows) >= 15, "判据自身失效：一行都没扫到"
    assert problems == [], "⛔ 档位与真实抛点不符：" + "；".join(problems)


def test_the_matrix_never_claims_an_automatic_source_switch() -> None:
    """单内核不替换 Provider：矩阵格子里不许出现"换到别处去取"的说法。

    只扫 §一/§三 的表格区（那两节是对外的现行口径），历史语境节 §四/§五 不在此列。
    每次这类词回到表格里，都意味着有人把 pre-v17 门面的行为当成今天的事实抄了回来。
    """
    body = _section("## 一、") + _section("## 三、")
    offenders = [token for token in ("兜底", "降级到", "自动降级") if token in body]
    assert offenders == [], f"矩阵格子里又出现了自动换源的说法：{offenders}"
