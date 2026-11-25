# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""v14 领域业务 Record（Phase 2 Domain Model）。

目标：用类型化 Domain Record 替换 web/session 层的 ``list[dict]`` 业务结果。
每个 Record 与 ``atst.typed_query`` 中的 Typed Query 契约一一对应，
并可通过 ``to_dict`` / ``from_dict`` 在 Runtime 边界做无损序列化。

P2-5b：九个 Record 原本各手写一份近乎相同的 ``to_dict`` / ``from_dict``
（``_clean_none`` + metrics 合并 + 键集过滤），现收敛进
:class:`_SerializableRecord` 声明式基类——子类只声明标量字段集
（字段名 + 类型转换 + 缺省值）与 metrics 键，序列化行为全部在基类，
输出键序与键集与旧手写版逐字一致。

单位约定（继承 :mod:`atst.domain.models`）::

    价格/金额    元（float）
    成交量       股（int）
    日期         "YYYY-MM-DD"（本地字符串）
    时间         "HH:MM:SS"（本地字符串）
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, ClassVar, Protocol, TypeVar

_T = TypeVar("_T")
_SelfT = TypeVar("_SelfT", bound="_SerializableRecord")


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
# 标量字段转换器（from_dict 的键缺失缺省值由声明项自带）
# --------------------------------------------------------------------------- #
def _as_str(value: Any) -> Any:
    return str(value)


def _as_int(value: Any) -> Any:
    return int(value or 0)


def _as_float(value: Any) -> Any:
    return float(value or 0.0)


def _as_opt_float(value: Any) -> Any:
    return float(value) if value is not None else None


def _as_raw(value: Any) -> Any:
    return value


# --------------------------------------------------------------------------- #
# 序列化基类（P2-5b）
# --------------------------------------------------------------------------- #
class _SerializableRecord:
    """``to_dict`` / ``from_dict`` 同构样板的声明式基类。

    子类以类属性声明契约，序列化行为全部收进本基类：

    * ``_SCALARS``：标量字段集，``(字段名, 转换器, 缺省值)`` 三元组，按声明
      顺序输出；缺省值供 ``from_dict`` 在键缺失时取用。
    * ``_METRICS_FIELD``：扩展指标字典字段名（``None`` 表示无 metrics）——
      ``to_dict`` 把它合并进标量之后，``from_dict`` 把剩余键全部收进它
      （键集过滤语义与旧手写版一致）。
    * ``_STRIP_FALSY``：除 ``None`` 外还要按 falsy 剥离的标量字段
      （如 ``FundRecord.fund_type`` 的 0 不输出），保持输出键集不变。

    注意 ``str`` 转换器对显式 ``None`` 值产 ``"None"``、对键缺失产 ``""``：
    这是旧手写版 ``str(d.get(k, ""))`` 的既有行为，本基类原样保留。
    """

    __slots__ = ()

    _SCALARS: ClassVar[tuple[tuple[str, Callable[[Any], Any], Any], ...]] = ()
    _METRICS_FIELD: ClassVar[str | None] = None
    _STRIP_FALSY: ClassVar[tuple[str, ...]] = ()

    def to_dict(self) -> dict[str, Any]:
        d = _clean_none({name: getattr(self, name) for name, _, _ in self._SCALARS})
        for name in self._STRIP_FALSY:
            if not d.get(name):
                d.pop(name, None)
        if self._METRICS_FIELD is not None:
            d.update(getattr(self, self._METRICS_FIELD))
        return d

    @classmethod
    def from_dict(cls: type[_SelfT], d: dict[str, Any]) -> _SelfT:
        kwargs = {name: cast(d.get(name, default)) for name, cast, default in cls._SCALARS}
        metrics_field = cls._METRICS_FIELD
        if metrics_field is None:
            return cls(**kwargs)
        keys = {name for name, _, _ in cls._SCALARS} | {metrics_field}
        kwargs[metrics_field] = {k: v for k, v in d.items() if k not in keys}
        return cls(**kwargs)


# --------------------------------------------------------------------------- #
# Financial 财务
# --------------------------------------------------------------------------- #
@dataclass(slots=True)
class FinancialRecord(_SerializableRecord):
    """财务数据记录（balance_sheet / income_sheet / cash_flow 等）。"""

    code: str = ""
    #: 报告期 "YYYY-MM-DD"
    report_date: str = ""
    #: 指标名 -> 数值/字符串
    metrics: dict[str, Any] = field(default_factory=dict)

    _SCALARS = (("code", _as_str, ""), ("report_date", _as_str, ""))
    _METRICS_FIELD = "metrics"


# --------------------------------------------------------------------------- #
# Fund 基金
# --------------------------------------------------------------------------- #
@dataclass(slots=True)
class FundRecord(_SerializableRecord):
    """基金基础信息 / 持仓 / 排名记录。"""

    code: str = ""
    name: str = ""
    #: 基金类型（东财 fund_type 数值）
    fund_type: int = 0
    metrics: dict[str, Any] = field(default_factory=dict)

    _SCALARS = (("code", _as_str, ""), ("name", _as_str, ""), ("fund_type", _as_int, 0))
    _METRICS_FIELD = "metrics"
    #: 旧实现 ``if self.fund_type:`` —— fund_type 为 0 时不输出
    _STRIP_FALSY = ("fund_type",)


# --------------------------------------------------------------------------- #
# Bond 债券
# --------------------------------------------------------------------------- #
@dataclass(slots=True)
class BondRecord(_SerializableRecord):
    """债券行情 / 成交记录。"""

    code: str = ""
    name: str = ""
    #: 债券类型（convertible/rets/treasury/corporate ...）
    bond_type: str = ""
    price: float = 0.0
    #: 成交收益率等扩展指标
    metrics: dict[str, Any] = field(default_factory=dict)

    _SCALARS = (
        ("code", _as_str, ""),
        ("name", _as_str, ""),
        ("bond_type", _as_str, ""),
        ("price", _as_float, 0.0),
    )
    _METRICS_FIELD = "metrics"


# --------------------------------------------------------------------------- #
# News 新闻 / Research 研报
# --------------------------------------------------------------------------- #
@dataclass(slots=True)
class NewsRecord(_SerializableRecord):
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

    _SCALARS = (
        ("id", _as_str, ""),
        ("title", _as_str, ""),
        ("source", _as_str, ""),
        ("time", _as_str, ""),
        ("url", _as_str, ""),
        ("summary", _as_str, ""),
        ("symbol", _as_str, ""),
    )


@dataclass(slots=True)
class ResearchRecord(_SerializableRecord):
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

    _SCALARS = (
        ("code", _as_str, ""),
        ("title", _as_str, ""),
        ("org", _as_str, ""),
        ("analyst", _as_str, ""),
        ("rating", _as_str, ""),
        ("date", _as_str, ""),
        ("target_price", _as_opt_float, None),
        ("summary", _as_str, ""),
    )


# --------------------------------------------------------------------------- #
# Option 期权
# --------------------------------------------------------------------------- #
@dataclass(slots=True)
class OptionRecord(_SerializableRecord):
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

    _SCALARS = (
        ("code", _as_str, ""),
        ("name", _as_str, ""),
        ("option_type", _as_str, ""),
        ("strike", _as_float, 0.0),
        ("expiry", _as_str, ""),
        ("price", _as_float, 0.0),
    )
    _METRICS_FIELD = "metrics"


# --------------------------------------------------------------------------- #
# MarketData 行情衍生
# --------------------------------------------------------------------------- #
@dataclass(slots=True)
class MarketDataRecord(_SerializableRecord):
    """行情衍生数据（hot_rank / limit_pool / northbound / margin ...）。"""

    #: 指标类型（hot_rank/limit_pool/northbound/...）
    kind: str = ""
    code: str = ""
    name: str = ""
    metrics: dict[str, Any] = field(default_factory=dict)

    _SCALARS = (("kind", _as_str, ""), ("code", _as_str, ""), ("name", _as_str, ""))
    _METRICS_FIELD = "metrics"


# --------------------------------------------------------------------------- #
# Search 搜索 / Macro 宏观
# --------------------------------------------------------------------------- #
@dataclass(slots=True)
class SearchRecord(_SerializableRecord):
    """搜索/选股结果（wencai / screening / suggest ...）。"""

    code: str = ""
    name: str = ""
    #: 匹配信息摘要（自然语言提问时）
    summary: str = ""
    metrics: dict[str, Any] = field(default_factory=dict)

    _SCALARS = (("code", _as_str, ""), ("name", _as_str, ""), ("summary", _as_str, ""))
    _METRICS_FIELD = "metrics"


@dataclass(slots=True)
class MacroRecord(_SerializableRecord):
    """宏观/全球数据（fx_rates / global_quotes ...）。"""

    #: 指标类型（fx_rates/index_quote/...）
    kind: str = ""
    #: 标的标识（USDCNY / INDEX_HSI / ...）
    key: str = ""
    name: str = ""
    value: Any = None
    metrics: dict[str, Any] = field(default_factory=dict)

    _SCALARS = (
        ("kind", _as_str, ""),
        ("key", _as_str, ""),
        ("name", _as_str, ""),
        ("value", _as_raw, None),
    )
    _METRICS_FIELD = "metrics"


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
