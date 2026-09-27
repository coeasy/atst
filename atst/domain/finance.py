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
