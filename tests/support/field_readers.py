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
