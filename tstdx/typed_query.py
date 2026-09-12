# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""Typed query contracts for the v14 capability runtime.

This module is the first step of v14: migrate high-value generic capability
calls from loosely typed kwargs into immutable request objects. The objects are
intentionally independent from providers; Provider selection remains a Planner
responsibility.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Generic, TypeVar


T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class CapabilityQuery:
    """Base typed query contract."""

    capability: str
    provider: str | None = None
    options: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class SymbolQuery(CapabilityQuery):
    """Typed query contract for symbol based capabilities."""

    symbol: str = ""


@dataclass(frozen=True, slots=True)
class BatchCapabilityQuery(CapabilityQuery):
    """Typed batch capability contract."""

    symbols: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class BalanceSheetQuery(SymbolQuery):
    """Typed balance sheet request."""

    capability: str = "balance_sheet"


@dataclass(frozen=True, slots=True)
class FundRankQuery(CapabilityQuery):
    """Typed fund ranking request."""

    capability: str = "fund_rank"
    fund_type: str = "all"


@dataclass(frozen=True, slots=True)
class OptionSnapshotQuery(SymbolQuery):
    """Typed option snapshot request."""

    capability: str = "options_snapshot"


@dataclass(frozen=True, slots=True)
class TypedQueryResult(Generic[T]):
    """Future typed result wrapper.

    Runtime integration will gradually replace ``dict`` payloads while keeping
    QueryResult/Provenance as the canonical execution envelope.
    """

    data: T
    capability: str
