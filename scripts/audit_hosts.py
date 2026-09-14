#!/usr/bin/env python3
# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""Source-checkout wrapper for :mod:`tstdx.tools.host_audit`.

The implementation lives in the installable package so ``tstdx hosts audit``
works from a normal wheel. This wrapper exists only for the repository-oriented
``python scripts/audit_hosts.py`` operator entrypoint.
"""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tstdx.tools.host_audit import (  # noqa: E402
    AuditReport,
    FamilyAudit,
    audit_all,
    audit_family,
    load_external_hosts,
    main,
    write_markdown_summary,
    write_report,
)

__all__ = [
    "AuditReport",
    "FamilyAudit",
    "audit_all",
    "audit_family",
    "load_external_hosts",
    "main",
    "write_markdown_summary",
    "write_report",
]


if __name__ == "__main__":
    raise SystemExit(main())
