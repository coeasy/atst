# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""Typed query contracts for the v14 capability runtime.

Typed queries describe business intent without bypassing the canonical
Provider/Channel/Capability registry. A typed capability is executable through
V14 semantic orchestration only after the same capability is present in
``tstdx.providers.PROVIDERS``; contracts for data-source methods that are not yet
registered therefore remain pending automatically instead of creating a second
capability namespace.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Generic, TypeVar

from .errors import ValidationError
from .providers import PROVIDERS

T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class CapabilityQuery:
    capability: str
    provider: str | None = None
    options: dict[str, Any] = field(default_factory=dict)

    @property
    def semantic_ready(self) -> bool:
        """Whether the canonical Provider registry can satisfy this capability."""
        try:
            if self.provider:
                return PROVIDERS.supports(self.provider, self.capability)
            return any(PROVIDERS.supports(provider, self.capability) for provider in PROVIDERS.ids())
        except ValidationError:
            return False


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
    fund_type: int = 0


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
    size: int = 30


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
