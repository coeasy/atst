"""SQLite K 线缓存测试（U4）。

覆盖：merge/get/count 往返、按 datetime 去重（增量只落新根）、
DataSourceRouter 读命中短路（命中时零网络）、写穿透合并、异常静默降级。
"""

from __future__ import annotations

import pytest

from tstdx.cache import KlineCache
from tstdx.config.schema import SourcesConfig
from tstdx.sources import DataSourceRouter


def _bar(dt: str, close: float) -> dict:
    return {
        "datetime": dt,
        "open": close,
        "high": close,
        "low": close,
        "close": close,
        "volume": 100,
        "amount": 10000.0,
        "code": "600519",
    }


@pytest.mark.unit
class TestKlineCache:
    def test_merge_get_roundtrip(self, tmp_path):
        cache = KlineCache(str(tmp_path / "k.sqlite3"))
        cache.merge("sh600519", "day", [_bar("2026-09-01 15:00", 1290.0)])
        bars = cache.get("sh600519", "day")
        assert bars is not None
        assert len(bars) == 1
        assert bars[0]["datetime"] == "2026-09-01 15:00"
        assert bars[0]["close"] == 1290.0
        # extra 字段（code）随行存取
        assert bars[0]["code"] == "600519"
        cache.close()

    def test_merge_dedups_by_datetime(self, tmp_path):
        """同一 datetime 重复 merge 不新增；新 datetime 才追加。"""
        cache = KlineCache(str(tmp_path / "k.sqlite3"))
        cache.merge("sh600519", "day", [_bar("2026-09-01 15:00", 1290.0)])
        changed = cache.merge("sh600519", "day", [_bar("2026-09-01 15:00", 1300.0)])
        assert changed == 1  # REPLACE 视为变更
        assert cache.count("sh600519", "day") == 1
        changed = cache.merge(
            "sh600519",
            "day",
            [_bar("2026-09-01 15:00", 1300.0), _bar("2026-09-02 15:00", 1310.0)],
        )
        assert cache.count("sh600519", "day") == 2
        cache.close()

    def test_get_count_slices_tail(self, tmp_path):
        cache = KlineCache(str(tmp_path / "k.sqlite3"))
        cache.merge(
            "sh600519",
            "day",
            [_bar(f"2026-09-{i:02d} 15:00", float(i)) for i in range(1, 6)],
        )
        tail = cache.get("sh600519", "day", count=3)
        assert [b["datetime"] for b in tail] == [
            "2026-09-03 15:00",
            "2026-09-04 15:00",
            "2026-09-05 15:00",
        ]
        cache.close()

    def test_missing_returns_none(self, tmp_path):
        cache = KlineCache(str(tmp_path / "k.sqlite3"))
        assert cache.get("sh600519", "day") is None
        assert cache.count("sh600519", "day") == 0
        cache.close()

    def test_clear(self, tmp_path):
        cache = KlineCache(str(tmp_path / "k.sqlite3"))
        cache.merge("sh600519", "day", [_bar("2026-09-01 15:00", 1290.0)])
        cache.merge("sh600000", "day", [_bar("2026-09-01 15:00", 10.0)])
        assert cache.clear("sh600519") >= 1
        assert cache.get("sh600519", "day") is None
        assert cache.get("sh600000", "day") is not None
        cache.close()

    def test_invalid_path_degrades_gracefully(self, tmp_path, monkeypatch):
        """建库失败（模拟只读/权限）→ 静默降级，不抛异常。"""
        import tstdx.cache as cache_mod

        def boom(*args, **kwargs):
            raise PermissionError("simulated unwritable")

        monkeypatch.setattr(cache_mod.sqlite3, "connect", boom)
        cache = KlineCache(str(tmp_path / "k.sqlite3"))
        assert cache.merge("sh600519", "day", [_bar("2026-09-01 15:00", 1290.0)]) == 0
        assert cache.get("sh600519", "day") is None
        assert cache.count("sh600519", "day") == 0


@pytest.mark.unit
class TestRouterCacheIntegration:
    def _router(self, tmp_path) -> DataSourceRouter:
        cache = KlineCache(str(tmp_path / "k.sqlite3"))
        return DataSourceRouter(
            config=SourcesConfig(order=["tdx"]),
            kline_cache=cache,
            tdx_hosts=["127.0.0.1:1"],  # 必然连不上：命中缓存时不会触达
        )

    def test_cache_hit_avoids_source(self, tmp_path):
        """缓存命中直接返回，不再触达 TDX（不可达主站也不报错）。"""
        router = self._router(tmp_path)
        bars = [_bar(f"2026-09-{i:02d} 15:00", float(i)) for i in range(1, 5)]
        router.kline_cache.merge("sh600519", "day", bars)
        out = router.kline("sh600519", period="day", count=3)
        assert len(out) == 3
        assert out[-1]["datetime"] == "2026-09-04 15:00"
        assert router.last_source == "cache"

    def test_miss_falls_back_and_writes(self, tmp_path):
        """缓存未命中 → 走真实源；源失败时正常抛 AllSourcesExhausted。"""
        router = self._router(tmp_path)
        with pytest.raises(Exception) as exc:
            router.kline("sh600519", period="day", count=5)
        assert "全部 K 线源失败" in str(exc.value) or "TDX" in str(exc.value)

    def test_write_through_populates_cache(self, tmp_path):
        """写穿透：源返回后写入缓存，二次调用命中。"""
        cache = KlineCache(str(tmp_path / "k.sqlite3"))
        router = DataSourceRouter(
            config=SourcesConfig(
                order=["synthetic"],
                enabled={"synthetic": True},
            ),
            kline_cache=cache,
        )
        out = router.kline("sh600519", period="day", count=5)
        assert len(out) == 5
        assert cache.count("sh600519", "day") == 5
        # 二次调用应命中缓存（last_source == cache）
        out2 = router.kline("sh600519", period="day", count=5)
        assert len(out2) == 5
        assert router.last_source == "cache"
