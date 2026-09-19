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
