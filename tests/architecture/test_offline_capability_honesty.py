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

F-66 (c)（2026-09-19）补的是**第三套命名**。能力发现面（``Client.capabilities()``、
``GET /v13/capabilities``、WS ``runtime.capabilities``）交出 172 个名字，而上面四判据的词汇只有
"client 方法名"与"内核直绑能力名"两套：``auction`` / ``volume_price`` / ``security_list_all`` 这类
注册表名两套都不是，于是它们既没被文档批注，也没被推导判出。本文件把推导再接一跳
（注册表能力名 → ``catalog`` 绑定 → 实现方法 → ``_t_*`` 模板 → 命令号），并钉住发现面的三件事：
名字集合可归类（:func:`test_the_discovery_face_classifies_every_published_name`）、
出口形状真的只有名字（:func:`test_the_discovery_face_really_delivers_names_only`）、
文档里那张死名字表与推导逐字相等（:func:`test_the_discovery_dead_name_table_is_derived_not_copied`、
:func:`test_the_discovery_face_claims_are_regenerated_from_runtime`）。

判据一律**从代码推导**（账本状态 + 拦截集 + 内核 binding 表 + catalog 绑定表 + 三层的调用图），
本文件不抄命令号清单也不抄能力名清单：抄一次就过期（F-42），过期之后它只会替错误说法作证。
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import tstdx.client.core as client_core
from tstdx.catalog.capability import MIGRATED_BINDINGS
from tstdx.client.api import Client
from tstdx.diagnostics import WarningCode
from tstdx.integration.mcp._tools_spec import TOOLS
from tstdx.protocol.commands import CMD, COMMANDS, STATUS_OFFLINE, TIER_L1, Family
from tstdx.providers import PROVIDERS
from tstdx.runtime.executor import DIRECT_BINDINGS

ROOT = Path(__file__).resolve().parents[2]
MIXIN = ROOT / "tstdx" / "client" / "_mixin.py"
EXECUTOR = ROOT / "tstdx" / "runtime" / "executor.py"
SYNC_CLIENT = ROOT / "tstdx" / "client" / "sync.py"
MCP_IMPL = ROOT / "tstdx" / "integration" / "mcp" / "_tools_impl.py"
HTTP_IMPL = ROOT / "tstdx" / "integration" / "runtime_http.py"
WS_IMPL = ROOT / "tstdx" / "integration" / "runtime_ws.py"
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


def _sync_methods() -> dict[str, ast.FunctionDef]:
    """``sync.py`` 里的公开客户端方法（``self._t_x(...)`` 的那层包装）。

    发现面按**能力名**发布，``catalog`` 绑定表按**实现方法名**登记，两者之间还隔着这一层：
    ``quotes_concurrent`` 是 ``TdxClient`` 的真方法、不是模板。不把它接进调用图，注册表词汇
    就在这一格断掉。``@overload`` 的签名行（函数体只有一个 ``...``）不算实现，跳过。
    """
    out: dict[str, ast.FunctionDef] = {}
    for node in ast.walk(_tree(SYNC_CLIENT)):
        if not isinstance(node, ast.FunctionDef) or node.name.startswith("_"):
            continue
        if len(node.body) == 1 and isinstance(node.body[0], ast.Expr):  # @overload 签名行
            continue
        out[node.name] = node
    return out


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
_SYNC = _sync_methods()
#: 内核直绑能力 → 该能力在 tdx 上实际调用的 client 方法名（``_tdx_bars`` 里那几个 ``client.x()``）。
_CAPABILITY_CALLS: dict[str, set[str]] = {}
for _binding in DIRECT_BINDINGS:
    if _binding.executor_name == "_migrated_capability":
        continue
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

#: catalog 绑定表里"实现落在 tdx 客户端族上"的后端：这类能力名会经 ``_t_*`` 打到命令账本。
_LEDGER_BACKENDS = frozenset(
    {"tdx_client", "ex_client", "goods_client", "mac_client", "f10_client"}
)
#: 其余后端一律不经过 tdx 命令账本：web 会话 / web adapter / channel adapter / composed。
#: 名单里一旦出现第六种后端，下面这条构造断言当场红——新后端不许被静默归进"免检"那一桶。
_NON_LEDGER_BACKENDS = frozenset({"web_session", "web_adapter", "direct_adapter", "composed"})

#: 注册表能力名 → tdx 侧实现方法名（``auction`` → ``auction_snapshot`` 这类第三套命名的桥）。
_REGISTRY_IMPLEMENTATIONS: dict[str, set[str]] = {}
_BACKENDS_BY_CAPABILITY: dict[str, set[str]] = {}
for _catalog_binding in MIGRATED_BINDINGS:
    _BACKENDS_BY_CAPABILITY.setdefault(_catalog_binding.capability, set()).add(
        _catalog_binding.backend
    )
    if _catalog_binding.backend in _LEDGER_BACKENDS:
        _REGISTRY_IMPLEMENTATIONS.setdefault(_catalog_binding.capability, set()).add(
            _catalog_binding.method
        )
    else:
        assert _catalog_binding.backend in _NON_LEDGER_BACKENDS, (
            f"绑定表出现了一个没登记过的后端 {_catalog_binding.backend!r}"
            f"（{_catalog_binding.key}）：它经不经 tdx 命令账本？"
            "先回答这个问题再改本文件，新后端不许被静默归进「免检」那一桶。"
        )


def _impl_node(name: str) -> ast.AST | None:
    """一个方法名对应的实现节点：先找 ``_t_*`` 模板，再找同步客户端的公开包装方法。"""
    return _TEMPLATES.get(f"_t_{name}") or _SYNC.get(name)


def _next_hops(node: ast.AST) -> set[str]:
    """一个实现节点的下一跳：``_op_call`` 点名、``self._t_x()``、``self.<公开包装方法>()``。"""
    calls = _attr_calls(node)
    return (
        _op_call_hops(node)
        | {c[3:] for c in calls if c.startswith("_t_")}
        | {c for c in calls if c in _SYNC}
    )


def discovery_names() -> set[str]:
    """发现面交付的那份名字——三处出口共同的来源。"""
    return set(Client.capabilities())


def ledger_reachable() -> set[str]:
    """发现面里"会打到 tdx 命令账本"的那部分名字（内核直绑 ∪ 原生 catalog 绑定）。"""
    return discovery_names() & (_CAPABILITY_CALLS.keys() | _REGISTRY_IMPLEMENTATIONS.keys())


def implementations(name: str) -> set[str]:
    """一个发现名在 tdx 侧**点得出名字**的实现方法。"""
    impls = _REGISTRY_IMPLEMENTATIONS.get(name)
    if impls:
        return {i for i in impls if _impl_node(i) is not None}
    return {c for c in _CAPABILITY_CALLS.get(name, set()) if _impl_node(c) is not None}


def providers_declaring(name: str) -> set[str]:
    """注册表里声明了这个能力名的 Provider（判"出路"用，不猜）。"""
    return {
        provider
        for provider in PROVIDERS.ids()
        if any(name in channel.capabilities for channel in PROVIDERS.get(provider).channels)
    }


def dead_commands(name: str) -> frozenset[int]:
    """一个方法名 / 能力名**最终**会打到的、注定发不出去的命令号（offline 不放行 ∪ inferred 拦截）。

    跨四层解析：trampoline 模板（含 ``_op_call`` 与 ``self._t_x()`` 的传递闭包）、同步客户端的
    包装方法、内核能力表与 catalog 绑定表互为邻居，所以 ``export_security_list``（自己不写
    ``CMD[...]``）、MCP 的 ``trades``（走内核而非模板）与注册表名 ``security_list_all``
    （走绑定表才连得上）都能被同一个函数解析出来。
    """
    return _dead_set(name, frozenset())


def _dead_set(name: str, seen: frozenset[str]) -> frozenset[int]:
    if name in seen:
        return frozenset()
    nxt = seen | {name}
    node = _impl_node(name)
    if node is not None:
        out = _direct_cmds(node) | {c for hop in _next_hops(node) for c in _dead_set(hop, nxt)}
        return frozenset(c for c in out if c in (OFFLINE_IDS - FALLBACK_OK_IDS) or c in BLOCKED_IDS)
    calls = _CAPABILITY_CALLS.get(name)
    if calls:
        return frozenset(c for sub in calls for c in _dead_set(sub, nxt))
    impls = _REGISTRY_IMPLEMENTATIONS.get(name)
    if impls:
        return frozenset(c for sub in impls for c in _dead_set(sub, nxt))
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
    for capability in sorted(_CAPABILITY_CALLS | _REGISTRY_IMPLEMENTATIONS.keys()):
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
        bound = _direct_cmds(node) | {c for hop in _next_hops(node) for c in bound_of(hop)}
        if not (bound & OFFLINE_IDS):
            continue
        doc = ast.get_docstring(node) or ""
        if OFFLINE_MEANING not in doc and "参数校正" not in doc and "回退" not in doc:
            offenders.append(name)
    assert offenders == [], (
        f"这些 offline 面既没复述账本定义（{OFFLINE_MEANING}），"
        f"也没写本仓的处置口径（保留待参数校正 / 走回退）：{offenders}"
    )


def bound_of(name: str, seen: frozenset[str] = frozenset()) -> frozenset[int]:
    """一个方法名绑定到的全部命令号（含传递闭包），不筛状态——T2 要的是"摸到 offline 命令"。"""
    if name in seen:
        return frozenset()
    nxt = seen | {name}
    node = _impl_node(name)
    if node is None:
        return frozenset()
    return frozenset(
        _direct_cmds(node) | {c for hop in _next_hops(node) for c in bound_of(hop, nxt)}
    )


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
API_README = ROOT / "docs" / "api" / "README.md"
DISCOVERY_SECTION_TITLE = "能力发现面：只有名字，没有可用性"


def _interface_rows() -> list[tuple[str, str]]:
    """``docs/api/interfaces.md`` 方法表里的 (方法名, 说明单元格)，只收反引号开头的数据行。

    「能力发现面」那一节整节跳过：那里的第一列是**注册表能力名**，不是方法名，单元格形状也
    不同（五列、最后一列是出路 Provider）。那一节由 :func:`_discovery_section` 系列的判据管，
    混进来只会让两侧互相误判。
    """
    text = _INTERFACES.read_text(encoding="utf-8").replace("\r\n", "\n")
    text = text.replace(_discovery_section(raw=True), "\n")
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
    """F-37 的裁决是"下调能力声称"：``0x000F``/``0x0010`` 的字段口径不许被写回成已验证。

    V18 第 9 轮把这条判据从文档那半边接到**账本那半边**：口径降级当时只落在 docstring
    与 Provider 文档上，``commands.py`` 里 0x000F 却仍写着 ``tier=L1, verified=True``
    （那个 L1 是 ``register_parser`` 的缺省值，不是判断）。于是同一件事在两个文件里
    互相矛盾而无人报红——读文档的人看到"字段语义不保证"，读账本的人看到"已验证"。
    """
    for name in ("_t_capital_changes", "_t_finance_info"):
        doc = ast.get_docstring(_TEMPLATES[name]) or ""
        assert "F-37" in doc, f"{name} 的字段口径降级说明被删掉了"
        assert "不保证" in doc, f"{name} 不再声明字段语义的边界"
    for number in (0x000F, 0x0010):
        row = COMMANDS[(Family.STANDARD, number)]
        assert row.verified is False, (
            f"0x{number:04X} 在账本里又变回 verified=True——它的实采样本重放后仍有字段值落在域外，"
            "先把布局判据补上再改这一格"
        )
        assert row.tier != TIER_L1, (
            f"0x{number:04X} 的 tier 写回了 L1，而本文件上面两句 docstring 口径仍写着「不保证」"
        )
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


# ---------------------------------------------------------------------------
# F-66 (c)：能力发现面——名字与可用性是两件事
# ---------------------------------------------------------------------------


def _discovery_section(raw: bool = False) -> str:
    """interfaces.md 里「能力发现面」一节的正文（含标题行，到下一个 ``###``/``##`` 为止）。"""
    text = _INTERFACES.read_text(encoding="utf-8")
    if not raw:
        text = text.replace("\r\n", "\n")
    header = f"### {DISCOVERY_SECTION_TITLE}"
    start = text.find(header)
    assert start != -1, f"docs/api/interfaces.md 找不到「{header}」小节，判据自身失效"
    end = len(text)
    for marker in (("\r\n### " if raw else "\n### "), ("\r\n## " if raw else "\n## ")):
        found = text.find(marker, start + len(header))
        if found != -1:
            end = min(end, found)
    return text[start:end]


def _markdown_rows(section: str, cells: int) -> dict[str, list[str]]:
    """某节里第一列是反引号名字、且恰好 ``cells`` 列的表格行：名字 → 单元格列表。"""
    rows: dict[str, list[str]] = {}
    for line in section.splitlines():
        parts = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(parts) != cells or not parts[0].startswith("`"):
            continue
        rows[parts[0].strip("`")] = parts
    return rows


def dead_discovery_names() -> dict[str, frozenset[int]]:
    """发现面里"经 tdx 这条链必然发不出去"的名字 → 那些命令号。"""
    return {name: dead_commands(name) for name in sorted(ledger_reachable()) if dead_commands(name)}


def test_the_discovery_face_classifies_every_published_name() -> None:
    """发现面交出的每个名字都要能归进恰好一格；归不进去 = 本门禁对这张面失明。

    三份名单必须同源（``Client.capabilities()`` == 注册表名字集 == 内核直绑 ∪ catalog 迁移），
    否则文档批注与推导判的就不是同一张面。唯一的解析盲区按名字登记，并要求它保持无害。
    """
    names = discovery_names()
    assert len(names) >= 150, f"发现面只有 {len(names)} 个名字，少于绑定表推导的分母"
    registry_names = {
        capability
        for provider in PROVIDERS.ids()
        for channel in PROVIDERS.get(provider).channels
        for capability in channel.capabilities
    }
    assert names == registry_names, (
        f"发现面与注册表名字集分叉了：{sorted(names ^ registry_names)}——"
        "两处出口发的不再是同一份名单"
    )
    assert names == set(_CAPABILITY_CALLS) | {b.capability for b in MIGRATED_BINDINGS}, (
        "发现面不等于「内核直绑 ∪ catalog 迁移」：有名字是谁发布的都对不上"
    )
    reachable = ledger_reachable()
    for name in sorted(names - reachable):
        kinds = _BACKENDS_BY_CAPABILITY.get(name, set())
        assert kinds, f"发现名 {name} 既不在内核直绑表也不在 catalog 绑定表——它从哪来的？"
        assert kinds <= _NON_LEDGER_BACKENDS, (
            f"发现名 {name} 的后端 {sorted(kinds)} 里有一类没登记「经不经过命令账本」"
        )
    #: 唯一的登记盲区：`f10_client` 按 capability 分岔（`executor.py` 里 `f10` → `download`、
    #: 其余 → `catalog`），绑定表的 `method` 只是标签，所以这一格对不上任何实现。
    unresolved = {name for name in reachable if not implementations(name)}
    assert unresolved == {"f10"}, (
        f"发现面里对不上实现的原生名字变了：{sorted(unresolved)}。"
        "新增的要先接进调用图，不许留成静默免检"
    )
    f10_cmds = {cmd for (family, cmd) in COMMANDS if family == Family.F10}
    assert not (f10_cmds & (OFFLINE_IDS | BLOCKED_IDS)), (
        "F10 族账本出现了 offline/拦截命令，而 `f10` 这一格本来就解析不到实现——"
        "盲区已不再无害，先把 f10_client 的 capability 分岔接进推导"
    )


def test_the_discovery_face_really_delivers_names_only() -> None:
    """文档写着「只有名字、没有可用性」，那两处 wire 出口就得真的放不下状态字段。

    裁决 (c) 是"发现面形状不动"。这条判据把那句话钉回代码：形状一旦长出第三个键或任何
    状态字段，本行先红，改的人必须同时改文档口径（或回去改裁决）。
    """
    published = Client.capabilities()
    assert published == tuple(sorted(published)), "发现面不再按字典序发布"
    assert all(isinstance(name, str) for name in published), "发现面里混进了非字符串条目"

    tree = _tree(HTTP_IMPL)
    http_payload = _capabilities_payload(tree)
    ws_payload = _capabilities_payload(_tree(WS_IMPL))
    for path, payload in ((HTTP_IMPL, http_payload), (WS_IMPL, ws_payload)):
        assert payload is not None, (
            f"{path.name} 里找不到 `{{capabilities, providers}}` 那份字典——"
            "出口形状变了，文档口径与本判据都要跟着改"
        )
        keys = {
            k.value
            for k in ast.walk(payload)
            if isinstance(k, ast.Constant) and isinstance(k.value, str)
        }
        invented = keys & {"status", "available", "availability", "offline", "degraded", "verified"}
        assert not invented, (
            f"{path.name} 的发现面长出了状态字段 {sorted(invented)}，"
            "而文档写的是「只有名字、没有可用性」"
        )
    section = _discovery_section()
    assert "没有可用性" in section and "不承诺" in section, (
        "「能力发现面」小节没把『名字 ≠ 可用性』这句话写下来"
    )
    row = {name: description for name, description in _interface_rows()}
    # 只留"见下节「…」"的指针不算写了口径：这一行自己必须把话说完。
    own_claim = re.sub(r"见下节「[^」]*」", "", row["capabilities"])
    assert "没有可用性" in own_claim, (
        "Client 表里 `capabilities` 那一行没带上这条口径——读方法表的人看不到它"
    )


def _capabilities_payload(tree: ast.Module) -> ast.Dict | None:
    """找出 ``{"capabilities": ..., "providers": ...}`` 那份返回字典的字面量节点。"""
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        keys = {
            k.value for k in node.keys if isinstance(k, ast.Constant) and isinstance(k.value, str)
        }
        if keys == {"capabilities", "providers"}:
            return node
    return None


def test_the_discovery_dead_name_table_is_derived_not_copied() -> None:
    """文档那张死名字表的四个字段全部现推：名字集合、命令号、实现方法、出路 Provider。

    两侧都判：漏一行等于把发不出去的名字平铺成可用能力，多一行等于把通的名字写坏；
    出路那一列只许写注册表真的声明过的 Provider，"改走哪里"不许靠想象。
    """
    derived = dead_discovery_names()
    assert len(derived) >= 8, (
        f"只推导出 {sorted(derived)} 个发不出去的发现名，少于账本+拦截集的分母"
    )
    rows = _markdown_rows(_discovery_section(), cells=5)
    assert set(rows) == set(derived), (
        f"发现面死名字表与推导不符：文档多 {sorted(set(rows) - set(derived))}、"
        f"文档缺 {sorted(set(derived) - set(rows))}"
    )
    problems: list[str] = []
    for name in sorted(derived):
        cells = rows[name]
        cmds = sorted(derived[name])
        if not all(f"0x{cmd:04X}" in cells[2] for cmd in cmds):
            problems.append(f"{name}: 命令号列 {cells[2]!r} 没写全 {[hex(c) for c in cmds]}")
        impls = implementations(name)
        if not any(f"`{method}`" in cells[1] for method in impls):
            problems.append(f"{name}: tdx 侧实现列 {cells[1]!r} 对不上推导的 {sorted(impls)}")
        why = cells[3]
        if cmds and all(cmd in OFFLINE_IDS for cmd in cmds):
            if OFFLINE_MEANING not in why or "CommandOffline" not in why:
                problems.append(f"{name}: offline 那半边没复述账本定义或没点出抛的异常")
        else:
            if "inferred" not in why or "NotImplementedFeature" not in why:
                problems.append(f"{name}: inferred 拦截那半边没写拦截依据或没点出抛的异常")
        outlets = providers_declaring(name) - {"tdx"}
        cited = set(re.findall(r"`([a-z_]+)`", cells[4]))
        if outlets:
            if cited != outlets:
                problems.append(
                    f"{name}: 出路列 {cells[4]!r} 与注册表声明的 {sorted(outlets)} 不等"
                )
        elif cited & set(PROVIDERS.ids()):
            problems.append(f"{name}: 除 tdx 外无人声明它，出路列却点了 {sorted(cited)}")
        elif "无" not in cells[4]:
            problems.append(f"{name}: 没有任何出路却不写明「无」")
    assert problems == [], "docs/api/interfaces.md「能力发现面」与推导不一致：" + "；".join(
        problems
    )


def test_the_discovery_face_claims_are_regenerated_from_runtime() -> None:
    """文档里所有规模数字都由运行期现算再回查原文——抄本过期即红，不靠人记得改。"""
    names = discovery_names()
    reachable = ledger_reachable()
    derived = dead_discovery_names()
    total_channels = sum(len(PROVIDERS.get(p).channels) for p in PROVIDERS.ids())
    offline_names = [n for n, cmds in derived.items() if all(c in OFFLINE_IDS for c in cmds)]
    claims = [
        (f"{len(names)} 项 capability 名", "发现面规模"),
        (f"`tuple[str, ...]`，{len(names)} 项", "出口形状"),
        (
            f"{len(names)} = {len(_CAPABILITY_CALLS)} 个内核直绑能力 ∪ "
            f"{len({b.capability for b in MIGRATED_BINDINGS})} 个 catalog 迁移能力",
            "名单构成恒等式",
        ),
        (f"{len(PROVIDERS.ids())} Provider × {total_channels} channel", "注册表规模"),
        (
            f"上有 {len(derived)} 个名字一调就必然失败：{len(offline_names)} 个名字踩在"
            "被账本判 `offline` 的命令上、",
            "死名字计数",
        ),
        (
            f"{len(reachable)} 个名字（{len(_CAPABILITY_CALLS)} 个内核直绑 + "
            f"{len(reachable) - len(_CAPABILITY_CALLS)} 个 tdx 客户端族 catalog 绑定）",
            "本表覆盖范围",
        ),
        (f"其余 {len(names) - len(reachable)} 个名字走", "本表覆盖不到的部分"),
    ]
    section = _discovery_section()
    offenders = [f"{label}：{claim}" for claim, label in claims if claim not in section]
    readme = API_README.read_text(encoding="utf-8")
    if (
        f"{len(PROVIDERS.ids())} Provider × {total_channels} channel × {len(names)} capability"
        not in readme
    ):
        offenders.append("README.md：Provider 三元组与运行期不符")
    text = _INTERFACES.read_text(encoding="utf-8").replace("\r\n", "\n")
    for heading in ("### HTTP REST 网关", "### WebSocket JSON-RPC"):
        start = text.find(heading)
        assert start != -1, f"找不到 {heading} 小节，互指判据自身失效"
        rest = text[start:]
        end = rest.find("\n### ", 1)
        body = rest[: end if end != -1 else len(rest)]
        if DISCOVERY_SECTION_TITLE not in body:
            offenders.append(f"{heading}：没互指「{DISCOVERY_SECTION_TITLE}」")
    assert offenders == [], f"发现面的文档口径与运行期不符：{offenders}"
