"""文档-代码一致性门禁（V17 Phase 4）。

活文档里的每一条 ``from atst... import X``、每一个 ``atst.a.b`` 引用、README
宣称的每个数字以及项目结构树里的每个 ``name/`` 与 ``name.py`` 条目，都必须与运行期
事实一致；``atst/__init__.py`` 的包 docstring 同样按活文档对待——分层图双向对上磁盘
布局，Quick start 的入口调用在禁网下真的构造得起来。README 与
``docs/ARCHITECTURE.md``、``docs/api/``、``docs/quickstart.md`` 等事实文档的规模数字
（命令账本 / 解析器 / 配置段 / 根级白名单 / 服务面方法数 / HTTP 源与契约下界）一律钉回
运行期真相源，WS 方法与 Domain Record 的**名单**也要逐个对上分派器与 ``__all__``——
WS 名单有两种形状（``docs/api/README.md`` 的括号清单、``docs/api/interfaces.md`` 的散文顿号
清单），两种都在射程内——抄一次就失真的数字与清单不再有藏身处。异常类计数走**反向**判据：
活文档不许写回 ``NN+ 异常类`` 这种快照（方案/台账文档除外），类数以 ``atst.errors`` 现读为准。
事实文档里反引号写出的**斜杠**路径（``facade/api.py`` 那种文件名形状）同样要落位——
按仓库根 / ``atst/`` / ``docs/`` 三个根各试一次；只有同一逻辑块（段落 / 列表项 / 表格行）
里写明删除史、或该目录由代码在运行期自建的，才允许以死路径出现。F-67 那整节虚构的
"门面层边界"正是被"只认点号形状"漏掉的。调用链之外的最后一格是**参数槽**：文档里写出的
``get_client("...")`` kind 必须与工厂注册表的键集合双向相等——幻影 kind 与漏写的真 kind
都红。
历史快照（``docs/archive/``、``docs/adr/``、``DESIGN.md``）记录的是当时
语境，不参与门禁。
"""

from __future__ import annotations

import ast
import functools
import importlib.util
import keyword
import re
import shlex
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

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


#: 只描述"当前事实"的文档：反引号里的 ``atst.*`` 必须是可解析的真实路径。
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
    import atst

    declared = set(atst.__all__) - NON_EXPORTS
    lazy = set(atst._LAZY)
    assert declared - lazy == set(), f"__all__ 有名字无法从根解析: {sorted(declared - lazy)}"
    assert lazy - declared == set(), f"惰性表偷偷扩大了公开面: {sorted(lazy - declared)}"


def test_every_documented_root_name_resolves() -> None:
    import atst

    for name in atst.__all__:
        assert hasattr(atst, name), f"atst.{name} 在 __all__ 里却不可解析"


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
                    if node.level or not node.module or not node.module.startswith("atst"):
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
                        if not alias.name.startswith("atst"):
                            continue
                        try:
                            _resolve(alias.name, None)
                        except Exception as exc:  # noqa: BLE001
                            offenders.append(
                                f"{path.relative_to(ROOT)}: import {alias.name} -> "
                                f"{type(exc).__name__}: {exc}"
                            )
    assert offenders == [], "\n".join(offenders)


def test_backticked_atst_paths_are_importable() -> None:
    offenders: list[str] = []
    pattern = re.compile(r"`(atst(?:\.[A-Za-z_][A-Za-z0-9_]*)+)`")
    for path in fact_docs():
        text = path.read_text(encoding="utf-8")
        for ref in sorted(set(pattern.findall(text))):
            if _resolves(ref):
                continue
            offenders.append(f"{path.relative_to(ROOT)}: 无法解析 `{ref}`")
    assert offenders == [], "\n".join(offenders)


# --------------------------------------------------------------------------
# 斜杠形式的死路径（F-67）
# --------------------------------------------------------------------------

#: 上面的点号判据只认 ``atst.a.b.C`` 那种形状，而 ``docs/errors.md`` §四 写的是
#: ``facade/api.py`` 这种**斜杠形式**——形状上就不进判据，于是整段虚构的"门面层边界"
#: 一路绿灯。本节把同一批文档按斜杠形式再扫一遍。
_BACKTICK = re.compile(r"`([^`\n]+)`")
_PATH_SUFFIXES = (".py", ".md", ".toml", ".json", ".txt", ".yaml", ".yml", ".cfg")
#: 删除史语境的判据词。只在**同一逻辑块**内有效：把整份文档当成一块等于没有判据。
_RETIRED_MARKERS = (
    "删除",
    "移除",
    "清理",
    "不再",
    "下线",
    "已消灭",
    "退役",
    "历史",
    "曾经",
    "retract",
)
#: 斜杠判据额外覆盖的事实文档（点号判据的集合不动，避免把本步之外的口径一起改写）。
_PATH_DOC_EXTRA = ("docs/tdx_status.md",)
_PATH_DOC_DIRS = ("docs/providers",)
_LIST_OR_ROW = re.compile(r"\s*(?:[-*+]\s|\d+\.\s|\|)")

#: 受体名单：迁移对照表的左列写的是**别家库**的 API（easyquotation 的 `hq`），
#: 那些链不是对 atst 的断言，按成员存在性判它们等于让文档不能提第三方接口。
#: 与 `BARE_WARN_ALLOWED` 同一条纪律：理由不再成立时判据即红，不许留空豁免。
FOREIGN_RECEIVERS: dict[str, str] = {
    "hq": "docs/migration/easyquotation.md 的对照表左列是 easyquotation 自己的 API",
}


def path_fact_docs() -> list[Path]:
    """按斜杠形式扫描的事实文档集合。"""
    paths = list(fact_docs())
    paths.extend(ROOT / rel for rel in _PATH_DOC_EXTRA)
    for rel in _PATH_DOC_DIRS:
        paths.extend(sorted((ROOT / rel).rglob("*.md")))
    return sorted({path for path in paths if path.is_file()})


def logical_blocks(text: str) -> list[str]:
    """切成"同一段落 / 同一个列表项 / 同一个表格行"的块。

    删除史通常写成"下列模块均已物理删除"的整句，跨行；而表格行是逐行的独立断言，
    一行说"活"就不能被另一行的"删除"赦免。
    """
    blocks: list[str] = []
    current: list[str] = []
    for line in text.splitlines():
        if not line.strip():
            if current:
                blocks.append("\n".join(current))
                current = []
            continue
        if current and _LIST_OR_ROW.match(line):
            blocks.append("\n".join(current))
            current = []
        current.append(line)
    if current:
        blocks.append("\n".join(current))
    return blocks


def _looks_like_repo_path(token: str) -> bool:
    if "/" not in token or token.startswith(("./", "../", "/", "http", "~")):
        return False
    #: 命令行、URI 与模板占位都不是"仓库里的一个路径"。
    if " " in token or "://" in token or "<" in token or ">" in token:
        return False
    if any(part in EXCLUDED_PARTS or part == ".." for part in token.split("/")):
        return False
    return token.endswith("/") or token.endswith(_PATH_SUFFIXES)


def _path_exists(token: str) -> bool:
    """相对仓库根 / ``atst/`` / ``docs/`` 任一处能落位，就算活路径。

    文档里同一个东西有三种写法（``docs/providers/tdx.md``、``facade/api.py`` 指
    ``atst/facade/api.py``、``archive/plans/…`` 指 ``docs/archive/plans/…``），
    只按一种根匹配会虚报——探针虚报比漏报更难查（F-67/F-44 的同形教训）。
    """
    return any((root / token).exists() for root in (ROOT, ROOT / "atst", ROOT / "docs"))


@functools.cache
def _runtime_dir_names() -> frozenset[str]:
    """代码在运行期自建的目录名（草稿区、缓存区）——不要求存在于版本库。

    判据是推导而不是一份名单：同一个 .py 文件里既调用 ``mkdir``，又把这个名字作为
    路径分量写出来（``... / "generated_draft" / "_generated.py"``）。刻意不用
    "代码里出现过这个字符串"当判据——``atst/security/`` 那类死目录会被
    ``/v13/security/count`` 这种 HTTP 路由字符串白白赦免。
    """
    names: set[str] = set()
    sources = list((ROOT / "atst").rglob("*.py")) + list((ROOT / "scripts").glob("*.py"))
    for path in sources:
        text = path.read_text(encoding="utf-8")
        if "mkdir" not in text:
            continue
        names.update(re.findall(r"[\"']([\w.\-]+)[\"']\s*/", text))
    return frozenset(names)


def _dead_path_findings() -> tuple[list[str], list[str]]:
    """返回 ``(违约清单, 被删除史或代码语义豁免的死路径)``。"""
    offenders: list[str] = []
    waived: list[str] = []
    for path in path_fact_docs():
        rel = path.relative_to(ROOT).as_posix()
        for block in logical_blocks(path.read_text(encoding="utf-8")):
            for raw in _BACKTICK.findall(block):
                token = raw.strip()
                if not _looks_like_repo_path(token) or _path_exists(token):
                    continue
                if any(marker in block for marker in _RETIRED_MARKERS):
                    waived.append(token)
                    continue
                if token.endswith("/") and token.split("/")[-2] in _runtime_dir_names():
                    waived.append(token)
                    continue
                offenders.append(f"{rel}: 把磁盘上不存在的 `{token}` 写成现行事实")
    return offenders, waived


def test_fact_docs_do_not_assert_dead_repo_paths() -> None:
    offenders, _ = _dead_path_findings()
    assert offenders == [], "\n".join(offenders)


def test_the_slash_ruler_itself_sees_the_deleted_layers() -> None:
    """自检：这套判据必须真的"看见"过删除史里的死路径，否则它可能只是在空转。"""
    _, waived = _dead_path_findings()
    seen = set(waived)
    assert {"execution/", "provider/", "atst/facade/"} <= seen, sorted(seen)
    assert len(seen) >= 5, sorted(seen)


# --------------------------------------------------------------------------
# 包目录的模块名册：写了几个，就必须是全部
# --------------------------------------------------------------------------

#: 目录名（``integration/`` 这种带尾斜杠的形态）。
_PKG_DIR_TOKEN = re.compile(r"^[A-Za-z0-9_.\-]+/$")
#: 归属名册里的一项（``runtime_ws.py``）；带斜杠的完整路径不属这里——它自带自己的目录，
#: 上面那把斜杠尺子已经管着它，再按"上一格目录的成员"判它等于把两把尺子搅成一团。
_MODULE_TOKEN = re.compile(r"^[A-Za-z0-9_]+\.py$")


def _package_dir(token: str) -> Path | None:
    """把 ``name/`` 落成一个**Python 包**目录（按仓库根 / ``atst/`` / ``docs/`` 三根）。

    只认包：判据断言的是"这个包的模块名册"，非包目录（``docs/api/``）里躺着什么
    不由它负责。
    """
    for root in (ROOT, ROOT / "atst", ROOT / "docs"):
        candidate = root / token
        if candidate.is_dir() and (candidate / "__init__.py").is_file():
            return candidate
    return None


def _module_roster(pkg: Path) -> set[str]:
    """包里当前的公开模块名（无 ``_`` 前缀）——名册的真相源。

    下划线开头的私有模块不参与：文档名册历来只报公开面，逼作者把
    ``_session_quote.py`` 那 11 个内部切片抄进架构表，只会换来一份更长的抄件。
    """
    return {path.stem for path in pkg.glob("*.py") if not path.name.startswith("_")}


def _roster_groups(text: str) -> list[tuple[Path, str, set[str]]]:
    """把一段文本切成 ``(包目录, 那一格, 被归到它名下的模块)``。

    归属是位置性的，但**严格锁在同一个逻辑格里**：一个裸 ``x.py`` 归给它前面最近的
    那个包目录，跨格（换段落 / 换表格行）不续账。上一版在这里漏了重置，于是
    "``profile/``、``reader/``"那一行的名册一路吃到下一行的 ``batch.py``——三条假阳性
    就是这么来的，而假阳性会把判据训练成"作者想办法让它闭嘴"的噪声。
    """
    groups: list[tuple[Path, str, set[str]]] = []
    for block in logical_blocks(text):
        if any(marker in block for marker in _RETIRED_MARKERS):
            continue
        current: Path | None = None
        named: set[str] = set()
        for raw in _BACKTICK.findall(block):
            token = raw.strip()
            if _PKG_DIR_TOKEN.match(token):
                package = _package_dir(token)
                if package is not None:
                    if current is not None and named:
                        groups.append((current, current.name, set(named)))
                    current, named = package, set()
            elif token.endswith(".py") and current is not None and _MODULE_TOKEN.match(token):
                named.add(token[: -len(".py")])
        if current is not None and named:
            groups.append((current, current.name, named))
    return groups


def _roster_findings() -> list[str]:
    offenders: list[str] = []
    for path in path_fact_docs():
        rel = path.relative_to(ROOT).as_posix()
        for package, name, named in _roster_groups(path.read_text(encoding="utf-8")):
            live = _module_roster(package)
            missing = sorted(live - named)
            phantom = sorted(named - live)
            if missing or phantom:
                offenders.append(
                    f"{rel}: `{name}/` 的名册写着 {sorted(named)}，"
                    f"磁盘上还在却查无此条 {missing}，名册里写了却没有 {phantom}"
                )
    return offenders


def test_fact_docs_module_rosters_are_complete() -> None:
    """事实文档一旦按目录点名了模块，那份名册就必须是该目录的**全部**公开模块。

    斜杠尺管"写出来的路径存不存在"，这条管"该写的没写、不该有的还留着"。
    第 3 遍量出的三条全是后者：``integration/`` 那格把已经物理删除的 ``tasks`` 写成
    现行模块（还不是反引号形态，斜杠尺看不见），``runtime/`` 少了活的 ``freshness.py``，
    ``client/`` 少了活的 ``factory.py``。
    """
    assert _roster_findings() == []


def test_the_roster_ruler_itself_sees_a_phantom_and_an_omission() -> None:
    """自检：造一格"少一个 + 多一个"的名册，判据必须两条都报出来。"""
    groups = _roster_groups("| x | `runtime/`（`kernel.py`、`executor.py`、`deleted.py`） | 活 |")
    assert [(name, named) for package, name, named in groups] == [
        ("runtime", {"kernel", "executor", "deleted"})
    ]
    live = _module_roster(ROOT / "atst" / "runtime")
    assert live - {"kernel", "executor", "deleted"}, "判据在这格上其实是空转"
    assert "freshness" in live - {"kernel", "executor", "deleted"}
    assert {"kernel", "executor", "deleted"} - live == {"deleted"}
    # 完整名册必须不报（否则判据只是无条件红，没有判出任何东西）
    full = (
        "| x | `runtime/`（"
        + "、".join(f"`{stem}.py`" for stem in sorted(_module_roster(ROOT / "atst" / "runtime")))
        + "） | 活 |"
    )
    assert _roster_groups(full)[0][2] == live
    #: 跨格不续账：换了表格行/段落，上一格的目录名吃不到下一格的模块名。
    assert _roster_groups("| a | `runtime/`（活） |\n| b | `batch.py`、`result.py` |") == []


def _resolves(dotted: str) -> bool:
    """``atst.a.b.C`` 可以是模块、类或属性——逐段回退解析即可。"""
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
# 点号调用链与小写成员（R-9 的两种新形状）
# --------------------------------------------------------------------------

#: 前两节各管一种形状：反引号里的 ``atst.a.b`` 点号路径、``facade/api.py`` 斜杠路径。
#: ``docs/troubleshooting.md`` 教用户调 ``client.router.last_errors()`` 时两种都不是——
#: 首段是一个变量名，其余全小写，判据按形状就收不进去，于是"照着做必然 AttributeError"
#: 的指令在活文档里存活了一整轮。这里把尺子从"路径"换成"成员"：调用链上除受体以外的
#: 每一段，都必须在生产代码里真的是某个属性/方法/类名。
_DOC_CALL = re.compile(r"`([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)+)\([^`]*\)`")


def chain_docs() -> list[Path]:
    """调用链判据覆盖的文档：全部活文档，但方案/台账类除外。

    后者的工作正是复述"某个名字曾经存在过"，块级"删除"豁免在这种文件上不成立——
    一份几千行的台账里"删除"二字到处都是（`logical_blocks` 的粒度救不了它）。
    """
    return [path for path in active_docs() if "REFACTOR_PLAN" not in path.name]


@functools.lru_cache(maxsize=1)
def _production_members() -> frozenset[str]:
    names: set[str] = set()
    for path in sorted((ROOT / "atst").rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Attribute):
                names.add(node.attr)
            elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                names.add(node.name)
    return frozenset(names)


def _dead_call_chain_findings() -> tuple[list[str], int, list[str]]:
    """返回 ``(违约清单, 扫到的调用链条数, 因外库受体而豁免的受体名)``。"""
    members = _production_members()
    offenders: list[str] = []
    scanned = 0
    waived: list[str] = []
    for path in chain_docs():
        rel = path.relative_to(ROOT).as_posix()
        for block in logical_blocks(path.read_text(encoding="utf-8")):
            for chain in _DOC_CALL.findall(block):
                segments = chain.split(".")
                if segments[0] in FOREIGN_RECEIVERS:
                    scanned += 1
                    waived.append(segments[0])
                    continue
                scanned += 1
                if any(marker in block for marker in _RETIRED_MARKERS):
                    continue
                for segment in segments[1:]:
                    if segment not in members:
                        offenders.append(
                            f"{rel}: `{chain}()` 的 `{segment}` 在生产代码里不是任何对象的成员"
                        )
                        break
    return offenders, scanned, sorted(set(waived))


def test_backticked_call_chains_name_real_members() -> None:
    offenders, scanned, waived = _dead_call_chain_findings()
    assert scanned > 10, f"活文档里只扫到 {scanned} 条点号调用链，说明扫描自身失效了"
    stale = sorted(set(FOREIGN_RECEIVERS) - set(waived))
    assert stale == [], f"外库受体豁免已经无人使用，请撤销：{stale}"
    assert offenders == [], "\n".join(offenders)


def test_the_member_ruler_itself_sees_the_router_chain() -> None:
    """正控：这套判据必须真的认得它要抓的那条链，否则它可能只是在空转。"""
    hit = _DOC_CALL.findall("`client.router.last_errors()`")
    assert hit == ["client.router.last_errors"], hit
    phantom, real = hit[0].split(".")[1:]
    assert phantom not in _production_members(), f"{phantom} 已经复活，正控失效"
    assert real in _production_members(), f"{real} 不在生产代码里，正控抓不到区分度"


# --------------------------------------------------------------------------
# 工厂 kind 字面量（调用链之外的那一格：参数槽）
# --------------------------------------------------------------------------

#: 上一条尺子管的是调用链上的**成员名**；`get_client("std")` 那种 bug 全在**参数槽**里，
#: 成员名 `get_client` 真实存在，链的形状也不符合要求，于是"照抄即 ValueError"的示例在
#: 使用接口文档里活了下来。工厂的 kind 是唯一一张真值表（`_CLIENT_REGISTRY`），所以这里
#: 连"写了的必须真存在"都不必单独判——直接双向对上：文档里出现的 kind 集合必须与注册表
#: 的键集合**相等**，多一个幻影少一个新 kind 都红。
_DOC_FACTORY_KIND = re.compile(r"get_client\(\s*[\"']([A-Za-z0-9_\-]+)[\"']")


@functools.lru_cache(maxsize=1)
def _factory_kinds() -> frozenset[str]:
    from atst.client.factory import _CLIENT_REGISTRY

    return frozenset(_CLIENT_REGISTRY)


def _factory_kind_findings() -> tuple[list[str], int]:
    """返回 ``(违约清单, 扫到的 kind 字面量条数)``。"""
    documented: set[str] = set()
    found: list[tuple[str, str]] = []
    scanned = 0
    for path in chain_docs():
        rel = path.relative_to(ROOT).as_posix()
        for block in logical_blocks(path.read_text(encoding="utf-8")):
            for kind in _DOC_FACTORY_KIND.findall(block):
                scanned += 1
                documented.add(kind)
                found.append((rel, kind))
    offenders = [
        f"{rel}: `get_client({kind!r})` 不是工厂认得的 kind"
        for rel, kind in found
        if kind not in _factory_kinds()
    ]
    undocumented = sorted(_factory_kinds() - documented)
    if undocumented:
        offenders.append(f"工厂有 kind 没写进文档: {undocumented}")
    return offenders, scanned


def test_documented_factory_kinds_match_the_registry() -> None:
    offenders, scanned = _factory_kind_findings()
    assert scanned > 0, '活文档里一条 get_client("...") 都没扫到，说明扫描自身失效了'
    assert _factory_kinds(), "注册表为空——判据失去分母"
    assert offenders == [], "\n".join(offenders)


def test_the_factory_kind_ruler_itself_sees_a_phantom() -> None:
    """正控：这套判据必须真的认得它要抓的那一格，否则它可能只是在空转。"""
    hit = _DOC_FACTORY_KIND.findall('`get_client("std")` 与 `get_client("async")`')
    assert hit == ["std", "async"], hit
    registry = _factory_kinds()
    assert "std" not in registry and "async" not in registry, "幻影 kind 已复活，正控失效"
    assert hit[0] not in registry and len(registry) >= 5, registry


# --------------------------------------------------------------------------
# 生产代码 docstring 里的 Sphinx 角色
# --------------------------------------------------------------------------

#: 点号判据的覆盖面是**文档**，于是包内的模块 docstring 成了一条空档：
#: ``atst/web/sources.py`` 曾用现在时语气写"在 :mod:`atst.sources.router` 中统一路由"，
#: 指向 v16 就物理删除的模块。它比文档里的假指令更糟——读代码的人正是写代码的人，
#: 而 :mod: 角色在任何 Sphinx 构建里都会直接报错。
_ROLE = re.compile(r":(?:mod|class|func|attr|meth|data):`(~?atst(?:\.[A-Za-z_][A-Za-z0-9_]*)+)`")


def test_sphinx_roles_in_package_docstrings_resolve() -> None:
    refs = 0
    offenders: list[str] = []
    for path in sorted((ROOT / "atst").rglob("*.py")):
        for target in _ROLE.findall(path.read_text(encoding="utf-8")):
            refs += 1
            dotted = target.removeprefix("~")
            if not _resolves(dotted):
                offenders.append(f"{path.relative_to(ROOT)}: `{target}` 无法解析")
    assert refs > 20, f"整包只扫到 {refs} 处 Sphinx 角色引用，说明扫描自身失效了"
    assert offenders == [], "\n".join(offenders)


# --------------------------------------------------------------------------
# README 宣称的数字
# --------------------------------------------------------------------------


def _readme() -> str:
    return (ROOT / "README.md").read_text(encoding="utf-8")


@functools.lru_cache(maxsize=1)
def _actual_facts() -> dict[str, int]:
    """运行期规模事实：构造一次 Client + HTTP app 就够全组门禁读（缓存避免重复对账）。"""
    from atst import Client
    from atst.cli import build_parser
    from atst.integration.mcp import TOOLS
    from atst.integration.runtime_http import create_runtime_app
    from atst.providers import PROVIDERS

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
    from atst.protocol.commands import COMMANDS

    return len(COMMANDS)


def _protocol_parsers() -> int:
    from atst.protocol.registry import PARSERS

    return len(PARSERS)


def _config_sections() -> int:
    import dataclasses

    from atst.config.schema import Config

    return len(dataclasses.fields(Config))


def _root_modules() -> int:
    return len(list((ROOT / "atst").glob("*.py")))


def _capabilities() -> int:
    return _actual_facts()["capabilities"]


def _providers() -> int:
    return _actual_facts()["providers"]


def _direct_bindings() -> int:
    """`DIRECT_BINDINGS` 条数：每次加/删绑定都变，README 三处抄本最容易静默过期。"""
    from atst.runtime.executor import DIRECT_BINDINGS

    return len(DIRECT_BINDINGS)


def _cli_subcommands() -> int:
    return _actual_facts()["cli_subcommands"]


def _http_routes() -> int:
    return _actual_facts()["http_endpoints"]


def _mcp_tools() -> int:
    return _actual_facts()["mcp_tools"]


def _domain_record_classes() -> int:
    from atst.domain import records

    return len(records.__all__)


def _domain_record_stems() -> set[str]:
    from atst.domain import records

    return {name[: -len("Record")] for name in records.__all__ if name.endswith("Record")}


def _ws_methods() -> set[str]:
    """WS 分派器真正认账的方法名（`if method == ...` 与 `method in {...}` 两种写法）。"""
    source = (ROOT / "atst" / "integration" / "runtime_ws.py").read_text(encoding="utf-8")
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


@functools.lru_cache(maxsize=1)
def _contract_audit() -> Any:
    """`scripts/contract_audit.py` 作为真相源加载一次（对外契约口径以它为准）。"""
    spec = importlib.util.spec_from_file_location(
        "_contract_audit", ROOT / "scripts" / "contract_audit.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _typed_contracts() -> int:
    return len(_contract_audit()._all_typed_queries())


def _change_types() -> int:
    from atst.web.fundflow import EastmoneyStockChangesSource

    return len(EastmoneyStockChangesSource.CHANGE_TYPES)


def _protocol_family_names() -> set[str]:
    """``Family`` 声明的协议族键集合（文档"5 协议族"与覆盖矩阵行标签的真相源）。"""
    from atst.protocol.commands import Family

    return {
        value
        for name, value in vars(Family).items()
        if isinstance(value, str) and not name.startswith("_")
    }


def _protocol_families() -> int:
    return len(_protocol_family_names())


def _parser_family_counts() -> dict[str, int]:
    """L1 精确解析器按族分布：``PARSERS`` 的键就是 ``(family, code)``。"""
    from atst.protocol.registry import PARSERS

    counts: dict[str, int] = {}
    for family, _code in PARSERS:
        counts[family] = counts.get(family, 0) + 1
    return counts


def _command_family_counts() -> dict[str, int]:
    """命令账本按族分布（覆盖矩阵"命令账本"列的真相源）。"""
    from atst.protocol.commands import COMMANDS

    counts: dict[str, int] = {}
    for command in COMMANDS.values():
        counts[command.family] = counts.get(command.family, 0) + 1
    return counts


def _family_port(family: str) -> int:
    """族 → 端口：内置主站池里该族唯一的那个端口。

    这张映射此前挂在 ``Command.port`` 上，而它在 ``atst/`` 生产树里没有任何读取点
    （连接池连的是 `HostEntry.port`，不是命令对象）——第 27 轮（V19 §4 P1-A2）把它删了，
    真相源随之换成池本身：文档宣称的端口必须就是代码真的拿去建连的那个端口。
    """
    from atst.transport.hosts import POOL_BY_FAMILY

    ports = {entry.port for entry in POOL_BY_FAMILY[family]}
    if len(ports) != 1:
        raise AssertionError(f"内置主站池里 {family} 的端口不唯一：{sorted(ports)}")
    return ports.pop()


def _web_source_modules() -> int:
    """定义了至少一个顶层 ``*Source`` 类的 ``atst/web/`` 模块数（文档"28 模块"的真相源）。

    V20 Phase 3 之后各家 HTTP 适配器按 Provider 归入 ``web/<provider>/adapters.py``，
    所以这里与 :func:`_web_source_classes` 同口径走递归扫描——"HTTP 源模块"数的是
    定义源类的文件，不是它们的目录深度。
    """
    modules = 0
    for path in sorted((ROOT / "atst" / "web").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        if any(
            isinstance(node, ast.ClassDef) and node.name.endswith("Source") for node in tree.body
        ):
            modules += 1
    return modules


def _client_methods() -> int:
    """`Client` 的公开方法数（文档"15 便捷方法"的真相源）。"""
    import inspect

    from atst import Client

    return sum(
        1
        for name, value in inspect.getmembers(Client, predicate=inspect.isfunction)
        if not name.startswith("_")
    )


#: 合并层数写在 `load_config` 的模块 docstring 里（代码自己的那份声明），文档抄了 4 遍。
_MERGE_ITEM = re.compile(r"^\s+\d+\.\s", re.M)


def _config_merge_layers() -> int:
    import atst.config.loader as loader

    return len(_MERGE_ITEM.findall(loader.__doc__ or ""))


def _typed_domain_base_names() -> set[str]:
    """``atst.typed_query`` 里的领域基类名（文档宣称的"10 领域基类"的真相源）。

    判据与 ``contract_audit`` 同源：抽象基类因缺必填参数构造不出来，所以"被其它契约直接
    继承的类"即基类；根 ``CapabilityQuery`` 本身是全部基类的父类，不算一个领域。
    """
    import dataclasses

    import atst.typed_query as tq

    classes = {
        name: value
        for name, value in vars(tq).items()
        if isinstance(value, type) and dataclasses.is_dataclass(value)
    }
    return {
        name
        for name, cls in classes.items()
        if name != "CapabilityQuery" and any(cls in other.__bases__ for other in classes.values())
    }


def _typed_domain_bases() -> int:
    return len(_typed_domain_base_names())


def _web_source_classes() -> int:
    """``atst/web/`` 里定义的 ``*Source`` 类个数（按 AST 数，不触发导入副作用）。"""
    total = 0
    for path in (ROOT / "atst" / "web").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        total += sum(
            1
            for node in ast.walk(tree)
            if isinstance(node, ast.ClassDef) and node.name.endswith("Source")
        )
    return total


@functools.cache
def _output_fmt_choices() -> dict[str, frozenset[str]]:
    """``atst/output`` 两处 fmt 分派真正认识的取值：按 AST 数，不读错误文案。

    ``write()`` 与 ``Sink.write()`` 的可选格式各写一份 ``if fmt == ...`` 链，两串的
    成员并不相同（CSV 只在前者）。README 把这两个数分开写，就必须分开钉。
    """
    tree = ast.parse((ROOT / "atst" / "output" / "__init__.py").read_text(encoding="utf-8"))
    found: dict[str, set[str]] = {}

    def is_fmt(node: ast.expr) -> bool:
        return (isinstance(node, ast.Name) and node.id == "fmt") or (
            isinstance(node, ast.Attribute) and node.attr == "fmt"
        )

    def collect(fn: ast.FunctionDef, key: str) -> None:
        values = {
            right.value
            for sub in ast.walk(fn)
            if isinstance(sub, ast.Compare)
            and len(sub.ops) == 1
            and isinstance(sub.ops[0], ast.Eq)
            and is_fmt(sub.left)
            and isinstance(sub.comparators[0], ast.Constant)
            and isinstance((right := sub.comparators[0]).value, str)
        }
        assert values, f"atst/output 里 {key} 的 fmt 分派形状变了，判据读不出来"
        found[key] = values

    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == "write":
            collect(node, "write")
        elif isinstance(node, ast.ClassDef) and node.name == "Sink":
            for method in node.body:
                if isinstance(method, ast.FunctionDef) and method.name == "write":
                    collect(method, "Sink.write")
    assert set(found) == {"write", "Sink.write"}, f"分派点少了一个：{sorted(found)}"
    return {key: frozenset(values) for key, values in found.items()}


def _output_write_fmts() -> int:
    return len(_output_fmt_choices()["write"])


def _sink_class_fmts() -> int:
    return len(_output_fmt_choices()["Sink.write"])


def _market_presets() -> int:
    """``PRESETS`` 的行数（文档"9 市场预设"的真相源）。"""
    from atst.profile.presets import PRESETS

    return len(PRESETS)


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
    ("README.md", "领域基类数", r"(\d+)\s*领域基类", _typed_domain_bases),
    ("docs/api/README.md", "领域基类数", r"(\d+)\s*领域基类", _typed_domain_bases),
    ("docs/api/interfaces.md", "领域基类数", r"(\d+)\s*领域基类", _typed_domain_bases),
    ("README.md", "Domain Record 族数", r"(\d+)\s*Domain Record 族", _domain_record_classes),
    # 第 19 步（F-42）：README 里同一事实的**第二种写法**曾长期在表外——"45 源"这种裸抄本
    # 与已钉的"45+ HTTP 源"是同一个数，"172 capability"在架构框图与内核小节各写一遍。
    # 表按"写法"逐行登记，每行的 `assert claimed` 保证写法一旦改名或删除就报"门禁失效"，
    # 而不是静默少对一处；新增一种写法时补一行即可。
    ("README.md", "capability 数", r"(\d+)\s+capabilit", _capabilities),
    ("README.md", "capability 数", r"(\d+)\s*项\s+capability", _capabilities),
    ("README.md", "Provider 数", r"(\d+)\s*个?\s*Provider", _providers),
    # 绑定条数在 README 写了三遍（框图 / 特性表 / 目录树），措辞各不相同，逐种写法各钉一行
    ("README.md", "执行绑定数", r"(\d+)\s*条精确绑定", _direct_bindings),
    ("README.md", "执行绑定数", r"（(\d+) 条）直调", _direct_bindings),
    ("README.md", "执行绑定数", r"executor\((\d+)\s*绑定\)", _direct_bindings),
    ("README.md", "CLI 子命令数", r"CLI\s*(\d+)\s*子命令", _cli_subcommands),
    ("README.md", "HTTP 路由数", r"(\d+)\s*端点", _http_routes),
    ("README.md", "MCP 工具数", r"(\d+)\s*工具", _mcp_tools),
    ("README.md", "协议族数", r"(\d+)\s*套?\s*协议族", _protocol_families),
    ("README.md", "协议族巡检数", r"全\s*(\d+)\s*族", _protocol_families),
    ("README.md", "协议族客户端数", r"(\d+)\s*族客户端", _protocol_families),
    ("README.md", "解析器族数", r"61\s*×\s*(\d+)\s*族", _protocol_families),
    ("README.md", "Client 便捷方法数", r"(\d+)\s*便捷方法", _client_methods),
    ("README.md", "Client 方法数", r"`Client`\s*(\d+)\s*方法", _client_methods),
    ("README.md", "HTTP 源模块数", r"(\d+)\s*模块", _web_source_modules),
    ("README.md", "配置合并层数", r"(\d+)\s*源合并", _config_merge_layers),
    ("docs/api/README.md", "配置合并层数", r"(\d+)\s*源合并", _config_merge_layers),
    ("docs/configuration.md", "配置合并层数", r"(\d+)\s*源合并", _config_merge_layers),
    ("docs/api/README.md", "协议族数", r"(\d+)\s*协议族", _protocol_families),
    # 第 15 轮（V18 R-15）：README 的"3 Sink 策略"字面上没错，却把只有 3 个取值的
    # `Sink` 当成了整个出口面，读者据此找 CSV 导出的那条路就找不到。两格分开钉。
    ("README.md", "Sink 可选格式数", r"`Sink`\s*(\d+)\s*种格式", _sink_class_fmts),
    ("README.md", "write 可选格式数", r"`write\(\)`\s*认\s*(\d+)\s*种格式", _output_write_fmts),
    # 第 16 轮（G11）：预设表从 12 列收缩到 3 列，"9 个预设"这一格用户文档写了两处，
    # 行数由此从"手抄"变成"派生"——删预设的人不必再记得改两份文档，加预设也一样。
    ("docs/api/README.md", "市场预设数", r"\|\s*(\d+)\s*市场预设", _market_presets),
    (
        "docs/cookbook/03_offline_vipdoc.md",
        "市场预设数",
        r"内置\s*(\d+)\s*个市场预设",
        _market_presets,
    ),
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


def test_csv_reaches_only_the_module_level_write() -> None:
    """fmt 判据的正控：两串取值必须真的不同，否则 README 那两格是在抄同一个数。

    这条同时钉住"CSV 只能经 ``write()`` 出口"这一 README 口径——哪天 ``Sink`` 认识了
    csv，README 与这两格数字要一起改，而不是让文档单独漂着。
    """
    choices = _output_fmt_choices()
    assert "csv" in choices["write"], "write() 的 csv 分派不见了"
    assert "csv" not in choices["Sink.write"], "Sink 现在也认 csv，README 的口径需同步"
    assert set(choices["Sink.write"]) < set(choices["write"])
    assert {"dataframe", "parquet", "duckdb"} <= choices["Sink.write"]


#: 下界宣称（``45+ HTTP 源`` / ``60+ 契约``）：加东西不必改文档，
#: 掉到宣称界之下必须改——把界写死成精确值只会诱使作者每次新增都改一遍文档，
#: 最后又变成一处过期数字。
_FLOOR_CLAIMS: tuple[tuple[str, str, str, Callable[[], int]], ...] = (
    ("README.md", "HTTP 源类", r"(\d+)\+\s*HTTP 源", _web_source_classes),
    ("README.md", "HTTP 源类", r"(\d+)\+\s*源类", _web_source_classes),
    ("README.md", "HTTP 源类", r"(\d+)\+\s*源\s*/", _web_source_classes),
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


#: 异常类计数是**反向**判据：第 44 步按 F-68 (a) 删掉 4 个从不发射的叶子之后，
#: "NN+ 异常类"这类快照当场过期。类数以 ``atst.errors`` 现读为准，活文档不得写回数字。
_ERROR_CLASS_COUNT = re.compile(r"\d+\s*\+?\s*(?:个)?\s*(?:异常|错误)\s*类")

#: 方案/台账文档记录的正是"某一步当时是多少"，它是工作日志而非对外宣称，不参与本判据。
_WORKLOG_DOCS = re.compile(r"^docs/REFACTOR")


def _worklog_docs() -> list[Path]:
    return [
        path for path in active_docs() if not _WORKLOG_DOCS.match(path.relative_to(ROOT).as_posix())
    ]


def test_the_class_count_scan_covers_the_live_doc_set() -> None:
    """金丝雀：扫描集本身必须非空并含 README，否则上一条判据会因为"没文档可读"而假绿。"""
    scanned = {p.relative_to(ROOT).as_posix() for p in _worklog_docs()}
    assert "README.md" in scanned
    assert len(scanned) >= 30, f"活文档只剩 {len(scanned)} 份，扫描面塌了"


@pytest.mark.parametrize("path", _worklog_docs(), ids=lambda p: p.relative_to(ROOT).as_posix())
def test_no_live_doc_states_an_error_class_count(path: Path) -> None:
    """活文档里出现"40+ 异常类"这种计数快照即红——它只会在下一次删类时静默变谎。"""
    text = path.read_text(encoding="utf-8")
    hits = [m.group(0) for m in _ERROR_CLASS_COUNT.finditer(text)]
    assert not hits, (
        f"{path.relative_to(ROOT).as_posix()} 写回了异常类计数快照 {hits}："
        f"类数请以 `atst.errors` 现读为准（见 `docs/errors.md` 头部条款）"
    )


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
            f"{source} 的 Record 清单与 `atst.domain.records.__all__` 不符："
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


#: 同一个服务面的方法清单在两份文档里各写一遍，形状却不同：``docs/api/README.md`` 把它
#: 塞进括号（``（10 方法：a/b/c）``），``docs/api/interfaces.md`` 用散文顿号列举并单独声明条数。
#: 只钉前者的话，后者就是一处无人对账的抄本——而它写的每一个名字都是用户会照抄的 JSON-RPC
#: 方法名，写错即 -32601（第 43 步把第二种形状纳入）。
_INTERFACE_WS_BLOCK = re.compile(
    r"### WebSocket JSON-RPC（(?P<count>\d+) 方法）(?P<body>.*?)### ", re.S
)


def test_interfaces_ws_method_prose_matches_the_dispatcher() -> None:
    source = "docs/api/interfaces.md"
    matched = _INTERFACE_WS_BLOCK.search(_doc_text(source))
    assert matched, f"{source} 不再有「WebSocket JSON-RPC（N 方法）」小节，门禁失效"
    listed = set(re.findall(r"`([a-z][a-z.]*)`", matched.group("body")))
    real = _ws_methods()
    assert listed, f"{source} 的 WS 小节解析不出方法名，门禁失效"
    assert listed == real, (
        f"{source} 的 WS 方法清单与 `runtime_ws._dispatch` 不符："
        f"多 {sorted(listed - real)} 缺 {sorted(real - listed)}"
    )
    assert int(matched.group("count")) == len(real), (
        f"{source} 声称 {matched.group('count')} 个方法，分派器实际 {len(real)} 个"
    )


_INTERFACE_MCP_COUNT = re.compile(r"### MCP stdio（(\d+) 工具）")


def test_interfaces_mcp_tool_count_matches_the_registry() -> None:
    from atst.integration.mcp import TOOLS

    source = "docs/api/interfaces.md"
    claimed = _INTERFACE_MCP_COUNT.findall(_doc_text(source))
    assert claimed, f"{source} 不再声明 MCP 工具数，门禁失效"
    assert [int(n) for n in claimed] == [len(TOOLS)] * len(claimed), (
        f"{source} 声称 {claimed} 个 MCP 工具，注册表实际 {len(TOOLS)} 个"
    )


#: ``docs/api/README.md`` 的"集成服务"表里，HTTP 与 MCP 两格各抄了一份**名单**，
#: 而门禁此前只钉过条数：把 ``/v13/security/count`` 写成 ``/v13/security/counts``、
#: 或者把 ``get_minute_today`` 改名，条数不变、判据不响，用户照抄即 404 / tool not found。
#: WS 那一格的名单早已有双向判据（上面两条），这里补齐同族的另外两格。
_FACE_CELL = re.compile(
    r"^\|\s*`atst\.integration\.(?P<face>runtime_http|mcp)`\s*\|(?P<cell>[^|\n]*)", re.M
)


def _app_route_paths() -> set[str]:
    from atst.integration.runtime_http import create_runtime_app

    return {
        str(route.path)
        for route in create_runtime_app().routes
        if str(getattr(route, "path", "")).startswith("/v13/")
    }


def _mcp_tool_names() -> set[str]:
    from atst.integration.mcp import TOOLS

    return {tool.name for tool in TOOLS}


@pytest.mark.parametrize(
    ("face", "declared", "truth", "token"),
    [
        ("runtime_http", "路由", _app_route_paths, r"`(/v13/[^`]*)`"),
        ("mcp", "项", _mcp_tool_names, r"`([a-z_]+)`"),
    ],
    ids=["http-routes", "mcp-tools"],
)
def test_documented_service_face_lists_match_the_runtime(
    face: str, declared: str, truth: Callable[[], set[str]], token: str
) -> None:
    """集成服务表里的路由/工具名单逐个等于运行期事实，两个方向都不许有差。"""
    source = "docs/api/README.md"
    cells = {
        match.group("face"): match.group("cell") for match in _FACE_CELL.finditer(_doc_text(source))
    }
    assert face in cells, f"{source} 不再有 `{face}` 那一格，门禁失效"
    header = re.search(rf"（(\d+) {declared}：", cells[face])
    assert header, f"`{face}` 那一格不再声明「N {declared}：…」，门禁失效"
    named = set(re.findall(token, cells[face]))
    real = truth()
    assert named, f"`{face}` 那一格一个名字都没扫到，判据读空了"
    assert len(named) == int(header.group(1)), (
        f"`{face}` 那一格声明 {header.group(1)} {declared}，"
        f"实际写出 {len(named)} 个：{sorted(named)}"
    )
    assert named == real, (
        f"`{face}` 的名单与运行期不符：文档写了而运行期没有 {sorted(named - real)}，"
        f"运行期有而文档没写 {sorted(real - named)}"
    )
    assert len(real) >= 9, f"{face} 的运行期名单只剩 {sorted(real)}，真相比判据先坏了"


# --------------------------------------------------------------------------
# 覆盖矩阵（README）
# --------------------------------------------------------------------------

#: 覆盖矩阵每行把族键写在标签里（``**7709 标准**（`quotation`）``），门禁因此不需要在测试里
#: 另抄一份"显示名→族键"映射：文档自己声明它指的是哪一族，测试只核对它给的键与数字是否成立。
_MATRIX_ROW = re.compile(
    r"^\| \*\*[^*]+?\*\*（`(?P<family>[a-z0-9_]+)`）\s*\|\s*(?P<port>\d+)\s*\|"
    r"[^|]*\|\s*(?P<commands>\d+)\s*\|\s*(?P<parsers>\d+)\s*\|",
    re.M,
)


def test_readme_protocol_matrix_matches_the_registries() -> None:
    """协议覆盖矩阵的每族分布必须逐行对上命令账本、解析器表与端口映射。

    第 19 步（F-42）实测：矩阵的总数（85 命令 / 61 解析器）一直被钉着，而每族分列是手抄本——
    4/5 行都错（MAC 8 实际 16、F10 15 实际 1、商品 8 实际 11、扩展市场 12 实际 15），
    且商品语义的端口写反（7709，而当时代码里的族→端口映射与主站池都是 7727；那条映射
    即第 27 轮删掉的 `Command.port`，现在端口只从内置主站池现读）。总数对得上让
    分列的错误看起来无害，所以这里比的是分布而不是和。
    """
    rows = {
        match["family"]: (
            int(match["port"]),
            int(match["commands"]),
            int(match["parsers"]),
        )
        for match in _MATRIX_ROW.finditer(_doc_text("README.md"))
    }
    assert rows, "README 不再有带族键的协议覆盖矩阵，门禁失效"
    commands, parsers = _command_family_counts(), _parser_family_counts()
    assert len(rows) == len(_MATRIX_ROW.findall(_doc_text("README.md"))), "矩阵里同一族写了多行"
    assert set(rows) == set(commands) == set(parsers), (
        f"矩阵的族集合与注册表不符：矩阵 {sorted(set(rows) - set(commands))} 多、"
        f"{sorted(set(commands) - set(rows))} 缺（新增协议族必须同步这张表）"
    )
    wrong = [
        f"{family}：宣称 端口 {port} / {command} 命令 / {parser} 解析器，"
        f"实际 {_family_port(family)} / {commands[family]} / {parsers[family]}"
        for family, (port, command, parser) in rows.items()
        if (port, command, parser) != (_family_port(family), commands[family], parsers[family])
    ]
    assert not wrong, "协议覆盖矩阵与注册表不符：\n" + "\n".join(wrong)


#: 同一个枚举数字也写在代码自己的注释与 docstring 里（F-35 发现 6 处写着 16，
#: 而字典有 20 项、且已有测试断言 20）：生产代码的口径同样要钉回真相源。
_CHANGE_TYPE_COUNT = re.compile(r"(\d+)\s*类")


def test_code_comments_about_change_types_match_the_enum() -> None:
    claims: list[tuple[str, int]] = []
    for path in sorted((ROOT / "atst").rglob("*.py")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if "异动" not in line:
                continue
            for num in _CHANGE_TYPE_COUNT.findall(line):
                claims.append((path.relative_to(ROOT).as_posix(), int(num)))
    assert claims, "代码里不再有关于异动类型的数字说明，门禁失效"
    real = _change_types()
    wrong = sorted({f"{rel}：宣称 {n} 类" for rel, n in claims if n != real})
    assert not wrong, f"`CHANGE_TYPES` 实际有 {real} 项，代码注释却写：{wrong}"


#: 审计脚本自述的规模数字同样是抄本（F-25 让它"描述它真正跑的门禁"，但没对账数字）。
#: V18 第 7 轮把覆盖口径改成三个派生集合之后，散文里**不该再有**计数：这条判据因此
#: 从"抄的数字对不对"换成"有没有再抄"，并留下一条反向自检——"没有抄本"必须是有人
#: 在算才有意义，否则它只是"没人算"的另一种说法。
#: 射程是**每一段** docstring：本轮一开始只盯模块那一段，而函数说明里同样写着
#: "17 个名字／12 个重复／9 个领域基类"——旧名单的规模是历史证据（记在重构方案，有日志可查），
#: 契约基类的数则是当前口径的抄本，两者都不该留在代码散文里。
_AUDIT_COUNT_PROSE = re.compile(r"\d+\s*[个项]")


def _audit_docstrings() -> dict[str, str]:
    """``scripts/contract_audit.py`` 里模块与所有类/函数的 docstring，按名字索引。"""
    tree = ast.parse((ROOT / "scripts" / "contract_audit.py").read_text(encoding="utf-8"))
    out: dict[str, str] = {}
    module_doc = ast.get_docstring(tree)
    if module_doc is not None:
        out["<module>"] = module_doc
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        doc = ast.get_docstring(node)
        if doc is not None:
            out[node.name] = doc
    return out


def test_contract_audit_docstring_numbers_match_the_audit() -> None:
    docs = _audit_docstrings()
    assert len(docs) >= 10, f"只解析出 {len(docs)} 段 docstring，门禁自身失效"
    copied = {
        name: found for name, doc in docs.items() if (found := _AUDIT_COUNT_PROSE.findall(doc))
    }
    assert not copied, f"contract_audit 的散文又抄了计数：{copied}（口径应现算在报告行里）"
    ca = _contract_audit()
    assert len(ca.registered_capabilities()) >= ca.MIN_REGISTERED
    assert len(ca.typed_capabilities()) >= ca.MIN_TYPED_COVERAGE
    assert len(ca.gateway_capabilities()) >= ca.MIN_GATEWAY_COVERAGE


def test_typed_query_denominator_is_not_a_hand_copied_list() -> None:
    """契约分母只能由结构判据算出，不能靠手抄的基类名单。

    同一份 12 个名字曾在 5 处各自抄一遍：漏更的那一处会把新增的抽象基类静默算成契约，
    于是对外宣称的契约数虚增——而"名单 vs 构造失败"两套判据同时成立时无人发现。
    """
    offenders: list[str] = []
    hand_copied = _typed_domain_base_names() | {"CapabilityQuery", "TypedQueryResult"}
    for rel in ("scripts/contract_audit.py", "tests/v14/test_contract_automation.py"):
        text = (ROOT / rel).read_text(encoding="utf-8")
        quoted = [name for name in hand_copied if f'"{name}"' in text]
        if quoted:
            offenders.append(f"{rel}: {sorted(quoted)}")
    assert not offenders, f"契约计数重新依赖手抄基类名单：{offenders}"


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
        if not (ROOT / "atst" / name.rstrip("/")).exists()
        and not (ROOT / "atst" / name.rstrip("/") / "__init__.py").exists()
    ]
    assert not missing, f"README 结构树指向磁盘不存在的路径：{missing}"


def test_readme_tree_covers_every_top_level_package() -> None:
    on_disk = {
        path.name + "/"
        for path in (ROOT / "atst").iterdir()
        if path.is_dir() and path.name != "__pycache__"
    }
    unlisted = sorted(on_disk - set(_readme_tree_names()))
    assert not unlisted, f"新增顶层包未写进 README 结构树：{unlisted}"


def test_readme_tree_lists_every_top_level_module() -> None:
    on_disk = {
        path.name
        for path in (ROOT / "atst").glob("*.py")
        if path.name == "__main__.py" or not path.name.startswith("_")
    }
    listed = set(_readme_tree_names())
    assert on_disk <= listed, f"新增顶层模块未写进 README 结构树：{sorted(on_disk - listed)}"


#: 审计文档不是用户会读的那一页：台账与变更日志里点名某个导出，不等于它有使用文档。
#: （`docs/archive`、`docs/adr` 已由 :func:`active_docs` 排掉。）
AUDIT_DOC_MARKS = ("REFACTOR", "CHANGELOG")


def _user_docs() -> list[Path]:
    return [
        path for path in active_docs() if not any(mark in path.name for mark in AUDIT_DOC_MARKS)
    ]


def _undocumented_exports(names: Iterable[str]) -> list[str]:
    """顶层导出名里，用户文档一个字都没提到的那几个。"""

    text = "\n".join(path.read_text(encoding="utf-8") for path in _user_docs())
    return sorted(name for name in names if not re.search(rf"\b{re.escape(name)}\b", text))


def test_every_root_export_is_named_by_user_docs() -> None:
    """包上挂着的名字，读者必须查得到（第 25 轮第 2 遍登记）。

    与 :func:`test_root_export_map_and_dunder_all_agree` 的分工：那条管"声明的名字解析得出"，
    这一条管"解析得出的名字说得出用途"。分母是运行期的 ``atst.__all__``，不是文档里的
    一份抄件——加一个顶层导出不写文档，当场红。第一版量出 45 个名字里恰缺 1 个
    （`FinanceReader`：包上挂着、`tests/reader/` 在用、用户文档一个字没提）。
    """
    import atst

    missing = _undocumented_exports(atst.__all__)
    assert not missing, f"顶层导出没有用户文档可查：{missing}"
    #: 正控：尺子必须认得一个它从没见过的名字。
    planted = "PlantedSurfaceName25"
    assert _undocumented_exports([*atst.__all__, planted]) == [planted], (
        "凭空加的顶层导出没被抓到——本判据对新增名字是瞎的"
    )


def _reader_surface_rows() -> list[str]:
    """``docs/api/interfaces.md`` 本地文件读取面表格的第一列。"""

    doc = (ROOT / "docs" / "api" / "interfaces.md").read_text(encoding="utf-8")
    parts = doc.split("### 本地文件读取面（vipdoc 落地文件）", 1)
    assert len(parts) == 2, "interfaces.md 不再有本地文件读取面那一节，门禁失效"
    body = re.split(r"^### |^## ", parts[1], maxsplit=1, flags=re.M)[0]
    rows = re.findall(r"^\| `(\w+)` \|", body, re.M)
    assert rows, "读取面表格解析不出任何一行，门禁失效"
    return rows


def test_reader_surface_table_matches_the_lazy_map() -> None:
    """读取面表格 = 顶层惰性导入表落在 `atst.reader` 的那一批，双向相等。

    这一族的"公开面"由 `atst._LAZY` 决定（它就是 `atst.<name>` 的来源），所以文档表格的
    分母从那里现取：往 `_LAZY` 里加一个 reader 名字而文档没跟上，或从表格里抹掉一行，都红。
    """
    import atst

    derived = sorted(
        name for name, (module, _attr) in atst._LAZY.items() if module == "atst.reader"
    )
    assert sorted(_reader_surface_rows()) == derived, (
        f"读取面表格 {sorted(_reader_surface_rows())} ≠ _LAZY 派生的 {derived}"
    )


def _client_face_rows() -> list[tuple[str, str]]:
    """``docs/api/interfaces.md`` 的 Client 方法表：``[(方法名, 签名摘要), ...]``。"""
    doc = (ROOT / "docs" / "api" / "interfaces.md").read_text(encoding="utf-8")
    parts = doc.split("### Client（唯一业务入口，同步）", 1)
    assert len(parts) == 2, "interfaces.md 不再有 Client 方法表，门禁失效"
    # 窗口收在本节之内：下一节「能力发现面」的表第一列是注册表能力名，不是方法名。
    body = re.split(r"^### ", parts[1], maxsplit=1, flags=re.M)[0]
    rows = re.findall(r"^\| `(\w+)` \| `([^`]*)` \|", body, re.M)
    assert rows, "Client 方法表里解析不出任何一行，门禁失效"
    return rows


def _tdx_face_rows() -> list[tuple[str, str]]:
    """``docs/api/interfaces.md`` §1 的 TdxClient 方法表（协议层客户端）。"""
    doc = (ROOT / "docs" / "api" / "interfaces.md").read_text(encoding="utf-8")
    parts = doc.split("### TdxClient（同步）", 1)
    assert len(parts) == 2, "interfaces.md 不再有 TdxClient 方法表，门禁失效"
    # 本节止于下一节 AsyncTdxClient：再往后的表属于多协议族客户端与命令账本查询面。
    body = re.split(r"^### ", parts[1], maxsplit=1, flags=re.M)[0]
    rows = re.findall(r"^\| `(\w+)` \| `([^`]*)` \|", body, re.M)
    assert rows, "TdxClient 方法表里解析不出任何一行，门禁失效"
    return rows


def _documented_params(sig: str) -> list[str]:
    """从 ``(<参数>) -> <返回>`` 摘要里取参数名（按 AST，不猜字符串形状）。"""
    args = ast.parse(f"def _f{sig}: pass").body[0].args
    names = [item.arg for item in args.posonlyargs + args.args + args.kwonlyargs]
    if args.vararg:
        names.append(args.vararg.arg)
    if args.kwarg:
        names.append(args.kwarg.arg)
    return names


def test_client_method_table_matches_the_real_signatures() -> None:
    """表里每行签名都是手抄本：给 ``Client`` 加一个形参而表没跟上，当场过期（F-42）。

    第 26 步给 ``Client.bars()`` 加 ``strict`` 时正是这张表先变的——它的行此前只被
    "方法数"门禁读过，参数名一个都不在校验范围内。双向判据：名单不漏行也不多行，
    每行参数名与真实签名求差为空。
    """
    import inspect

    from atst import Client

    rows = _client_face_rows()
    public = {
        name
        for name, value in inspect.getmembers(Client, predicate=inspect.isfunction)
        if not name.startswith("_")
    }
    listed = {name for name, _sig in rows}
    assert listed == public, (
        f"Client 方法表与真实公开方法不一致：表里缺 {sorted(public - listed)}，"
        f"多出 {sorted(listed - public)}"
    )
    for name, sig in rows:
        real = [
            item for item in inspect.signature(getattr(Client, name)).parameters if item != "self"
        ]
        documented = _documented_params(sig)
        assert set(documented) == set(real), (
            f"interfaces.md 的 `Client.{name}` 签名摘要已过期：文档 {sorted(documented)} "
            f"≠ 真实 {sorted(real)}"
        )


def test_tdx_client_method_table_matches_the_real_signatures() -> None:
    """协议层客户端那张表与 ``Client`` 表同形，却一度只在人眼里核对过。

    V18 第 15 轮给 ``TdxClient`` 表补 `request_result` / `capital_changes` / `open` /
    `close` 四行时，全仓没有任何判据读过这张表的第二列——同一份文档里
    `Client` 表被钉到参数名级别，而它上面那张表连"漏了一整个公开方法"都不会响。
    """
    import inspect

    from atst.client import TdxClient

    rows = _tdx_face_rows()
    public = {
        name
        for name, value in inspect.getmembers(TdxClient, predicate=inspect.isfunction)
        if not name.startswith("_")
    }
    listed = {name for name, _sig in rows}
    assert listed == public, (
        f"TdxClient 方法表与真实公开方法不一致：表里缺 {sorted(public - listed)}，"
        f"多出 {sorted(listed - public)}"
    )
    for name, sig in rows:
        # 下划线前缀参数是内部通道（如 `quotes(_collect=...)` 的失败袋），不进对外签名表。
        real = [
            item
            for item in inspect.signature(getattr(TdxClient, name)).parameters
            if item != "self" and not item.startswith("_")
        ]
        documented = _documented_params(sig)
        assert set(documented) == set(real), (
            f"interfaces.md 的 `TdxClient.{name}` 签名摘要已过期：文档 {sorted(documented)} "
            f"≠ 真实 {sorted(real)}"
        )


# --------------------------------------------------------------------------
# 包 docstring：分层图 ↔ 磁盘布局，Quick start ↔ 真实入口（审计 F-31）
# --------------------------------------------------------------------------

_LAYER_BLOCK = re.compile(r"分层（自底向上）::\n\n((?:    .+\n)+)")
_LAYER_LINE = re.compile(r"^    (\w+)\s\s+\S", flags=re.M)
_QUICKSTART_BLOCK = re.compile(r"Quick start[^:\n]*::\n\n((?:    .+\n)+)")


def _atst_root() -> Path:
    import atst

    return Path(atst.__file__).parent


def test_dunder_docstring_layer_map_matches_the_package_layout() -> None:
    """分层图是包自己对该库的第一张地图：漏一层或指向已删除的一层都是矛盾。"""
    import atst

    matched = _LAYER_BLOCK.search(atst.__doc__ or "")
    assert matched, "atst/__init__.py 不再有「分层（自底向上）」图，门禁失效"
    documented = set(_LAYER_LINE.findall(matched.group(1)))

    root = _atst_root()
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

    class _Blocked(RuntimeError):
        pass

    def _blocked(*_args: Any, **_kwargs: Any) -> None:
        raise _Blocked("门禁内禁止触网")

    monkeypatch.setattr(socket, "getaddrinfo", _blocked)
    monkeypatch.setattr(socket, "create_connection", _blocked)

    import atst

    offenders: list[str] = []
    blocks = _QUICKSTART_BLOCK.findall(atst.__doc__ or "")
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

_CMD_LINE = re.compile(r"^\s*(?:\$ )?atst(?:\.exe)?\s+(\S+)(.*)$")
#: "用法语法"而非可执行示例的形状：可选旗标 ``[--start N]``、尖括号占位符 ``<market>``、
#: 竖线分支 ``day|week``、省略号。旧口径还额外把**任何**裸 ``[`` 算进来，于是
#: ``--args [[8201]]`` 这类真实示例整行免检——第 23 轮文档里那条坏示例正是这样全绿的。
_USAGE_SYNTAX = re.compile(r"\[--|<[^>\s]+>|\||…|\.\.\.")
_INLINE_CODE = re.compile(r"`([^`\n]+)`")


def _cli_examples() -> list[tuple[str, str, list[str] | None]]:
    """活文档中所有形如 ``atst <sub> …`` 的可执行示例（围栏块 + 行内代码）。

    返回 ``(相对路径, 原始行, token 列表)``；token 按 :func:`shlex.split`（POSIX shell 的
    引号/分词规则——示例的交付口径就是用户在 shell 里照抄）切，引号不闭合（这行本来就
    粘不进 shell）时 token 列表为 ``None``，由调用方判红而不是静默跳过。
    """
    found: list[tuple[str, str, list[str] | None]] = []
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
            try:
                tokens: list[str] | None = [matched.group(1), *shlex.split(rest)]
            except ValueError:
                tokens = None
            found.append((path.relative_to(ROOT).as_posix(), line.strip(), tokens))
    return found


def test_every_documented_cli_example_parses() -> None:
    """README/文档写的每条 CLI 命令都要能被真实 parser 接受。

    parser 的选项名会随重构变化（F-27 里 `serve --host` 就已被 `--bind` 取代），
    没有这道门禁，示例会像那条一样静默失效：用户照抄即得到 exit 2。
    """
    examples = _cli_examples()
    assert examples, "活文档里找不到任何 CLI 示例，门禁失效"

    from atst.cli.parser import build_parser

    broken: list[str] = []
    for rel, raw, tokens in examples:
        if tokens is None:
            broken.append(f"{rel}: {raw} → 引号不闭合，这行粘不进 shell")
            continue
        try:
            build_parser().parse_args(tokens)
        except SystemExit as exc:  # argparse 对未知选项 exit(2)
            if exc.code not in (0, None):
                broken.append(f"{rel}: {' '.join(tokens)} → exit {exc.code}")
        except Exception as exc:  # noqa: BLE001 - 解析期不应抛别的异常
            broken.append(f"{rel}: {raw} → {type(exc).__name__}: {exc}")
    assert not broken, "文档里的 CLI 示例无法解析：\n" + "\n".join(broken)


def test_cli_example_usage_filter_lets_quoted_json_through() -> None:
    """豁免本身不许失明：窄口径必须比旧的整行 ``[`` 扫描少放走真实示例。

    三格都是人工种下的，不读活文档：①旧口径（``[\\[<>|…]|...``）会把文档那条 query
    示例整行判成"用法语法"而永不解析——这正是第 23 轮它坏了却全绿的机制；②新口径必须
    解析它；③新口径仍然放过真的用法语法（``[--start N]`` / ``<market>`` / ``day|week``）。
    """
    example = "atst query stock_changes --args '[[8201]]' --kwargs '{\"size\": 5}'"
    matched = _CMD_LINE.match(example)
    assert matched is not None
    rest = matched.group(2)
    assert re.search(r"[\[<>|…]|\.\.\.", rest), "旧口径已不再放宽：这一格对照测不到收窄"
    assert not _USAGE_SYNTAX.search(rest), "新口径把加了引号的 JSON 示例整行放走了"

    for usage in (
        "atst list <market>",
        "atst list 0 [--start N]",
        "atst bars sh600519 --period day|week",
        "atst query …",
    ):
        matched_usage = _CMD_LINE.match(usage)
        assert matched_usage is not None
        assert _USAGE_SYNTAX.search(matched_usage.group(2)), f"真用法语法被当成可执行示例：{usage}"

    #: 引号不闭合的行必须进清单并被标成 ``None``，而不是被静默跳过。
    broken_line = "atst query stock_changes --args '[[8201]]"
    matched_broken = _CMD_LINE.match(broken_line)
    assert matched_broken is not None
    assert not _USAGE_SYNTAX.search(matched_broken.group(2))
    with pytest.raises(ValueError):
        shlex.split(matched_broken.group(2))


# --------------------------------------------------------------------------
# 文档里的**命令形状**点名的模块/对象必须可解析（V18 第 5 轮）
# --------------------------------------------------------------------------

#: 上一节钉的是 `atst <子命令>` 这一类 CLI 示例；这一节钉"把 atst 的某个模块交给外部
#: 解释器"的写法：`python -m atst.x.y`、`uvicorn atst.x.y:attr`、`from/import atst.x`。
#: 反引号点号判据看不见它们——那条正则要求**整格反引号恰好是一个点号路径**，而命令行里的
#: 引用后面跟着 `:attr` 与参数，还多半躺在代码围栏里。`docs/FAQ.md` 的部署段就因此教了
#: 用户一条当场 `ModuleNotFoundError` 的命令（`atst.integration.http_server` 早已不存在，
#: 真身是 `runtime_http.create_runtime_app`），本轮全量扫描 101 处里唯一的一处。
#:
#: **为什么不扩成"所有点号引用都扫"**：取证探针按那个口径扫全部活文档得到 20 处命中，19 处是
#: `atst.git`（URL 尾巴）、`atst.toml`（文件名）与迁移/发布说明里的**否定句**（"不再有
#: `atst.compat`"）——第 4 轮探针 C 那条"散文里对否定是瞎的"在这里原形重现。命令形状没有
#: 叙述语境（用户照着敲），所以这一格不需要任何豁免名单。
_CMD_TARGET = re.compile(
    r"(?:python(?:\d\.\d+)?|uvicorn)\s+(?:-m\s+)?[\"']?"
    r"(atst(?:\.[A-Za-z_][A-Za-z0-9_]*)+)(?::([A-Za-z_][A-Za-z0-9_]*))?"
)
_IMPORT_TARGET = re.compile(r"\b(?:from|import)\s+(atst(?:\.[A-Za-z_][A-Za-z0-9_]*)*)")


def _command_targets(text: str) -> list[tuple[str, str | None]]:
    """正文里以命令/导入形状点名的 ``(模块, 可选对象名)``；对象名来自 `module:attr` 写法。"""
    found: list[tuple[str, str | None]] = [
        (m.group(1), m.group(2)) for m in _CMD_TARGET.finditer(text)
    ]
    found += [(m.group(1), None) for m in _IMPORT_TARGET.finditer(text)]
    return found


def _target_offense(module: str, attr: str | None) -> str | None:
    """模块导得进来、且 `:attr` 点名的对象真在，才算这条命令跑得起来。"""
    try:
        obj: object = importlib.import_module(module)
    except Exception as exc:  # noqa: BLE001 - 报告而非中断
        return f"{module} 导入失败（{type(exc).__name__}）"
    if attr is not None and not hasattr(obj, attr):
        return f"{module} 里没有 {attr}"
    return None


def test_documented_command_targets_resolve() -> None:
    offenders: list[str] = []
    checked = 0
    for path in chain_docs():
        text = path.read_text(encoding="utf-8")
        rel = path.relative_to(ROOT).as_posix()
        for module, attr in _command_targets(text):
            checked += 1
            why = _target_offense(module, attr)
            if why:
                offenders.append(f"{rel}: {why}")
    assert checked >= 60, f"只扫到 {checked} 处命令/导入形状，判据自身失明"
    assert offenders == [], "文档教了跑不起来的命令：\n" + "\n".join(sorted(set(offenders)))


def test_the_command_target_ruler_sees_a_planted_dead_target() -> None:
    """正控：形状必须是"命令里的模块引用"，且 `:attr` 那一半也在射程内。"""
    planted = (
        "```bash\n"
        "docker run -p 8000:8000 atst python -m uvicorn "
        "atst.integration.http_server:create_app --factory\n"
        "```\n"
    )
    targets = _command_targets(planted)
    assert targets == [("atst.integration.http_server", "create_app")], targets
    offense = _target_offense(*targets[0])
    assert offense is not None and "http_server" in offense, offense
    # 反面对照：写对了就不许报，否则"零缺陷"是恒报报出来的
    assert _target_offense("atst.integration.runtime_http", "create_runtime_app") is None
    # 模块存在但对象名是抄错的，同样必须报
    assert _target_offense("atst.integration.runtime_http", "no_such_app") is not None


# --------------------------------------------------------------------------
# 反向指路牌：代码注释里的 docs 路径也要落位（V18 R-15）


#: 代码里指向文档的"指路牌"。中文文件名要进字符类，否则 ADR 那类引用扫不到；
#: 斜杠也要进，否则 "docs" 目录下多层展开的那类路径整个不在形状内。
_DOCS_CITE = re.compile(r"(?<![\w./-])docs/[\w./\-\u4e00-\u9fff]+\.md")


def _doc_citations_in(text: str) -> list[str]:
    return _DOCS_CITE.findall(text)


def _code_doc_citations() -> list[tuple[str, str]]:
    """``[(文件:行, 所引 docs 路径)]``：``atst/``、``scripts/``、``tests/`` 全量。"""
    found: list[tuple[str, str]] = []
    for sub in ("atst", "scripts", "tests"):
        for path in sorted((ROOT / sub).rglob("*.py")):
            rel = path.relative_to(ROOT).as_posix()
            for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
                found.extend((f"{rel}:{lineno}", token) for token in _doc_citations_in(line))
    return found


def test_code_cites_only_docs_paths_that_exist() -> None:
    """代码把文档路径写进注释当证据出处，文档一归档这个出处就指空。

    第 15 轮把 5 份对标文档移入 ``docs/archive/parity/`` 时，实测有 4 处这样的断指路牌
    （``atst/web/limits.py`` 引 v5 计划、两处测试引已归档计划、一处引刚移动的对标件）——
    文档侧的路径判据是"文档 → 磁盘"，从来没有人反向量过"代码 → 文档"。
    """
    cited = _code_doc_citations()
    assert len(cited) >= 25, f"只扫到 {len(cited)} 处代码里的 docs 引用，判据自身失明"
    offenders = sorted({f"{loc} -> {token}" for loc, token in cited if not (ROOT / token).exists()})
    assert offenders == [], "代码把不存在（多半是已归档）的文档路径写成出处：\n" + "\n".join(
        offenders
    )


def test_the_docs_citation_ruler_sees_a_planted_dead_path() -> None:
    """正控：指路牌的形状必须被抓得到，且"移动后失效"这种改法要报得出来。

    路径按运行期拼接，源码里不留下一条完整的死路径——否则本文件自己就是第一个违约者。
    """
    dead = "docs/archive/parity/" + "no_such_plan.md"
    line = f"# 见 docs/quickstart.md 与 {dead} 两处的口径"
    assert _doc_citations_in(line) == ["docs/quickstart.md", dead]
    cited = _code_doc_citations()
    assert [loc for loc, token in cited if token == "docs/quickstart.md"], "活文档路径没被扫到"


#: 账本表格行的形状：一行必须与它所在表格的表头同竖线数。
#: 第 18 轮由自己踩出来：往 §2 账本插 G13 那一行时，插入手法把上一行（G11）的 814 字符尾巴
#: 粘进了新行——一行 8 个竖线而表头是 5 个，读者看到的是"一条行里装着两条账"。
#: 本文件其余判据都按**内容**读文档（路径、数字、代码块），没有一把尺量过表格行的形状。
_TABLE_EXEMPT: dict[str, str] = {}


def _table_cells(line: str) -> int:
    """数一行 markdown 表格的竖线：行内代码整段不算，转义竖线不算。"""

    return re.sub(r"`[^`]*`", "", line).replace("\\|", "").count("|")


def _markdown_table_blocks(text: str) -> list[list[tuple[int, str]]]:
    """按连续行切出表格块（至少表头 + 分隔行 + 一行内容）。

    围栏代码块内的行即使以 ``|`` 开头也不是表格：ATST 架构文档把发布流程图
    画成 ASCII 框线（``| - wheel |``），只按行首字符读会把示意图误判成坏表格。
    """

    blocks: list[list[tuple[int, str]]] = []
    current: list[tuple[int, str]] = []
    in_fence = False
    for number, line in enumerate(text.replace("\r\n", "\n").split("\n"), start=1):
        stripped = line.lstrip()
        if stripped.startswith("```") or stripped.startswith("~~~"):
            in_fence = not in_fence
            if len(current) >= 3:
                blocks.append(current)
            current = []
            continue
        if in_fence:
            continue
        if stripped.startswith("|"):
            current.append((number, line))
        else:
            if len(current) >= 3:
                blocks.append(current)
            current = []
    if len(current) >= 3:
        blocks.append(current)
    return blocks


def _table_shape_offenders(text: str) -> list[tuple[int, int, int, str]]:
    """返回 (行号, 本行竖线数, 表头竖线数, 行首单元格摘要)。"""

    offenders: list[tuple[int, int, int, str]] = []
    for block in _markdown_table_blocks(text):
        header = _table_cells(block[0][1])
        for number, line in block:
            cells = _table_cells(line)
            if cells != header:
                summary = line.strip().strip("|").split("|")[0].strip()
                offenders.append((number, cells, header, summary[:40]))
    return offenders


def _offense_keys(path: Path) -> set[str]:
    """把每处形状不符折算成 ``文件#行首格`` ——豁免按这个身份点名。"""

    relative = path.relative_to(ROOT).as_posix()
    return {
        f"{relative}#{summary.split(' ')[0]}"
        for _, _, _, summary in _table_shape_offenders(path.read_text(encoding="utf-8"))
    }


def test_markdown_table_rows_match_their_header() -> None:
    """活文档里每张表格的行形状：与表头不同竖线数的行必须被点名豁免。"""

    offenders: list[str] = []
    hit: set[str] = set()
    for path in active_docs():
        relative = path.relative_to(ROOT).as_posix()
        for number, cells, header, summary in _table_shape_offenders(
            path.read_text(encoding="utf-8")
        ):
            key = f"{relative}#{summary.split(' ')[0]}"
            if key in _TABLE_EXEMPT:
                hit.add(key)
                continue
            offenders.append(f"{relative}:{number} 本行 {cells} 竖线 / 表头 {header}：{summary}")
    assert offenders == [], "表格行与表头列数不符：\n" + "\n".join(offenders)
    assert hit == set(_TABLE_EXEMPT), (
        f"这些豁免已不再需要，要从清单里删掉：{sorted(set(_TABLE_EXEMPT) - hit)}"
    )


def test_the_table_shape_ruler_itself_sees_a_merged_row() -> None:
    """正控：上一行的尾巴粘进新行必须量得到，行内代码里的竖线不许被误当列。"""

    merged = "| # | 问题 | 处置 |\n|---|---|---|\n| A | 甲 | 已改 |\n"
    assert _table_shape_offenders(merged) == []
    planted = merged + "| B | 乙 | 已改 |：甲 的旧尾巴又粘了一遍 | 已改 |\n"
    offenders = _table_shape_offenders(planted)
    assert len(offenders) == 1 and offenders[0][0] == 4 and offenders[0][3] == "B", (
        f"粘行的形状没被量到（{offenders}），尺子失效"
    )
    assert _table_shape_offenders("| a | `x | y` |\n|---|---|\n| b | `p | q` |\n") == []


# --------------------------------------------------------------------------- #
# 指标门面表格 ↔ 运行期注册表（第 26 轮 F-112 / F-115 / F-117 / F-118）
# --------------------------------------------------------------------------- #


def _interfaces_metric_series_rows() -> list[tuple[str, str, tuple[str, ...], str]]:
    """``docs/api/interfaces.md`` §8 指标表：``[(序列名, 类型, 标签, 写入方), ...]``。"""
    doc = (ROOT / "docs" / "api" / "interfaces.md").read_text(encoding="utf-8")
    parts = doc.split("### 指标门面（`atst.observability.metrics`）", 1)
    assert len(parts) == 2, "interfaces.md 不再有指标门面那一节，门禁失效"
    #: 窗口收在本节之内，别把下一节导出器表格的第一列吃进来。
    body = re.split(r"^### |^## ", parts[1], maxsplit=1, flags=re.M)[0]
    rows = re.findall(r"^\| `(atst_\w+)` \| (\w+)(?:（([^（）]*)）)? \|([^|\n]*)\|", body, re.M)
    assert rows, "指标表格解析不出任何一行，门禁失效"
    return [
        (
            name,
            kind,
            tuple(x.strip() for x in labels.split(" / ")) if labels else (),
            writer.strip().strip("|").strip(),
        )
        for name, kind, labels, writer in rows
    ]


def _metric_series_diff(
    doc_rows: list[tuple[str, str, tuple[str, ...], str]],
    live: dict[str, tuple[str, tuple[str, ...]]],
) -> tuple[list[str], list[str], list[str]]:
    """文档表格与注册表之间的三种分歧：漏写、多写、类型或标签抄错。"""
    doc = {name: (kind, labels) for name, kind, labels, _writer in doc_rows}
    missing = sorted(set(live) - set(doc))
    extra = sorted(set(doc) - set(live))
    mismatched = sorted(n for n in set(doc) & set(live) if doc[n] != live[n])
    return missing, extra, mismatched


def test_interfaces_metric_series_table_matches_the_registry() -> None:
    """§8 那张表与运行期注册表双向相等，且每一格都说得出谁在写（第 26 轮）。

    分母是 ``metrics.registry.snapshot_metrics()`` 的现值，不是文档里的抄件：往门面里注册一格
    新指标而文档没跟上，或从表格里抹掉一行，都当场红。第三列「写入方」非空是这条判据的
    真正目的——F-112 撤下的 ``atst_active_connections`` 就是这么一根仪表：注册了、被
    ``/metrics`` 渲染了、还被 statsd 的线格式示例引用过，却全仓没人写它。表格不许再给
    这种"没人写的对外声称"留位置。
    """
    from atst.observability import Counter, Gauge, Histogram, Summary  # noqa: F401
    from atst.observability.metrics import metrics

    kinds = {"Counter": "counter", "Gauge": "gauge", "Histogram": "histogram", "Summary": "summary"}
    live = {
        m.name: (kinds[type(m).__name__], tuple(m.labelnames))
        for m in metrics.registry.snapshot_metrics()
    }
    rows = _interfaces_metric_series_rows()
    assert live, "注册表是空的——本判据失去比对对象"
    missing, extra, mismatched = _metric_series_diff(rows, live)
    assert not (missing or extra or mismatched), (
        f"表格漏写 {missing}；凭空多写 {extra}；类型或标签与现值不符 {mismatched}"
    )
    for name, _kind, _labels, writer in rows:
        assert writer, f"{name} 这一格没写「谁在写」——没人写的对外声称按 F-112 撤下，不许留表位"
    assert "atst_active_connections" not in live, "F-112 撤下的恒初值仪表又回来了"

    #: 正控：三型分歧各自必须抓得住，否则这条判据对新增/漏写/抄错是瞎的。
    assert _metric_series_diff(rows[:-1], live)[0] == [rows[-1][0]]
    planted = (*rows[:-1], (rows[-1][0], rows[-1][1], ("planted",), "没人写"))
    assert _metric_series_diff(planted, live)[2] == [planted[-1][0]]
    phantom = [*rows, ("atst_planted_series", "counter", (), "凭空造的一格")]
    assert _metric_series_diff(phantom, live)[1] == ["atst_planted_series"]


# --------------------------------------------------------------------------- #
# 三份服务面的 Client 所有权表 ↔ 实现（第 26 轮 F-100 / G40 同族）
# --------------------------------------------------------------------------- #


#: 那张表所在的小节锚，与表格行的形状。行首要读回 `entry(param=None)`：
#: 表里写的签名就是用户会照抄的入口签名，签名变了而表格没变要当场红。
_OWNERSHIP_ANCHOR = "**三份服务面的 `Client` 所有权是同一口径**"
_OWNERSHIP_ROW = re.compile(r"^\|\s*(HTTP|WS|MCP)\s+`(\w+)\((\w+)=None\)`")

#: 每一面量哪个模块：文档只说"造出 `Client` 的一方"和"收尾的地方"，判据按面去现读实现，
#: 不认文档里抄的文件名（那一格写错也没人红）。
_OWNERSHIP_FACE: dict[str, tuple[str, str]] = {
    "HTTP": ("atst.integration.runtime_http", "atst/integration/runtime_http.py"),
    "WS": ("atst.integration.runtime_ws_server", "atst/integration/runtime_ws_server.py"),
    "MCP": ("atst.integration.mcp._server", "atst/integration/mcp/_server.py"),
}

#: 反引号里写出的名字：允许点号尾段（`server.atst_handler`）与可调用括号（`stop()`）。
#: 带斜杠的是路径不是符号，含 `=` 的是签名，两者都不进判据。
_OWNERSHIP_TOKEN = re.compile(r"`([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z0-9_]+)*(?:\(\))?)`")


def _ownership_section() -> str:
    """所有权那一节从锚到下一个二级/三级标题为止。"""

    parts = _doc_text("docs/api/interfaces.md").split(_OWNERSHIP_ANCHOR, 1)
    assert len(parts) == 2, f"interfaces.md 不再有「{_OWNERSHIP_ANCHOR}」那一节，门禁失效"
    return re.split(r"^### |^## ", parts[1], maxsplit=1, flags=re.M)[0]


def _ownership_rows() -> list[tuple[str, str, str, str]]:
    """``[(面, 入口, 入参, 整行原文), ...]``。"""

    rows = [
        (m.group(1), m.group(2), m.group(3), line)
        for line in _ownership_section().splitlines()
        if (m := _OWNERSHIP_ROW.match(line))
    ]
    assert rows, "所有权表格解析不出任何一行，门禁失效"
    return rows


def _ownership_tokens(line: str) -> set[str]:
    """这一行反引号点到的符号名（取点号尾段）。

    `finally` / `yield` 这类反引号包住的是散文里的关键字，不是任何人会去 import 的名字，
    按 Python 关键字撤下；其余一律要求实现里读得到（:func:`_ownership_symbols`）。
    """

    return {
        word.rsplit(".", 1)[-1]
        for raw in _OWNERSHIP_TOKEN.findall(line)
        for word in [raw[:-2] if raw.endswith("()") else raw]
        if not keyword.iskeyword(word)
    }


@functools.cache
def _ownership_symbols(rel: str) -> frozenset[str]:
    """这个模块的 AST 里读得到的所有名字段。

    收字符串常量是有意的：MCP 面把 ``shutdown`` 写成分派表里的一个键，代码里根本没有同名
    标识符，而文档那一格必须说得出它。代价是这一族对纯字符串的名字不设防，所以「谁造谁关」
    这件事由 :func:`_ownership_flag` 从代码里**反推**，不由文档自选。
    """

    tree = ast.parse((ROOT / rel).read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            found.add(node.name)
        elif isinstance(node, ast.Name):
            found.add(node.id)
        elif isinstance(node, ast.Attribute):
            found.add(node.attr)
        elif isinstance(node, ast.arg):
            found.add(node.arg)
        elif isinstance(node, ast.keyword):
            if node.arg is not None:
                found.add(node.arg)
        elif isinstance(node, ast.alias):
            found.add(node.name.rsplit(".", 1)[-1])
        elif (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", node.value)
        ):
            found.add(node.value)
    return frozenset(found)


def _ownership_flag(rel: str, param: str) -> set[str]:
    """实现里 `flag = <param> is None` 的那个 flag 名——「谁造的」在代码里的形状。"""

    tree = ast.parse((ROOT / rel).read_text(encoding="utf-8"))
    flags: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.AnnAssign):
            targets: list[ast.expr] = [node.target]
        elif isinstance(node, ast.Assign):
            targets = list(node.targets)
        else:
            continue
        value = node.value
        if value is None or not isinstance(value, ast.Compare) or len(value.ops) != 1:
            continue
        if not isinstance(value.ops[0], ast.Is):
            continue
        comparator = value.comparators[0]
        if not (isinstance(comparator, ast.Constant) and comparator.value is None):
            continue
        left = value.left
        if not (isinstance(left, ast.Name) and left.id == param):
            continue
        for target in targets:
            if isinstance(target, ast.Name):
                flags.add(target.id)
            elif isinstance(target, ast.Attribute):
                flags.add(target.attr)
    return flags


def _ownership_offenders(face: str, param: str, line: str) -> tuple[list[str], list[str]]:
    """这一行的两种违约：点到实现里没有的名字，以及没点名实现里那个所有权 flag。"""

    _module, rel = _OWNERSHIP_FACE[face]
    tokens = _ownership_tokens(line)
    flag = _ownership_flag(rel, param)
    return (
        sorted(tokens - set(_ownership_symbols(rel))),
        [] if tokens & flag else [f"没点名实现里的 {param} is None 标志 {sorted(flag)}"],
    )


def test_service_plane_client_ownership_table_matches_the_code() -> None:
    """那张三行表说的每一格，都在实现里读得到；「谁造谁关」的 flag 必须被点名（第 26 轮）。

    F-100 之前 HTTP 面只是 `return app`：文档写"传入的归调用方"没有任何人会去核，因为它
    在代码里根本没有对应的形状。修完之后三面各有一个 `x = client is None` 式的 flag，
    于是"这一面自己造不造"第一次成了可以现读的东西——本判据据此双向收口：文档点到的名字
    要在实现里读得到（写错的钩子、凭空造的属性当场红），实现里那个 flag 要被文档点名
    （悄悄把收尾改成无条件 `close()`、flag 撤掉了，表格那一格也就说不下去了）。
    """

    import inspect

    rows = _ownership_rows()
    assert sorted(r[0] for r in rows) == ["HTTP", "MCP", "WS"], (
        f"所有权表应当恰好三行，现在是 {sorted(r[0] for r in rows)}"
    )
    assert "归调用方" in _ownership_section(), "那一节没再写明「传进来的那份归调用方」"

    for face, entry, param, line in rows:
        module_name, _rel = _OWNERSHIP_FACE[face]
        obj = importlib.import_module(module_name)
        for part in entry.split("."):
            obj = getattr(obj, part)
        signature = inspect.signature(obj if not isinstance(obj, type) else obj.__init__)
        declared = signature.parameters[param]
        assert declared.default is None, (
            f"{face} 行写着 `{entry}({param}=None)`，签名现值是 {declared!r}"
        )
        unresolved, flag_missing = _ownership_offenders(face, param, line)
        assert not unresolved, f"{face} 行点到 {module_name} 里读不到的名字：{unresolved}"
        assert not flag_missing, f"{face} 行没说出谁造的那份：{flag_missing}"

    #: 正控：凭空造的钩子与撤掉 flag 点名，两型都必须当场抓得住。
    bogus = "| WS `serve_runtime_ws(handler=None)` | 造一份 | 收尾时读 `server.atst_bogus_hook` |"
    unresolved, _flag_missing = _ownership_offenders("WS", "handler", bogus)
    assert unresolved == ["atst_bogus_hook"], f"凭空钩子没被量到（{unresolved}），尺子失效"
    silent = "| HTTP `create_runtime_app(client=None)` | 造一份 | 收尾时调 `api.close()` |"
    _unresolved, flag_missing = _ownership_offenders("HTTP", "client", silent)
    assert flag_missing, "漏写所有权 flag 没被量到，尺子失效"
