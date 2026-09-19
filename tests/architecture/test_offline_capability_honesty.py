# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""「发不出去的命令」必须在**调用方读得到的那一面**写着它是发不出去的。

命令账本把 9 条命令登记为 ``STATUS_OFFLINE``，另有 2 条 inferred 命令被
``core._UNVERIFIED_STRUCTURED_BLOCK`` 拦在结构化 API 外面。这两类都不是"慢"或"偶尔失败"，
而是**客户端主动不发**。F-63② 的实测是：这类事实当时只活在账本和 ``_guard_offline`` 的抛错
消息里，6 个绑定了 offline 命令的 trampoline 模板没有一个在 docstring 上说，
``docs/providers/tdx.md`` 与 MCP 工具描述同样把注定失败的入口平铺成可用能力。
用户裁决是"保留，只把『已下线』写清"（2026-09-19），于是这里钉的是这句话有没有留下：

* :func:`test_a_template_that_cannot_send_says_so_where_the_caller_reads_it` —— 实现面；
* :func:`test_the_provider_doc_marks_every_dead_direct_capability` —— Provider 文档面；
* :func:`test_the_mcp_tool_list_marks_every_dead_tool` —— 模型只能看见描述字符串的那一面；
* :func:`test_the_two_unclosed_field_layouts_stay_downgraded` —— F-37 的裁决那半边。

判据一律**从代码推导**（账本状态 + 拦截集 + 内核 binding 表 + 三层的调用图），本文件不抄
命令号清单也不抄能力名清单：抄一次就过期（F-42），过期之后它只会替错误说法作证。
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import tstdx.client.core as client_core
from tstdx.diagnostics import WarningCode
from tstdx.integration.mcp._tools_spec import TOOLS
from tstdx.protocol.commands import CMD, COMMANDS, STATUS_OFFLINE
from tstdx.runtime.executor import _CORE_BINDINGS

ROOT = Path(__file__).resolve().parents[2]
MIXIN = ROOT / "tstdx" / "client" / "_mixin.py"
EXECUTOR = ROOT / "tstdx" / "runtime" / "executor.py"
MCP_IMPL = ROOT / "tstdx" / "integration" / "mcp" / "_tools_impl.py"
PROVIDER_DOC = ROOT / "docs" / "providers" / "tdx.md"

#: 账本对 ``offline`` 这个状态自身的定义；面上的说明不许换成别的口径。
OFFLINE_MEANING = "多主站实测无响应"

OFFLINE_IDS = {c.cmd for c in COMMANDS.values() if c.status == STATUS_OFFLINE}
BLOCKED_IDS = set(client_core._UNVERIFIED_STRUCTURED_BLOCK)
FALLBACK_OK_IDS = set(client_core._OFFLINE_FALLBACK_OK)


def _tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8").replace("\r\n", "\n"), filename=str(path))


def _templates() -> dict[str, ast.FunctionDef]:
    return {
        node.name: node
        for node in ast.walk(_tree(MIXIN))
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def _attr_calls(body: ast.AST) -> set[str]:
    """``x.<name>(...)`` 形式的被调用方法名。"""
    return {
        node.func.attr
        for node in ast.walk(body)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }


def _op_call_hops(body: ast.AST) -> set[str]:
    """trampoline 模板里 ``_op_call("<方法名>", ...)`` 点名的下一跳。"""
    return {
        node.args[0].value
        for node in ast.walk(body)
        if isinstance(node, ast.Call)
        and getattr(node.func, "id", "") == "_op_call"
        and node.args
        and isinstance(node.args[0], ast.Constant)
        and isinstance(node.args[0].value, str)
    }


def _direct_cmds(body: ast.AST) -> set[int]:
    """函数体内 ``CMD["..."]`` 直接点名的命令号。"""
    return {
        CMD[node.slice.value]
        for node in ast.walk(body)
        if isinstance(node, ast.Subscript)
        and isinstance(node.value, ast.Name)
        and node.value.id == "CMD"
        and isinstance(node.slice, ast.Constant)
        and node.slice.value in CMD
    }


_TEMPLATES = _templates()
#: 内核直绑能力 → 该能力在 tdx 上实际调用的 client 方法名（``_tdx_bars`` 里那几个 ``client.x()``）。
_CAPABILITY_CALLS: dict[str, set[str]] = {}
for _binding in _CORE_BINDINGS:
    if _binding.provider != "tdx":
        continue
    _executor_tree = _tree(EXECUTOR)
    _body = next(
        (
            n
            for n in ast.walk(_executor_tree)
            if isinstance(n, ast.FunctionDef) and n.name == _binding.executor_name
        ),
        None,
    )
    assert _body is not None, f"内核 binding 指向的方法 {_binding.executor_name} 不存在"
    _CAPABILITY_CALLS.setdefault(_binding.capability, set()).update(_attr_calls(_body))


def dead_commands(name: str) -> frozenset[int]:
    """一个方法名 / 能力名**最终**会打到的、注定发不出去的命令号（offline 不放行 ∪ inferred 拦截）。

    跨三层解析：trampoline 模板（含 ``_op_call`` 传递闭包）与内核能力表互为邻居，所以
    ``export_security_list``（自己不写 ``CMD[...]``）和 MCP 的 ``trades``（走内核而非模板）
    都能被同一个函数解析出来。
    """
    return _dead_set(name, frozenset())


def _dead_set(name: str, seen: frozenset[str]) -> frozenset[int]:
    if name in seen:
        return frozenset()
    nxt = seen | {name}
    node = _TEMPLATES.get(f"_t_{name}")
    if node is not None:
        out = _direct_cmds(node) | {c for hop in _op_call_hops(node) for c in _dead_set(hop, nxt)}
        return frozenset(c for c in out if c in (OFFLINE_IDS - FALLBACK_OK_IDS) or c in BLOCKED_IDS)
    calls = _CAPABILITY_CALLS.get(name)
    if calls:
        return frozenset(c for sub in calls for c in _dead_set(sub, nxt))
    return frozenset()


def _verdict_markers(doc: str) -> dict[str, bool]:
    return {
        "names_status": "STATUS_OFFLINE" in doc or "已下线" in doc,
        "names_block": "NotImplementedFeature" in doc,
        "claims_usable": "本方法仍可用" in doc,
        "names_fallback": "回退" in doc,
        "names_offline_exc": "CommandOffline" in doc,
    }


def test_a_template_that_cannot_send_says_so_where_the_caller_reads_it() -> None:
    """绑定了"必发不出去"命令的模板，docstring 必须写清它已下线、以及凭哪个异常下线。"""
    scanned: list[str] = []
    missing: list[str] = []
    contradictory: list[str] = []
    for name, node in sorted(_TEMPLATES.items()):
        if not name.startswith("_t_"):
            continue
        bound = bound_of(name[3:])
        hard_dead = bound & (OFFLINE_IDS - FALLBACK_OK_IDS) | (bound & BLOCKED_IDS)
        exempt_offline = bound & OFFLINE_IDS & FALLBACK_OK_IDS
        if not (hard_dead or exempt_offline):
            continue
        scanned.append(name)
        markers = _verdict_markers(ast.get_docstring(node) or "")
        if not (markers["names_status"] or markers["names_block"]):
            missing.append(name)
            continue
        # 判据分叉：被 _OFFLINE_FALLBACK_OK 放行的那一面（0x054C）恰恰**可用**，
        # 给它套 fail-fast 套话同样是假话，所以两边都不许含糊。
        if not hard_dead and exempt_offline:
            if not (markers["claims_usable"] and markers["names_fallback"]):
                contradictory.append(f"{name}: 放行的 offline 命令必须写明仍可用且走回退")
        else:
            if markers["claims_usable"]:
                contradictory.append(f"{name}: 命令未放行却声称可用")
            if bound & OFFLINE_IDS and not markers["names_offline_exc"]:
                contradictory.append(f"{name}: 未点出下线时抛的异常名")

    assert scanned, "没有任何模板被判定为发不出去——扫描自身失效（账本被清空？）"
    assert len(scanned) >= 9, f"只扫到 {len(scanned)} 个下线面，少于账本推导的分母：{scanned}"
    assert "_t_export_security_list" in scanned, (
        "_op_call 的传递闭包没跟上：export_security_list 自己不写 CMD[...]，"
        "只有经 security_list 才知道它也发不出去"
    )
    assert missing == [], f"这些方法一发就必然失败，docstring 却没写：{missing}"
    assert contradictory == [], f"下线口径与账本不一致：{contradictory}"


def test_the_business_entry_face_names_the_exception_it_always_raises() -> None:
    """``Client`` 是唯一业务入口：注定失败的能力面不许留一句光秃秃的签名。"""
    tree = _tree(ROOT / "tstdx" / "client" / "api.py")
    client_class = next(
        node for node in ast.walk(tree) if isinstance(node, ast.ClassDef) and node.name == "Client"
    )
    api = {
        node.name: node
        for node in client_class.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    checked: list[str] = []
    silent: list[str] = []
    for capability in sorted(_CAPABILITY_CALLS):
        if not dead_commands(capability):
            continue
        node = api.get(capability)
        if node is None:
            continue  # 该能力没有 ``Client`` 方法面（只能经 ``call``/QuerySpec 到达）
        checked.append(capability)
        doc = ast.get_docstring(node) or ""
        if "CommandOffline" not in doc and "NotImplementedFeature" not in doc:
            silent.append(capability)
    assert len(checked) >= 3, f"只扫到 {checked} 个注定失败的 Client 方法面，判据自身失效"
    assert silent == [], (
        f"这些 ``Client`` 方法一调就必然抛错，docstring 却没点出抛哪个异常：{silent}"
    )


def test_the_offline_wording_quotes_the_ledger_definition_not_an_invented_one() -> None:
    """写"已下线"不许替现场编原因：``offline`` 在账本里只有一个定义。"""
    offenders: list[str] = []
    for name, node in sorted(_TEMPLATES.items()):
        if not name.startswith("_t_"):
            continue
        bound = _direct_cmds(node) | {c for hop in _op_call_hops(node) for c in bound_of(hop)}
        if not (bound & OFFLINE_IDS):
            continue
        doc = ast.get_docstring(node) or ""
        if OFFLINE_MEANING not in doc and "参数校正" not in doc and "回退" not in doc:
            offenders.append(name)
    assert offenders == [], (
        f"这些 offline 面既没复述账本定义（{OFFLINE_MEANING}），"
        f"也没写本仓的处置口径（保留待参数校正 / 走回退）：{offenders}"
    )


def bound_of(name: str) -> frozenset[int]:
    """``_t_<name>`` 绑定到的全部命令号（含传递闭包），不筛状态——T2 要的是"摸到 offline 命令"。"""
    node = _TEMPLATES.get(f"_t_{name}")
    if node is None:
        return frozenset()
    return frozenset(_direct_cmds(node) | {c for hop in _op_call_hops(node) for c in bound_of(hop)})


def _doc_quotation_capabilities() -> dict[str, str]:
    """``docs/providers/tdx.md`` quotation 小节里 ``Capabilities`` 块的 能力名 → 整行。"""
    text = PROVIDER_DOC.read_text(encoding="utf-8").replace("\r\n", "\n")
    section = text.split("### quotation", 1)
    assert len(section) == 2, "文档结构变了：找不到 '### quotation' 小节，判据自身失效"
    block = re.search(r"Capabilities：\s*```text\n(.*?)```", section[1], re.S)
    assert block, "找不到 quotation 的 Capabilities 代码块，判据自身失效"
    return {
        line.split("（")[0].strip(): line for line in block.group(1).splitlines() if line.strip()
    }


def test_the_provider_doc_marks_every_dead_direct_capability() -> None:
    """内核直绑能力里凡是链路注定失败的，Provider 文档那一行必须带批注；通的不许被写坏。"""
    dead = sorted(cap for cap in _CAPABILITY_CALLS if dead_commands(cap))
    alive = sorted(cap for cap in _CAPABILITY_CALLS if not dead_commands(cap))
    assert dead, "推导不出任何注定失败的内核能力——判据自身失效"
    lines = _doc_quotation_capabilities()
    listed = sorted(cap for cap in dead if cap in lines)
    assert listed, (
        f"推导出的注定失败能力 {dead} 一个都不在文档能力清单里，说明文档结构或推导键名变了"
    )
    unmarked = sorted(cap for cap in listed if "（" not in lines[cap])
    assert unmarked == [], f"文档把这些发不出去的能力平铺成了可用能力：{unmarked}"
    wrongly = sorted(cap for cap in alive if "（" in lines.get(cap, ""))
    assert wrongly == [], f"这些能力链路是通的，不许顺手写成受限能力：{wrongly}"


def test_the_mcp_tool_list_marks_every_dead_tool() -> None:
    """MCP 工具描述是模型唯一的可见信息：发不出去的工具必须在描述里写明，别让人来试。

    两类失败要分措辞——账本判定的 offline 与"request/parser 仍是 inferred 故客户端不发"是
    两回事，写成同一句话会让读的人以为等一等就好。
    """
    impl = {n.name: n for n in ast.walk(_tree(MCP_IMPL)) if isinstance(n, ast.FunctionDef)}
    dead: list[str] = []
    for tool in TOOLS:
        handler = impl.get(tool.handler.__name__)
        assert handler is not None, f"MCP 处理器 {tool.handler.__name__} 不在实现文件里"
        reached = sorted(name for name in _attr_calls(handler) if dead_commands(name))
        if not reached:
            continue
        dead.append(tool.name)
        bound = {c for name in reached for c in bound_of(name)}
        description = tool.description.lower()
        #: 独立成词的状态词，不是异常类名里的子串——只写 ``CommandOffline`` 不算数。
        if bound & OFFLINE_IDS:
            assert re.search(r"\boffline\b", description), (
                f"MCP 工具 {tool.name}（调用 {reached}）绑定的命令已被账本判 offline，"
                "描述里却没有 offline 字样——模型只看得见这句话"
            )
        else:
            assert "unavailable" in description, (
                f"MCP 工具 {tool.name}（调用 {reached}）因 inferred 拦截发不出去，"
                "描述里没有写明不可用——注意这类不许写成 offline"
            )
    assert len(dead) >= 3, f"只推导出 {dead} 注定失败的 MCP 工具，少于账本+拦截集的分母"


_INTERFACES = ROOT / "docs" / "api" / "interfaces.md"


def _interface_rows() -> list[tuple[str, str]]:
    """``docs/api/interfaces.md`` 方法表里的 (方法名, 说明单元格)，只收反引号开头的数据行。"""
    text = _INTERFACES.read_text(encoding="utf-8").replace("\r\n", "\n")
    rows: list[tuple[str, str]] = []
    for line in text.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 2 or not cells[0].startswith("`"):
            continue
        rows.append((cells[0].strip("`"), cells[-1]))
    assert rows, "接口表解析不出任何一行，判据自身失效"
    return rows


def test_the_api_interface_table_matches_the_ledger() -> None:
    """方法表既不许漏掉"已下线"，也不许把通的行写成下线——两侧都判。"""
    checked = 0
    resolved = 0
    problems: list[str] = []
    for name, description in _interface_rows():
        bound = bound_of(name)
        hard_dead = bool(dead_commands(name))
        exempt = bool(bound & OFFLINE_IDS & FALLBACK_OK_IDS) and not hard_dead
        if name in {key[3:] for key in _TEMPLATES} or name in _CAPABILITY_CALLS:
            resolved += 1
        if not (hard_dead or exempt):
            if "已下线" in description:
                problems.append(f"{name}: 链路是通的，表里却写成已下线")
            checked += 1
            continue
        if exempt:
            if "回退" not in description:
                problems.append(f"{name}: 被放行的 offline 面必须写明走回退因而可用")
        else:
            if "已下线" not in description:
                problems.append(f"{name}: 发不出去却没写已下线")
            elif "CommandOffline" not in description and "NotImplementedFeature" not in description:
                problems.append(f"{name}: 写了已下线却没点出抛哪个异常")
        checked += 1
    assert checked >= 20, f"接口表只核对到 {checked} 行，说明解析或表结构变了"
    assert resolved >= 15, f"接口表里只有 {resolved} 行能推导到方法/能力，判据自身失效"
    assert problems == [], "docs/api/interfaces.md 与命令账本不一致：" + "；".join(problems)


def test_the_two_unclosed_field_layouts_stay_downgraded() -> None:
    """F-37 的裁决是"下调能力声称"：``0x000F``/``0x0010`` 的字段口径不许被写回成已验证。"""
    for name in ("_t_capital_changes", "_t_finance_info"):
        doc = ast.get_docstring(_TEMPLATES[name]) or ""
        assert "F-37" in doc, f"{name} 的字段口径降级说明被删掉了"
        assert "不保证" in doc, f"{name} 不再声明字段语义的边界"
    lines = _doc_quotation_capabilities()
    for capability in ("finance", "capital_changes"):
        assert "F-37" in lines.get(capability, ""), (
            f"docs/providers/tdx.md 的能力清单里 {capability} 又变回无保留可用了"
        )


def test_an_unreachable_warning_stays_declared_as_such() -> None:
    """``SECURITY_LIST_EMPTY_FIRST_PAGE`` 只能由测试桩触发：裁决保留了这个面，就得把"到不了"写下来。

    F-63② 的另一半：0x044D 已下线 ⇒ 这条告警在真实客户端路径上永不可达。用户裁决是保留该面，
    所以本判据不要求删除它，只要求**任何读到它的人**都能看见它为什么到不了：发射点所在模板的
    docstring 已写明下线（T1 覆盖），而 ``WarningCode`` 的这条成员必须仍然只有一个发射点、
    且那个发射点在 offline 模板里——否则它就不再是"永不可达的残留"，而是另一条被丢弃的告警。
    """
    emitters = [
        node.name
        for node in ast.walk(_tree(MIXIN))
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and any(
            isinstance(sub, ast.Attribute)
            and sub.attr == WarningCode.SECURITY_LIST_EMPTY_FIRST_PAGE.name
            for sub in ast.walk(node)
        )
    ]
    assert emitters == ["_t_export_security_list"], (
        f"这条告警的发射点变了：{emitters}。它按裁决是"
        "「已下线面上的残留」，新增发射点或搬家都要先重新判定可达性"
    )
