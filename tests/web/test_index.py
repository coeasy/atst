"""东财指数成分股源（P0-2）测试。

纯函数映射测试（mock ``fetch_rows``）+ 分页逻辑测试 + 代码解析测试，
全部离线，不发起真实 HTTP。
"""

from __future__ import annotations

import pytest

from atst.errors import WebSourceError
from atst.web.eastmoney.adapters import (
    INDEX_TYPE_MAP,
    EastmoneyIndexConstituentsSource,
    _resolve_index_code,
)
from atst.web.sources import INDEX_CONS, get_source, list_sources

pytestmark = pytest.mark.unit

_RAW = [
    {
        "SECURITY_CODE": "000001",
        "SECURITY_NAME_ABBR": "平安银行",
        "SECUCODE": "000001.SZ",
        "WEIGHT": 0.45,
        "INDUSTRY": "银行",
        "REGION": "广东",
        "CLOSE_PRICE": 11.42,
        "CHANGE_RATE": 1.06,
        "PE": 5.2,
        "EPS": 2.2,
        "ROE": 12.3,
        "BPS": 20.0,
        "TOTAL_SHARES": 194059181900.0,
        "FREE_SHARES": 193740000000.0,
        "FREE_CAP": 221324000000.0,
        "TYPE": "1",
    },
    {
        "SECURITY_CODE": "600519",
        "SECURITY_NAME_ABBR": "贵州茅台",
        "SECUCODE": "600519.SH",
        "WEIGHT": None,  # 部分指数族无权重
        "INDUSTRY": "酿酒行业",
        "CLOSE_PRICE": 1456.0,
        "CHANGE_RATE": -0.5,
        "PE": None,
        "TYPE": "1",
    },
]


class TestRegistration:
    def test_source_registered(self) -> None:
        spec = get_source(INDEX_CONS)
        assert INDEX_CONS in list_sources(capability="index_constituents")
        assert spec.capabilities == ("index_constituents",)
        assert spec.default_rate == 3

    def test_type_map_known_entries(self) -> None:
        """已知指数的 TYPE 归属（2026-09 实测 + 交叉验证）。"""
        assert INDEX_TYPE_MAP["000300"] == "1"  # 沪深300
        assert INDEX_TYPE_MAP["000016"] == "2"  # 上证50
        assert INDEX_TYPE_MAP["000905"] == "3"  # 中证500
        assert INDEX_TYPE_MAP["000688"] == "4"  # 科创50
        assert INDEX_TYPE_MAP["930050"] == "5"  # 中证A50
        assert INDEX_TYPE_MAP["000510"] == "6"  # 中证A500
        assert INDEX_TYPE_MAP["000852"] == "7"  # 中证1000
        assert INDEX_TYPE_MAP["399850"] == "8"  # 深证50
        assert INDEX_TYPE_MAP["399330"] == "9"  # 深证100
        assert INDEX_TYPE_MAP["899050"] == "10"  # 北证50
        assert INDEX_TYPE_MAP["000010"] == "11"  # 上证180
        assert INDEX_TYPE_MAP["000903"] == "12"  # 中证A100
        assert INDEX_TYPE_MAP["932000"] == "13"  # 中证2000


class TestCodeResolution:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("000300", "000300"),
            ("sh000300", "000300"),
            ("SZ399330", "399330"),
            ("bj899050", "899050"),
            ("000905.SH", "000905"),
            (" 930050 ", "930050"),
        ],
    )
    def test_resolve(self, raw: str, expected: str) -> None:
        assert _resolve_index_code(raw) == expected


class TestFetchConstituents:
    def test_parses_and_normalizes(self, monkeypatch: pytest.MonkeyPatch) -> None:
        src = EastmoneyIndexConstituentsSource()
        monkeypatch.setattr(src, "fetch_rows", lambda **kw: list(_RAW))
        rows = src.fetch_constituents("000300")
        assert len(rows) == 2
        assert rows[0] == {
            "code": "000001",
            "name": "平安银行",
            "secucode": "000001.SZ",
            "weight": 0.45,
            "industry": "银行",
            "region": "广东",
            "price": 11.42,
            "change_pct": 1.06,
            "pe": 5.2,
            "eps": 2.2,
            "roe": 12.3,
            "bps": 20.0,
            "total_shares": 194059181900.0,
            "free_shares": 193740000000.0,
            "free_cap": 221324000000.0,
            "type": "1",
        }
        # 无权重 / 无 PE 的指数族 → None 保留（不填充 0）
        assert rows[1]["weight"] is None
        assert rows[1]["pe"] is None

    def test_passes_type_filter(self, monkeypatch: pytest.MonkeyPatch) -> None:
        captured: dict = {}

        def _fake(**kw: object) -> list[dict]:
            captured.update(kw)
            return []

        src = EastmoneyIndexConstituentsSource()
        monkeypatch.setattr(src, "fetch_rows", _fake)
        src.fetch_constituents("930050")  # 中证A50 → TYPE=5
        assert captured["filters"] == ['TYPE="5"']
        assert captured["size"] == 500

    def test_paginates_large_index(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """中证1000（1000 只）自动分页：每页 500，拉两页后停止。"""

        def _fake(**kw: object) -> list[dict]:
            page = int(kw["page"])
            return list(_RAW) * 250 if page in (1, 2) else []

        src = EastmoneyIndexConstituentsSource()
        monkeypatch.setattr(src, "fetch_rows", _fake)
        rows = src.fetch_constituents("000852")
        assert len(rows) == 1000

    def test_stops_at_page_size_boundary(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """恰为一整页（500 只）时下一页为空 → 只请求两页（不越界）。"""
        calls: list[int] = []

        def _fake(**kw: object) -> list[dict]:
            page = int(kw["page"])
            calls.append(page)
            return [_RAW[0]] * 500 if page == 1 else []

        src = EastmoneyIndexConstituentsSource()
        monkeypatch.setattr(src, "fetch_rows", _fake)
        rows = src.fetch_constituents("000510")  # 中证A500（500 只）
        assert len(rows) == 500
        assert calls == [1, 2]

    def test_unknown_index_raises(self) -> None:
        src = EastmoneyIndexConstituentsSource()
        with pytest.raises(WebSourceError) as exc:
            src.fetch_constituents("999999")
        assert "未知指数代码" in str(exc.value)

    def test_source_name(self) -> None:
        assert EastmoneyIndexConstituentsSource().source_name == INDEX_CONS
