"""G25：CLI 那张接口表必须是真接口面，不是一句"见 `--help`"。

第 22 轮回答"具体接口文档是否全部更新"时先把四面各数了一遍：HTTP 10 路由、WS 10 方法、
MCP 9 工具三张表在 ``docs/api/interfaces.md`` 里逐名列出，且与运行时一致；**CLI 那格是空的**
——§3 只写 ``tstdx --help`` 加一句"全部子命令委托 `Client`"。两件事都是假的：

* 36 个叶子命令里有 **11 支在 46 份活文档里逐名查不到**（``adjusted-bars`` / ``all-market`` /
  ``feedback submit`` / ``fund estimate`` / ``fund list`` / ``fund nav`` / ``hosts list`` /
  ``hosts scan`` / ``index constituents`` / ``minute-klines`` / ``sector-flow``；
  口径与现场见 ``scratch_v18b22/census_docs22.log``——按"示例行 ``tstdx <命令>`` 或反引号里的
  名字"算点到，HEAD 的 blob 用 ``git show`` 读，所以本轮改完文档之后这一格仍可重跑对账）；
* "全部委托 `Client`"不成立——36 个叶子里 **15 支碰不到内核**：6 支直连传输层、
  1 支拉起 HTTP 应用、4 支主站诊断、2 支反馈、2 支元信息（``version`` 打包版本、
  ``capabilities`` 打 ``Client.capabilities()`` 那份静态名单——第 24 轮把后一支从
  ``内核·typed`` 改判到这里：处理器连 ``Client`` 都没构造，就谈不上 ``runtime.execute``）。
  这一格的分母由 :func:`_landing` 现读处理器源码派生，判据 ④ 管它，所以这里的分类改不动。
  同一句话还掩盖了 ``serve`` 不承载 WebSocket
  这件事（:func:`tstdx.cli.runtime_commands._cmd_serve` 的 docstring 当时写着"40+ 端点 +
  WebSocket"，而 ``create_runtime_app()`` 是 10 支路由、零条 websocket 路由）。

处置不是补一段散文，而是把这张表变成**从 argparse 与处理器源码派生**的接口面：命令名、位置
参数、旗标顺序、落点四格全部现扫，改一格就要改文档，否则这里红。"尺子自己能看见漂移"由
:func:`test_the_ruler_itself_sees_a_planted_drift` 四处单点篡改守着（改名 / 删旗标 / 改落点 /
篡分母，每处都必须只抓出那一格）；真文件一侧的四处变异（给 `changes` 加一支旗标、改命令名、
把 `_cmd_changes` 从 rows 挪到 typed、删一条 `tstdx bars` 示例）逐格跑过并记在
``scratch_v18b22/cli_mutate22.log``。
"""

from __future__ import annotations

import ast
import inspect
import re
from typing import Any

import pytest

from tests.support.field_readers import REPO_ROOT
from tstdx.cli.parser import build_parser

pytestmark = pytest.mark.unit

DOC = REPO_ROOT / "docs" / "api" / "interfaces.md"

#: 表头逐字锁：文档改了表头形状，本判据就当众红，而不是静默变成"没有行"。
TABLE_HEADER = "| 命令 | 位置参数 | 旗标 | 落点 |"
HEADING = re.compile(r"^### CLI（(?P<tops>\d+) 子命令 / (?P<leaves>\d+) 个叶子命令）$", re.M)
_NONE = "—"

#: 落点词汇表：每格只能是这六个之一，含义表里必须六个都在且只写这六个。
LANDINGS = (
    "内核·typed",
    "内核·rows",
    "直连传输层",
    "服务面宿主",
    "传输·诊断",
    "反馈",
    "元信息",
)


# ---------------------------------------------------------------------------
# 运行时那一侧：从 argparse 现值派生叶子命令规格
# ---------------------------------------------------------------------------


def _subparsers() -> dict[str, Any]:
    action = next(a for a in build_parser()._actions if a.dest == "command")
    return action.choices


def _group_action(sp: Any):
    """子命令组自身的那个位置参数（``feedback``/``fund``/``hosts``/``index``）。"""
    for a in sp._actions:
        if (
            a.choices
            and a.dest != "command"
            and all(not o.startswith("-") for o in a.option_strings)
        ):
            return a
    return None


def _positionals(sp: Any, *, skip: str) -> list[str]:
    return [
        a.metavar or a.dest
        for a in sp._actions
        if not a.option_strings and a.dest not in ("help", "command", skip)
    ]


def _flags(sp: Any) -> list[str]:
    return [
        "/".join(a.option_strings) for a in sp._actions if a.option_strings and a.dest != "help"
    ]


def _landing(fn: Any) -> str:
    """一支命令的落点：只读处理器源码，不读文档。"""
    source = inspect.getsource(fn)
    tree = ast.parse(source)
    names = {
        n.func.id
        for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
    }
    attrs = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    idents = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    seen = names | attrs | idents
    # 顺序就是优先级：越靠前的越具体，落到最后一个"碰得到 Client"。
    if "create_runtime_app" in seen:
        return "服务面宿主"
    if "_ClientRows" in names:
        return "内核·rows"
    if {"TdxClient", "get_client", "family_client"} & names:
        return "直连传输层"
    if {"FeedbackReporter", "UserStats"} & names:
        return "反馈"
    if {"resolve_hosts", "speedtest", "speedtest_and_save", "host_audit_main"} & names:
        return "传输·诊断"
    if "__version__" in idents:
        return "元信息"
    if "Client" in seen:
        # ``Client`` 只是被**提到**（类级 static/classmethod，例如 ``Client.capabilities()``）
        # 不等于碰得到内核：没有构造点就没有 ``runtime.execute``。落点写"内核"，处理器就得
        # 真的构造 ``Client``——否则这一格是在替文档撒一个"看起来走了内核"的谎。
        return "内核·typed" if "Client" in names else "元信息"
    raise AssertionError(f"{getattr(fn, '__name__', fn)} 的落点不在词汇表里——先扩上面的判据，别猜")


def runtime_leaves() -> dict[str, dict[str, Any]]:
    """``{"bars": {"positional": ..., "flags": [...], "landing": ..., "handler": ...}, ...}``。"""
    out: dict[str, dict[str, Any]] = {}
    for name, sp in _subparsers().items():
        group = _group_action(sp)
        if group is None:
            func = sp._defaults["func"]
            out[name] = {
                "positional": " ".join(_positionals(sp, skip="")) or _NONE,
                "flags": _flags(sp),
                "landing": _landing(func),
                "handler": func.__name__,
            }
            continue
        for leaf, child in sorted(group.choices.items()):
            func = child._defaults.get("func") or sp._defaults.get("func")
            out[f"{name} {leaf}"] = {
                "positional": " ".join(_positionals(child, skip=group.dest)) or _NONE,
                "flags": _flags(child),
                "landing": _landing(func),
                "handler": func.__name__,
            }
    return out


# ---------------------------------------------------------------------------
# 文档那一侧
# ---------------------------------------------------------------------------


def _row_cells(line: str) -> list[str]:
    return [c.strip().strip("`").strip() for c in line.strip().strip("|").split("|")]


_TICKED = re.compile(r"`([^`\n]+)`")


def _ticked(cell: str) -> list[str]:
    """格子里的反引号令牌：`` `--count` `--timeout` `` → ``["--count", "--timeout"]``。"""
    return _TICKED.findall(cell)


def doc_rows(text: str) -> dict[str, dict[str, Any]]:
    """文档表格里逐命令那一格的三条声明（位置参数 / 旗标 / 落点）。"""
    rows: dict[str, dict[str, Any]] = {}
    started = False
    for line in text.splitlines():
        if line.strip() == TABLE_HEADER:
            started = True
            continue
        if not started:
            continue
        if not line.lstrip().startswith("|"):
            break
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if set("".join(cells)) <= {"-"}:
            continue
        command, positional, flags, landing = cells
        rows[command.strip("`")] = {
            "positional": " ".join(_ticked(positional)) or _NONE,
            "flags": _ticked(flags),
            "landing": landing,
        }
    return rows


def cli_section(text: str) -> str:
    start = text.index("### CLI（")
    tail = text[start:]
    for marker in ("\n### ", "\n---\n", "\n## "):
        cut = tail.find(marker, len("### CLI（"))
        if cut != -1:
            tail = tail[:cut]
    return tail


@pytest.fixture(scope="module")
def doc_text() -> str:
    text = DOC.read_text(encoding="utf-8")
    assert TABLE_HEADER in text, "CLI 参考表被整段删掉了——这张表就是 CLI 的接口面"
    return text


# ---------------------------------------------------------------------------
# 判据
# ---------------------------------------------------------------------------


def test_the_documented_command_set_is_the_runtime_command_set(doc_text: str) -> None:
    documented, runtime = set(doc_rows(doc_text)), set(runtime_leaves())
    assert documented == runtime, (
        f"文档多出 {sorted(documented - runtime)}；文档缺名 {sorted(runtime - documented)}"
    )


def test_every_positional_cell_is_the_argparse_metavar(doc_text: str) -> None:
    rows, runtime = doc_rows(doc_text), runtime_leaves()
    drift = [
        f"{name}: 文档 `{rows[name]['positional']}` vs 运行时 `{runtime[name]['positional']}`"
        for name in runtime.keys() & rows.keys()
        if rows[name]["positional"] != runtime[name]["positional"]
    ]
    assert not drift, "位置参数漂移：" + "；".join(drift)


def test_every_flag_cell_is_exactly_argparses_own_list(doc_text: str) -> None:
    """顺序也管：插一支新旗标就得改这一格，漏了旗标或多写一个都红。"""
    rows, runtime = doc_rows(doc_text), runtime_leaves()
    drift = [
        f"{name}: 文档 {rows[name]['flags']} vs 运行时 {runtime[name]['flags']}"
        for name in runtime.keys() & rows.keys()
        if rows[name]["flags"] != runtime[name]["flags"]
    ]
    assert not drift, "旗标漂移：" + "；".join(drift)


def test_the_landing_cell_is_what_the_handler_actually_touches(doc_text: str) -> None:
    """落点：文档说"内核·typed"，处理器就得真的构造 ``Client``。"""
    rows, runtime = doc_rows(doc_text), runtime_leaves()
    drift = [
        f"{name}: 文档 {rows[name]['landing']} vs 源码 {runtime[name]['landing']}"
        for name in runtime.keys() & rows.keys()
        if rows[name]["landing"] != runtime[name]["landing"]
    ]
    assert not drift, "落点漂移：" + "；".join(drift)


def test_the_landing_vocabulary_is_closed_in_both_directions(doc_text: str) -> None:
    """含义表与表格用词互相闭合：写了没定义、定义了没人用，都算不连贯。"""
    section = cli_section(doc_text)
    used = {row["landing"] for row in doc_rows(doc_text).values()}
    defined = set()
    for line in section.splitlines():
        if line.count("|") == 3:
            first = _row_cells(line)[0]
            if first in LANDINGS:
                defined.add(first)
    assert used <= set(LANDINGS), f"表格里出现词汇表外的落点：{sorted(used - set(LANDINGS))}"
    assert used == defined, f"仅定义 {sorted(defined - used)}；仅使用 {sorted(used - defined)}"


def test_the_heading_counts_are_the_derived_ones(doc_text: str) -> None:
    match = HEADING.search(doc_text)
    assert match, "### CLI（N 子命令 / M 个叶子命令）这一行标题是这张表的分母，不能改形状"
    runtime = runtime_leaves()
    assert int(match["tops"]) == len(_subparsers()), (
        f"标题写 {match['tops']}，argparse 有 {len(_subparsers())}"
    )
    assert int(match["leaves"]) == len(runtime), f"标题写 {match['leaves']}，叶子有 {len(runtime)}"
    assert len(doc_rows(doc_text)) == len(runtime), "表体行数与分母不符"


def test_the_four_group_commands_never_appear_as_a_row(doc_text: str) -> None:
    """组命令必须给叶子：表里出现光杆 ``hosts`` 就是给了一个跑不通的用法。"""
    groups = {name for name, sp in _subparsers().items() if _group_action(sp) is not None}
    assert groups == {"feedback", "fund", "hosts", "index"}, f"组命令集合变了：{sorted(groups)}"
    bare = {name for name in doc_rows(doc_text) if " " not in name}
    assert not bare & groups, f"组命令被当成可执行命令写进表里：{sorted(bare & groups)}"


# ---------------------------------------------------------------------------
# ⑦ 内核·rows 那几支把方法名交给人写的字符串：名字必须是真能力
# ---------------------------------------------------------------------------


def _handler_source() -> str:
    import tstdx.cli.runtime_commands as rc

    return inspect.getsource(rc)


def rows_command_calls(source: str | None = None) -> dict[str, list[str]]:
    """``{"changes": ["stock_changes"], ...}``：落点为 ``内核·rows`` 的处理器转发了谁。"""
    tree = ast.parse(source if source is not None else _handler_source())
    handlers = {n.name: n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
    out: dict[str, list[str]] = {}
    for name, spec in runtime_leaves().items():
        if spec["landing"] != "内核·rows":
            continue
        forwarded = sorted(
            {
                n.attr
                for n in ast.walk(handlers[spec["handler"]])
                if isinstance(n, ast.Attribute)
                and isinstance(n.value, ast.Name)
                and n.value.id == "api"
            }
        )
        assert forwarded, f"`{name}` 落在 内核·rows，却没转发任何 ``api.<method>``"
        out[name] = forwarded
    return out


def test_every_rows_command_forwards_a_real_capability(doc_text: str) -> None:
    """``_ClientRows.__getattr__`` 把任意名字转给 ``Client``：拼错不在编译期红，只在用户手里红。

    分母不硬抄：它与文档表里 ``内核·rows`` 那几行必须是同一个集合，两边任一处变化都会在这里红。
    """
    from tstdx.catalog.capability import is_migrated_capability
    from tstdx.client.api import Client

    forwarding = rows_command_calls()
    documented = {name for name, row in doc_rows(doc_text).items() if row["landing"] == "内核·rows"}
    assert set(forwarding) == documented, (
        f"转发面与表格不符：仅源码 {sorted(set(forwarding) - documented)}；仅文档 {sorted(documented - set(forwarding))}"
    )
    unresolvable = sorted(
        {
            method
            for methods in forwarding.values()
            for method in methods
            if not (callable(getattr(Client, method, None)) or is_migrated_capability(method))
        }
    )
    assert not unresolvable, f"CLI 转发了不存在的能力：{unresolvable}"


def test_the_capability_ruler_sees_a_planted_typo() -> None:
    from tstdx.catalog.capability import is_migrated_capability

    planted = rows_command_calls(
        source=_handler_source().replace("api.stock_changes(", "api.stock_changesd(", 1)
    )
    assert "stock_changesd" in planted["changes"], "篡改转发名却没被派生出来，尺子读错了源码"
    assert not is_migrated_capability("stock_changesd"), "拼错的名字竟然是合法能力，判据 ⑦ 是空的"


def test_the_landing_ruler_sees_a_handler_that_only_borrows_the_name() -> None:
    """正控（第 24 轮）：把 ``Client`` 当**名字**用，不等于构造了它。

    ``capabilities`` 这一格 originally 被读成 ``内核·typed``——处理器里只有
    ``Client.capabilities()`` 一处类级静态调用，没有构造点，也就没有 ``runtime.execute``。
    判据不许靠人记住这件事：同一把尺子对两个形状必须给出两个答案，且答案要从**源码**出，
    所以这里把两段函数体喂进 :func:`_landing`，一段必须落 ``元信息``、一段必须落 ``内核·typed``。
    """
    import linecache

    from tstdx.client.api import Client

    def landing_of(body: str) -> str:
        source = f"def planted(args):\n{body}\n"
        linecache.cache["<planted-landing>"] = (
            len(source),
            None,
            source.splitlines(True),
            "<planted-landing>",
        )
        namespace: dict[str, Any] = {"Client": Client, "TdxClient": Client}
        exec(compile(source, "<planted-landing>", "exec"), namespace)  # noqa: S102
        return _landing(namespace["planted"])

    borrowed = landing_of("    print(list(Client.capabilities()))")
    constructed = landing_of(
        "    with Client() as client:\n        return client.snapshot('sh600519')"
    )
    assert borrowed == "元信息", f"只借类名读静态名单的处理器被判成了 {borrowed}"
    assert constructed == "内核·typed", f"真的构造 Client 的处理器被判成了 {constructed}"


def test_the_ruler_itself_sees_a_planted_drift(doc_text: str) -> None:
    """正控：四类失效各造一次，尺子必须只点出那一处——锚点全部取自真表，不硬抄字面量。"""
    section = cli_section(doc_text)
    runtime = runtime_leaves()
    body = [line for line in section.splitlines() if line.startswith("| `")]
    assert len(body) == len(runtime), "基准行数就不对，后面的对比全无效"
    assert all(row["flags"] == runtime[name]["flags"] for name, row in doc_rows(section).items()), (
        "未改动的真表自己就不一致，正控失去意义"
    )

    sample = sorted(runtime)[0]
    renamed = section.replace(f"| `{sample}` |", "| `renamed-command` |", 1)
    assert set(doc_rows(renamed)) ^ set(runtime) == {sample, "renamed-command"}, (
        "改个命令名没人看见"
    )

    multi = next(line for line in body if len(_ticked(line.split("|")[3])) > 1)
    last = _ticked(multi.split("|")[3])[-1]
    mutated = section.replace(multi, multi.replace(f" `{last}`", "", 1), 1)
    assert any(
        row["flags"] != runtime[name]["flags"]
        for name, row in doc_rows(mutated).items()
        if name in runtime
    ), f"从真行里删掉 `{last}` 也照样绿，旗标那把尺子是瞎的"

    typed_line = next(line for line in body if _row_cells(line)[3] == "内核·typed")
    landed = section.replace(typed_line, typed_line.replace("| 内核·typed |", "| 直连传输层 |"), 1)
    assert any(
        row["landing"] != runtime[name]["landing"]
        for name, row in doc_rows(landed).items()
        if name in runtime
    ), "落点被改掉也看不见"

    miscounted = HEADING.search(section)
    assert miscounted and int(miscounted["leaves"]) == len(runtime)
    broken = section.replace(f"{len(runtime)} 个叶子命令", f"{len(runtime) + 1} 个叶子命令", 1)
    assert int(HEADING.search(broken)["leaves"]) != len(runtime), "分母篡不了就谈不上量它"


# ---------------------------------------------------------------------------
# ⑧ 使用说明：36 支叶子命令各要有一条能照抄的示例
# ---------------------------------------------------------------------------

_EXAMPLE = re.compile(r"^\s*tstdx\s+(\S+)(?:\s+(\S+))?")


def example_leaves(text: str) -> set[str]:
    """CLI 小节里 ```bash 围栏块点到的叶子命令。"""
    leaves = set(runtime_leaves())
    group_of = {name.split()[0]: name.split()[0] for name in leaves if " " in name}
    found: set[str] = set()
    in_block = False
    for line in cli_section(text).splitlines():
        if line.startswith("```bash"):
            in_block = True
            continue
        if in_block and line.startswith("```"):
            in_block = False
            continue
        if not in_block:
            continue
        matched = _EXAMPLE.match(line)
        if matched is None:
            continue
        first, second = matched.group(1), matched.group(2)
        if first in leaves:
            found.add(first)
        elif first in group_of and second and f"{first} {second}" in leaves:
            found.add(f"{first} {second}")
    return found


def test_every_leaf_command_has_an_example(doc_text: str) -> None:
    runtime = set(runtime_leaves())
    covered = example_leaves(doc_text)
    assert covered, "CLI 小节里解析不出一条示例，判据 ⑧ 是瞎的"
    assert covered <= runtime, f"示例点了不存在的命令名：{sorted(covered - runtime)}"
    assert runtime == covered, f"这些叶子命令没有可照抄的示例：{sorted(runtime - covered)}"


def test_the_example_ruler_sees_a_removed_example(doc_text: str) -> None:
    runtime = set(runtime_leaves())
    victim = sorted(name for name in runtime if " " not in name)[0]
    stripped = "\n".join(
        line
        for line in doc_text.splitlines()
        if not re.match(rf"^\s*tstdx\s+{re.escape(victim)}\s", line)
    )
    assert example_leaves(stripped) == runtime - {victim}, "删掉一条示例却看不出来"


# ---------------------------------------------------------------------------
# ⑨ 「真机口径」那一节必须点到每一支叶子命令的名
# ---------------------------------------------------------------------------

#: 标题按"含真机口径"找，不把它里面的数字写进锚点：36/37 这一格本轮正好量错过
#: （37 行示例 = 36 支叶子），把会随口径变的数字当锚点等于给下一次改动埋一条假绿。
_VERDICT_HEADING = re.compile(r"^### [^\n]*真机口径[^\n]*$", re.M)


def verdict_section(text: str) -> str:
    """「真机口径」那一节：从它的标题到下一个 `##` / `---` 之前。"""
    matched = _VERDICT_HEADING.search(text)
    if matched is None:
        raise AssertionError("文档里没有「真机口径」那一节，判据 ⑨ 失去对象")
    tail = text[matched.end() :]
    cut = re.search(r"^(?:## |---$)", tail, re.M)
    return tail[: cut.start()] if cut else tail


def section_leaves(section: str) -> set[str]:
    """节内反引号片段点到的叶子命令：按**词首**匹配，不吃子串。

    `minute` 不能因为 `minute-klines` 在场就算被点到——否则抹掉分时那一格，判据照样绿。
    """
    leaves = set(runtime_leaves())
    found: set[str] = set()
    for token in _TICKED.findall(section):
        words = token.split()
        if words[:1] == ["tstdx"]:
            words = words[1:]
        if not words:
            continue
        if words[0] in leaves:
            found.add(words[0])
        if len(words) > 1 and f"{words[0]} {words[1]}" in leaves:
            found.add(f"{words[0]} {words[1]}")
    return found


def test_every_leaf_command_has_a_real_machine_verdict(doc_text: str) -> None:
    runtime = set(runtime_leaves())
    section = verdict_section(doc_text)
    covered = section_leaves(section)
    assert covered, "「真机口径」那一节里点不出任何命令名，判据 ⑨ 是瞎的"
    assert covered <= runtime, f"那一节点了不存在的命令名：{sorted(covered - runtime)}"
    assert sorted(runtime - covered) == [], (
        f"这些叶子命令在「真机口径」那一节里没有口径：{sorted(runtime - covered)}"
    )


def test_the_verdict_ruler_sees_a_blanked_command_name(doc_text: str) -> None:
    """正控：把 `server-test` 的名字从那一节抹干净，判据必须当场报它缺席。

    这不是假想的形状。第 23 轮的普查脚本用 ``line.startswith("tstdx serve")`` 挑要跳过的
    长驻服务，于是 ``tstdx server-test`` 跟着被吞，一行从没跑过的示例被写进"每一行都跑过"；
    旧读数里 `server-test` 只在 ``--help`` 那一段露过面。那一节是覆盖率声明唯一的人质，
    所以它必须逐名点齐。
    """
    section = verdict_section(doc_text)
    named = section_leaves(section)
    assert "server-test" in named, "基准读数里就没有 server-test，正控失去意义"
    blinded = section.replace("server-test", "另一支命令")
    assert section_leaves(blinded) == named - {"server-test"}, "抹掉名字却看不出来"
