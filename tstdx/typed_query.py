# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""Typed query contracts for the v14 capability runtime.

v14 migrates business capabilities away from generic kwargs into immutable
request objects. Provider selection remains a Planner responsibility; query
objects describe intent only.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Generic, TypeVar

T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class CapabilityQuery:
    capability: str
    provider: str | None = None
    options: dict[str, Any] = field(default_factory=dict)


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
