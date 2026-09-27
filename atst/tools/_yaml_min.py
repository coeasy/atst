# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""最小 YAML 读写器（零依赖，仅供 PROTOCOL_SPEC / golden 样本使用）。

设计要点
--------
* **零外部依赖**：仅使用 Python 标准库（无 PyYAML / ruamel.yaml 等）。
* **YAML 子集**：仅支持 PROTOCOL_SPEC/*.yaml 与 golden ``meta.yaml``
  实际使用的特性：
  * ``key: value``（标量值：字符串/数字/布尔/null）
  * 嵌套字典（缩进区分层级）
  * 列表：``- item``（标量）和 ``- key: value``（字典）
  * 内联列表：``[a, b, c]`` 与内联空容器 ``[]`` / ``{}``
  * 块标量：``|``（保留换行）和 ``>``（折叠为空格）
  * 注释：全行 ``#`` 和行内 ``#``（引号内不生效）
  * 空值：``null`` / ``~`` / 空字符串
* **Windows-safe**：使用 ``pathlib`` 和 UTF-8 编码。
* **容错**：不认识的语法降级为原始字符串，不抛异常。
* **读写闭环**：:func:`dump_yaml` 产出的文本保证 :func:`load_yaml`
  可无损读回（标量类型不漂移——数值字符串自动加引号）。

本模块由 :mod:`atst.tools.codegen`、:mod:`atst.tools.spec_audit` 与
:mod:`atst.tools.capture` 共享（§3-4：三套 YAML 实现收敛于此）。
"""

from __future__ import annotations

import ast
from typing import Any

__all__ = ["load_yaml", "dump_yaml"]


# --------------------------------------------------------------------------- #
# 入口
# --------------------------------------------------------------------------- #
def load_yaml(text: str) -> Any:
    """解析 YAML 文本为 Python 对象（dict / list / scalar）。

    Parameters
    ----------
    text : str
        YAML 文本内容。

    Returns
    -------
    Any
        解析后的 Python 对象。空文本返回 ``{}``。
    """
    lines = _prepare_lines(text)
    if not lines:
        return {}
    result, _ = _parse_block(lines, 0)
    return result


# --------------------------------------------------------------------------- #
# 预处理
# --------------------------------------------------------------------------- #
def _prepare_lines(text: str) -> list[tuple[int, str]]:
    """将文本预处理为 ``(indent, content)`` 列表。

    跳过空行和注释行，保留原始缩进。
    """
    result: list[tuple[int, str]] = []
    for raw_line in text.split("\n"):
        line = _strip_comment(raw_line)
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip(" "))
        content = line.strip()
        result.append((indent, content))
    return result


def _strip_comment(line: str) -> str:
    """去除行内 YAML 注释，引号内的 ``#`` 不视为注释起始。"""
    in_quote: str | None = None
    for i, ch in enumerate(line):
        if in_quote:
            if ch == in_quote and (i == 0 or line[i - 1] != "\\"):
                in_quote = None
        elif ch in ('"', "'"):
            in_quote = ch
        elif ch == "#" and (i == 0 or line[i - 1] in (" ", "\t")):
            return line[:i].rstrip()
    return line


def _find_colon(text: str) -> int:
    """返回引号外第一个 ``:`` 的位置，未找到返回 ``-1``。"""
    in_quote: str | None = None
    for i, ch in enumerate(text):
        if in_quote:
            if ch == in_quote and (i == 0 or text[i - 1] != "\\"):
                in_quote = None
        elif ch in ('"', "'"):
            in_quote = ch
        elif ch == ":":
            return i
    return -1


# --------------------------------------------------------------------------- #
# 递归解析
# --------------------------------------------------------------------------- #
def _parse_block(lines: list[tuple[int, str]], pos: int) -> tuple[Any, int]:
    """解析一个块（字典或列表），返回 ``(值, 新位置)``。"""
    if pos >= len(lines):
        return None, pos
    indent = lines[pos][0]
    # 裸 ``-`` 行也是列表项起始（嵌套列表/旧 capture dump 的字典列表项）
    if lines[pos][1].startswith("- ") or lines[pos][1] == "-":
        return _parse_list(lines, pos, indent)
    return _parse_dict(lines, pos, indent)


def _parse_dict(lines: list[tuple[int, str]], pos: int, indent: int) -> tuple[dict, int]:
    """解析字典块，返回 ``(dict, 新位置)``。"""
    result: dict[Any, Any] = {}
    while pos < len(lines):
        line_indent, content = lines[pos]
        if line_indent < indent:
            break
        if line_indent > indent:
            pos += 1
            continue

        colon_pos = _find_colon(content)
        if colon_pos < 0:
            pos += 1
            continue

        key = _parse_scalar(content[:colon_pos].strip())
        value_str = content[colon_pos + 1 :].strip()
        pos += 1

        if value_str.startswith("|") or value_str.startswith(">"):
            # 块标量
            block_lines: list[str] = []
            while pos < len(lines) and lines[pos][0] > indent:
                block_lines.append(lines[pos][1])
                pos += 1
            joiner = "\n" if value_str[0] == "|" else " "
            result[key] = joiner.join(block_lines)
        elif value_str.startswith("["):
            result[key] = _parse_inline_list(value_str)
        elif value_str == "":
            # 嵌套块或 null
            if pos < len(lines) and lines[pos][0] > indent:
                nested, pos = _parse_block(lines, pos)
                result[key] = nested
            else:
                result[key] = None
        elif value_str == "[]":
            result[key] = []
        elif value_str == "{}":
            result[key] = {}
        else:
            result[key] = _parse_scalar(value_str)

    return result, pos


def _parse_list(lines: list[tuple[int, str]], pos: int, indent: int) -> tuple[list, int]:
    """解析列表块，返回 ``(list, 新位置)``。"""
    result: list[Any] = []
    while pos < len(lines):
        line_indent, content = lines[pos]
        if line_indent < indent:
            break
        if line_indent > indent:
            pos += 1
            continue
        if not (content.startswith("- ") or content == "-"):
            break

        # 裸 ``-`` 行（无内联内容）+ 嵌套块 = 嵌套列表/字典项；
        # 旧版 capture _yaml_dump 的字典列表项即此形态，读侧必须兼容
        item_content = "" if content == "-" else content[2:].strip()
        pos += 1

        if not item_content:
            if pos < len(lines) and lines[pos][0] > indent:
                nested, pos = _parse_block(lines, pos)
                result.append(nested)
            else:
                result.append(None)
        elif item_content == "{}":
            result.append({})
        elif _find_colon(item_content) >= 0:
            # 列表项为字典
            item_dict, pos = _parse_list_dict_item(item_content, lines, pos, indent)
            result.append(item_dict)
        else:
            result.append(_parse_scalar(item_content))

    return result, pos


def _parse_list_dict_item(
    first_line: str,
    lines: list[tuple[int, str]],
    pos: int,
    list_indent: int,
) -> tuple[dict, int]:
    """解析列表中的字典项。

    第一个键值对内联在 ``- `` 之后，后续键值对在 ``list_indent + 2`` 缩进层级。
    """
    item_dict: dict[Any, Any] = {}
    key_indent = list_indent + 2

    # 解析第一个键值对
    colon_pos = _find_colon(first_line)
    key = _parse_scalar(first_line[:colon_pos].strip())
    value_str = first_line[colon_pos + 1 :].strip()

    if value_str.startswith("|") or value_str.startswith(">"):
        block_lines: list[str] = []
        while pos < len(lines) and lines[pos][0] > key_indent:
            block_lines.append(lines[pos][1])
            pos += 1
        joiner = "\n" if value_str[0] == "|" else " "
        item_dict[key] = joiner.join(block_lines)
    elif value_str.startswith("["):
        item_dict[key] = _parse_inline_list(value_str)
    elif value_str == "":
        if pos < len(lines) and lines[pos][0] > key_indent:
            nested, pos = _parse_block(lines, pos)
            item_dict[key] = nested
        else:
            item_dict[key] = None
    elif value_str == "[]":
        item_dict[key] = []
    elif value_str == "{}":
        item_dict[key] = {}
    else:
        item_dict[key] = _parse_scalar(value_str)

    # 解析后续键值对（同一缩进层级）
    while pos < len(lines) and lines[pos][0] == key_indent:
        line_content = lines[pos][1]
        colon_pos = _find_colon(line_content)
        if colon_pos < 0:
            pos += 1
            continue

        key = _parse_scalar(line_content[:colon_pos].strip())
        value_str = line_content[colon_pos + 1 :].strip()
        pos += 1

        if value_str.startswith("|") or value_str.startswith(">"):
            block_lines = []
            while pos < len(lines) and lines[pos][0] > key_indent:
                block_lines.append(lines[pos][1])
                pos += 1
            joiner = "\n" if value_str[0] == "|" else " "
            item_dict[key] = joiner.join(block_lines)
        elif value_str.startswith("["):
            item_dict[key] = _parse_inline_list(value_str)
        elif value_str == "":
            if pos < len(lines) and lines[pos][0] > key_indent:
                nested, pos = _parse_block(lines, pos)
                item_dict[key] = nested
            else:
                item_dict[key] = None
        elif value_str == "[]":
            item_dict[key] = []
        elif value_str == "{}":
            item_dict[key] = {}
        else:
            item_dict[key] = _parse_scalar(value_str)

    return item_dict, pos


# --------------------------------------------------------------------------- #
# 标量与内联列表
# --------------------------------------------------------------------------- #
def _parse_scalar(text: str) -> Any:
    """解析 YAML 标量值为 Python 对象。"""
    text = text.strip()
    if not text or text in ("null", "Null", "NULL", "~"):
        return None
    if text in ("true", "True", "TRUE"):
        return True
    if text in ("false", "False", "FALSE"):
        return False
    if text == "[]":
        return []
    if text.startswith("[") and text.endswith("]"):
        return _parse_inline_list(text)
    # 带引号字符串
    if text.startswith('"') and text.endswith('"'):
        return (
            text[1:-1]
            .replace("\\n", "\n")
            .replace("\\t", "\t")
            .replace('\\"', '"')
            .replace("\\\\", "\\")
        )
    if text.startswith("'") and text.endswith("'"):
        return text[1:-1].replace("''", "'")
    # 整数
    try:
        return int(text)
    except ValueError:
        pass
    # 浮点数
    try:
        return float(text)
    except ValueError:
        pass
    return text


def _parse_inline_list(text: str) -> list[Any]:
    """解析内联列表 ``[a, b, c]``。

    优先使用 ``ast.literal_eval``（语法兼容），失败则手动分割。
    """
    text = text.strip()
    if not (text.startswith("[") and text.endswith("]")):
        return [_parse_scalar(text)]
    inner = text[1:-1].strip()
    if not inner:
        return []

    # 优先用 ast.literal_eval
    try:
        result = ast.literal_eval(text)
        if isinstance(result, list):
            return result
    except (ValueError, SyntaxError):
        pass

    # 手动分割
    items: list[str] = []
    current = ""
    in_quote: str | None = None
    bracket_depth = 0
    for ch in inner:
        if in_quote:
            current += ch
            if ch == in_quote:
                in_quote = None
        elif ch in ('"', "'"):
            in_quote = ch
            current += ch
        elif ch in ("[", "{"):
            bracket_depth += 1
            current += ch
        elif ch in ("]", "}"):
            bracket_depth -= 1
            current += ch
        elif ch == "," and bracket_depth == 0:
            items.append(current.strip())
            current = ""
        else:
            current += ch
    if current.strip():
        items.append(current.strip())

    return [_parse_scalar(item) for item in items]


# --------------------------------------------------------------------------- #
# 写侧（dump）：与 PROTOCOL_SPEC / golden meta.yaml 风格兼容
# --------------------------------------------------------------------------- #
def dump_yaml(data: Any) -> str:
    """序列化为 YAML 文本（PROTOCOL_SPEC / golden ``meta.yaml`` 风格）。

    契约：``load_yaml(dump_yaml(obj)) == obj``（标量类型不漂移——会被读侧
    误判为数字/布尔的字符串自动加引号）。浮点仅支持有限值（nan/inf 不入
    PROTOCOL_SPEC 语料，不做特殊处理）。空容器输出 ``[]`` / ``{}``；
    多行字符串输出带 ``\\n`` 转义的双引号标量。

    Parameters
    ----------
    data : Any
        dict / list / 标量（与 :func:`load_yaml` 的产出同构）。

    Returns
    -------
    str
        YAML 文本（UTF-8 无 BOM，尾部单个换行；空输入返回空串）。
    """
    lines: list[str] = []
    _emit_block(lines, data, 0)
    if not lines:
        return ""
    return "\n".join(lines) + "\n"


def _emit_block(lines: list[str], obj: Any, indent: int) -> None:
    """递归输出一个块（dict 或 list），``indent`` 为当前层级（2 空格/层）。"""
    pad = "  " * indent
    if isinstance(obj, dict):
        for key, value in obj.items():
            key_str = _format_scalar(key)
            if isinstance(value, (dict, list)) and value:
                lines.append(f"{pad}{key_str}:")
                _emit_block(lines, value, indent + 1)
            elif isinstance(value, dict):
                lines.append(f"{pad}{key_str}: {{}}")
            elif isinstance(value, list):
                lines.append(f"{pad}{key_str}: []")
            else:
                lines.append(f"{pad}{key_str}: {_format_scalar(value)}")
    elif isinstance(obj, list):
        _emit_list_items(lines, obj, indent)
    else:  # 顶层裸标量（YAML 合法；语料中不出现）
        lines.append(f"{pad}{_format_scalar(obj)}")


def _emit_list_items(lines: list[str], items: list[Any], indent: int) -> None:
    """输出列表项。

    字典项与读侧 :func:`_parse_list_dict_item` 的缩进契约对齐
    （``key_indent = list_indent + 2``，即后续键与首键同列）：
    首对键值内联在 ``- `` 之后，后续键值对在 ``indent + 1`` 层级，
    键下嵌套块再深一层（``indent + 2``）。
    """
    pad = "  " * indent
    sub_pad = "  " * (indent + 1)
    for item in items:
        if isinstance(item, dict) and item:
            first = True
            for key, value in item.items():
                key_str = _format_scalar(key)
                if first:
                    first = False
                    if isinstance(value, (dict, list)) and value:
                        lines.append(f"{pad}- {key_str}:")
                        _emit_block(lines, value, indent + 2)
                    else:
                        lines.append(f"{pad}- {key_str}: {_format_scalar(value)}")
                elif isinstance(value, (dict, list)) and value:
                    lines.append(f"{sub_pad}{key_str}:")
                    _emit_block(lines, value, indent + 2)
                elif isinstance(value, dict):
                    lines.append(f"{sub_pad}{key_str}: {{}}")
                elif isinstance(value, list):
                    lines.append(f"{sub_pad}{key_str}: []")
                else:
                    lines.append(f"{sub_pad}{key_str}: {_format_scalar(value)}")
        elif isinstance(item, dict):  # 空字典
            lines.append(f"{pad}- {{}}")
        elif isinstance(item, list):
            if item:
                lines.append(f"{pad}-")
                _emit_block(lines, item, indent + 1)
            else:
                lines.append(f"{pad}- []")
        else:
            lines.append(f"{pad}- {_format_scalar(item)}")


def _format_scalar(value: Any) -> str:
    """标量 → YAML 文本。

    宽松规则（capture 旧 ``_scalar``）：含特殊字符 / 首尾空白 /
    布尔词形的字符串加引号；在此之上用读侧 :func:`_parse_scalar`
    校验无损——类型漂移（如纯数字字符串 ``"600519"``）强制加引号。
    """
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    text = str(value)
    candidate = _loose_scalar(text)
    if not candidate.startswith('"') and _parse_scalar(candidate) == value:
        return candidate
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n") + '"'


def _loose_scalar(text: str) -> str:
    """宽松标量格式化：特殊字符触发引号，其余原样。"""
    if (
        not text
        or any(ch in text for ch in ":#{}[]&*?|-<>=!%@`\"'")
        or text.lower() in ("true", "false", "null", "yes", "no")
        or text[0].isspace()
        or text[-1].isspace()
        or "\n" in text
        or "\r" in text
        or "\t" in text
    ):
        return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return text
