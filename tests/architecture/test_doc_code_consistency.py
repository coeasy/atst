"""文档-代码一致性门禁（V17 Phase 4）。

活文档里的每一条 ``from tstdx... import X``、每一个 ``tstdx.a.b`` 引用、README
宣称的每个数字以及项目结构树里的每个 ``name/`` 与 ``name.py`` 条目，都必须与运行期
事实一致；``tstdx/__init__.py`` 的包 docstring 同样按活文档对待——分层图双向对上磁盘
布局，Quick start 的入口调用在禁网下真的构造得起来。README 与
``docs/ARCHITECTURE.md``、``docs/api/``、``docs/quickstart.md`` 等事实文档的规模数字
（命令账本 / 解析器 / 配置段 / 根级白名单 / 服务面方法数 / HTTP 源与契约下界）一律钉回
运行期真相源，WS 方法与 Domain Record 的**名单**也要逐个对上分派器与 ``__all__``——
抄一次就失真的数字与清单不再有藏身处。
历史快照（``docs/archive/``、``docs/adr/``、``DESIGN.md``）记录的是当时
语境，不参与门禁。
"""

from __future__ import annotations

import ast
import functools
import importlib
import re
from collections.abc import Callable
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

#: 参与门禁的活文档：仓库根说明 + docs/（排除归档与 ADR 历史语境）。
EXCLUDED_PARTS = {"archive", "adr", "node_modules", ".venv"}

NON_EXPORTS = {"__version__", "configure", "get_config"}


def active_docs() -> list[Path]:
    files = [ROOT / "README.md", ROOT / "SECURITY.md", ROOT / "CONTRIBUTING.md"]
    files.extend(sorted((ROOT / "docs").rglob("*.md")))
    return [
        path for path in files if path.is_file() and not EXCLUDED_PARTS.intersection(path.parts)
    ]


def fenced_code(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8")
    return re.findall(r"```[^\n]*\n(.*?)```", text, flags=re.S)


#: 只描述"当前事实"的文档：反引号里的 ``tstdx.*`` 必须是可解析的真实路径。
#: 迁移指南/发布说明/规划案里出现已删除路径是刻意的历史语境，不参与该检查。
FACT_DOC_PATHS = (
    "README.md",
    "docs/ARCHITECTURE.md",
    "docs/configuration.md",
    "docs/quickstart.md",
    "docs/errors.md",
    "docs/api/README.md",
    "docs/api/interfaces.md",
)
FACT_DOC_DIRS = ("docs/cookbook",)


def fact_docs() -> list[Path]:
    paths = [ROOT / rel for rel in FACT_DOC_PATHS]
    for rel in FACT_DOC_DIRS:
        paths.extend(sorted((ROOT / rel).glob("*.md")))
    return [path for path in paths if path.is_file()]


def import_nodes(source: str) -> list[ast.stmt]:
    try:
        tree = ast.parse(source)
    except SyntaxError:  # 伪代码块：只按行提取可辨识的 import 语句
        nodes: list[ast.stmt] = []
        for line in source.splitlines():
            if re.match(r"\s*(from|import)\s+\S", line):
                try:
                    nodes.extend(ast.parse(line).body)
                except SyntaxError:
                    continue
        return nodes
    return [n for n in tree.body if isinstance(n, ast.Import | ast.ImportFrom)]


# --------------------------------------------------------------------------
# 顶层导出面
# --------------------------------------------------------------------------


def test_root_export_map_and_dunder_all_agree() -> None:
    """``__all__`` 与惰性导入表必须是同一份事实，不得各有超集。"""
    import tstdx

    declared = set(tstdx.__all__) - NON_EXPORTS
    lazy = set(tstdx._LAZY)
    assert declared - lazy == set(), f"__all__ 有名字无法从根解析: {sorted(declared - lazy)}"
    assert lazy - declared == set(), f"惰性表偷偷扩大了公开面: {sorted(lazy - declared)}"


def test_every_documented_root_name_resolves() -> None:
    import tstdx

    for name in tstdx.__all__:
        assert hasattr(tstdx, name), f"tstdx.{name} 在 __all__ 里却不可解析"


# --------------------------------------------------------------------------
# 文档里的 import / 模块引用
# --------------------------------------------------------------------------


def _resolve(module: str, name: str | None) -> None:
    mod = importlib.import_module(module)
    if name is not None and not hasattr(mod, name):
        raise AttributeError(f"{module} 没有 {name}")


def test_markdown_import_statements_resolve() -> None:
    offenders: list[str] = []
    for path in active_docs():
        for block in fenced_code(path):
            for node in import_nodes(block):
                if isinstance(node, ast.ImportFrom):
                    if node.level or not node.module or not node.module.startswith("tstdx"):
                        continue
                    for alias in node.names:
                        if alias.name == "*":
                            continue
                        try:
                            _resolve(node.module, alias.name)
                        except Exception as exc:  # noqa: BLE001 - 报告而非中断
                            offenders.append(
                                f"{path.relative_to(ROOT)}: from {node.module} import "
                                f"{alias.name} -> {type(exc).__name__}: {exc}"
                            )
                else:
                    for alias in node.names:
                        if not alias.name.startswith("tstdx"):
                            continue
                        try:
                            _resolve(alias.name, None)
                        except Exception as exc:  # noqa: BLE001
                            offenders.append(
                                f"{path.relative_to(ROOT)}: import {alias.name} -> "
                                f"{type(exc).__name__}: {exc}"
                            )
    assert offenders == [], "\n".join(offenders)


def test_backticked_tstdx_paths_are_importable() -> None:
    offenders: list[str] = []
    pattern = re.compile(r"`(tstdx(?:\.[A-Za-z_][A-Za-z0-9_]*)+)`")
    for path in fact_docs():
        text = path.read_text(encoding="utf-8")
        for ref in sorted(set(pattern.findall(text))):
            if _resolves(ref):
                continue
            offenders.append(f"{path.relative_to(ROOT)}: 无法解析 `{ref}`")
    assert offenders == [], "\n".join(offenders)


def _resolves(dotted: str) -> bool:
    """``tstdx.a.b.C`` 可以是模块、类或属性——逐段回退解析即可。"""
    parts = dotted.split(".")
    for cut in range(len(parts), 0, -1):
        module = ".".join(parts[:cut])
        try:
            obj: object = importlib.import_module(module)
        except Exception:  # noqa: BLE001 - 继续尝试更短的模块前缀
            continue
        try:
            for attr in parts[cut:]:
                obj = getattr(obj, attr)
        except AttributeError:
            return False
        return True
    return False


# --------------------------------------------------------------------------
# README 宣称的数字
# --------------------------------------------------------------------------


def _readme() -> str:
    return (ROOT / "README.md").read_text(encoding="utf-8")


@functools.lru_cache(maxsize=1)
def _actual_facts() -> dict[str, int]:
    """运行期规模事实：构造一次 Client + HTTP app 就够全组门禁读（缓存避免重复对账）。"""
    from tstdx import Client
    from tstdx.cli import build_parser
    from tstdx.integration.mcp import TOOLS
    from tstdx.integration.runtime_http import create_runtime_app
    from tstdx.providers import PROVIDERS

    parser = build_parser()
    commands: dict[str, object] = {}
    for action in parser._actions:
        if action.dest == "command" and action.choices:
            commands = dict(action.choices)
            break
    routes = [
        route
        for route in create_runtime_app().routes
        if getattr(route, "path", "").startswith("/v13/")
        and getattr(route, "methods", None)
        and route.methods & {"GET", "POST"}
    ]
    return {
        "cli_subcommands": len(commands),
        "http_endpoints": len(routes),
        "mcp_tools": len(TOOLS),
        "capabilities": len(Client().capabilities()),
        "providers": len(PROVIDERS.ids()),
    }


@pytest.mark.parametrize(
    ("fact", "pattern"),
    [
        ("cli_subcommands", r"CLI\s*(\d+)\s*子命令"),
        ("mcp_tools", r"MCP\s*(\d+)\s*工具"),
        ("http_endpoints", r"HTTP REST\s*(\d+)\s*端点"),
        ("capabilities", r"(\d+)\s*capability"),
        ("providers", r"(\d+)\s*个?\s*Provider 注册表"),
    ],
)
def test_readme_numbers_match_runtime(fact: str, pattern: str) -> None:
    claimed = {int(n) for n in re.findall(pattern, _readme())}
    assert claimed, f"README 不再声明 {fact}，门禁失效"
    assert claimed == {_actual_facts()[fact]}, f"README {fact}={sorted(claimed)} 与运行期事实不符"


# --------------------------------------------------------------------------
# README 宣称的门禁规模（审计 F-24）
# --------------------------------------------------------------------------


def _ci_job_count() -> int:
    """ci.yml 里 `jobs:` 下的 job 键个数（两空格缩进的 `name:` 行）。"""
    text = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    jobs_block = text.split("\njobs:\n", 1)[1]
    return len(re.findall(r"^  [a-z][a-z0-9_-]*:$", jobs_block, flags=re.M))


def _gates_step_count() -> int:
    line = next(
        line
        for line in (ROOT / "Makefile").read_text(encoding="utf-8").splitlines()
        if line.startswith("gates:")
    )
    return len(line.split(":", 1)[1].split())


@pytest.mark.parametrize(
    ("pattern", "actual"),
    [(r"CI[：:]\s*(\d+)\s*jobs", _ci_job_count), (r"(\d+)\s*步确定性门禁", _gates_step_count)],
)
def test_readme_gate_scale_matches_definition(pattern: str, actual: Callable[[], int]) -> None:
    """README 说门禁有几步 / CI 有几个 job，必须与 workflow 与 Makefile 本身的定义一致。

    门禁链每删一项（F-24 的 native-compat 就是删出来的），这些数字都会静默失真。
    """
    claimed = {int(n) for n in re.findall(pattern, _readme())}
    assert claimed, f"README 不再声明 {pattern}，门禁失效"
    assert claimed == {actual()}, f"README 声称 {sorted(claimed)}，实际定义是 {actual()}"


# --------------------------------------------------------------------------
# 事实文档宣称的规模数字（审计 F-34 / F-35）
# --------------------------------------------------------------------------


def _doc_text(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def _protocol_commands() -> int:
    from tstdx.protocol.commands import COMMANDS

    return len(COMMANDS)


def _protocol_parsers() -> int:
    from tstdx.protocol.registry import PARSERS

    return len(PARSERS)


def _config_sections() -> int:
    import dataclasses

    from tstdx.config.schema import Config

    return len(dataclasses.fields(Config))


def _root_modules() -> int:
    return len(list((ROOT / "tstdx").glob("*.py")))


def _capabilities() -> int:
    return _actual_facts()["capabilities"]


def _providers() -> int:
    return _actual_facts()["providers"]


def _cli_subcommands() -> int:
    return _actual_facts()["cli_subcommands"]


def _http_routes() -> int:
    return _actual_facts()["http_endpoints"]


def _mcp_tools() -> int:
    return _actual_facts()["mcp_tools"]


def _domain_record_classes() -> int:
    from tstdx.domain import records

    return len(records.__all__)


def _domain_record_stems() -> set[str]:
    from tstdx.domain import records

    return {name[: -len("Record")] for name in records.__all__ if name.endswith("Record")}


def _ws_methods() -> set[str]:
    """WS 分派器真正认账的方法名（`if method == ...` 与 `method in {...}` 两种写法）。"""
    source = (ROOT / "tstdx" / "integration" / "runtime_ws.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    dispatch = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "_dispatch"
    )
    found: set[str] = set()
    for node in ast.walk(dispatch):
        if not (isinstance(node, ast.Compare) and isinstance(node.left, ast.Name)):
            continue
        if node.left.id != "method":
            continue
        for op, comparator in zip(node.ops, node.comparators, strict=True):
            if not isinstance(op, (ast.Eq, ast.In)):
                continue
            values = (
                [comparator]
                if isinstance(comparator, ast.Constant)
                else list(comparator.elts)
                if isinstance(comparator, (ast.Set, ast.Tuple, ast.List))
                else []
            )
            for value in values:
                if isinstance(value, ast.Constant) and isinstance(value.value, str):
                    found.add(value.value)
    return found


def _typed_contracts() -> int:
    """`scripts/contract_audit.py` 眼里的 Typed Query 契约数（对外口径以它为准）。"""
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "_contract_audit", ROOT / "scripts" / "contract_audit.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return len(module._all_typed_queries())


def _change_types() -> int:
    from tstdx.web.fundflow import EastmoneyStockChangesSource

    return len(EastmoneyStockChangesSource.CHANGE_TYPES)


def _web_source_classes() -> int:
    """``tstdx/web/`` 里定义的 ``*Source`` 类个数（按 AST 数，不触发导入副作用）。"""
    total = 0
    for path in (ROOT / "tstdx" / "web").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        total += sum(
            1
            for node in ast.walk(tree)
            if isinstance(node, ast.ClassDef) and node.name.endswith("Source")
        )
    return total


#: ``(文档, 事实, 定位模式, 真相源)``：第 14 步之前数字门禁只读 README，
#: 于是同一件事实在 ``docs/api/`` 等副本里漂移无人发现。这里把每个事实的
#: **所有**文档出处都列进表——真相源只有一个，文档侧只有抄本。
_EXACT_CLAIMS: tuple[tuple[str, str, str, Callable[[], int]], ...] = (
    ("docs/ARCHITECTURE.md", "capability 数", r"(\d+)\s+capabilit", _capabilities),
    ("docs/ARCHITECTURE.md", "协议命令数", r"（(\d+) 命令", _protocol_commands),
    ("docs/ARCHITECTURE.md", "解析器数", r"、(\d+) 解析器", _protocol_parsers),
    ("docs/ARCHITECTURE.md", "配置段数", r"（(\d+) 段", _config_sections),
    ("docs/ARCHITECTURE.md", "根级模块白名单", r"根级白名单\s*(\d+)\s*项", _root_modules),
    ("README.md", "协议命令数", r"(\d+)\s*命令账本", _protocol_commands),
    ("README.md", "解析器数", r"(\d+)\s*精确解析器", _protocol_parsers),
    ("docs/api/README.md", "capability 数", r"(\d+)\s+capability", _capabilities),
    ("docs/api/README.md", "Provider 数", r"(\d+)\s+Provider", _providers),
    ("docs/api/README.md", "协议命令数", r"(\d+)\s*命令账本", _protocol_commands),
    ("docs/api/README.md", "CLI 子命令数", r"CLI 子命令（(\d+) 项", _cli_subcommands),
    ("docs/api/README.md", "MCP 工具数", r"MCP stdio 工具（(\d+) 项", _mcp_tools),
    ("docs/api/README.md", "WS 方法数", r"（(\d+) 方法：", lambda: len(_ws_methods())),
    ("docs/api/README.md", "Domain Record 类数", r"(\d+)\s*类：", _domain_record_classes),
    ("docs/api/README.md", "盘中异动类型数", r"（(\d+) 类异动枚举）", _change_types),
    ("docs/api/interfaces.md", "capability 数", r"(\d+)\s*项\s+capability", _capabilities),
    ("docs/api/interfaces.md", "HTTP 路由数", r"（(\d+) 路由", _http_routes),
    ("docs/api/interfaces.md", "MCP 工具数", r"（(\d+) 工具", _mcp_tools),
    ("docs/api/interfaces.md", "CLI 子命令数", r"（(\d+) 子命令", _cli_subcommands),
    ("docs/api/interfaces.md", "Domain Record 类数", r"(\d+)\s*类：", _domain_record_classes),
    ("docs/quickstart.md", "capability 数", r"(\d+)\s*项\s+capability", _capabilities),
    ("docs/troubleshooting.md", "协议命令数", r"(\d+)\s*命令账本", _protocol_commands),
    ("docs/cookbook/06_custom_command.md", "协议命令数", r"(\d+)\s*命令账本", _protocol_commands),
)


@pytest.mark.parametrize(("source", "fact", "pattern", "actual"), _EXACT_CLAIMS)
def test_fact_doc_numbers_match_their_truth_source(
    source: str, fact: str, pattern: str, actual: Callable[[], int]
) -> None:
    """事实型文档的规模数字必须等于它背后的注册表 / schema / 磁盘目录。

    真相源是命令账本、解析器表、配置 dataclass 这类运行期对象：文档抄一次数字，
    此后每次增删都静默失真，所以把它钉回真相源。
    """
    claimed = {int(n) for n in re.findall(pattern, _doc_text(source))}
    assert claimed, f"{source} 不再声明 {fact}（{pattern}），门禁失效"
    assert claimed == {actual()}, f"{source} 声称 {fact}={sorted(claimed)}，真相源是 {actual()}"


#: 下界宣称（``45+ HTTP 源`` / ``60+ 契约``）：加东西不必改文档，
#: 掉到宣称界之下必须改——把界写死成精确值只会诱使作者每次新增都改一遍文档，
#: 最后又变成一处过期数字。
_FLOOR_CLAIMS: tuple[tuple[str, str, str, Callable[[], int]], ...] = (
    ("README.md", "HTTP 源类", r"(\d+)\+\s*HTTP 源", _web_source_classes),
    ("docs/ARCHITECTURE.md", "HTTP 源类", r"(\d+)\+\s*HTTP 源", _web_source_classes),
    ("docs/api/README.md", "Typed 契约", r"(\d+)\+\s*契约", _typed_contracts),
)


@pytest.mark.parametrize(("source", "fact", "pattern", "actual"), _FLOOR_CLAIMS)
def test_documented_floors_still_hold(
    source: str, fact: str, pattern: str, actual: Callable[[], int]
) -> None:
    floors = [int(n) for n in re.findall(pattern, _doc_text(source))]
    assert floors, f"{source} 不再声明 {fact}（{pattern}），门禁失效"
    real = actual()
    assert max(floors) <= real, f"{source} 声称 {max(floors)}+ 个{fact}，实际只有 {real} 个"


#: 类名清单也是事实：两份文档各抄一遍 ``Domain Record`` 族的九个名字。
_RECORD_LIST = re.compile(r"(\d+)\s*类[：:]\s*`?([A-Za-z][A-Za-z/]+)")


@pytest.mark.parametrize("source", ["docs/api/README.md", "docs/api/interfaces.md"])
def test_documented_domain_record_names_match_the_module(source: str) -> None:
    matches = _RECORD_LIST.findall(_doc_text(source))
    assert matches, f"{source} 不再列出 Domain Record 族清单，门禁失效"
    for count, names in matches:
        listed = set(names.split("/"))
        real = _domain_record_stems()
        assert int(count) == len(listed) == len(real), (
            f"{source} 声称 {count} 类，清单 {len(listed)} 项，模块实际 {len(real)} 项"
        )
        assert listed == real, (
            f"{source} 的 Record 清单与 `tstdx.domain.records.__all__` 不符："
            f"多 {sorted(listed - real)} 缺 {sorted(real - listed)}"
        )


#: 服务面公示的方法名同样是事实：文档写了分派器不认的名字，用户照抄即 -32601。
_WS_METHOD_LIST = re.compile(r"（(\d+) 方法：([A-Za-z][A-Za-z./]*)）")


def test_documented_ws_method_list_matches_the_dispatcher() -> None:
    source = "docs/api/README.md"
    matches = _WS_METHOD_LIST.findall(_doc_text(source))
    assert matches, f"{source} 不再列出 WS JSON-RPC 方法清单，门禁失效"
    for count, names in matches:
        listed = set(names.split("/"))
        real = _ws_methods()
        assert int(count) == len(listed) == len(real), (
            f"{source} 声称 {count} 个方法，清单 {len(listed)} 项，分派器实际 {len(real)} 项"
        )
        assert listed == real, (
            f"{source} 的 WS 方法清单与 `runtime_ws._dispatch` 不符："
            f"多 {sorted(listed - real)} 缺 {sorted(real - listed)}"
        )


#: 同一个枚举数字也写在代码自己的注释与 docstring 里（F-35 发现 6 处写着 16，
#: 而字典有 20 项、且已有测试断言 20）：生产代码的口径同样要钉回真相源。
_CHANGE_TYPE_COUNT = re.compile(r"(\d+)\s*类")


def test_code_comments_about_change_types_match_the_enum() -> None:
    claims: list[tuple[str, int]] = []
    for path in sorted((ROOT / "tstdx").rglob("*.py")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if "异动" not in line:
                continue
            for num in _CHANGE_TYPE_COUNT.findall(line):
                claims.append((path.relative_to(ROOT).as_posix(), int(num)))
    assert claims, "代码里不再有关于异动类型的数字说明，门禁失效"
    real = _change_types()
    wrong = sorted({f"{rel}：宣称 {n} 类" for rel, n in claims if n != real})
    assert not wrong, f"`CHANGE_TYPES` 实际有 {real} 项，代码注释却写：{wrong}"


# --------------------------------------------------------------------------
# README 项目结构树（审计 F-23）
# --------------------------------------------------------------------------

_BRANCH = re.compile(r"^\s*[├└]──\s+(.*)$")


def _readme_tree_names() -> list[str]:
    """树状图里出现的 ``name/`` 与 ``name.py`` 条目（注释列不算）。"""
    names: list[str] = []
    for line in _readme().splitlines():
        head = line.split("#", 1)[0]
        matched = _BRANCH.match(head)
        if not matched:
            continue
        names.extend(token for token in matched.group(1).split() if token.endswith(("/", ".py")))
    return names


def test_readme_tree_lists_existing_paths() -> None:
    names = _readme_tree_names()
    assert names, "README 不再含项目结构树，门禁失效"
    missing = [
        name
        for name in names
        if not (ROOT / "tstdx" / name.rstrip("/")).exists()
        and not (ROOT / "tstdx" / name.rstrip("/") / "__init__.py").exists()
    ]
    assert not missing, f"README 结构树指向磁盘不存在的路径：{missing}"


def test_readme_tree_covers_every_top_level_package() -> None:
    on_disk = {
        path.name + "/"
        for path in (ROOT / "tstdx").iterdir()
        if path.is_dir() and path.name != "__pycache__"
    }
    unlisted = sorted(on_disk - set(_readme_tree_names()))
    assert not unlisted, f"新增顶层包未写进 README 结构树：{unlisted}"


def test_readme_tree_lists_every_top_level_module() -> None:
    on_disk = {
        path.name
        for path in (ROOT / "tstdx").glob("*.py")
        if path.name == "__main__.py" or not path.name.startswith("_")
    }
    listed = set(_readme_tree_names())
    assert on_disk <= listed, f"新增顶层模块未写进 README 结构树：{sorted(on_disk - listed)}"


# --------------------------------------------------------------------------
# 包 docstring：分层图 ↔ 磁盘布局，Quick start ↔ 真实入口（审计 F-31）
# --------------------------------------------------------------------------

_LAYER_BLOCK = re.compile(r"分层（自底向上）::\n\n((?:    .+\n)+)")
_LAYER_LINE = re.compile(r"^    (\w+)\s\s+\S", flags=re.M)
_QUICKSTART_BLOCK = re.compile(r"Quick start[^:\n]*::\n\n((?:    .+\n)+)")


def _tstdx_root() -> Path:
    import tstdx

    return Path(tstdx.__file__).parent


def test_dunder_docstring_layer_map_matches_the_package_layout() -> None:
    """分层图是包自己对该库的第一张地图：漏一层或指向已删除的一层都是矛盾。"""
    import tstdx

    matched = _LAYER_BLOCK.search(tstdx.__doc__ or "")
    assert matched, "tstdx/__init__.py 不再有「分层（自底向上）」图，门禁失效"
    documented = set(_LAYER_LINE.findall(matched.group(1)))

    root = _tstdx_root()
    packages = {
        path.name for path in root.iterdir() if path.is_dir() and path.name != "__pycache__"
    }
    modules = {
        path.stem
        for path in root.glob("*.py")
        if path.name == "__main__.py" or not path.name.startswith("_")
    }
    missing = sorted(packages - documented)
    ghosts = sorted(documented - packages - modules)
    assert not missing, f"新增顶层包未写进包 docstring 分层图：{missing}"
    assert not ghosts, f"包 docstring 分层图指向磁盘不存在的层：{ghosts}"


def test_dunder_docstring_quickstart_examples_construct(monkeypatch: pytest.MonkeyPatch) -> None:
    """Quick start 里的每个入口调用都必须真的构造得起来。

    只执行名字直接调用（``Client(...)``、``DayBarReader()``），属性调用
    （``c.bars(...)``）留给真实网络冒烟；解析与域名一并禁掉，因此"因禁网/读文件而
    失败"算通过，``TypeError`` 一类的签名或入口缺陷才算红。
    """
    import socket
    from typing import Any

    class _Blocked(RuntimeError):
        pass

    def _blocked(*_args: Any, **_kwargs: Any) -> None:
        raise _Blocked("门禁内禁止触网")

    monkeypatch.setattr(socket, "getaddrinfo", _blocked)
    monkeypatch.setattr(socket, "create_connection", _blocked)

    import tstdx

    offenders: list[str] = []
    blocks = _QUICKSTART_BLOCK.findall(tstdx.__doc__ or "")
    assert blocks, "包 docstring 不再有 Quick start 段，门禁失效"
    for raw in blocks:
        source = "\n".join(line[4:] for line in raw.splitlines())
        tree = ast.parse(source)
        namespace: dict[str, Any] = {}
        for node in tree.body:
            if isinstance(node, ast.Import | ast.ImportFrom):
                exec(compile(ast.Module([node], []), "<doc>", "exec"), namespace)
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)):
                continue
            label = ast.unparse(node)
            try:
                value = eval(compile(ast.Expression(node), "<doc>", "eval"), namespace)
            except (TypeError, NameError, AttributeError, ImportError) as exc:
                offenders.append(f"{label} -> {type(exc).__name__}: {exc}")
            except Exception:  # noqa: BLE001 - 触网/读文件被拦即证明入口与签名成立
                continue
            close = getattr(value, "close", None)
            if callable(close):
                close()
    assert offenders == []


# --------------------------------------------------------------------------
# 文档里的 CLI 示例必须是真实可解析的命令行（审计 F-27）
# --------------------------------------------------------------------------

_CMD_LINE = re.compile(r"^\s*(?:\$ )?tstdx(?:\.exe)?\s+(\S+)(.*)$")
#: 含这些记号的是"用法语法"（`tstdx list <market> [--start N]`），不是可执行示例。
_USAGE_SYNTAX = re.compile(r"[\[<>|…]|\.\.\.")
_INLINE_CODE = re.compile(r"`([^`\n]+)`")


def _cli_examples() -> list[tuple[str, str, list[str]]]:
    """活文档中所有形如 ``tstdx <sub> …`` 的可执行示例（围栏块 + 行内代码）。"""
    found: list[tuple[str, str, list[str]]] = []
    for path in active_docs():
        text = path.read_text(encoding="utf-8")
        lines: list[str] = []
        for block in fenced_code(path):
            lines.extend(block.splitlines())
        for line in text.splitlines():
            lines.extend(_INLINE_CODE.findall(line))
        for line in lines:
            matched = _CMD_LINE.match(line.split("#", 1)[0].strip())
            if matched is None:
                continue
            rest = matched.group(2)
            if _USAGE_SYNTAX.search(rest):
                continue
            tokens = [matched.group(1), *(rest.split())]
            found.append((path.relative_to(ROOT).as_posix(), line.strip(), tokens))
    return found


def test_every_documented_cli_example_parses() -> None:
    """README/文档写的每条 CLI 命令都要能被真实 parser 接受。

    parser 的选项名会随重构变化（F-27 里 `serve --host` 就已被 `--bind` 取代），
    没有这道门禁，示例会像那条一样静默失效：用户照抄即得到 exit 2。
    """
    examples = _cli_examples()
    assert examples, "活文档里找不到任何 CLI 示例，门禁失效"

    from tstdx.cli.parser import build_parser

    broken: list[str] = []
    for rel, raw, tokens in examples:
        try:
            build_parser().parse_args(tokens)
        except SystemExit as exc:  # argparse 对未知选项 exit(2)
            if exc.code not in (0, None):
                broken.append(f"{rel}: {raw} → exit {exc.code}")
        except Exception as exc:  # noqa: BLE001 - 解析期不应抛别的异常
            broken.append(f"{rel}: {raw} → {type(exc).__name__}: {exc}")
    assert not broken, "文档里的 CLI 示例无法解析：\n" + "\n".join(broken)
