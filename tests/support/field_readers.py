"""跨测试目录共用的"这个字段有没有人按它行动"尺子。

单实现是刻意的：F-50/F-52/F-54 这条家族先后量过 `QueryPlan`、`ProviderSpec`、`ChannelSpec`，
每量一次就复制一份 AST 遍历，等于给自己再造一个"抄一次就过期"的东西。消费者只提供入参：
被量的类、**手工核对过**的 owner 变量名集合，以及（可选）实例挂在自己身上的属性名 `holders`。

owner 名单的校准纪律（本仓登记过的判据边界）：

* 分母永远取自 `dataclasses.fields(cls)`，不抄清单——新增字段没有读取点即当场变红。
* 名单里**不放 `self`**：`self.capability` 可能是任何类的属性（`tstdx/catalog/capability.py`
  就有），放进去等于给假绿开门。数据面类（`Provenance`/`ResultMeta`）因此只认调用方真正
  绑定到的变量名。
* 三把防盲保险由调用方负责：字段非空、扫过的模块数足够大、读取集合非空——扫描自身失明时
  必须自曝，而不是"零孤儿"地绿过去。
"""

from __future__ import annotations

import ast
import dataclasses
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]


def unread_fields(
    cls: type[Any], owners: set[str], *, holders: set[str] | None = None
) -> tuple[set[str], int, set[str]]:
    """扫 ``tstdx/`` 找 ``cls`` 各字段的读取点：返回 (全部字段, 扫过的模块数, 命中的字段)。

    本函数是 :func:`unread_field_sites` 的投影；需要知道"这个读取点是谁"时直接量那个。
    """

    fields, scanned, sites = unread_field_sites(cls, owners, holders=holders)
    return fields, scanned, set().union(*sites.values()) if sites else set()


def unread_field_sites(
    cls: type[Any], owners: set[str], *, holders: set[str] | None = None
) -> tuple[set[str], int, dict[str, set[str]]]:
    """同 :func:`unread_fields`，但读取点按**文件**留着：返回 (字段, 模块数, 相对路径 → 命中的字段)。

    ``holders`` 补的是"字段挂在实例属性上"这一类：``self.profile.amount_unit`` 的链条塌到
    ``Name('self')``，而 ``self`` 按名单纪律不能进 owners——于是持有 DataProfile 的读取器
    （``reader/formats.py``、``sink/local_day.py``）在尺子下全隐身，把有人读的字段量成孤儿。
    传 ``{"profile"}`` 表示"链条中段叫 ``profile`` 的命中也算读取点"。名单仍要手工核对：
    只放**本类实例真正挂在上面的**属性名。

    按文件留痕是为了补上本族判据登记过的假绿（变异 M4）：owner 名单里一个 ``p`` 会同时命中
    好几个类的 ``p.name``。消费者拿到站点字典后可以用**独立事实**筛一遍（例如"这个模块是否
    真的提到被量类的名字"），把分数交给代码而不是交给人情。
    """

    fields = {item.name for item in dataclasses.fields(cls)}
    held = holders or set()
    sites: dict[str, set[str]] = {}
    scanned = 0
    for path in sorted((REPO_ROOT / "tstdx").rglob("*.py")):
        scanned += 1
        relative = str(path.relative_to(REPO_ROOT)).replace("\\", "/")
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Attribute) or node.attr not in fields:
                continue
            base = node.value
            hit = False
            while isinstance(base, ast.Attribute):
                if base.attr in held:
                    hit = True
                    break
                base = base.value
            if not hit and isinstance(base, ast.Name) and base.id in owners:
                hit = True
            if hit:
                sites.setdefault(relative, set()).add(node.attr)
    return fields, scanned, sites


def members_referenced(owner: str, *, skip: str = "") -> tuple[int, set[str]]:
    """扫 ``tstdx/`` 找 ``<owner>.<MEMBER>`` 的引用点：返回 (扫过的模块数, 命中的成员名)。

    与 :func:`unread_fields` 同族：分母取枚举自身（`{item.name for item in SomeEnum}`），
    名单由本函数产出，于是"声明了一个没人生产的成员"当场红，而不是靠注释保证。成员名按
    全大写识别——本仓两个数据面枚举（``WarningCode``/``ProvenanceKind``）都是这个形状。
    ``skip`` 传文件相对路径（如 ``tstdx/diagnostics.py``）以排除声明处自身。
    """

    scanned, sites = member_reference_sites(owner, skip=skip)
    return scanned, set(sites)


@dataclass(frozen=True)
class ConstantVocabulary:
    """「常量类」词表的实测结果。"""

    target: str
    scanned: int
    members: dict[str, list[str]]
    values: dict[str, dict[str, str]]
    tables: dict[str, list[str]]
    other: dict[str, list[str]]
    table_members: dict[str, dict[str, list[str]]]
    reads: dict[str, set[str]]
    foreign: dict[str, set[str]]
    unresolved: dict[str, set[str]]

    def sites(self, cls: str, attr: str) -> set[str]:
        """解析到本定义的 ``cls.attr`` 读取点所在模块。"""

        return self.reads.get(f"{cls}.{attr}", set())

    def is_member(self, cls: str, attr: str) -> bool:
        return attr in self.members.get(cls, [])

    def is_table(self, cls: str, attr: str) -> bool:
        return attr in self.tables.get(cls, [])

    def members_in_tables(self, cls: str) -> dict[str, list[str]]:
        """本类里"以裸成员名当键/元素"的表：表名 → 用到的成员名。

        类体内 ``CODES = {SH: 1, ...}`` 的键是 ``Name`` 而非 ``Market.SH``，按属性走查看不见；
        而表一旦被有人查，这些成员就是**按值被行动**的，不能量成孤儿。
        """

        return self.table_members.get(cls, {})


def constant_class_vocabulary(target: str, classes: tuple[str, ...]) -> ConstantVocabulary:
    """量"常量类"（类体里只有 ``NAME = "value"`` 与 ``TABLE = {...}`` 的那类词表）。

    与 :func:`member_reference_sites` 的**按名**走查不同，本函数先解析导入，再决定一次
    ``Class.MEMBER`` 命中该记在**哪个定义**头上。理由是本仓真实踩过的那格假绿：
    ``tstdx/reader/profile.py`` 与 ``tstdx/domain/symbol.py`` **都叫 ``Market``**，按名统计
    时 ``symbol`` 的 8 处 ``Market.ALL`` 会被记到档案层那同名成员上，把一张零读取点的表量成
    "有人查"。字段含义：

    * ``scanned``：解析过的 ``tstdx/`` 模块数（防盲分母）
    * ``members``：类名 → 成员名（按源码顺序）
    * ``values``：类名 → 成员名 → 取值（只收字符串常量，判据用它核对"按值被行动"）
    * ``tables``：类名 → 类级表名
    * ``table_members``：类名 → 表名 → 该表以裸成员名写下的键/元素
    * ``other``：类名 → 类体里既非常量也非表的赋值（判据要求它为空，否则新增形态静默隐身）
    * ``reads``：``"类.属性"`` → 命中且**解析到本定义**的模块
    * ``foreign``：``"类.属性"`` → 命中但解析到**同名别处定义**（正向对照的证据）
    * ``unresolved``：``"类.属性"`` → 解析不出定义的模块（被局部变量遮蔽等）

    只认 ``from … import X [as Y]`` 这一种绑定形态；``import a.b`` 后写 ``a.b.C.D`` 的形态落进
    ``unresolved``，而不是静默记给别处或本处。
    """

    repo_root = REPO_ROOT
    files = sorted((repo_root / "tstdx").rglob("*.py"))
    trees = {path: ast.parse(path.read_text(encoding="utf-8")) for path in files}
    rel_of = {path: path.relative_to(repo_root).as_posix() for path in files}

    defined: dict[str, set[str]] = {}  # 相对路径 → 模块级类名
    imports: dict[tuple[str, str], str] = {}  # (引用文件, 绑定名) → 被导入模块相对路径

    def package_root() -> Path:
        return repo_root / "tstdx"

    def module_path(node: ast.ImportFrom, current: Path) -> str | None:
        """把一条 ``from`` 解析成被导入模块的仓内相对路径（目录取 ``__init__.py``）。"""

        if node.level:
            base = current.parent
            for _ in range(node.level - 1):
                base = base.parent
            found: Path | None = base if not node.module else base.joinpath(*node.module.split("."))
        elif node.module == "tstdx" or node.module.startswith("tstdx."):
            tail = node.module[len("tstdx") :].lstrip(".")
            found = package_root() if not tail else package_root().joinpath(*tail.split("."))
        else:
            return None
        if found is None:
            return None
        if found.is_dir():
            found = found / "__init__.py"
        elif found.suffix != ".py":
            found = found.with_suffix(".py")
        if not found.exists():
            return None
        try:
            return found.relative_to(repo_root).as_posix()
        except ValueError:
            return None

    for path, tree in trees.items():
        rel = rel_of[path]
        defined[rel] = {node.name for node in tree.body if isinstance(node, ast.ClassDef)}
        # 函数体内的 import 也算：tstdx/web/fundflow.py 就在函数里 `from ..domain.symbol import Market`，
        # 只摸模块级会把那处 ``Market.BJ`` 记成"解析不出"而不是"解析到别处"。
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom):
                continue
            source = module_path(node, path)
            if source is None:
                continue
            for alias in node.names:
                if alias.name != "*":
                    imports[(rel, alias.asname or alias.name)] = source

    def defining_module(rel: str, name: str) -> str | None:
        """``name`` 在 ``rel`` 里最终定义于哪个模块（跟随再导出链条，遇环返回 None）。"""

        seen: set[str] = set()
        while rel not in seen:
            seen.add(rel)
            if name in defined.get(rel, set()):
                return rel
            nxt = imports.get((rel, name))
            if nxt is None:
                return None
            rel = nxt
        return None

    members: dict[str, list[str]] = {name: [] for name in classes}
    values: dict[str, dict[str, str]] = {name: {} for name in classes}
    tables: dict[str, list[str]] = {name: [] for name in classes}
    other: dict[str, list[str]] = {name: [] for name in classes}
    table_members: dict[str, dict[str, list[str]]] = {name: {} for name in classes}
    for node in ast.parse((repo_root / target).read_text(encoding="utf-8")).body:
        if not isinstance(node, ast.ClassDef) or node.name not in members:
            continue
        for stmt in node.body:
            attr: str | None
            if isinstance(stmt, ast.Assign):
                attr = (
                    stmt.targets[0].id
                    if len(stmt.targets) == 1 and isinstance(stmt.targets[0], ast.Name)
                    else None
                )
                value: ast.expr | None = stmt.value
            elif isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
                attr, value = stmt.target.id, stmt.value
            else:
                attr, value = None, None
            bucket = node.name
            if attr is None or value is None:
                continue
            if isinstance(value, ast.Constant):
                members[bucket].append(attr)
                if isinstance(value.value, str):
                    values[bucket][attr] = value.value
            elif isinstance(value, (ast.Dict, ast.Tuple, ast.List, ast.Set)):
                tables[bucket].append(attr)
                operands = value.keys if isinstance(value, ast.Dict) else value.elts
                table_members[bucket][attr] = [
                    item.id for item in operands if isinstance(item, ast.Name)
                ]
            else:
                other[bucket].append(attr)

    reads: dict[str, set[str]] = {}
    foreign: dict[str, set[str]] = {}
    unresolved: dict[str, set[str]] = {}
    for path, tree in trees.items():
        rel = rel_of[path]
        for node in ast.walk(tree):
            if (
                not isinstance(node, ast.Attribute)
                or not isinstance(node.value, ast.Name)
                or node.value.id not in classes
            ):
                continue
            cls = node.value.id
            key = f"{cls}.{node.attr}"
            origin = defining_module(rel, cls)
            if origin == target:
                reads.setdefault(key, set()).add(rel)
            elif origin is None:
                unresolved.setdefault(key, set()).add(rel)
            else:
                foreign.setdefault(key, set()).add(f"{rel} → {origin}")
    return ConstantVocabulary(
        target=target,
        scanned=len(files),
        members=members,
        values=values,
        tables=tables,
        other=other,
        table_members=table_members,
        reads=reads,
        foreign=foreign,
        unresolved=unresolved,
    )


def member_reference_sites(owner: str, *, skip: str = "") -> tuple[int, dict[str, set[str]]]:
    """同一把尺子的**按文件**版本：返回 (扫过的模块数, 成员名 → 引用它的仓内相对路径)。

    ``members_referenced`` 只回答"有没有人生产这个成员"，回答不了"文档说它由甲模块发射，
    究竟是不是甲"。判据要钉住后者就得留下文件这一维，而重复实现一份 AST 走查正是本模块
    存在的理由所要避免的，于是前者由本函数投影得到。
    """

    refs: dict[str, set[str]] = {}
    scanned = 0
    for path in sorted((REPO_ROOT / "tstdx").rglob("*.py")):
        relative = str(path.relative_to(REPO_ROOT)).replace("\\", "/")
        if relative == skip:
            continue
        scanned += 1
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Attribute)
                and node.attr.isupper()
                and isinstance(node.value, ast.Name)
                and node.value.id == owner
            ):
                refs.setdefault(node.attr, set()).add(relative)
    return scanned, refs
