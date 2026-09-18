# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""v14 领域业务 Record（Phase 2 Domain Model）。

目标：用类型化 Domain Record 替换 web/facade 层的 ``list[dict]`` 业务结果。
每个 Record 与 ``tstdx.typed_query`` 中的 Typed Query 契约一一对应，
并可通过 ``to_dict`` / ``from_dict`` 在 Runtime 边界做无损序列化。

单位约定（继承 :mod:`tstdx.domain.models`）::

    价格/金额    元（float）
    成交量       股（int）
    日期         "YYYY-MM-DD"（本地字符串）
    时间         "HH:MM:SS"（本地字符串）
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, TypeVar

_T = TypeVar("_T")


class _RecordFactory(Protocol):
    """最小协议：拥有 from_dict 类方法的 Record 类型。"""

    @classmethod
    def from_dict(cls: type[_T], d: dict[str, Any]) -> _T: ...


__all__ = [
    "FinancialRecord",
    "FundRecord",
    "BondRecord",
    "NewsRecord",
    "ResearchRecord",
    "OptionRecord",
    "MarketDataRecord",
    "SearchRecord",
    "MacroRecord",
]


def _clean_none(values: dict[str, Any]) -> dict[str, Any]:
    """去掉 None 字段，保持输出与历史 list[dict] 兼容。"""
    return {k: v for k, v in values.items() if v is not None}


# --------------------------------------------------------------------------- #
# Financial 财务
# --------------------------------------------------------------------------- #
@dataclass(slots=True)
class FinancialRecord:
    """财务数据记录（balance_sheet / income_sheet / cash_flow 等）。"""

    code: str = ""
    #: 报告期 "YYYY-MM-DD"
    report_date: str = ""
    #: 指标名 -> 数值/字符串
    metrics: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = _clean_none({"code": self.code, "report_date": self.report_date})
        d.update(self.metrics)
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> FinancialRecord:
        return cls(
            code=str(d.get("code", "")),
            report_date=str(d.get("report_date", "")),
            metrics={k: v for k, v in d.items() if k not in ("code", "report_date")},
        )


@dataclass(slots=True)
class DividendRecord:
    """分红记录。"""

    code: str = ""
    date: str = ""
    #: 每 10 股派息（元）
    dividend: float = 0.0
    #: 送转比例（每 10 股）
    bonus_ratio: float = 0.0
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = _clean_none(
            {
                "code": self.code,
                "date": self.date,
                "dividend": self.dividend,
                "bonus_ratio": self.bonus_ratio,
            }
        )
        d.update(self.extra)
        return d


# --------------------------------------------------------------------------- #
# Fund 基金
# --------------------------------------------------------------------------- #
@dataclass(slots=True)
class FundRecord:
    """基金基础信息 / 持仓 / 排名记录。"""

    code: str = ""
    name: str = ""
    #: 基金类型（东财 fund_type 数值）
    fund_type: int = 0
    metrics: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = _clean_none({"code": self.code, "name": self.name})
        if self.fund_type:
            d["fund_type"] = self.fund_type
        d.update(self.metrics)
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> FundRecord:
        return cls(
            code=str(d.get("code", "")),
            name=str(d.get("name", "")),
            fund_type=int(d.get("fund_type", 0) or 0),
            metrics={k: v for k, v in d.items() if k not in ("code", "name", "fund_type")},
        )


# --------------------------------------------------------------------------- #
# Bond 债券
# --------------------------------------------------------------------------- #
@dataclass(slots=True)
class BondRecord:
    """债券行情 / 成交记录。"""

    code: str = ""
    name: str = ""
    #: 债券类型（convertible/rets/treasury/corporate ...）
    bond_type: str = ""
    price: float = 0.0
    #: 成交收益率等扩展指标
    metrics: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = _clean_none(
            {
                "code": self.code,
                "name": self.name,
                "bond_type": self.bond_type,
                "price": self.price,
            }
        )
        d.update(self.metrics)
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> BondRecord:
        keys = {"code", "name", "bond_type", "price"}
        return cls(
            code=str(d.get("code", "")),
            name=str(d.get("name", "")),
            bond_type=str(d.get("bond_type", "")),
            price=float(d.get("price", 0.0) or 0.0),
            metrics={k: v for k, v in d.items() if k not in keys},
        )


# --------------------------------------------------------------------------- #
# News 新闻 / Research 研报
# --------------------------------------------------------------------------- #
@dataclass(slots=True)
class NewsRecord:
    """财经新闻记录。"""

    id: str = ""
    title: str = ""
    #: 来源（eastmoney/sina/...）
    source: str = ""
    #: 发布时间 "YYYY-MM-DD HH:MM:SS"
    time: str = ""
    url: str = ""
    summary: str = ""
    #: 关联标的代码（如有）
    symbol: str = ""

    def to_dict(self) -> dict[str, Any]:
        return _clean_none(
            {
                "id": self.id,
                "title": self.title,
                "source": self.source,
                "time": self.time,
                "url": self.url,
                "summary": self.summary,
                "symbol": self.symbol,
            }
        )

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> NewsRecord:
        return cls(**{k: str(d.get(k, "")) for k in cls.__dataclass_fields__})


@dataclass(slots=True)
class ResearchRecord:
    """研报记录（research_reports / research_visits）。"""

    code: str = ""
    title: str = ""
    #: 机构名称
    org: str = ""
    #: 分析师
    analyst: str = ""
    #: 评级（买入/增持/中性/减持/卖出）
    rating: str = ""
    #: 报告日期
    date: str = ""
    #: 目标价（如有）
    target_price: float | None = None
    summary: str = ""

    def to_dict(self) -> dict[str, Any]:
        return _clean_none(
            {
                "code": self.code,
                "title": self.title,
                "org": self.org,
                "analyst": self.analyst,
                "rating": self.rating,
                "date": self.date,
                "target_price": self.target_price,
                "summary": self.summary,
            }
        )

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ResearchRecord:
        return cls(
            code=str(d.get("code", "")),
            title=str(d.get("title", "")),
            org=str(d.get("org", "")),
            analyst=str(d.get("analyst", "")),
            rating=str(d.get("rating", "")),
            date=str(d.get("date", "")),
            target_price=float(d["target_price"]) if d.get("target_price") is not None else None,
            summary=str(d.get("summary", "")),
        )


# --------------------------------------------------------------------------- #
# Option 期权
# --------------------------------------------------------------------------- #
@dataclass(slots=True)
class OptionRecord:
    """期权快照 / 列表记录。"""

    code: str = ""
    name: str = ""
    #: 期权类型（call/put）
    option_type: str = ""
    #: 行权价（元）
    strike: float = 0.0
    #: 到期日 "YYYY-MM-DD"
    expiry: str = ""
    price: float = 0.0
    #: 希腊字母等扩展指标
    metrics: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = _clean_none(
            {
                "code": self.code,
                "name": self.name,
                "option_type": self.option_type,
                "strike": self.strike,
                "expiry": self.expiry,
                "price": self.price,
            }
        )
        d.update(self.metrics)
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> OptionRecord:
        keys = {"code", "name", "option_type", "strike", "expiry", "price"}
        return cls(
            code=str(d.get("code", "")),
            name=str(d.get("name", "")),
            option_type=str(d.get("option_type", "")),
            strike=float(d.get("strike", 0.0) or 0.0),
            expiry=str(d.get("expiry", "")),
            price=float(d.get("price", 0.0) or 0.0),
            metrics={k: v for k, v in d.items() if k not in keys},
        )


# --------------------------------------------------------------------------- #
# MarketData 行情衍生
# --------------------------------------------------------------------------- #
@dataclass(slots=True)
class MarketDataRecord:
    """行情衍生数据（hot_rank / limit_pool / northbound / margin ...）。"""

    #: 指标类型（hot_rank/limit_pool/northbound/...）
    kind: str = ""
    code: str = ""
    name: str = ""
    metrics: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = _clean_none({"kind": self.kind, "code": self.code, "name": self.name})
        d.update(self.metrics)
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> MarketDataRecord:
        keys = {"kind", "code", "name"}
        return cls(
            kind=str(d.get("kind", "")),
            code=str(d.get("code", "")),
            name=str(d.get("name", "")),
            metrics={k: v for k, v in d.items() if k not in keys},
        )


# --------------------------------------------------------------------------- #
# Search 搜索 / Macro 宏观
# --------------------------------------------------------------------------- #
@dataclass(slots=True)
class SearchRecord:
    """搜索/选股结果（wencai / screening / suggest ...）。"""

    code: str = ""
    name: str = ""
    #: 匹配信息摘要（自然语言提问时）
    summary: str = ""
    metrics: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = _clean_none({"code": self.code, "name": self.name, "summary": self.summary})
        d.update(self.metrics)
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> SearchRecord:
        keys = {"code", "name", "summary"}
        return cls(
            code=str(d.get("code", "")),
            name=str(d.get("name", "")),
            summary=str(d.get("summary", "")),
            metrics={k: v for k, v in d.items() if k not in keys},
        )


@dataclass(slots=True)
class MacroRecord:
    """宏观/全球数据（fx_rates / global_quotes ...）。"""

    #: 指标类型（fx_rates/index_quote/...）
    kind: str = ""
    #: 标的标识（USDCNY / INDEX_HSI / ...）
    key: str = ""
    name: str = ""
    value: Any = None
    metrics: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = _clean_none(
            {"kind": self.kind, "key": self.key, "name": self.name, "value": self.value}
        )
        d.update(self.metrics)
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> MacroRecord:
        keys = {"kind", "key", "name", "value"}
        return cls(
            kind=str(d.get("kind", "")),
            key=str(d.get("key", "")),
            name=str(d.get("name", "")),
            value=d.get("value"),
            metrics={k: v for k, v in d.items() if k not in keys},
        )


def as_record_list(items: list[dict[str, Any]], record_cls: type[_RecordFactory]) -> list[Any]:
    """批量把 list[dict] 业务结果转换为 Domain Record 列表。"""
    return [record_cls.from_dict(d) for d in items]


def record_to_dicts(records: list[Any]) -> list[dict[str, Any]]:
    """批量把 Domain Record 列表还原为 list[dict]（序列化兼容）。"""
    return [r.to_dict() for r in records]


def normalize_to_records(data: Any, record_cls: type[_RecordFactory]) -> list[Any]:
    """统一把 dict / list[dict] / Record 归一化为 Record 列表。"""
    if isinstance(data, record_cls):
        return [data]
    if isinstance(data, dict):
        return [record_cls.from_dict(data)]
    if isinstance(data, list):
        return [record_cls.from_dict(d) if isinstance(d, dict) else d for d in data]
    return []
