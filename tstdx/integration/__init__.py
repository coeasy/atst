# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""Integration adapters — protocol edges over the canonical v12 runtime.

The package-level ``create_app`` is the official FastAPI factory. It uses the
QueryPlan-backed Provider service and bounded TaskManager v2 while preserving
the established REST route surface. ``tstdx.integration.http_server`` remains a
legacy route-definition module for compatibility and test injection.
"""

from __future__ import annotations

__all__ = ["create_app"]


def create_app(*args, **kwargs):  # noqa: ANN002,ANN003,ANN201
    from .http_app import create_app as _create_app

    return _create_app(*args, **kwargs)
