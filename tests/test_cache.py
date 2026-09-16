"""SQLite K 线缓存测试（U4）。

覆盖：``KlineCache`` 存储引擎的 merge/get/count 往返、按 datetime 去重（增量
只落新根）、异常静默降级；以及 **v13 §5.5 契约**——``DataSourceRouter`` 内的
legacy cache 读写路径已退役（存储引擎保留，但 router 不再隐式读写）。

审计结论：legacy cache 的 key 不匹配完整 ``QueryFingerprint``，且行内没有
Provider provenance；由 router 隐式读写会让缓存命中改变数据来源。
"""

from __future__ import annotations

import pytest

from tstdx.cache import KlineCache
from tstdx.config.schema import SourcesConfig
from tstdx.errors import SourceUnavailable
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
class TestRouterLegacyCacheIsRetired:
    """v13 §5.5：``DataSourceRouter`` 不再有 cache 读/写路径。

    存储引擎 :class:`~tstdx.cache.KlineCache` 仍然可用（上面的 TestKlineCache），
    但注入给 router 的 legacy cache 只是**构造兼容参数**，不参与任何请求。
    """

    def _cache(self, tmp_path) -> KlineCache:
        return KlineCache(str(tmp_path / "k.sqlite3"))

    def test_router_never_reads_injected_kline_cache(self, tmp_path, monkeypatch):
        """即使缓存里有数据，router 也不会读它（命中不再短路）。"""
        cache = self._cache(tmp_path)
        cache.merge("sh600519", "day", [_bar(f"2026-09-{i:02d} 15:00", float(i)) for i in range(1, 5)])
        reads: list = []
        original_get = cache.get
        monkeypatch.setattr(
            cache, "get", lambda *a, **k: reads.append(a) or original_get(*a, **k)
        )

        router = DataSourceRouter(
            config=SourcesConfig(order=["tdx"]),
            kline_cache=cache,
            tdx_hosts=["127.0.0.1:1"],  # 必然连不上：证明没有缓存短路
        )
        try:
            with pytest.raises(SourceUnavailable):
                router.kline("sh600519", period="day", count=3)
            assert reads == []
            assert router.last_source != "cache"
        finally:
            router.close()
            cache.close()

    def test_router_never_writes_injected_kline_cache(self, tmp_path, monkeypatch):
        """成功取数也不回写 legacy cache（无 provenance，写穿透已退役）。"""
        cache = self._cache(tmp_path)
        merges: list = []
        monkeypatch.setattr(cache, "merge", lambda *a, **k: merges.append(a))

        router = DataSourceRouter(
            order=["synthetic"],
            kline_cache=cache,
            allow_synthetic=True,
        )
        try:
            out = router.kline("sh600519", period="day", count=5)
            assert len(out) == 5
            assert router.last_source == "tdx"
            assert merges == []
            assert cache.count("sh600519", "day") == 0
        finally:
            router.close()
            cache.close()

    def test_router_quotes_does_not_consult_injected_quote_cache(
        self, tmp_path, monkeypatch
    ) -> None:  # noqa: ANN001
        """``quote_cache`` 同样是构造兼容参数，quotes 不再读写它。"""

        class _QuoteCache:
            def __init__(self) -> None:
                self.get_calls = 0
                self.put_calls = 0

            def get(self, symbols):  # noqa: ANN001, ANN201
                self.get_calls += 1
                return [{"code": "600519", "price": 999.0}]

            def put(self, symbols, rows) -> None:  # noqa: ANN001
                self.put_calls += 1

        quote_cache = _QuoteCache()
        router = DataSourceRouter(
            config=SourcesConfig(order=["tdx"]),
            quote_cache=quote_cache,
            tdx_hosts=["127.0.0.1:1"],
        )
        try:
            with pytest.raises(SourceUnavailable):
                router.quotes(["sh600519"])
            assert quote_cache.get_calls == 0
            assert quote_cache.put_calls == 0
        finally:
            router.close()
