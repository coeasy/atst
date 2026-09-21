# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""告警通道的结构性门禁：类别表不许虚设，发射口不许旁路，分派点不许丢袋。

``WarningCode`` 是一张名单，名单的价值取决于有没有人把守它（F-39/F-42 的教训：
抄一次就过期的清单比没有清单更糟）。这里的判据分别看住名单的几个方向，
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
