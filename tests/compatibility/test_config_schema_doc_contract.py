"""配置键清单与 ``atst.config.schema`` 运行期形状的最小守卫。

Schema 有 6 段 27 个字段；``docs/configuration.md`` 按段分组写成表格，小标题里是
``### `[core]``` 这种带反引号的写法，表格首列只写裸字段名。本判据按段还原成
``core.timeout`` 这样的全名再与 dataclass 字段集合比对，既能挡住「新增键没写文档」，
也能挡住「文档留着已删除的键」。散文里的例子（``ATST_CORE_TIMEOUT=5``、
``web.rate_limit``）不会被误当成表格声明，也不会因为换了措辞就让判据失效。
"""

from __future__ import annotations

import re
from dataclasses import fields
from pathlib import Path

from atst.config.schema import Config

ROOT = Path(__file__).resolve().parents[2]
DOC = ROOT / "docs" / "configuration.md"

_SECTION_HEADING = re.compile(r"^###\s+`\[([a-z_]+)\]`")
_ANY_H3 = re.compile(r"^###\s+")
_TABLE_KEY = re.compile(r"^\|\s*`([A-Za-z_][A-Za-z0-9_]*)`")
_QUALIFIED = re.compile(r"`([a-z_]+)\.([a-z_][a-z0-9_]*)`")

# 用 Config() 而非全局默认实例：字段集合与默认值无关，也不受进程内 configure() 影响。
DEFAULT_CONFIG = Config()


def _schema_keys() -> set[str]:
    keys = set(Config._SUBCONFIGS)
    for section in Config._SUBCONFIGS:
        keys.update(f"{section}.{field.name}" for field in fields(getattr(DEFAULT_CONFIG, section)))
    return keys


def _doc_block() -> str:
    text = DOC.read_text(encoding="utf-8")
    start_marker = "## 3. 键清单"
    end_marker = "## 4. 环境变量规则"
    assert start_marker in text and end_marker in text, "文档章节标题变了，判据自身失效"
    return text.split(start_marker, 1)[1].split(end_marker, 1)[0]


def _documented_table_keys(block: str) -> set[str]:
    section: str | None = None
    documented: set[str] = set()
    for line in block.splitlines():
        match = _SECTION_HEADING.match(line)
        if match:
            section = match.group(1)
            documented.add(section)
            continue
        if _ANY_H3.match(line):
            section = None
            continue
        match = _TABLE_KEY.match(line) if section else None
        if match:
            documented.add(f"{section}.{match.group(1)}")
    return documented


def test_schema_keys_are_documented() -> None:
    schema = _schema_keys()
    assert len(schema) > len(Config._SUBCONFIGS), "schema 字段枚举为空，判据自身失效"

    text = DOC.read_text(encoding="utf-8")
    documented = _documented_table_keys(_doc_block())
    # 散文里显式写出的全名也算「提到过」：这条判据只要求键可达，不强制它必须是一张表行。
    documented.update(
        f"{section}.{name}"
        for section, name in _QUALIFIED.findall(text)
        if section in Config._SUBCONFIGS
    )

    missing = schema - documented
    assert missing == set(), f"schema 键未在 docs/configuration.md 出现：{sorted(missing)}"


def test_key_table_has_no_removed_keys() -> None:
    """§3 的表格首列不得留着 schema 里已经不存在的键。"""
    table_keys = _documented_table_keys(_doc_block())
    assert table_keys, "没扫到任何表行，判据自身失效"
    stale = table_keys - _schema_keys()
    assert stale == set(), f"docs/configuration.md 仍在登记已删除的配置键：{sorted(stale)}"
