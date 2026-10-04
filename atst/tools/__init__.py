# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""工具集（§20：协议全覆盖 / 质量工具）。

Exports are loaded on demand so ``python -m atst.tools.spec_audit`` can execute
the module once, without importing it as a side effect of package startup.
"""

from __future__ import annotations

from importlib import import_module
from typing import Any

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

_EXPORT_MODULES = {
    "load_spec": ".codegen",
    "load_all_specs": ".codegen",
    "generate_parser_code": ".codegen",
    "generate_command_entry": ".codegen",
    "SpecAuditResult": ".spec_audit",
    "audit_spec": ".spec_audit",
    "audit_all": ".spec_audit",
    "coverage_summary": ".spec_audit",
}


def __getattr__(name: str) -> Any:
    """Resolve the small public helper surface without eager submodule imports."""
    try:
        module_name = _EXPORT_MODULES[name]
    except KeyError:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from None
    value = getattr(import_module(module_name, __name__), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
