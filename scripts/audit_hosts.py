#!/usr/bin/env python3
# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""Source-checkout wrapper for :mod:`atst.tools.host_audit`.

The implementation lives in the installable package so ``atst hosts audit``
works from a normal wheel. This wrapper exists only for the repository-oriented
``python scripts/audit_hosts.py`` operator entrypoint.

For backwards compatibility with the pre-extraction script surface, the
canonical pool/probe symbols are re-exported here as well, so operators and
tests that reached into this module keep working.
"""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from atst.tools.host_audit import (  # noqa: E402
    AuditReport,
    FamilyAudit,
    audit_all,
    audit_family,
    load_external_hosts,
    main,
    write_markdown_summary,
    write_report,
)
from atst.tools.host_audit import (  # noqa: E402
    _norm_family as _norm_family,  # re-exported for the host-audit test surface
)
from atst.transport.hosts import (  # noqa: E402
    DEFAULT_HOST_POOL,
    POOL_BY_FAMILY,
    HostEntry,
)
from atst.transport.speedtest import ProbeResult, probe  # noqa: E402

__all__ = [
    "AuditReport",
    "DEFAULT_HOST_POOL",
    "FamilyAudit",
    "HostEntry",
    "POOL_BY_FAMILY",
    "ProbeResult",
    "audit_all",
    "audit_family",
    "load_external_hosts",
    "main",
    "probe",
    "write_markdown_summary",
    "write_report",
]


if __name__ == "__main__":
    raise SystemExit(main())
