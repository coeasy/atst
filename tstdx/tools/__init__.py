# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""工具集（§20：协议全覆盖 / 质量工具）。

包含 spec/codegen 与 golden 样本采集等开发工具。
"""

from .codegen import (  # noqa: F401
    generate_command_entry,
    generate_parser_code,
    load_all_specs,
    load_spec,
)
from .spec_audit import (  # noqa: F401
    SpecAuditResult,
    audit_all,
    audit_spec,
    coverage_summary,
)

__all__ = [
    "load_spec",
    "load_all_specs",
    "generate_parser_code",
    "generate_command_entry",
    "SpecAuditResult",
    "audit_spec",
    "audit_all",
    "coverage_summary",
]
