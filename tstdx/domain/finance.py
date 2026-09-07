# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""财务 / 除权除息数据语义层（§9 扩展）。

**定位**：把「结构化但无语义」的财务数值数组（本地 ``gpcw*.dat``、
在线 0x0010 FINANCE_INFO）映射为带字段名的字典，并提供
:class:`~tstdx.domain.models.CapitalChange` 的统一构造入口
（供 :mod:`tstdx.domain.adjust` 复权引擎消费）。

两条数据链路（F1 打通）
------------------------
1. **本地 ``gpcw*.dat``**（:class:`~tstdx.reader.formats.FinanceReader`）：
   float32 扁平数组，字段序由通达信 gpcw.txt 规定。见
   :data:`GPCW_FIELD_NAMES`。
2. **在线 0x0010 FINANCE_INFO**（:class:`~tstdx.client.TdxClient.finance_info`）：
   记录 = 1B 市场 + 6B 代码 + float32 数组。见 :data:`FINANCE_INFO_FIELDS`。

.. warning::
   ``FINANCE_INFO_FIELDS`` 为**尽力而为**映射：0x0010 字段顺序依赖主站
   版本，公开资料与实测尚未以 golden 样本锁定（协议解析器标注 L2）。
   字段序在部分主站可能偏移——取值时应结合业务常识校验，勿盲信单一
   字段。gpcw 字段序相对稳定（多年未变），置信度更高。
"""

from __future__ import annotations

import contextlib
import json
import os
import time
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict
from typing import Any

from .models import CapitalChange

__all__ = [
    "GPCW_FIELD_NAMES",
    "FINANCE_INFO_FIELDS",
    "map_finance_values",
    "to_capital_changes",
    "CapitalChangeCache",
    "get_capital_change_cache",
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
    :class:`~tstdx.domain.models.CapitalChange`（F1：adjust 复权引擎的直接输入）。

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


# --------------------------------------------------------------------------- #
# 除权除息事件 TTL 缓存（N4）
# --------------------------------------------------------------------------- #
#: 落盘缓存目录环境变量（测试 / 部署可覆盖，缺省 ~/.tstdx/factors）。
ENV_FACTOR_CACHE_DIR = "TSTDX_FACTOR_CACHE_DIR"
#: 默认 TTL：除权事件为低频数据（仅在除权日更新），24h 内复用安全。
DEFAULT_FACTOR_TTL_SECONDS = 24 * 3600
_FETCHED_AT_KEY = "_fetched_at"


class CapitalChangeCache:
    """除权除息事件 TTL 缓存（进程级内存 + 可选落盘，N4/I2 落地）。

    * 进程级：``{symbol: (fetched_at, events)}``，命中（未过期且非空）直接
      返回，跳过 0x0010/gpcw 网络与解析；
    * 可选落盘：``disk_dir/{symbol}.json``（含 ``_fetched_at`` 时间戳），
      离线复用，跨进程共享；
    * 降级：TTL 过期 / JSON 损坏 / 缺失字段 → 返回 ``None`` 并（对损坏文件）
      删除后重取，绝不因缓存故障阻塞主链路。

    返回值为重建的 :class:`~tstdx.domain.models.CapitalChange` 列表（深拷贝），
    调用方修改不污染缓存。
    """

    def __init__(
        self, ttl: float = DEFAULT_FACTOR_TTL_SECONDS, disk_dir: str | None = None
    ) -> None:
        self.ttl = ttl
        self.disk_dir = disk_dir
        self._mem: dict[str, tuple[float, list[CapitalChange]]] = {}

    # -- 读写 ---------------------------------------------------------------- #
    def get(self, symbol: str) -> list[CapitalChange] | None:
        """命中返回事件列表（深拷贝），未命中 / 过期 / 损坏返回 ``None``。"""
        hit = self._mem.get(symbol)
        if hit is not None:
            ts, events = hit
            if events and time.time() - ts <= self.ttl:
                return self._clone(events)
            self._mem.pop(symbol, None)
        return self._load_disk(symbol)

    def put(self, symbol: str, events: Sequence[CapitalChange]) -> None:
        """写入缓存并落盘。空事件列表不缓存（避免永久屏蔽后续真实查询）。"""
        rows = [e for e in events if isinstance(e, CapitalChange)]
        if not rows:
            return
        self._mem[symbol] = (time.time(), rows)
        self._save_disk(symbol, rows)

    def clear(self, symbol: str | None = None) -> None:
        """清空全部（symbol=None）或单个 symbol 的缓存（内存 + 落盘）。"""
        if symbol is None:
            self._mem.clear()
            if self.disk_dir and os.path.isdir(self.disk_dir):
                for name in os.listdir(self.disk_dir):
                    if name.endswith(".json"):
                        with contextlib.suppress(OSError):
                            os.remove(os.path.join(self.disk_dir, name))
            return
        self._mem.pop(symbol, None)
        if self.disk_dir:
            with contextlib.suppress(OSError):
                os.remove(self._disk_path(symbol))

    # -- 落盘 ---------------------------------------------------------------- #
    def _disk_path(self, symbol: str) -> str:
        return os.path.join(self.disk_dir, f"{symbol}.json")  # type: ignore[arg-type]

    def _load_disk(self, symbol: str) -> list[CapitalChange] | None:
        if not self.disk_dir:
            return None
        try:
            with open(self._disk_path(symbol), encoding="utf-8") as fh:
                raw = json.load(fh)
            fetched = float(raw.get(_FETCHED_AT_KEY, 0.0))
            if time.time() - fetched > self.ttl:
                return None
            rows = to_capital_changes(raw.get("events", []))
            if not rows:
                return None
            self._mem[symbol] = (time.time(), rows)
            return self._clone(rows)
        except (OSError, ValueError, TypeError, AttributeError):
            # 损坏 / 缺字段 → 删除降级重取
            self._discard_disk(symbol)
            return None

    def _save_disk(self, symbol: str, rows: Sequence[CapitalChange]) -> None:
        if not self.disk_dir:
            return
        try:
            os.makedirs(self.disk_dir, exist_ok=True)
            payload = {_FETCHED_AT_KEY: time.time(), "events": [asdict(e) for e in rows]}
            tmp = self._disk_path(symbol) + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, ensure_ascii=False)
            os.replace(tmp, self._disk_path(symbol))
        except OSError:
            # 落盘失败（只读目录 / 磁盘满）不阻断主链路
            pass

    def _discard_disk(self, symbol: str) -> None:
        if not self.disk_dir:
            return
        with contextlib.suppress(OSError):
            os.remove(self._disk_path(symbol))

    @staticmethod
    def _clone(events: Sequence[CapitalChange]) -> list[CapitalChange]:
        return [CapitalChange(**asdict(e)) for e in events]


def default_factor_cache_dir() -> str | None:
    """落盘目录：环境变量覆盖，缺省 ``~/.tstdx/factors``（不保证存在）。"""
    return os.environ.get(ENV_FACTOR_CACHE_DIR) or os.path.join(
        os.path.expanduser("~"), ".tstdx", "factors"
    )


_CAPITAL_CHANGE_CACHE: CapitalChangeCache | None = None


def get_capital_change_cache() -> CapitalChangeCache:
    """进程级单例（惰性创建）。测试请注入独立 ``CapitalChangeCache`` 实例，
    避免污染全局状态。"""
    global _CAPITAL_CHANGE_CACHE
    if _CAPITAL_CHANGE_CACHE is None:
        _CAPITAL_CHANGE_CACHE = CapitalChangeCache(disk_dir=default_factor_cache_dir())
    return _CAPITAL_CHANGE_CACHE
