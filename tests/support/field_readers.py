"""跨测试目录共用的"这个字段有没有人按它行动"尺子。

单实现是刻意的：F-50/F-52/F-54 这条家族先后量过 `QueryPlan`、`ProviderSpec`、`ChannelSpec`，
每量一次就复制一份 AST 遍历，等于给自己再造一个"抄一次就过期"的东西。消费者只提供两个入参：
被量的类，以及**手工核对过**的 owner 变量名集合。

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


def unread_fields(cls: type[Any], owners: set[str]) -> tuple[set[str], int, set[str]]:
    """扫 ``tstdx/`` 找 ``cls`` 各字段的读取点：返回 (全部字段, 扫过的模块数, 命中的字段)。"""

    fields = {item.name for item in dataclasses.fields(cls)}
    reads: set[str] = set()
    scanned = 0
    for path in sorted((REPO_ROOT / "tstdx").rglob("*.py")):
        scanned += 1
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Attribute) or node.attr not in fields:
                continue
            base = node.value
            while isinstance(base, ast.Attribute):
                base = base.value
            if isinstance(base, ast.Name) and base.id in owners:
                reads.add(node.attr)
    return fields, scanned, reads


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
