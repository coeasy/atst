# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""Spec → Code 生成器（§4 / §20：协议全覆盖）。

从 PROTOCOL_SPEC/*.yaml 生成：

1. **Parser 骨架代码**：BaseParser 子类 + ``parse_payload()`` 方法存根
2. **Command ledger 条目**：commands.py 中的 ``_c(...)`` 行
3. **CLI**：

   * ``python -m tstdx.tools.codegen <spec.yaml>``  — 打印生成代码
   * ``python -m tstdx.tools.codegen --write <spec.yaml>`` — 写入
     ``tstdx/tools/generated_draft/_generated.py``（草稿区，不进包内）
   * ``python -m tstdx.tools.codegen --list`` — 列出所有 spec

设计要点
--------
* **零依赖**：YAML 解析由 :mod:`tstdx.tools._yaml_min` 完成。
* **安全**：生成的代码为存根（stub），不会覆盖已有解析器。
* **Windows-safe**：使用 ``pathlib``，UTF-8 编码。
"""

from __future__ import annotations

import argparse
import contextlib
import sys
from pathlib import Path
from typing import Any

from ._yaml_min import load_yaml

__all__ = [
    "load_spec",
    "load_all_specs",
    "generate_parser_code",
    "generate_command_entry",
    "main",
]


# --------------------------------------------------------------------------- #
# Spec 加载
# --------------------------------------------------------------------------- #
def load_spec(path: str) -> dict:
    """加载单个 YAML spec 文件。

    Parameters
    ----------
    path : str
        spec 文件路径。

    Returns
    -------
    dict
        解析后的 spec 字典。

    Raises
    ------
    FileNotFoundError
        文件不存在。
    ValueError
        文件无法解析。
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Spec file not found: {p}")
    text = p.read_text(encoding="utf-8")
    result = load_yaml(text)
    if not isinstance(result, dict):
        raise ValueError(f"Spec file did not parse to a dict: {p}")
    return result


def load_all_specs(spec_dir: str = "PROTOCOL_SPEC") -> dict[str, dict]:
    """加载目录下所有 YAML spec，以 ``spec_id`` 为键。

    Parameters
    ----------
    spec_dir : str
        spec 根目录（默认 ``PROTOCOL_SPEC``）。
        若为相对路径，基于项目根目录解析。

    Returns
    -------
    dict[str, dict]
        ``{spec_id: spec_dict}`` 映射。
    """
    spec_path = Path(spec_dir)
    if not spec_path.is_absolute():
        project_root = Path(__file__).resolve().parents[2]
        spec_path = project_root / spec_dir

    result: dict[str, dict] = {}
    if not spec_path.is_dir():
        return result

    for path in sorted(spec_path.glob("**/*.yaml")):
        try:
            spec = load_spec(str(path))
        except (FileNotFoundError, ValueError):
            continue
        spec_id = spec.get("spec_id", "")
        if spec_id:
            # 记录来源文件（spec_audit 等工具依赖；以仓库根为基准的相对路径）
            try:
                rel = path.resolve().relative_to(_get_project_root())
            except ValueError:
                rel = path
            spec["_file"] = Path(rel).as_posix()
            result[spec_id] = spec

    return result


def _get_project_root() -> Path:
    """返回项目根目录（tstdx/ 的父目录）。"""
    return Path(__file__).resolve().parents[2]


# --------------------------------------------------------------------------- #
# 工具函数
# --------------------------------------------------------------------------- #
def _pascal_case(name: str) -> str:
    """snake_case → PascalCase。"""
    return "".join(part.capitalize() for part in name.split("_"))


def _parser_class_name(name: str) -> str:
    """生成解析器类名（如 ``SecurityBarsParser``）。"""
    return _pascal_case(name) + "Parser"


def _family_constant(family: str) -> str:
    """将 family 字符串映射为 Family 常量名。"""
    mapping = {
        "7709": "Family.STANDARD",
        "7727": "Family.EXTENDED",
        "MAC": "Family.MAC",
        "F10": "Family.F10",
        "GOODS": "Family.GOODS",
    }
    return mapping.get(family, "Family.STANDARD")


def _tier_constant(status: str) -> str:
    """将 status 映射为 Tier 常量名。"""
    if status in ("stable", "verified"):
        return "TIER_L1"
    if status in ("inferred", "draft"):
        return "TIER_L2"
    return "TIER_DECLARED"


def _read_method(type_str: str) -> str:
    """YAML 类型 → BinaryReader 方法调用。"""
    type_str = type_str.strip()
    if type_str == "uint8":
        return "reader.uint8()"
    if type_str == "int8":
        return "reader.int8()"
    if type_str == "uint16":
        return "reader.uint16()"
    if type_str == "int16":
        return "reader.int16()"
    if type_str == "uint32":
        return "reader.uint32()"
    if type_str == "int32":
        return "reader.int32()"
    if type_str == "float32":
        return "reader.float32()"
    if type_str == "float64":
        return "reader.float64()"
    if type_str == "leb128":
        return "reader.leb128()"
    if type_str == "tdx_float":
        return "reader.tdx_float()"
    if type_str == "varint":
        return "reader.varint()"
    if type_str == "bool":
        return "bool(reader.uint8())"
    if type_str.startswith("string["):
        n = type_str[7:-1]
        return f"decode_gbk(reader.bytes({n}))"
    if type_str.startswith("raw["):
        n = type_str[4:-1]
        return f"reader.bytes({n})"
    return f"# TODO: read {type_str}"


def _header_size(spec: dict) -> int:
    """计算响应 header 的字节数（用于 HEAD 元信息）。"""
    response = spec.get("response", {})
    header = response.get("header", [])
    if not header:
        return 0
    total = 0
    for field in header:
        if isinstance(field, dict):
            length = field.get("length")
            if length is not None:
                with contextlib.suppress(ValueError, TypeError):
                    total += int(length)
    return total


# --------------------------------------------------------------------------- #
# 代码生成
# --------------------------------------------------------------------------- #
def _parse_spec_id(spec_id: Any) -> int:
    """解析 spec_id 为命令号（非法值**前置 raise**，两处生成入口统一走此函数）。

    Raises
    ------
    ValueError
        spec_id 不是合法十六进制命令号。
    """
    try:
        return int(str(spec_id), 16)
    except (ValueError, TypeError) as exc:
        raise ValueError(
            f"非法 spec_id: {spec_id!r}（应为 0x 前缀的十六进制命令号，如 0x0530）"
        ) from exc


def generate_parser_code(spec: dict) -> str:
    """从 spec 生成 Python Parser 骨架代码。

    Parameters
    ----------
    spec : dict
        加载后的 spec 字典。

    Returns
    -------
    str
        生成的 Python 代码文本。

    Raises
    ------
    ValueError
        spec_id 非法（前置校验，不再静默吞掉）。
    """
    name = spec.get("name", "unknown")
    spec_id = spec.get("spec_id", "0x0000")
    family = spec.get("family", "7709")
    description = spec.get("description", "")
    status = spec.get("status", "draft")
    class_name = _parser_class_name(name)
    family_const = _family_constant(family)
    tier_const = _tier_constant(status)
    head = _header_size(spec)

    # 解析命令号（前置 raise，与 generate_command_entry 统一口径）
    _parse_spec_id(spec_id)

    # 响应字段
    response = spec.get("response", {})
    resp_fields = response.get("fields", [])
    record_size = response.get("record_size")
    encoding = response.get("encoding", "raw")

    # 构建 parse_payload 方法体
    method_lines: list[str] = []

    # 读取记录数（如果有 header）
    has_record_count = False
    for hf in response.get("header", []):
        if isinstance(hf, dict) and hf.get("name") == "record_count":
            has_record_count = True
            break

    if has_record_count:
        method_lines.append("        count = reader.uint16()")
        method_lines.append("        rows: list[dict[str, Any]] = []")
        method_lines.append("        for _ in range(count):")
    else:
        method_lines.append("        rows: list[dict[str, Any]] = []")

    # 字段读取行缩进：有 record_count 时在 for 循环体内（12 空格），否则 8 空格
    body_indent = "            " if has_record_count else "        "

    # 读取响应字段
    if resp_fields:
        method_lines.append(f"{body_indent}row: dict[str, Any] = {{}}")
        for field in resp_fields:
            if not isinstance(field, dict):
                continue
            fname = field.get("name", "unknown")
            ftype = field.get("type", "uint16")
            read_call = _read_method(ftype)
            method_lines.append(f"{body_indent}row['{fname}'] = {read_call}")
        method_lines.append(f"{body_indent}rows.append(row)")
    else:
        method_lines.append(f"{body_indent}# TODO: 无响应字段定义")
        method_lines.append(f"{body_indent}rows.append({{}})")

    method_lines.append("        return rows")

    method_body = "\n".join(method_lines)

    # RECORD_SIZE 类属性
    record_size_line = ""
    if record_size is not None:
        try:
            rs = int(record_size)
            record_size_line = f"\n    RECORD_SIZE = {rs}"
        except (ValueError, TypeError):
            record_size_line = f"\n    RECORD_SIZE = {record_size!r}"

    # 构建完整代码
    lines: list[str] = []
    lines.append('"""Spec-generated parser skeleton (auto-generated by codegen).')
    lines.append("")
    lines.append(".. warning::")
    lines.append("   This file is auto-generated. Do not edit manually.")
    lines.append("   Run ``python -m tstdx.tools.codegen --write <spec.yaml>`` to regenerate.")
    lines.append('"""')
    lines.append("from __future__ import annotations")
    lines.append("")
    lines.append("from typing import Any")
    lines.append("")
    lines.append("from ...codec.primitive import BinaryReader, decode_gbk")
    lines.append("from ..commands import Family")
    lines.append("from ..registry import BaseParser, register_parser")
    lines.append("")
    lines.append(f'__all__ = ["{class_name}"]')
    lines.append("")
    lines.append("# --------------------------------------------------------------------------- #")
    lines.append(f"# {class_name} — {description}")
    lines.append("# --------------------------------------------------------------------------- #")
    lines.append(
        f"@register_parser({spec_id}, family={family_const}, "
        f'name="{name.upper()}", head={head}, tier="{tier_const}")'
    )
    lines.append(f"class {class_name}(BaseParser):")
    lines.append(f'    """{description}"""')
    lines.append("")

    if record_size_line:
        lines.append(record_size_line.rstrip())
        lines.append("")

    if encoding and encoding != "raw":
        lines.append(f'    ENCODING = "{encoding}"')
        lines.append("")

    lines.append(
        "    def parse_payload(self, reader: BinaryReader, **ctx: Any) -> list[dict[str, Any]]:"
    )
    lines.append('        """Parse response payload."""')
    lines.append(method_body)

    lines.append("")
    lines.append("")
    return "\n".join(lines)


def generate_command_entry(spec: dict) -> str:
    """生成 commands.py 的 ledger 条目行。

    Parameters
    ----------
    spec : dict
        加载后的 spec 字典。

    Returns
    -------
    str
        生成的 ``_c(...)`` 行。

    Raises
    ------
    ValueError
        spec_id 非法（前置校验，与 generate_parser_code 统一走 _parse_spec_id）。
    """
    name = spec.get("name", "unknown")
    spec_id = spec.get("spec_id", "0x0000")
    description = spec.get("description", "")
    status = spec.get("status", "draft")

    cmd_int = _parse_spec_id(spec_id)
    name_upper = name.upper()
    tier = _tier_constant(status)
    verified = "True" if status in ("stable", "verified") else "False"

    entry = (
        f'_c(0x{cmd_int:04X}, "{name_upper}", "{description}", tier={tier}, verified={verified}),'
    )
    return entry


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def _list_specs(spec_dir: str = "PROTOCOL_SPEC") -> None:
    """列出所有 spec 文件及其基本信息。"""
    specs = load_all_specs(spec_dir)
    if not specs:
        print(f"No specs found in {spec_dir}")
        return

    print(f"{'Spec ID':<12} {'Name':<25} {'Family':<8} {'Status':<12} {'Description'}")
    print("-" * 90)
    for spec_id, spec in sorted(specs.items()):
        name = spec.get("name", "")
        family = spec.get("family", "")
        status = spec.get("status", "")
        desc = spec.get("description", "")
        print(f"{spec_id:<12} {name:<25} {family:<8} {status:<12} {desc[:40]}")

    print(f"\nTotal: {len(specs)} specs")


def _generate_output(spec_paths: list[str] | None, spec_dir: str) -> str:
    """生成代码输出。

    Parameters
    ----------
    spec_paths : list[str] | None
        要处理的 spec 文件路径列表。``None`` 表示处理所有 spec。
    spec_dir : str
        spec 根目录。

    Returns
    -------
    str
        生成的完整代码文本。
    """
    if spec_paths:
        specs = [load_spec(p) for p in spec_paths]
    else:
        specs = list(load_all_specs(spec_dir).values())

    output_parts: list[str] = []
    first_block = True
    for spec in specs:
        parser_code = generate_parser_code(spec)
        entry_code = generate_command_entry(spec)

        if not first_block:
            # 合并文件中 __future__/import 只能出现一次：去掉后续块的模块前导，
            # 保留从第一个分隔注释行开始的类定义部分。
            cut_at = None
            for idx, line in enumerate(parser_code.splitlines()):
                if line.startswith("# ---"):
                    cut_at = idx
                    break
            if cut_at is not None:
                parser_code = "\n".join(parser_code.splitlines()[cut_at:])
        first_block = False

        output_parts.append(
            "# ============================================================================="
        )
        output_parts.append(
            f"# {spec.get('spec_id', '')} {spec.get('name', '')} — {spec.get('description', '')}"
        )
        output_parts.append(
            "# ============================================================================="
        )
        output_parts.append("")
        output_parts.append("# --- Parser skeleton ---")
        output_parts.append(parser_code)
        output_parts.append("")
        output_parts.append("# --- Command ledger entry ---")
        output_parts.append(f"# {entry_code}")
        output_parts.append("")
        output_parts.append("")

    return "\n".join(output_parts)


def main(argv: list[str] | None = None) -> int:
    """CLI 入口。

    Parameters
    ----------
    argv : list[str] | None
        命令行参数（默认 ``sys.argv[1:]``）。

    Returns
    -------
    int
        退出码：0 = 成功，1 = 错误。
    """
    parser = argparse.ArgumentParser(
        prog="python -m tstdx.tools.codegen",
        description="从 PROTOCOL_SPEC YAML 生成 Parser 骨架和 Command ledger 条目",
    )
    parser.add_argument(
        "spec",
        nargs="?",
        default=None,
        help="单个 spec 文件路径（省略则处理所有 spec）",
    )
    parser.add_argument(
        "--spec-dir",
        default="PROTOCOL_SPEC",
        help="spec 根目录（默认 PROTOCOL_SPEC）",
    )
    parser.add_argument(
        "--write",
        action="store_true",
        help="写入 tstdx/tools/generated_draft/_generated.py（草稿区，不进包内）",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="列出所有 spec",
    )
    args = parser.parse_args(argv)

    if args.list:
        _list_specs(args.spec_dir)
        return 0

    spec_paths = [args.spec] if args.spec else None

    output = _generate_output(spec_paths, args.spec_dir)

    if args.write:
        # 草稿输出到 tools/generated_draft/（不再写包内孤儿位置
        # protocol/parsers/_generated.py —— 生成物无消费者、还可能遮蔽真解析器）
        output_path = Path(__file__).resolve().parent / "generated_draft" / "_generated.py"
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(output, encoding="utf-8")
        print(f"Written to {output_path}", file=sys.stderr)
    else:
        print(output)

    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
