# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""告警通道的结构性门禁：类别表不许虚设，发射口不许旁路，分派点不许丢袋。

``WarningCode`` 是一张名单，名单的价值取决于有没有人把守它（F-39/F-42 的教训：
抄一次就过期的清单比没有清单更糟）。这里的判据分别看住名单的几个方向，
并且各自带"扫不到东西就自杀"的自检。
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

from tests.support.field_readers import member_reference_sites, members_referenced
from tstdx.diagnostics import WarningCode

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "tstdx"
CHANNEL = "tstdx/diagnostics.py"
#: 面向使用者的类别表所在文档与小节标题。
WARN_DOC = ROOT / "docs" / "errors.md"
WARN_SECTION = "## 一之四、结果侧数据瑕疵：`WarningCode`"
_PY_IN_CELL = re.compile(r"`([^`]+\.py)`")

#: 允许直接 ``warnings.warn`` 的文件与理由。豁免的门槛是"这条告警**不是**某个结果的
#: 事实"——只有通道的发射口自身过这道门槛。执行器的 binding 审计曾在此列（"import 期
#: 没有 QueryPlan 可以携带它"）：那句话只解释了它为什么不能进通道，没有解释它为什么
#: 可以不报错——它现在直接 raise。``tstdx/deprecation.py`` 也曾在此列（"API 生命周期
#: 提示"）：那个模块在包内零消费者，V18 第 8 轮按 F-74/D3 删除，豁免随宿主一起撤销。
BARE_WARN_ALLOWED: dict[str, str] = {
    CHANNEL: "通道的唯一发射口",
}


def _warning_warn_calls(tree: ast.AST) -> int:
    hits = 0
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "warn"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "warnings"
        ):
            hits += 1
    return hits


def test_bare_warn_calls_stay_inside_the_channel_and_one_justified_site() -> None:
    """新增一条绕过通道的 ``warnings.warn`` = 重新制造一条 wire 上看不见的事实。"""
    found: dict[str, int] = {}
    for path in sorted(SOURCE.rglob("*.py")):
        calls = _warning_warn_calls(ast.parse(path.read_text(encoding="utf-8"), filename=str(path)))
        if calls:
            found[str(path.relative_to(ROOT)).replace("\\", "/")] = calls
    assert found, "全仓一个 warnings.warn 都没扫到，说明扫描自身失效了"
    outside = sorted(set(found) - set(BARE_WARN_ALLOWED))
    assert outside == [], f"绕过告警通道的发射点：{outside}"
    stale = sorted(
        item for item, reason in BARE_WARN_ALLOWED.items() if item not in found and reason
    )
    assert stale == [], f"豁免名单里仍有文件已经不再直接 warn，请撤销豁免：{stale}"


def test_every_declared_warning_code_has_an_emit_site() -> None:
    """声明了却没人发射的类别，就是 wire 上一个永远不会出现的键（幻影开关的镜像）。

    遍历本身取自 :func:`tests.support.field_readers.members_referenced`——同一把尺子在
    第 31 步量 `ProvenanceKind`，两处判据不该各养一份 AST 走查。
    """

    scanned, emitted = members_referenced("WarningCode", skip=CHANNEL)
    assert scanned > 30, f"只扫到 {scanned} 个模块，扫描自身失效"
    assert emitted, "全仓没有一处 WarningCode 引用，说明扫描自身失效了"
    declared = {item.name for item in WarningCode}
    dead = sorted(declared - emitted)
    undeclared = sorted(emitted - declared)
    assert dead == [], f"声明了却无人发射的告警类别：{dead}"
    assert undeclared == [], f"引用了不存在的告警类别：{undeclared}"


def test_every_dispatch_site_forwards_the_decode_caveats() -> None:
    """解码层每一条判断都必须有人读：`_mixin.py` 的每个分派点共用同一个转发口。

    第 26 步（F-51）的接线只覆盖了 ``bars`` 一条命令：15 个 ``dispatch(`` 调用点里
    14 个仍然 ``return result.rows``，把"声明 N 实收 M""降级为 L3 透传"整族丢弃
    （F-63①）。所以这条判据不能钉在某个函数上，只能钉在形状上——**任何调用
    ``dispatch(`` 的函数体内必须出现 ``_forward_decode_caveats(``**，否则修一条漏十四条。
    """
    tree = ast.parse((SOURCE / "client" / "_mixin.py").read_text(encoding="utf-8"))

    def called_names(node: ast.AST) -> set[str]:
        names: set[str] = set()
        for item in ast.walk(node):
            if not isinstance(item, ast.Call):
                continue
            if isinstance(item.func, ast.Name):
                names.add(item.func.id)
            elif isinstance(item.func, ast.Attribute):
                names.add(item.func.attr)
        return names

    scanned = 0
    unwired: list[str] = []
    for fn in ast.walk(tree):
        if not isinstance(fn, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        hits = called_names(fn)
        if "dispatch" not in hits:
            continue
        scanned += 1
        if "_forward_decode_caveats" not in hits:
            unwired.append(fn.name)
    assert scanned >= 15, f"只扫到 {scanned} 个含 dispatch 调用的函数，扫描自身失效"
    assert unwired == [], f"这些分派点把解码层的判断丢在了袋里：{unwired}"


#: 模板函数名与公共方法名不一致的两处：0x06B9 的单包 seam 只由 ``file_download``
#: 消费；``request`` 与 ``request_result`` 共用 `_t_request_result` 这一个分派口。
_LABEL_NAME_ALIASES = {"_t_file_download_once": "file_download", "_t_request_result": "request"}


def test_every_forwarded_caveat_is_blamed_on_the_method_that_asked_for_it() -> None:
    """转发标签的前缀必须是用户真能调用的方法名，并且与本函数一一对应。

    15 个标签是手写的：复制粘贴把 ``_t_ex_bars`` 的标签写成 ``goods_bars(`` 这类错，
    数据照旧、判断照旧上 wire，只有归属换了个人——所以钉在"标签前缀 == 本模板的
    公共名"这个对应关系上，而不是钉在某一条文案的字样上。
    """
    from tstdx.client.async_ import AsyncTdxClient
    from tstdx.client.sync import (
        ExMarketClient,
        F10Client,
        GoodsClient,
        MacClient,
        TdxClient,
    )

    api: set[str] = set()
    for cls in (TdxClient, AsyncTdxClient, GoodsClient, ExMarketClient, MacClient, F10Client):
        api |= {item for item in dir(cls) if not item.startswith("_")}
    assert {"bars", "quotes", "goods_bars", "ex_bars", "file_download"} <= api, "取公共名自身失效"

    tree = ast.parse((SOURCE / "client" / "_mixin.py").read_text(encoding="utf-8"))
    scanned = 0
    offenders: list[str] = []
    for fn in ast.walk(tree):
        if not isinstance(fn, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        for call in ast.walk(fn):
            if (
                not isinstance(call, ast.Call)
                or not isinstance(call.func, ast.Name)
                or call.func.id != "_forward_decode_caveats"
                or len(call.args) < 2
            ):
                continue
            scanned += 1
            label = call.args[1]
            head = ""
            if isinstance(label, ast.JoinedStr) and label.values:
                first = label.values[0]
                head = first.value if isinstance(first, ast.Constant) else ""
            elif isinstance(label, ast.Constant):
                head = str(label.value)
            expected = _LABEL_NAME_ALIASES.get(fn.name, fn.name.removeprefix("_t_"))
            # 标签允许三种起法：`name(`、`name(arg=`、`name `（后跟别的标识符字符
            # 就算另一个名字，比如把 ex_bars 写成 barsfoo）。
            ok = head.startswith(expected) and (
                len(head) == len(expected) or head[len(expected)] in ("(", " ")
            )
            if not ok or expected not in api:
                offenders.append(f"{fn.name}: 标签前缀 {head!r}，应为公共方法 {expected!r}")
    assert scanned >= 15, f"只扫到 {scanned} 处转发调用，扫描自身失效"
    assert offenders == [], "解码告警指名道姓错了：" + "；".join(offenders)


def test_the_forwarder_records_into_the_channel_it_claims_to_fill() -> None:
    """转发口若不再 ``record_warning``，上一条判据就成了空转的形状检查。

    两头都要钉：读 ``ParseResult.warnings`` + 走 ``DECODE_CAVEAT`` 发射，缺任一
    都等于把接回来的判断原样丢掉。
    """
    tree = ast.parse((SOURCE / "client" / "_mixin.py").read_text(encoding="utf-8"))
    fn = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "_forward_decode_caveats"
    )
    reads_caveats = any(
        isinstance(node, ast.Attribute) and node.attr == "warnings" for node in ast.walk(fn)
    )
    records = any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "record_warning"
        for node in ast.walk(fn)
    )
    uses_decode_caveat_code = any(
        isinstance(node, ast.Attribute) and node.attr == "DECODE_CAVEAT" for node in ast.walk(fn)
    )
    assert reads_caveats and records and uses_decode_caveat_code, (
        "_forward_decode_caveats 不再把 ParseResult.warnings 发进 DECODE_CAVEAT 通道"
    )


def test_the_forwarder_also_runs_the_domain_ruler_over_every_page() -> None:
    """G7：同一个转发口必须把值域尺子也跑在每一页的行上。

    两路判断在 wire 上是同一个缺陷的两半：解码层自己记下的判断（上一条）与解码层
    **不记**、只有把值放回域里量才看得见的错位（这一条）。少了这一环，"这页字段错位"
    就又回到只有读源码的人才知道的地方——实采载荷的重放判据在
    ``tests/client/test_decode_caveat_wiring.py``，本判据一旦空转它立刻红。
    """
    tree = ast.parse((SOURCE / "client" / "_mixin.py").read_text(encoding="utf-8"))
    fn = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "_forward_decode_caveats"
    )
    reads_rows = any(
        isinstance(node, ast.Attribute) and node.attr == "rows" for node in ast.walk(fn)
    )
    calls_ruler = any(
        isinstance(node, ast.Call)
        and (
            (isinstance(node.func, ast.Name) and node.func.id == "row_violations")
            or (isinstance(node.func, ast.Attribute) and node.func.attr == "row_violations")
        )
        for node in ast.walk(fn)
    )
    uses_domain_code = any(
        isinstance(node, ast.Attribute) and node.attr == "FIELD_OUT_OF_DOMAIN"
        for node in ast.walk(fn)
    )
    assert reads_rows and calls_ruler and uses_domain_code, (
        "_forward_decode_caveats 不再把行交给值域尺子/不再发 FIELD_OUT_OF_DOMAIN"
        f"（读行={reads_rows} 调尺子={calls_ruler} 发码={uses_domain_code}）"
    )


def _warning_table_rows() -> dict[str, list[str]]:
    """取 `docs/errors.md` 那张类别表的行：``code`` → 其余各列。"""
    text = WARN_DOC.read_text(encoding="utf-8").replace("\r\n", "\n")
    start = text.find(WARN_SECTION)
    assert start != -1, f"docs/errors.md 里找不到小节 {WARN_SECTION!r}——判据自身失效"
    rest = text[start + len(WARN_SECTION) :]
    end = rest.find("\n## ")
    section = rest if end == -1 else rest[:end]
    rows: dict[str, list[str]] = {}
    for line in section.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 3 or not cells[0].startswith("`"):
            continue
        code = cells[0].strip("`")
        if code == "code":  # 表头
            continue
        assert code not in rows, f"类别表里 {code!r} 出现两行，判据读到的将是巧合"
        rows[code] = cells[1:]
    return rows


def test_the_user_doc_table_is_the_same_closed_set_as_the_enum_and_names_real_emitters() -> None:
    """类别要有使用者读得到的说明，而说明里的"谁发射"必须是事实。

    三个方向一起判：文档漏一行 = 该类别在面向使用者的文档里隐身（调用方在 wire 上见到
    一个没人解释过的键）；文档多一行 = 幻影类别；"发射口"一列与代码引用点不等 = 把一条
    判断记在了不产出它的模块头上。分母取枚举自身，路径集合取 `tstdx/` 现扫，两处都不抄名单。
    """
    scanned, sites = member_reference_sites("WarningCode", skip=CHANNEL)
    assert scanned > 30, f"只扫到 {scanned} 个模块，扫描自身失效"
    assert len(sites) >= 10, f"只扫到 {sorted(sites)} 这些发射点，少于枚举规模，扫描自身失效"
    rows = _warning_table_rows()
    declared = {item.name: item.value for item in WarningCode}
    assert set(rows) == set(declared.values()), (
        f"文档类别表与枚举不等：文档多 {sorted(set(rows) - set(declared.values()))}、"
        f"文档缺 {sorted(set(declared.values()) - set(rows))}"
    )
    problems: list[str] = []
    for name, value in sorted(declared.items()):
        when, outlet = rows[value]
        if len(when) < 20:
            problems.append(f"{value}: 『什么时候会出现』一列只有 {when!r}，等于没写")
        cited = set(_PY_IN_CELL.findall(outlet))
        if cited != sites[name]:
            problems.append(
                f"{value}: 文档写发射口 {sorted(cited)}，代码里引用它的却是 {sorted(sites[name])}"
            )
    assert problems == [], "docs/errors.md 的 WarningCode 表与代码不符：" + "；".join(problems)
