# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""Typed query contracts for the v14 capability runtime.

Typed queries describe business intent without bypassing the canonical
Provider/Channel/Capability registry.  A typed capability is executable through
V14 semantic orchestration only after the same capability is present in
``tstdx.providers.PROVIDERS``; contracts for data-source methods that are not yet
registered remain explicit pending contracts rather than silently falling back
to a second capability namespace.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Generic, TypeVar

T = TypeVar("T")


CANONICAL_TYPED_CAPABILITIES = frozenset(
    {
        "fund_holdings",
        "bond_kline",
        "futures_kline",
        "options_snapshot",
        "research_reports",
        "f10",
    }
)

# These source methods already exist, but the canonical Provider registry does
# not yet advertise them.  Keep them visible as migration contracts while
# preventing Runtime from executing them through a parallel capability truth.
PENDING_TYPED_CAPABILITIES = frozenset(
    {
        "balance_sheet",
        "income_sheet",
        "cash_flow",
        "fund_rank",
        "news_financial",
    }
)


@dataclass(frozen=True, slots=True)
class CapabilityQuery:
    capability: str
    provider: str | None = None
    options: dict[str, Any] = field(default_factory=dict)

    @property
    def semantic_ready(self) -> bool:
        """Whether this typed contract is backed by the canonical registry."""
        return self.capability in CANONICAL_TYPED_CAPABILITIES


@dataclass(frozen=True, slots=True)
class SymbolQuery(CapabilityQuery):
    symbol: str = ""


@dataclass(frozen=True, slots=True)
class BatchCapabilityQuery(CapabilityQuery):
    symbols: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class BalanceSheetQuery(SymbolQuery):
    capability: str = "balance_sheet"


@dataclass(frozen=True, slots=True)
class IncomeStatementQuery(SymbolQuery):
    capability: str = "income_sheet"


@dataclass(frozen=True, slots=True)
class CashFlowQuery(SymbolQuery):
    capability: str = "cash_flow"


@dataclass(frozen=True, slots=True)
class FundRankQuery(CapabilityQuery):
    capability: str = "fund_rank"
    fund_type: str = "all"


@dataclass(frozen=True, slots=True)
class FundHoldingsQuery(SymbolQuery):
    capability: str = "fund_holdings"


@dataclass(frozen=True, slots=True)
class BondKlineQuery(SymbolQuery):
    capability: str = "bond_kline"


@dataclass(frozen=True, slots=True)
class FuturesKlineQuery(SymbolQuery):
    capability: str = "futures_kline"


@dataclass(frozen=True, slots=True)
class OptionSnapshotQuery(SymbolQuery):
    capability: str = "options_snapshot"


@dataclass(frozen=True, slots=True)
class NewsQuery(CapabilityQuery):
    capability: str = "news_financial"
    page: int = 1
    size: int = 20


@dataclass(frozen=True, slots=True)
class ResearchReportQuery(SymbolQuery):
    capability: str = "research_reports"


@dataclass(frozen=True, slots=True)
class F10Query(SymbolQuery):
    capability: str = "f10"
    section: str = ""


@dataclass(frozen=True, slots=True)
class TypedQueryResult(Generic[T]):
    """Typed payload wrapper carried inside canonical QueryResult."""

    data: T
    capability: str
