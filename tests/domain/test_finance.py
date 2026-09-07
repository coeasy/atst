# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""F1：财务 / 除权除息语义层测试。

覆盖 :mod:`tstdx.domain.finance`：
* ``map_finance_values``：数值数组 → 带字段名字典（含未知索引 f{n} 兜底、drop_zero）；
* ``to_capital_changes``：0x000F 解析行 → :class:`CapitalChange`（缺字段容忍）；
* 客户端/解析器复用：``client._row_to_capital`` 委托同一转换器。
"""

from __future__ import annotations

import pytest

from tstdx.client import _row_to_capital
from tstdx.domain.finance import (
    FINANCE_INFO_FIELDS,
    GPCW_FIELD_NAMES,
    map_finance_values,
    to_capital_changes,
)
from tstdx.domain.models import CapitalChange

pytestmark = pytest.mark.unit


class TestMapFinanceValues:
    def test_known_fields_named(self) -> None:
        values = [100.0, 50.0, 0.0, 2.5]
        out = map_finance_values(values, GPCW_FIELD_NAMES)
        assert out["total_shares"] == 100.0
        assert out["state_shares"] == 50.0
        assert out["promoter_legal_shares"] == 0.0  # 零值默认保留
        assert out["legal_shares"] == 2.5

    def test_unknown_index_fallback(self) -> None:
        # 索引 27（gpcw 扩展字段）未被 GPCW_FIELD_NAMES 覆盖 → f27
        values = [0.0] * 28
        values[27] = 1.25
        out = map_finance_values(values, GPCW_FIELD_NAMES)
        assert out["f27"] == 1.25

    def test_drop_zero(self) -> None:
        out = map_finance_values([1.0, 0.0, 2.0], GPCW_FIELD_NAMES, drop_zero=True)
        assert "state_shares" not in out
        assert out["total_shares"] == 1.0

    def test_finance_info_fields_distinct_names(self) -> None:
        """0x0010 与 gpcw 字段序不同源（各自独立映射）。"""
        assert FINANCE_INFO_FIELDS[0] == "float_shares"
        assert GPCW_FIELD_NAMES[0] == "total_shares"

    def test_values_rounded_six(self) -> None:
        out = map_finance_values([1.0 / 3.0], GPCW_FIELD_NAMES)
        assert out["total_shares"] == pytest.approx(0.333333, abs=1e-6)


class TestToCapitalChanges:
    def test_full_row(self) -> None:
        rows = [
            {
                "code": "600519",
                "market": 1,
                "category": 1,
                "category_name": "除权除息",
                "date": "2024-06-03",
                "dividend": 30.0,
                "rights_price": 0.0,
                "bonus_ratio": 0.0,
                "rights_ratio": 0.0,
            }
        ]
        ev = to_capital_changes(rows)[0]
        assert isinstance(ev, CapitalChange)
        assert ev.code == "600519"
        assert ev.dividend == 30.0
        assert ev.date == "2024-06-03"

    def test_missing_fields_tolerated(self) -> None:
        """record_size < 29 时无 dividend/rights 字段 → 缺省 0（不炸）。"""
        ev = to_capital_changes([{"code": "000001", "date": "2023-01-01"}])[0]
        assert ev.dividend == 0.0
        assert ev.rights_price == 0.0
        assert ev.bonus_ratio == 0.0
        assert ev.rights_ratio == 0.0

    def test_client_row_to_capital_delegates(self) -> None:
        """client._row_to_capital 与共享转换器输出一致（单一事实源）。"""
        row = {
            "code": "600000",
            "market": 1,
            "category": 1,
            "date": "2024-06-03",
            "dividend": 10.0,
        }
        shared = to_capital_changes([row])[0]
        via_client = _row_to_capital(row)
        assert via_client.to_dict() == shared.to_dict()

    def test_to_dict_roundtrip(self) -> None:
        ev = to_capital_changes([{"code": "600519", "date": "2024-06-03", "dividend": 30.0}])[0]
        d = ev.to_dict()
        assert d["dividend"] == 30.0
        assert d["category"] == 0


class TestCapitalChangeCache:
    """N4：除权事件 TTL 缓存（内存命中 / TTL 过期 / 落盘往返 / 损坏降级）。"""

    def _ev(self, code: str = "600519", dividend: float = 30.0) -> CapitalChange:
        return to_capital_changes(
            [{"code": code, "market": 1, "date": "2024-06-03", "dividend": dividend}]
        )[0]

    def test_mem_hit_within_ttl(self) -> None:
        from tstdx.domain.finance import CapitalChangeCache

        c = CapitalChangeCache(ttl=3600)
        assert c.get("sh600519") is None  # miss
        c.put("sh600519", [self._ev()])
        hit = c.get("sh600519")
        assert hit is not None and len(hit) == 1
        assert hit[0].dividend == 30.0

    def test_ttl_expired_returns_none(self) -> None:
        from tstdx.domain.finance import CapitalChangeCache

        c = CapitalChangeCache(ttl=0.0)  # TTL=0 → 立即过期
        c.put("sh600519", [self._ev()])
        assert c.get("sh600519") is None

    def test_empty_events_not_cached(self) -> None:
        """空事件列表不缓存（避免屏蔽后续真实查询）。"""
        from tstdx.domain.finance import CapitalChangeCache

        c = CapitalChangeCache()
        c.put("sh600519", [])
        assert c.get("sh600519") is None

    def test_deep_copy_isolation(self) -> None:
        """返回值为深拷贝：外部修改不污染缓存。"""
        from tstdx.domain.finance import CapitalChangeCache

        c = CapitalChangeCache()
        c.put("sh600519", [self._ev()])
        got = c.get("sh600519")
        assert got is not None
        got[0].dividend = 999.0
        again = c.get("sh600519")
        assert again is not None and again[0].dividend == 30.0

    def test_disk_roundtrip(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """落盘：新实例同目录 → 命中磁盘（跨进程离线复用）。"""
        from tstdx.domain.finance import CapitalChangeCache

        c1 = CapitalChangeCache(disk_dir=str(tmp_path))
        c1.put("sh600519", [self._ev()])
        c2 = CapitalChangeCache(disk_dir=str(tmp_path))
        hit = c2.get("sh600519")
        assert hit is not None and hit[0].dividend == 30.0

    def test_corrupt_disk_degrades_and_removes(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """损坏 JSON → 返回 None + 删除文件（降级重取）。"""
        from tstdx.domain.finance import CapitalChangeCache

        path = tmp_path / "sh600519.json"
        path.write_text("{not valid json", encoding="utf-8")
        c = CapitalChangeCache(disk_dir=str(tmp_path))
        assert c.get("sh600519") is None
        assert not path.exists()  # 损坏文件已被清理

    def test_disk_expired_ignored(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """磁盘缓存超 TTL → 视为 miss（不返回陈旧事件）。"""
        import json

        from tstdx.domain.finance import CapitalChangeCache

        payload = {"_fetched_at": 0.0, "events": [self._ev().to_dict()]}
        (tmp_path / "sh600519.json").write_text(json.dumps(payload), encoding="utf-8")
        c = CapitalChangeCache(ttl=3600, disk_dir=str(tmp_path))
        assert c.get("sh600519") is None

    def test_clear_symbol_and_all(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        from tstdx.domain.finance import CapitalChangeCache

        c = CapitalChangeCache(disk_dir=str(tmp_path))
        c.put("sh600519", [self._ev()])
        c.put("sz000001", [self._ev("000001")])
        c.clear("sh600519")
        assert c.get("sh600519") is None
        assert c.get("sz000001") is not None
        c.clear()
        assert c.get("sz000001") is None
        assert list(tmp_path.iterdir()) == []
