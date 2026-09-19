# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""告警通道的两条结构性门禁：类别表不许虚设，发射口不许旁路。

``WarningCode`` 是一张名单，名单的价值取决于有没有人把守它（F-39/F-42 的教训：
抄一次就过期的清单比没有清单更糟）。这里两条判据分别看住名单的两个方向，
并且各自带"扫不到东西就自杀"的自检。
"""

from __future__ import annotations

import ast
from pathlib import Path

from tests.support.field_readers import members_referenced
from tstdx.diagnostics import WarningCode

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "tstdx"
CHANNEL = "tstdx/diagnostics.py"

#: 允许直接 ``warnings.warn`` 的文件与理由。豁免的门槛是"这条告警**不是**某个结果的
#: 事实"——通道的发射口自身、生命周期提示、以及没有所属结果的 import 期审计。
BARE_WARN_ALLOWED: dict[str, str] = {
    CHANNEL: "通道的唯一发射口",
    "tstdx/deprecation.py": "DeprecationWarning 是 API 生命周期提示，不属于任何一次查询的结果",
    "tstdx/runtime/executor.py": "import 期 binding 审计，此时还没有 QueryPlan 可以携带它",
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


def test_bare_warn_calls_stay_inside_the_channel_and_two_justified_sites() -> None:
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


def test_bars_pagination_consumes_what_the_decoder_recorded() -> None:
    """解码层每一页都记了"声明 N 实收 M"，模板层必须把它接进通道而不是丢弃。

    判据落在形状上：``_t_bars`` 既要读 ``result.warnings``，也要在函数体内调用
    ``record_warning``——只满足其一都说明那条链断了。
    """
    source = (SOURCE / "client" / "_mixin.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    fn = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "_t_bars"
    )
    reads_page_caveats = any(
        isinstance(node, ast.Attribute) and node.attr == "warnings" for node in ast.walk(fn)
    )
    records = any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "record_warning"
        for node in ast.walk(fn)
    )
    assert reads_page_caveats and records, (
        "_t_bars 不再把解码层的分页判断接进告警通道（ParseResult.warnings 又变成没人读的袋）"
    )
