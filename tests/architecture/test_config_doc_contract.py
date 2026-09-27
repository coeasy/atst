# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""配置面文档 ⇄ schema / loader 的双向对账（V17 Phase 4 第 43 步，F-69）。

``docs/configuration.md`` 是配置面的**唯一用户口径**：一张表列全 5 段 17 键，每行还写着
默认值、取值范围与读取方；§4 定下环境变量命名规则，§5 逐格承诺"这种写法会以什么消息
报错"。第 43 步之前，这一整面声称里只有"配置段数 = 5"一个数字被门禁读过——键名、默认值、
范围、错误消息、环境变量五类抄本全在射程外，而它们恰好是历史上反复出错的那几类（F-13 的
死配置段、F-16 的"写了 TOML 不生效"、F-20 的幻影键 ``hosts.heartbeat_cmd``、F-43 的幻影
开关 ``allow_partial``）。本模块把它们逐类钉回运行期事实：

* 段与键：**双向**——schema 有的必须都记录，记录的不许是幻影；
* 默认值：对上运行期 ``DEFAULT_CONFIG`` 的实际取值，不猜 dataclass 元数据形状；
* 取值范围：**行为化**判据——文档写的边界必须真被 ``validate()`` 接受、越界一档必须真被拒；
* 注册表成员声称：以 ``PROVIDERS`` / ``KNOWN_SOURCES`` 两个真相源逐个试正反例；
* fail-closed 场景表：就地触发，异常类型与文档写出的消息片段逐个命中；
* 环境变量：命名规则对**全部** 17 键成立；§4 的"专用 runtime 变量"表与 loader 的登记表
  双向对上；并且发行代码里出现的每个 ``ATST_*`` 名字都必须有归属——最后这条就是 F-69
  要防的那件事（``ATST_WENCAI_COOKIE`` 未登记，用户照错误消息设置后 ``Client()`` 反而抛
  ``ConfigError``）。

``tests/`` 不参与 ``ATST_*`` 对账：那里的 ``ATST_CORE_TIMOUT`` 一类是刻意的负例夹具。
"""

from __future__ import annotations

import ast
import dataclasses
import json
import math
import re
import tempfile
from pathlib import Path
from typing import Any

import pytest

from atst.config import loader
from atst.config.schema import DEFAULT_CONFIG, Config, validate_keys
from atst.errors import ConfigError, ValidationError

ROOT = Path(__file__).resolve().parents[2]
DOC_REL = "docs/configuration.md"

SECTIONS: tuple[str, ...] = tuple(Config._SUBCONFIGS)
_ENV_NAME = re.compile(r"ATST_[A-Z][A-Z0-9_]*")
#: 以 ``ATST_RATE_LIMIT_*`` 这种写法出现的是前缀示意，不是完整变量名。
_TRUNCATED_NAME = re.compile(r"ATST_[A-Z0-9_]*_$")
_RANGE = re.compile(r"(\d+(?:\.\d+)?)\s*[–—-]\s*(\d+(?:\.\d+)?)")


# --------------------------------------------------------------------------- #
# 文档解析
# --------------------------------------------------------------------------- #


def _doc() -> str:
    return (ROOT / DOC_REL).read_text(encoding="utf-8")


def _section_blocks() -> dict[str, str]:
    """``### `[core]` …`` 小节正文（到下一个 ``##`` / ``###`` 为止）。"""
    blocks: dict[str, str] = {}
    current: str | None = None
    buffer: list[str] = []
    for line in _doc().splitlines():
        head = re.match(r"^### `\[(\w+)\]`", line)
        if head:
            if current is not None:
                blocks[current] = "\n".join(buffer)
            current, buffer = head.group(1), []
            continue
        if current is not None and re.match(r"^(?:##|###) ", line):
            blocks[current] = "\n".join(buffer)
            current, buffer = None, []
            continue
        if current is not None:
            buffer.append(line)
    if current is not None:
        blocks[current] = "\n".join(buffer)
    return blocks


def _table_rows(block: str) -> list[list[str]]:
    rows: list[list[str]] = []
    for line in block.splitlines():
        if not line.startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if not cells or cells[0] in {"键", "场景"} or set("".join(cells)) <= set("-: "):
            continue
        rows.append(cells)
    return rows


def _key_tables() -> dict[str, dict[str, list[str]]]:
    """``{段: {键: [该行除键、默认值之外的单元格]}}``。"""
    tables: dict[str, dict[str, list[str]]] = {}
    for section, block in _section_blocks().items():
        entries: dict[str, list[str]] = {}
        for cells in _table_rows(block):
            key = cells[0].strip("`")
            if not re.fullmatch(r"\w+", key):
                raise AssertionError(f"{DOC_REL} [{section}] 有解析不出键名的行：{cells!r}")
            entries[key] = cells[2:]
        tables[section] = entries
    return tables


def _norm(cell: str) -> str:
    return re.sub(r"\s+", " ", cell.replace("`", "")).strip()


# --------------------------------------------------------------------------- #
# 运行期真相源
# --------------------------------------------------------------------------- #


def fields_of(section: str) -> list[dataclasses.Field[Any]]:
    return list(dataclasses.fields(getattr(DEFAULT_CONFIG, section)))


def _literal(value: Any) -> str:
    """文档默认列的写法：字符串带引号、bool 小写、容器走 JSON、数字用 ``str()``。"""
    if value is None:
        return "None"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return f'"{value}"'
    if isinstance(value, (int, float)):
        return str(value)
    return json.dumps(value, ensure_ascii=False)


def _real_default(section: str, key: str) -> Any:
    return getattr(getattr(DEFAULT_CONFIG, section), key)


def _override(section: str, key: str, value: Any) -> None:
    Config().with_overrides(**{section: {key: value}}).validate()


# --------------------------------------------------------------------------- #
# 1. 段与键：双向
# --------------------------------------------------------------------------- #


def test_documented_section_list_is_the_schema_sections() -> None:
    documented = set(_section_blocks())
    assert documented == set(SECTIONS), (
        f"{DOC_REL} §3 的段与 schema 不符：文档多 {sorted(documented - set(SECTIONS))}"
        f"、缺 {sorted(set(SECTIONS) - documented)}"
    )
    claimed = [int(n) for n in re.findall(r"共\s*(\d+)\s*段", _doc())]
    assert claimed, f"{DOC_REL} 不再声明段数，门禁失效"
    assert set(claimed) == {len(SECTIONS)}, f"文档声称 {claimed} 段，真相源是 {SECTIONS}"


def test_key_tables_are_bidirectional_with_the_schema() -> None:
    tables = _key_tables()
    ghosts: list[str] = []
    undocumented: list[str] = []
    for section in SECTIONS:
        documented = set(tables.get(section, {}))
        real = {item.name for item in fields_of(section)}
        ghosts.extend(f"[{section}].{key}" for key in sorted(documented - real))
        undocumented.extend(f"[{section}].{key}" for key in sorted(real - documented))
    assert not ghosts, f"{DOC_REL} 把 schema 里不存在的键写成现行事实：{ghosts}"
    assert not undocumented, f"schema 有键却未进 {DOC_REL} §3（用户口径必须列全）：{undocumented}"


# --------------------------------------------------------------------------- #
# 2. 默认值
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("section", SECTIONS)
def test_documented_defaults_match_the_running_config(section: str) -> None:
    block = _section_blocks()[section]
    real = {item.name: _literal(_real_default(section, item.name)) for item in fields_of(section)}
    wrong: list[str] = []
    for cells in _table_rows(block):
        key = cells[0].strip("`")
        if key not in real:
            continue  # 幻影键由上一条判据负责，这里不重复报
        if cells[1].strip("`") != real[key]:
            wrong.append(f"[{section}].{key}：文档 `{cells[1]}`，运行期 `{real[key]}`")
    assert not wrong, f"{DOC_REL} 的默认值抄本已过期：{wrong}"


# --------------------------------------------------------------------------- #
# 3. 取值范围：把文档边界变成可执行判据
# --------------------------------------------------------------------------- #


def _schema_keys(section: str) -> set[str]:
    """幻影键由双向判据专门报告；其余判据只量真实存在的键，避免一处笔误炸出三条无关崩溃。"""
    return {item.name for item in fields_of(section)}


def _numeric_bounds() -> list[tuple[str, str, float, float]]:
    """``[(段, 键, 下界, 上界)]``；``rate_limit`` 段把范围写在表外的一句 prose 里。"""
    out: list[tuple[str, str, float, float]] = []
    for section, block in _section_blocks().items():
        prose = re.search(r"取值范围均为\s*(\d+(?:\.\d+)?)\s*[–—-]\s*(\d+(?:\.\d+)?)", block)
        for cells in _table_rows(block):
            key = cells[0].strip("`")
            if key not in _schema_keys(section):
                continue
            value = _real_default(section, key)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                continue
            found = [pair for cell in cells[2:] for pair in _RANGE.findall(cell)]
            if not found and prose is not None:
                found = [(prose.group(1), prose.group(2))]
            assert found, f"{DOC_REL} [{section}].{key} 是数值键却没写取值范围，门禁失效"
            out.append((section, key, float(found[0][0]), float(found[0][1])))
    return out


def test_documented_ranges_are_what_the_validator_enforces() -> None:
    rows = _numeric_bounds()
    assert len(rows) >= 5, f"数值范围判据只命中 {len(rows)} 行，说明它自身失效了"
    wrong: list[str] = []
    for section, key, low, high in rows:
        integer = isinstance(_real_default(section, key), int)
        step = 1 if integer else 0.01
        for probe, should_accept in (
            (low, True),
            (high, True),
            (low - step, False),
            (high + step, False),
        ):
            value = int(probe) if integer else probe
            try:
                _override(section, key, value)
                accepted = True
            except ValidationError:
                accepted = False
            if accepted is not should_accept:
                wrong.append(
                    f"[{section}].{key} 文档范围 {low}–{high}：{value} 实际"
                    f"{'通过' if accepted else '拒绝'}，期望"
                    f"{'通过' if should_accept else '拒绝'}"
                )
    assert not wrong, "文档写的取值范围与 validate() 的实际行为不符：\n" + "\n".join(wrong)


# --------------------------------------------------------------------------- #
# 4. 注册表成员声称
# --------------------------------------------------------------------------- #

#: ``{(段, 键): 把候选值包成该键覆盖形状}``。文档在"取值范围/读取方"列点名注册表的行必须
#: 逐条落在这里，反之亦然——两边对不上账即红，判据不会静默少量一行。
_MEMBERSHIP_SHAPES: dict[tuple[str, str], Any] = {
    ("core", "default_provider"): lambda value: {"core": {"default_provider": value}},
    ("web", "enabled_sources"): lambda value: {"web": {"enabled_sources": [value]}},
    ("web", "rate_limit"): lambda value: {"web": {"rate_limit": {value: 5}}},
}


def _registries() -> dict[str, set[str]]:
    from atst.providers import PROVIDERS
    from atst.web.sources import KNOWN_SOURCES

    return {"PROVIDERS": set(PROVIDERS.ids()), "KNOWN_SOURCES": set(KNOWN_SOURCES)}


def _claimed_memberships() -> dict[tuple[str, str], str]:
    """文档为哪个键点名了哪个注册表：``{(段, 键): 注册表名}``。

    只量真实存在的键：幻影键由双向判据专门报告，否则一处笔误会同时让本判据报出
    一条误导性的"成员声称对不上账"（变异验证 M1 实测到这条级联）。
    """
    claimed: dict[tuple[str, str], str] = {}
    for (section, key), cells in sorted(_flatten_rows()):
        if key not in _schema_keys(section):
            continue
        names = {
            name for cell in cells for name in _registries() if re.search(rf"\b{name}\b", cell)
        }
        assert len(names) <= 1, f"[{section}].{key} 同时点名多个注册表：{sorted(names)}"
        if names:
            claimed[(section, key)] = next(iter(names))
    return claimed


def _flatten_rows() -> list[tuple[tuple[str, str], list[str]]]:
    return [
        ((section, key), cells)
        for section, table in _key_tables().items()
        for key, cells in table.items()
    ]


def test_membership_claims_match_the_registries() -> None:
    claimed = _claimed_memberships()
    assert set(claimed) == set(_MEMBERSHIP_SHAPES), (
        f"{DOC_REL} 的注册表成员声称与判据对不上账：文档新增 "
        f"{sorted(set(claimed) - set(_MEMBERSHIP_SHAPES))}、判据多余 "
        f"{sorted(set(_MEMBERSHIP_SHAPES) - set(claimed))}"
    )
    registries = _registries()
    wrong: list[str] = []
    for (section, key), registry in sorted(claimed.items()):
        known = sorted(registries[registry])
        assert known, f"注册表 {registry} 为空，成员声称无从对账"
        shape = _MEMBERSHIP_SHAPES[(section, key)]
        try:
            Config().with_overrides(**shape(known[0])).validate()
        except Exception as exc:  # noqa: BLE001
            wrong.append(f"[{section}].{key} 的真实成员 {known[0]!r} 被拒：{exc}")
        try:
            Config().with_overrides(**shape("no-such-member")).validate()
            wrong.append(f"[{section}].{key} 接受 {registry} 之外的 'no-such-member'")
        except (ValidationError, ConfigError):
            pass
    assert not wrong, "注册表成员声称与真相源不符：\n" + "\n".join(wrong)


# --------------------------------------------------------------------------- #
# 5. fail-closed 场景表
# --------------------------------------------------------------------------- #

#: 判据认识的 §5 场景原文，与文档逐字对齐：改文档措辞必须同步改这里，否则对账报红。
_HANDLED_SCENARIOS = (
    "未知配置段（`[cache]`）",
    "段内未知字段",
    "未知 `ATST_*` 变量",
    "bool 位置写 `1`/`0`",
    "整数位置写 `1.5`",
    "`timeout=nan/inf`",
    "`default_provider` 不在注册表",
    "配置文件语法错误 / 是目录",
)


def _failclosed_rows() -> dict[str, str]:
    """``{归一化场景 -> 该行"结果"单元格原文}``。"""
    text = _doc()
    assert "## 5. fail-closed" in text, f"{DOC_REL} 不再有 §5 fail-closed 表，门禁失效"
    section = text.split("## 5. fail-closed", 1)[1].split("\n## ", 1)[0]
    return {
        _norm(cells[0]): cells[1]
        for cells in (
            [cell.strip() for cell in line.strip().strip("|").split("|")]
            for line in section.splitlines()
            if line.startswith("|")
        )
        if len(cells) > 1 and cells[0] not in {"场景"} and set("".join(cells)) - set("-: ")
    }


def _keys_of_type(kind: type) -> list[tuple[str, str]]:
    return [
        (section, key)
        for section in SECTIONS
        for key in set(_key_tables().get(section, {})) & _schema_keys(section)
        if type(_real_default(section, key)) is kind
    ]


def _capture(action: Any) -> tuple[str, str]:
    try:
        action()
    except Exception as exc:  # noqa: BLE001 - 判据要看的正是这个异常
        return type(exc).__name__, str(exc)
    raise AssertionError("文档承诺会报错的场景实际没有抛出")


def _raise_for(scenario: str) -> tuple[str, str]:
    """按文档写出的场景就地触发。"""
    if scenario.startswith("未知配置段"):
        dead = re.search(r"未知配置段（\[(\w+)\]）", scenario)
        assert dead, f"场景原文里取不到段名：{scenario!r}"
        return _capture(lambda: validate_keys({dead.group(1): {}}))
    if scenario == "段内未知字段":
        return _capture(lambda: Config().with_overrides(core={"__no_such_field__": 1}))
    if scenario.startswith("未知 ATST_"):
        return _capture(lambda: loader.config_from_env({"ATST_NOT_A_SECTION_X": "1"}))
    if scenario.startswith("bool 位置写"):
        numbers = [int(n) for n in re.findall(r"\d+", scenario)]
        flags = _keys_of_type(bool)
        assert flags, "schema 里不再有 bool 键，本行判据失去对象"
        return _capture(
            lambda: [_override(section, key, n) for section, key in flags for n in numbers]
        )
    if scenario.startswith("整数位置写"):
        probe = re.search(r"整数位置写\s*([\d.]+)", scenario)
        assert probe, f"场景未给出触发值：{scenario!r}"
        ints = _keys_of_type(int)
        assert ints, "schema 里不再有整数键，本行判据失去对象"
        return _capture(
            lambda: [_override(section, key, float(probe.group(1))) for section, key in ints]
        )
    if re.fullmatch(r"\w+=\w+/\w+", scenario):
        field, _, values = scenario.partition("=")
        probes = {"nan": math.nan, "inf": math.inf}
        targets = [
            (section, key)
            for section in SECTIONS
            for key in set(_key_tables().get(section, {})) & _schema_keys(section)
            if key == field and type(_real_default(section, key)) is float
        ]
        assert targets, f"{scenario!r} 指向的键不再是浮点键"
        return _capture(
            lambda: [
                _override(section, key, probes[name])
                for section, key in targets
                for name in values.split("/")
            ]
        )
    if scenario.endswith("不在注册表"):
        field = scenario.split()[0]
        return _capture(lambda: _override("core", field, "no-such-provider"))
    if scenario.startswith("配置文件"):
        return _capture(_raise_bad_files)
    raise AssertionError(f"{DOC_REL} §5 出现了本门禁不认识的场景：{scenario!r}")


def _raise_bad_files() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        broken = root / "broken.toml"
        broken.write_text("this is = = not toml\n", encoding="utf-8")
        loader.load_toml(broken)
        loader.load_toml(root)


def _result_claim(cell: str) -> tuple[str, list[str]]:
    """从"结果"单元格取出 ``(异常类名, 必须出现在消息里的片段)``；``…`` 是省略号。"""
    backticked = re.findall(r"`([^`]+)`", cell)
    assert backticked, f"§5 结果列里没有任何反引号声称可核对：{cell!r}"
    class_name, _, body = backticked[0].partition(":")
    class_name = class_name.strip()
    assert class_name in {
        "ValidationError",
        "ConfigError",
    }, f"§5 结果列声称了本门禁不认识的异常：{class_name!r}"
    return class_name, [piece.strip() for piece in body.split("…") if piece.strip()]


def test_every_failclosed_scenario_raises_the_documented_way() -> None:
    rows = _failclosed_rows()
    assert set(rows) == {_norm(s) for s in _HANDLED_SCENARIOS}, (
        f"{DOC_REL} §5 的场景与判据对不上账：文档新增 {sorted(set(rows) - {_norm(s) for s in _HANDLED_SCENARIOS})}、"
        f"判据多余 {sorted({_norm(s) for s in _HANDLED_SCENARIOS} - set(rows))}"
    )
    for scenario, result_cell in sorted(rows.items()):
        class_name, message = _raise_for(scenario)
        claimed_class, fragments = _result_claim(result_cell)
        assert class_name == claimed_class, (
            f"§5「{scenario}」文档承诺 {claimed_class}，实际抛 {class_name}：{message}"
        )
        for fragment in fragments:
            assert fragment in message, (
                f"§5「{scenario}」文档承诺消息含 {fragment!r}，实际是：{message}"
            )
        if scenario == "段内未知字段":
            for name in ("default_provider", "timeout", "vipdoc_root"):
                assert name in message, f"§5「{scenario}」承诺给出可选字段，消息里没有 {name!r}"


def test_deleted_sections_still_fail_closed() -> None:
    listed = re.findall(r"已删除、\*\*写了会立即报错\*\*的段：(.+?)。\n", _doc(), re.S)
    assert listed, f"{DOC_REL} §5 不再列已删除段，门禁失效"
    names = set(re.findall(r"`(\w+)`", listed[0]))
    assert names, "已删除段清单解析不出名字，门禁失效"
    for name in sorted(names):
        assert name not in SECTIONS, f"文档说 `{name}` 已删除，schema 里却仍有该段"
        with pytest.raises(ValidationError):
            validate_keys({name: {}})


# --------------------------------------------------------------------------- #
# 6. 环境变量
# --------------------------------------------------------------------------- #


def test_env_naming_rule_reaches_every_documented_key() -> None:
    failures: list[str] = []
    total = 0
    for section, keys in _key_tables().items():
        for key in sorted(keys):
            total += 1
            name = f"ATST_{section.upper()}_{key.upper()}"
            parsed = loader.config_from_env({name: "1"})
            if parsed.get(section, {}).get(key) != 1:
                failures.append(f"{name} → {parsed!r}")
    assert total >= 15, f"命名规则只量了 {total} 个键，说明判据自身失效"
    assert not failures, f"§4 的 ATST_<SECTION>_<KEY> 规则对部分键不成立：{failures}"


def _runtime_table_names() -> set[str]:
    """§4「专用 runtime 变量」表里出现的全部 ``ATST_*``（一行可写三个）。"""
    assert "## 4. 环境变量规则" in _doc(), f"{DOC_REL} 不再有 §4，门禁失效"
    section = _doc().split("## 4. 环境变量规则", 1)[1].split("\n## ", 1)[0]
    names: set[str] = set()
    for line in section.splitlines():
        if line.startswith("| `ATST_"):
            names.update(_ENV_NAME.findall(line))
    assert names, "§4 的专用 runtime 变量表解析不出变量名，门禁失效"
    return names


def test_runtime_env_table_is_the_loader_registration() -> None:
    documented = _runtime_table_names()
    registered = set(loader._RUNTIME_ENV_KEYS)
    assert documented == registered, (
        f"§4 的专用 runtime 变量表与 loader 登记表不符：文档多 "
        f"{sorted(documented - registered)}、loader 多 {sorted(registered - documented)}——"
        "后者意味着代码在读一个用户文档里没有的开关，前者意味着用户设置它就会被"
        "strict 扫描判成拼写错误"
    )


def _env_names_in_shipped_code() -> dict[str, set[str]]:
    found: dict[str, set[str]] = {}
    for base in (ROOT / "atst", ROOT / "scripts"):
        for path in sorted(base.rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            names = set(_ENV_NAME.findall(path.read_text(encoding="utf-8")))
            whole = {name for name in names if not _TRUNCATED_NAME.fullmatch(name)}
            if whole:
                found[path.relative_to(ROOT).as_posix()] = whole
    return found


def _is_schema_env(name: str) -> bool:
    body = name[len(loader.ENV_PREFIX) :].lower()
    split = loader._split_env_section(body)
    if split is None:
        return False
    section, key = split
    return key in {item.name for item in fields_of(section)}


def test_every_env_var_the_library_reads_is_registered_or_schema() -> None:
    """F-69 的防回潮判据：发行代码用到的每个 ``ATST_*`` 都必须有归属。

    ``ATST_`` 是保留命名空间：未登记的变量会被 strict 扫描判成拼写错误并让整条配置
    加载 fail closed。所以"代码读它"与"扫描器认它"必须同时成立，缺一即用户按错误消息
    操作后反而构造不出 ``Client()``。
    """
    seen = _env_names_in_shipped_code()
    assert len(seen) >= 5, f"扫描只覆盖 {len(seen)} 个文件，判据自身失效"
    registered = set(loader._RUNTIME_ENV_KEYS)
    unowned = {
        rel: sorted(name for name in names if name not in registered and not _is_schema_env(name))
        for rel, names in seen.items()
    }
    unowned = {rel: names for rel, names in unowned.items() if names}
    assert not unowned, (
        f"代码使用却未登记进 `loader._RUNTIME_ENV_KEYS` 的 {loader.ENV_PREFIX} 变量"
        f"（用户设置后 load_config 会 fail closed）：{unowned}"
    )
    assert "ATST_WENCAI_COOKIE" in registered, "F-69 的那一格必须由 loader 登记"


# --------------------------------------------------------------------------- #
# 7. 修复回归：设置已登记的 runtime 变量后，配置链与真实消费者照常工作
# --------------------------------------------------------------------------- #


def test_setting_a_registered_runtime_var_does_not_break_the_client(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from atst import Client

    monkeypatch.setenv("ATST_WENCAI_COOKIE", "a-token")
    monkeypatch.chdir(tmp_path)
    client = Client()
    try:
        assert client.runtime.config.core.timeout == DEFAULT_CONFIG.core.timeout
    finally:
        client.close()


def test_the_wencai_consumer_reads_the_registered_var(monkeypatch: pytest.MonkeyPatch) -> None:
    from atst.web.wencai import WencaiSource

    monkeypatch.setenv("ATST_WENCAI_COOKIE", "token-from-env")
    assert WencaiSource().cookie == "token-from-env"
    assert WencaiSource(cookie="explicit").cookie == "explicit"


# --------------------------------------------------------------------------- #
# 8. 自检：本模块的解析器与判据不是空转
# --------------------------------------------------------------------------- #


def test_the_probe_itself_reads_the_whole_key_surface() -> None:
    tables = _key_tables()
    assert set(tables) == set(SECTIONS)
    total = sum(len(keys) for keys in tables.values())
    real = sum(len(fields_of(section)) for section in SECTIONS)
    assert total == real >= 15, f"§3 表读到 {total} 键，schema 有 {real} 键"
    unknown_field = _failclosed_rows().get("段内未知字段")
    assert unknown_field is not None, "§5 不再有『段内未知字段』行，本判据失去对象"
    assert _result_claim(unknown_field) == ("ValidationError", [])


def test_registration_cannot_shrink_in_silence() -> None:
    """登记表每缩一条，都要确认对应读取点也已消失（见上一条派生判据）。"""
    assert len(loader._RUNTIME_ENV_KEYS) >= 6, (
        f"专用 runtime 变量登记表缩到 {len(loader._RUNTIME_ENV_KEYS)} 条"
    )
    tree = ast.parse((ROOT / "atst" / "config" / "loader.py").read_text(encoding="utf-8"))
    literals = {
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and _ENV_NAME.fullmatch(node.value)
    }
    missing = set(loader._RUNTIME_ENV_KEYS) - literals
    assert not missing, f"登记表里的名字在 loader 源码中找不到字面量：{sorted(missing)}"
