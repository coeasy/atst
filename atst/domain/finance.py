# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""财务 / 除权除息数据语义层（§9 扩展）。

**定位**：把「结构化但无语义」的财务数值数组（本地 ``gpcw*.dat``、
在线 0x0010 FINANCE_INFO）映射为带字段名的字典，并提供
:class:`~atst.domain.models.CapitalChange` 的统一构造入口
（供 :mod:`atst.domain.adjust` 复权引擎消费）。

两条数据链路（F1 打通）
------------------------
1. **本地 ``gpcw*.dat``**（:class:`~atst.reader.formats.FinanceReader`）：
   float32 扁平数组，字段序由通达信 gpcw.txt 规定。见
   :data:`GPCW_FIELD_NAMES`。
2. **在线 0x0010 FINANCE_INFO**（:class:`~atst.client.TdxClient.finance_info`）：
   记录 = 1B 市场 + 6B 代码 + float32 数组。见 :data:`FINANCE_INFO_FIELDS`。

.. warning::
   ``FINANCE_INFO_FIELDS`` 为**尽力而为**映射：0x0010 字段顺序依赖主站
   版本，公开资料与实测尚未以 golden 样本锁定（协议解析器标注 L2）。
   字段序在部分主站可能偏移——取值时应结合业务常识校验，勿盲信单一
   字段。gpcw 字段序相对稳定（多年未变），置信度更高。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from .models import CapitalChange

__all__ = [
    "GPCW_FIELD_NAMES",
    "FINANCE_INFO_FIELDS",
    "map_finance_values",
    "to_capital_changes",
    "capital_changes_from_dividends",
    "capital_changes_from_dividends_and_rights",
    "reject_implausible_changes",
]

# --------------------------------------------------------------------------- #
# gpcw*.dat 字段序（通达信 gpcw.txt 约定，多年稳定）
# --------------------------------------------------------------------------- #
GPCW_FIELD_NAMES: dict[int, str] = {
    0: "total_shares",  # 总股本（万股）
    1: "state_shares",  # 国家股
    2: "promoter_legal_shares",  # 发起人法人股
    3: "legal_shares",  # 法人股
    4: "b_shares",  # B 股
    5: "h_shares",  # H 股
    6: "employee_shares",  # 职工股
    7: "total_assets",  # 总资产
    8: "current_assets",  # 流动资产
    9: "fixed_assets",  # 固定资产
    10: "intangible_assets",  # 无形资产
    11: "shareholder_count",  # 股东人数
    12: "current_liabilities",  # 流动负债
    13: "total_liabilities",  # 负债合计
    14: "long_term_liabilities",  # 长期负债
    15: "capital_reserve",  # 资本公积
    16: "net_assets",  # 净资产
    17: "operating_revenue",  # 主营业务收入
    18: "operating_profit",  # 主营业务利润
    19: "total_profit",  # 利润总额
    20: "net_profit",  # 净利润
    21: "retained_earnings",  # 未分配利润
    22: "net_assets_per_share",  # 每股净资产
    23: "capital_reserve_per_share",  # 每股公积金
    24: "eps",  # 每股收益
    25: "retained_earnings_per_share",  # 每股未分配利润
    26: "operating_cashflow_per_share",  # 每股经营现金流
    # 索引 >= 27 的扩展字段因通达信版本而异，按 f{n} 保留
}


# --------------------------------------------------------------------------- #
# 0x0010 FINANCE_INFO 字段序（尽力而为，L2 待样本锁定）
# --------------------------------------------------------------------------- #
FINANCE_INFO_FIELDS: dict[int, str] = {
    0: "float_shares",  # 流通股本
    1: "total_shares",  # 总股本
    2: "net_assets_per_share",  # 每股净资产
    3: "capital_reserve_per_share",  # 每股公积金
    4: "retained_earnings_per_share",  # 每股未分配利润
    5: "eps",  # 每股收益
    6: "profit_per_share",  # 每股利润
    7: "operating_cashflow_per_share",  # 每股经营现金流
    8: "operating_revenue",  # 主营收入
    9: "operating_profit",  # 主营利润
    10: "total_profit",  # 利润总额
    11: "net_profit",  # 净利润
    12: "retained_earnings",  # 未分配利润
    13: "shareholder_count",  # 股东人数
    14: "total_assets",  # 总资产
    15: "current_assets",  # 流动资产
    16: "fixed_assets",  # 固定资产
    17: "total_liabilities",  # 总负债
    18: "current_liabilities",  # 流动负债
    19: "long_term_liabilities",  # 长期负债
    20: "capital_reserve",  # 资本公积
    21: "net_assets",  # 净资产
    22: "cash",  # 货币资金
    23: "notes_receivable",  # 应收票据
    24: "inventory",  # 存货
    25: "roe",  # 净资产收益率（%）
    26: "operating_margin",  # 主营业务利润率（%）
    # 索引 >= 27 的扩展字段因主站版本而异，按 f{n} 保留
}


def map_finance_values(
    values: Sequence[float],
    field_map: Mapping[int, str] = GPCW_FIELD_NAMES,
    *,
    drop_zero: bool = False,
) -> dict[str, float]:
    """把无语义数值数组映射为带字段名的字典。

    Parameters
    ----------
    values:
        float32 数值数组（gpcw 记录或 0x0010 记录的 value 切片）。
    field_map:
        字段序映射；未覆盖的索引保留为 ``f{n}``。
    drop_zero:
        True 时丢弃值为 0 的字段（减少噪音），默认保留（字段序完整性）。

    Returns
    -------
    dict[str, float]
        字段名 → 数值（``round(v, 6)``）。
    """
    out: dict[str, float] = {}
    for i, v in enumerate(values):
        if drop_zero and not v:
            continue
        name = field_map.get(i, f"f{i}")
        out[name] = round(float(v), 6)
    return out


def to_capital_changes(rows: Iterable[Mapping[str, Any]]) -> list[CapitalChange]:
    """把除权除息解析行（0x000F 或等价 dict）统一构造为
    :class:`~atst.domain.models.CapitalChange`（F1：adjust 复权引擎的直接输入）。

    容忍缺字段（如 record_size < 29 时无 dividend/rights 字段），缺省为 0。
    """
    out: list[CapitalChange] = []
    for row in rows:
        out.append(
            CapitalChange(
                code=str(row.get("code", "")),
                market=int(row.get("market", 0)),
                category=int(row.get("category", 0)),
                category_name=row.get("category_name", ""),
                date=row.get("date", ""),
                dividend=row.get("dividend", 0.0),
                rights_price=row.get("rights_price", 0.0),
                bonus_ratio=row.get("bonus_ratio", 0.0),
                rights_ratio=row.get("rights_ratio", 0.0),
            )
        )
    return out


def capital_changes_from_dividends(
    rows: Iterable[Mapping[str, Any]],
    *,
    code: str = "",
    market: int = 0,
) -> list[CapitalChange]:
    """只要分红送转、不要配股时的入口（:func:`capital_changes_from_dividends_and_rights`
    的 ``rights_rows=()`` 特例）。

    **它就是那个合并且函数的薄壳**，自身不含任何比例映射逻辑——两条独立的实现迟早会
    在"同日多行要不要并成一条"这类口径上分叉，而分叉出来的那半边不会有人发现。

    为什么需要第二条入口：``0x000F``（TDX 除权除息）的记录布局**至今没有真机 golden
    锁定**（``atst/protocol/parsers/std7709.py`` 标 ``待样本锁定``），实测解码出来的
    ``market``/``code``/``date`` 是错位值，直接喂给复权引擎会得到一条
    ``无法解析日期`` 的错误、或者更糟——一组碰巧能解析但完全错误的因子。东财
    ``RPT_SHAREBONUS_DET`` 是已实测可用的源，字段名与语义都对拍过，因此它才是复权的
    **默认事件源**。

    注意：本函数产出的 ``rights_ratio`` / ``rights_price`` 恒为 0——分红送转表里没有
    配股列。要完整的除权除息事件集（配股标的的 ``Pr·R`` 项不能丢）请用
    :func:`capital_changes_from_dividends_and_rights`。
    """
    return capital_changes_from_dividends_and_rights(rows, (), code=code, market=market)


def reject_implausible_changes(events: Sequence[Any]) -> None:
    """复权事件的硬闸：布局错位的记录不许进入复权引擎。

    ``0x000F`` 解码错位时的形状是稳定的：``market`` 落在 TDX 二进制市场编号
    ``[0, 1, 2]`` 之外、``code`` 带控制字符或为空、``date`` 不是 ``YYYY-MM-DD``
    而是某个被当成整数读过的大数（如 ``'1082130432'``）。这些值即使碰巧能被
    :func:`~atst.domain.adjust` 的日期解析吞下，也只会算出一组错误的因子——
    **错位的复权比不复权更危险**，因为它长得完全正常。

    所以这里就地判死，并且把话说清楚：布局未锁定的命令不要拿来复权，改用东财源。
    """
    from ..errors import ValidationError

    bad: list[dict[str, Any]] = []
    for index, event in enumerate(events):
        reasons: list[str] = []
        if int(getattr(event, "market", 0)) not in (0, 1, 2):
            reasons.append(f"market={getattr(event, 'market', None)} 不是 TDX 市场编号 [0,1,2]")
        code = str(getattr(event, "code", "") or "")
        if not code or not code.isprintable() or not code.isascii():
            reasons.append(f"code={code!r} 不是可见 ASCII 代码")
        date = str(getattr(event, "date", "") or "")
        if not _is_day(date):
            reasons.append(f"date={date!r} 不是 YYYY-MM-DD")
        if reasons:
            bad.append({"index": index, "reasons": reasons})
    if not bad:
        return
    raise ValidationError(
        "除权除息记录布局不可信：TDX 0x000F 的记录布局尚未由真机 golden 锁定，"
        f"{len(bad)}/{len(events)} 条记录的字段值落在库自己声明的取值域之外。"
        "用它们复权会得到错误的因子，因此当场拒绝而不是算出一个看起来正常的结果。"
        "请改用 event_source='eastmoney'（默认），或直接传 events=",
        context={
            "phase": "adjust_event_validation",
            "command": "0x000F",
            "bad_records": bad[:5],
            "bad_count": len(bad),
        },
    )


def capital_changes_from_dividends_and_rights(
    dividend_rows: Iterable[Mapping[str, Any]],
    rights_rows: Iterable[Mapping[str, Any]] = (),
    *,
    code: str = "",
    market: int = 0,
) -> list[CapitalChange]:
    """把东财两路公司行动合成**每个除权日一条** :class:`CapitalChange`。

    为什么不直接把配股表的结果 append 在分红表后面：除权参考价
    ``P_ref = (P - D + Pr·R) / (1 + S + R)`` 是一个**整体**公式。同一天既有送转派息
    又有配股时（``10送2配3派1`` 这类组合方案很常见），拆成两条事件各自算一次再连乘，

        1/k = P(1+S)/(P-D) · P(1+R)/(P+Pr·R)

    而正确值是 ``P(1+S+R)/(P-D+Pr·R)``——分母的交叉项 ``S·R`` 丢了，因子系统性偏大。
    所以按 ``ex_dividend_date`` 归并，一天只产出一条事件。

    两路来源的分工（实测，2026-10-07）：

    * ``dividend_rows`` —— ``RPT_SHAREBONUS_DET``：送股 / 转增 / 派息；
    * ``rights_rows`` —— ``RPT_IPO_ALLOTMENT``：配股比例与配股价。那张分红表
      **一条配股都没有**（工行 / 中行 / 招行 / 浦发 / 兴业 2010 年集体配股，表里
      0 条含"配"的方案），配股只能走独立报表。
    """
    merged: dict[str, dict[str, float]] = {}
    codes: dict[str, str] = {}

    def slot(day: str, row: Mapping[str, Any]) -> dict[str, float]:
        rec = merged.setdefault(
            day,
            {"dividend": 0.0, "bonus": 0.0, "rights_ratio": 0.0, "rights_price": 0.0},
        )
        if not codes.get(day):
            codes[day] = str(row.get("code") or code)
        return rec

    for row in dividend_rows:
        date = _first_text(row, "ex_dividend_date", "EX_DIVIDEND_DATE")
        if not date:
            continue
        rec = slot(_normalize_day(date), row)
        rec["bonus"] += _as_float(_first(row, "bonus_shares_per_10", "BONUS_RATIO")) + _as_float(
            _first(row, "transfer_shares_per_10", "IT_RATIO")
        )
        rec["dividend"] += _as_float(_first(row, "cash_dividend_per_10", "PRETAX_BONUS_RMB"))

    for row in rights_rows:
        date = _first_text(row, "ex_dividend_date", "EX_DIVIDEND_DATE")
        if not date:
            continue
        rec = slot(_normalize_day(date), row)
        rec["rights_ratio"] += _as_float(_first(row, "rights_ratio_per_10", "PLACING_RATIO"))
        price = _as_float(_first(row, "rights_price", "ISSUE_PRICE"))
        if price > 0:
            rec["rights_price"] = price

    out: list[CapitalChange] = []
    for day in sorted(merged):
        rec = merged[day]
        out.append(
            CapitalChange(
                code=codes.get(day) or code,
                market=market,
                category=1,
                category_name="除权除息",
                date=day,
                dividend=rec["dividend"],
                rights_price=rec["rights_price"],
                bonus_ratio=rec["bonus"],
                rights_ratio=rec["rights_ratio"],
            )
        )
    return out


def _first(row: Mapping[str, Any], *names: str) -> Any:
    for name in names:
        if name in row and row[name] is not None:
            return row[name]
    return None


def _first_text(row: Mapping[str, Any], *names: str) -> str:
    value = _first(row, *names)
    return str(value).strip() if value is not None else ""


def _as_float(value: Any) -> float:
    if value is None:
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _normalize_day(value: str) -> str:
    """``'2026-06-26 00:00:00'`` → ``'2026-06-26'``（东财日期带时间尾巴）。"""
    return value.split(" ")[0].split("T")[0]


def _is_day(value: str) -> bool:
    """只认 ``YYYY-MM-DD``，且必须是个真实存在的日期。
    ``_normalize_day`` 已经把时间尾巴切掉了，所以这里不需要再容忍 ``'... 00:00:00'``。
    """
    if len(value) != 10 or value[4] != "-" or value[7] != "-":
        return False
    head, mid, tail = value[:4], value[5:7], value[8:10]
    if not (head.isdigit() and mid.isdigit() and tail.isdigit()):
        return False
    from datetime import date

    try:
        date(int(head), int(mid), int(tail))
    except ValueError:
        return False
    return True
