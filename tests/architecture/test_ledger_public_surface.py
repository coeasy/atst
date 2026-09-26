"""命令账本公开函数面的门禁（F-65 裁决 (b) 的执行侧，第 46 步）。

F-64 量的是账本的**字段**侧（10 个字段里 4 个无人读），F-65 是同一把尺子量**函数**侧：
`stats()` 零生产调用点、`get_command_by_name()` 连测试都没有，`unknown_command_ids()`
挂在两处 `__all__` 上却全包无人调用。裁决 (b) 要的是"保留 `by_family`/
`unknown_command_ids` 为公开查询面，补文档与用例，删 `stats()`/`get_command_by_name()`"
——"补"与"删"都得有人看着，否则下一次漂移是同一件事重演：

* 名单侧：函数面形状锁死，改名或再登记一个没人读的聚合器当场红；
* 两面一致性：包面（`tstdx.protocol.__all__`）不得宣称模块面（`commands.__all__`）
  没声明的名字——被删的 `stats` 正是这样：它从未进过 `commands.__all__`，却挂在包面上；
* 文档侧：`docs/api/interfaces.md` 的表与声明名单**双向**相等，文档里的规模数字全部由
  运行期重算后再回查原文，抄本一旦过期就红（不是靠人记得去改）；
* 用例侧：每个声明的函数都必须由"真的从账本模块 import 了它、并且调用它"的测试文件够到，
  只 import 不调用不算读者。
* 成员侧：`Command` 除字段之外只剩 `hex` 一个成员——族→端口那种"账本对外宣称传输细节、
  而真正的行动处在别处"的成员不得再登记回来（V19 §4 P1-A2）。
"""

from __future__ import annotations

import ast
import dataclasses
import re
from pathlib import Path

import tstdx.protocol
from tstdx.protocol import commands as ledger
from tstdx.protocol.commands import (
    COMMANDS,
    STATUS_OFFLINE,
    STATUS_ONLINE,
    Family,
    by_status,
)

ROOT = Path(__file__).resolve().parents[2]
INTERFACES = ROOT / "docs" / "api" / "interfaces.md"
API_README = ROOT / "docs" / "api" / "README.md"
LEDGER_SECTION = "### 命令账本查询面"

#: 裁决 (b) 之后账本对外的全部函数。新增一行必须同时给出文档与用例，删除一行是收窄契约。
DECLARED_FUNCTIONS = {
    "cmd",
    "get_command",
    "by_family",
    "by_status",
    "unknown_command_ids",
}

#: 由用户从包上拿的公开查询面（F-65 裁决 (b) 点名的两个）。
PUBLIC_QUERY_SURFACE = {"by_family", "unknown_command_ids"}

#: `Command` 实例上的公开成员（字段之外）——只有 `hex`。第 27 轮（V19 §4 P1-A2）删掉了
#: `port`：族→端口是传输细节，连接池真的拿去建连的是 `HostEntry.port`，`Command.port`
#: 在生产树里零读取点，只剩文档门禁在替它"证明存在"。
DECLARED_COMMAND_MEMBERS = {"hex"}

#: 族常量全集，文档里的分布串按这个顺序写。
FAMILIES = (
    Family.STANDARD,
    Family.EXTENDED,
    Family.MAC,
    Family.GOODS,
    Family.F10,
)


def _functions_in_face() -> set[str]:
    """`__all__` 上的可调用、且不是类的名字——也就是这个模块的函数面。

    类（``Family``/``Command``）在这里必须排除：``Family`` 是个纯常量容器，按
    ``callable()`` 判会把每个常量袋都算成函数，名单锁就成了摆设。
    """
    return {
        name
        for name in ledger.__all__
        if callable(getattr(ledger, name, None))
        and not isinstance(getattr(ledger, name), type)
        and not name.startswith("_")
    }


def _module_level_functions() -> set[str]:
    tree = ast.parse(
        (ROOT / "tstdx" / "protocol" / "commands.py").read_text(encoding="utf-8"),
        filename="commands.py",
    )
    return {
        node.name
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and not node.name.startswith("_")
    }


def test_ledger_function_face_is_exactly_the_read_helpers() -> None:
    """函数面形状锁：模块里每个公开函数都要在 `__all__` 上，`__all__` 也不许多宣称。

    锁的是"两个方向都不得有富余"。F-65 那批孤儿就是靠"登记了但没人调"活了很久——
    名字一旦上名单，删它就成了对外收窄，所以入口比出口该更严。
    """
    declared = _functions_in_face()
    defined = _module_level_functions()
    assert declared == DECLARED_FUNCTIONS, (
        f"账本函数面与本步锁定的名单不符：多 {sorted(declared - DECLARED_FUNCTIONS)}、"
        f"缺 {sorted(DECLARED_FUNCTIONS - declared)}"
    )
    assert defined == declared, (
        f"模块里的公开函数与 `__all__` 不符：未声明 {sorted(defined - declared)}、"
        f"宣称了却不存在 {sorted(declared - defined)}"
    )


def test_package_face_declares_nothing_the_module_denies() -> None:
    """包面（`tstdx.protocol`）宣称的账本名字，必须也是模块面（`commands.__all__`）的。

    被删的 `stats` 恰好相反：`commands.__all__` 从未列它，包面却 import 它并挂进
    `__all__`——两个名单各说各话，读者按包面写的 `from tstdx.protocol import stats`
    确实能用，于是这条假承诺没有暴露面。
    """
    package_ledger_names = {
        name
        for name in tstdx.protocol.__all__
        if getattr(tstdx.protocol, name, None) is getattr(ledger, name, object())
    }
    assert package_ledger_names, "包面一个账本名字都没对上，说明本判据自身失效"
    undeclared = sorted(package_ledger_names - set(ledger.__all__))
    assert undeclared == [], f"包面宣称了模块面未声明的账本名字：{undeclared}"
    missing = sorted(PUBLIC_QUERY_SURFACE - package_ledger_names)
    assert missing == [], f"裁决 (b) 保留的公开查询面从包面上消失了：{missing}"


def _ledger_doc_block() -> str:
    text = INTERFACES.read_text(encoding="utf-8")
    start = text.index(LEDGER_SECTION)
    rest = text[start + len(LEDGER_SECTION) :]
    end = len(rest)
    for marker in ("\n### ", "\n## "):
        found = rest.find(marker)
        if found != -1:
            end = min(end, found)
    return rest[:end]


def _documented_surface_names(block: str) -> set[str]:
    return {
        match.group(1)
        for line in block.splitlines()
        if (match := re.match(r"^\|\s*`([A-Za-z_][A-Za-z0-9_]*)`\s*\|", line))
    }


def test_documented_surface_matches_the_declared_functions() -> None:
    """文档表的名单与声明名单双向相等：文档不得 advertised 一个没声明的函数。

    反向的一半更要紧——`docs/api/README.md` 指着这张表说"逐个写了口径"，那么漏掉一个
    声明名字就是假承诺：读者按图找口径会找不到。
    """
    block = _ledger_doc_block()
    documented = _documented_surface_names(block)
    assert len(documented) >= 5, f"表里只数到 {len(documented)} 行，判据自身失效"
    assert documented == DECLARED_FUNCTIONS, (
        f"文档表与函数面不符：文档多 {sorted(documented - DECLARED_FUNCTIONS)}、"
        f"文档缺 {sorted(DECLARED_FUNCTIONS - documented)}"
    )


def _regenerated_claims() -> list[tuple[Path, str, str]]:
    """文档里每个规模字符串都由运行期现算，再回查原文有没有这句话。"""
    family_counts: dict[str, int] = {}
    for (family, _), _command in COMMANDS.items():
        family_counts[family] = family_counts.get(family, 0) + 1
    unverified_total = sum(1 for c in COMMANDS.values() if not c.verified)
    distribution = "`" + " / ".join(f"{f} {family_counts[f]}" for f in FAMILIES) + "`"
    return [
        (INTERFACES, "规模", f"{len(COMMANDS)} 行、{len(FAMILIES)} 协议族"),
        (INTERFACES, "族分布", distribution),
        (
            INTERFACES,
            "状态分布",
            f"`online` {len(by_status(STATUS_ONLINE))} / "
            f"`offline` {len(by_status(STATUS_OFFLINE))} / "
            f"`degraded` {len(by_status('degraded'))}",
        ),
        (
            INTERFACES,
            "未经 golden 校正数",
            f"默认族 {len(ledger.unknown_command_ids())} 条、全账本 {unverified_total} 条",
        ),
        (API_README, "函数面规模", f"它的 {len(DECLARED_FUNCTIONS)} 个查询函数"),
    ]


def test_documented_numbers_are_regenerated_from_the_ledger() -> None:
    """抄一次就失真的那类数字：文档留着它们，就必须能被运行期逐字回查。

    第 19 步（F-42）与第 45 步都在同一件事上栽过——一份台账里连错四个数，没人能一眼看出。
    这里不做"范围合理即可"的松弛判断，只认原文里有没有这句由代码算出来的话。
    """
    claims = _regenerated_claims()
    assert claims, "重算清单为空，说明本判据自身失效"
    block = _ledger_doc_block()
    offenders = [
        f"{path.name} / {label}：{claim}"
        for path, label, claim in claims
        if claim not in (block if path is INTERFACES else path.read_text(encoding="utf-8"))
    ]
    assert offenders == [], f"文档里的账本规模数字与运行期不符：{offenders}"


def _ledger_call_sites() -> dict[str, set[str]]:
    """tests/ 里"从账本模块 import 了它、并且调用它"的文件。"""
    sites = {name: set[str]() for name in DECLARED_FUNCTIONS}
    for path in sorted((ROOT / "tests").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        imported: set[str] = set()
        for node in ast.walk(tree):
            if not (isinstance(node, ast.ImportFrom) and node.module):
                continue
            if not re.fullmatch(r"tstdx\.protocol(\.commands)?", node.module):
                continue
            imported.update(alias.asname or alias.name for alias in node.names)
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id in (DECLARED_FUNCTIONS & imported)
            ):
                # 只认函数面的名字：`Command(...)` 同样从账本模块 import，构造它不算读函数
                sites[node.func.id].add(path.name)
    return sites


def test_every_declared_function_is_reached_by_a_test() -> None:
    """公开面里不允许躺着"名单上有、用例里没有"的名字。

    裁决 (b) 的"补用例"就是这条：`unknown_command_ids` 在补本步用例前是零调用点零测试，
    却挂在两处 `__all__` 上——那时没有任何判据会因为"它其实没人用"而红。
    """
    sites = _ledger_call_sites()
    assert any(sites.values()), "一条调用点都没扫到，说明扫描自身失效"
    untested = sorted(name for name, files in sites.items() if not files)
    assert untested == [], f"声明为公开面却无任何用例调用：{untested}"


def test_deleted_aggregators_stay_deleted() -> None:
    """裁决 (b) 删掉的两个函数不得回来，也不得以别名形式回来。

    模块面与包面两边都要断言"取不到"：把函数搬去别的模块、再从 `tstdx.protocol` 再导出
    同一个名字，正是这仓历史上"兼容层靠别名续命"的走法，只看一边测不出来。
    """
    for name in ("stats", "get_command_by_name"):
        assert not hasattr(ledger, name), f"tstdx.protocol.commands.{name} 又回来了"
        assert not hasattr(tstdx.protocol, name), f"tstdx.protocol.{name} 以别名的方式回来了"


def test_command_member_face_has_no_transport_mapping() -> None:
    """`Command` 除字段外的成员账只剩 「hex」，「port」 那种假承诺不得回来。

    「port」 的病与 「stats」 同形：挂在公开类上像是账本对外口径，实际全 「tstdx/」 零读取点——
    连接池按 「HostEntry.port」 建连，族→端口的真相源在主站池里。文档的协议覆盖矩阵曾按这条成员
    核对端口，于是「只有门禁读它」的孤儿被一份文档判据供成了事实源；第 27 轮（V19 §4 P1-A2）
    先把端口真相源换成池本身（「test_doc_code_consistency.py::_family_port」），再删掉它。
    """
    fields = {field.name for field in dataclasses.fields(ledger.Command)}
    members = {
        name
        for name in dir(ledger.Command)
        if not name.startswith("_")
        and name not in fields
        and isinstance(getattr(ledger.Command, name, None), property)
    }
    assert members == DECLARED_COMMAND_MEMBERS, (
        f"Command 成员面与本步锁定的名单不符：多 {sorted(members - DECLARED_COMMAND_MEMBERS)}、"
        f"缺 {sorted(DECLARED_COMMAND_MEMBERS - members)}"
    )
    assert not hasattr(ledger.Command, "port"), "Command.port 回来了：族→端口只该由主站池给出"

    from tstdx.transport.hosts import POOL_BY_FAMILY

    ports = {
        family: {entry.port for entry in entries} for family, entries in POOL_BY_FAMILY.items()
    }
    assert set(ports) == {f for f in FAMILIES}, f"主站池族集合与账本族不符：{sorted(ports)}"
    not_unique = sorted(family for family, values in ports.items() if len(values) != 1)
    assert not_unique == [], f"主站池里这些族的端口不唯一，端口真相源不成立：{not_unique}"
