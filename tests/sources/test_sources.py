"""数据源路由测试（§34）：验证 DataSourceRouter 的降级链与失败记录。

覆盖：tdx→web→reader→cache→synthetic 全链降级、
AllSourcesExhausted、顺序保持、首个成功即返回、每次失败均记录。
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from tstdx.config.schema import SourcesConfig
from tstdx.errors import AllSourcesExhausted
from tstdx.sources import DataSourceRouter, SourceUnavailable


@pytest.mark.unit
class TestDataSourceRouter:
    """DataSourceRouter 降级链测试。"""

    def _make_config(self, order=None, enabled=None):
        return SourcesConfig(
            order=order or ["tdx", "web", "reader", "cache", "synthetic"],
            enabled=enabled
            or {
                "tdx": True,
                "web": True,
                "reader": True,
                "cache": True,
                "synthetic": True,
            },
        )

    @pytest.fixture
    def router(self, tmp_path):
        """创建 router，vipdoc_root 指向空目录（reader 会失败）。"""
        return DataSourceRouter(
            config=self._make_config(),
            vipdoc_root=tmp_path / "vipdoc",
            golden_root=None,
        )

    def test_tdx_fails_web_fails_reader_fails_cache_fails_synthetic_ok(self, router):
        """#1 全链降级：tdx→web→reader→cache→synthetic 最终成功。"""
        with (
            patch("tstdx.client.TdxClient") as MockTdx,
            patch("tstdx.web.WebQuoteClient") as MockWeb,
        ):
            MockTdx.return_value.__enter__.return_value.bars.side_effect = SourceUnavailable(
                "tdx down"
            )
            MockWeb.return_value.klines.side_effect = SourceUnavailable("web down")

            bars = router.kline("600000", period="day", count=5, as_format="dict")
            assert router.last_source == "synthetic"
            assert len(bars) == 5
            # 每源失败都被记录
            failed_sources = {s for s, _ in router.last_errors}
            assert "tdx" in failed_sources
            assert "web" in failed_sources

    def test_tdx_fails_web_ok(self, router):
        """#2 tdx 失败 → web 成功。"""
        with (
            patch("tstdx.client.TdxClient") as MockTdx,
            patch("tstdx.web.WebQuoteClient") as MockWeb,
        ):
            MockTdx.return_value.__enter__.return_value.bars.side_effect = SourceUnavailable(
                "tdx down"
            )
            # web 返回假数据
            MockWeb.return_value.klines.return_value = [
                {
                    "datetime": "2024-01-01 15:00",
                    "open": 10,
                    "high": 11,
                    "low": 9,
                    "close": 10.5,
                    "volume": 1000,
                    "amount": 10500.0,
                }
            ]

            bars = router.kline("600000", period="day", count=5, as_format="dict")
            assert router.last_source == "web"
            assert len(bars) == 1

    def test_all_fail_raises_all_sources_exhausted(self):
        """#3 全部源失败 → AllSourcesExhausted。"""
        cfg = SourcesConfig(
            order=["tdx", "web"],
            enabled={"tdx": True, "web": True, "reader": False, "cache": False, "synthetic": False},
        )
        r = DataSourceRouter(config=cfg)
        with (
            patch("tstdx.client.TdxClient") as MockTdx,
            patch("tstdx.web.WebQuoteClient") as MockWeb,
        ):
            MockTdx.return_value.__enter__.return_value.bars.side_effect = SourceUnavailable(
                "tdx down"
            )
            MockWeb.return_value.klines.side_effect = SourceUnavailable("web down")

            with pytest.raises(AllSourcesExhausted):
                r.kline("600000", period="day", count=5)

        assert r.last_source is None
        failed = {s for s, _ in r.last_errors}
        assert failed == {"tdx", "web"}

    def test_order_preserved(self, router):
        """#4 降级顺序保持。"""
        assert router.order == ["tdx", "web", "reader", "cache", "synthetic"]

    def test_active_sources_filters_enabled(self):
        """#5 _active_sources 按 enabled 过滤。"""
        cfg = SourcesConfig(
            order=["tdx", "web", "reader"],
            enabled={"tdx": True, "web": False, "reader": True},
        )
        r = DataSourceRouter(config=cfg)
        assert r._active_sources() == ["tdx", "reader"]

    def test_first_success_wins(self, router):
        """#6 首个成功源即返回（不继续尝试后续源）。"""
        with patch("tstdx.client.TdxClient") as MockTdx:
            MockTdx.return_value.__enter__.return_value.bars.return_value = [
                {
                    "datetime": "2024-01-01 15:00",
                    "open": 10,
                    "high": 11,
                    "low": 9,
                    "close": 10.5,
                    "volume": 1000,
                    "amount": 10500.0,
                }
            ]
            router.kline("600000", period="day", count=5, as_format="dict")
            assert router.last_source == "tdx"
            # web/reader/cache/synthetic 不应被记录为失败
            assert len(router.last_errors) == 0

    def test_last_errors_recorded(self, router):
        """#7 每次失败都被记录到 last_errors。"""
        with (
            patch("tstdx.client.TdxClient") as MockTdx,
            patch("tstdx.web.WebQuoteClient") as MockWeb,
        ):
            MockTdx.return_value.__enter__.return_value.bars.side_effect = SourceUnavailable(
                "tdx down"
            )
            MockWeb.return_value.klines.side_effect = SourceUnavailable("web down")
            MockTdx.return_value.__enter__.return_value.bars.side_effect = SourceUnavailable(
                "tdx down again"
            )

            router.kline("600000", period="day", count=3, as_format="dict")
            assert router.last_source == "synthetic"
            # tdx 和 web 失败都被记录
            assert len(router.last_errors) >= 2
            failed_sources = [s for s, _ in router.last_errors]
            assert "tdx" in failed_sources
            assert "web" in failed_sources

    def test_continue_on_error_false(self):
        """#8 continue_on_error=False → 任一失败即抛错。"""
        cfg = SourcesConfig(
            order=["tdx", "web"],
            enabled={"tdx": True, "web": True, "reader": False, "cache": False, "synthetic": False},
            continue_on_error=False,
        )
        r = DataSourceRouter(config=cfg)
        with patch("tstdx.client.TdxClient") as MockTdx:
            MockTdx.return_value.__enter__.return_value.bars.side_effect = SourceUnavailable(
                "tdx down"
            )
            with pytest.raises(SourceUnavailable):
                r.kline("600000", period="day", count=5)

    def test_quotes_fallback(self, router):
        """#9 quotes() 降级链。"""
        with (
            patch("tstdx.client.TdxClient") as MockTdx,
            patch("tstdx.web.WebQuoteClient") as MockWeb,
        ):
            MockTdx.return_value.__enter__.return_value.quotes.side_effect = SourceUnavailable(
                "tdx down"
            )
            MockWeb.return_value.quotes.return_value = [
                {
                    "code": "600519",
                    "price": 100.0,
                    "volume": 1000,
                    "amount": 103000.0,
                    "last_close": 99.0,
                    "open": 101.0,
                    "high": 105.0,
                    "low": 99.0,
                }
            ]
            qs = router.quotes(["600519"], as_format="dict")
            assert router.last_source == "web"
            assert len(qs) == 1

    def test_synthetic_kline_marked(self, router):
        """#10 synthetic 数据标注 synthetic=True。"""
        with (
            patch("tstdx.client.TdxClient") as MockTdx,
            patch("tstdx.web.WebQuoteClient") as MockWeb,
        ):
            MockTdx.return_value.__enter__.return_value.bars.side_effect = SourceUnavailable(
                "tdx down"
            )
            MockWeb.return_value.klines.side_effect = SourceUnavailable("web down")

            bars = router.kline("999999", period="day", count=3, as_format="dict")
            assert router.last_source == "synthetic"
            assert bars[0]["synthetic"] is True

    def test_empty_order_raises(self):
        """#11 空 order → AllSourcesExhausted。"""
        cfg = SourcesConfig(order=[], enabled={})
        r = DataSourceRouter(config=cfg)
        with pytest.raises(AllSourcesExhausted):
            r.kline("600000", period="day", count=5)

    def test_last_source_none_initially(self, router):
        """#12 last_source 初始为 None。"""
        assert router.last_source is None
        assert router.last_errors == []


@pytest.mark.unit
class TestKlineAdjustConsistency:
    """W#2 回归：降级链 K 线复权口径一致性（tdx 与 web 分支必须同为原始价）。"""

    def _make_config(self):
        from tstdx.config.schema import SourcesConfig

        return SourcesConfig(
            order=["tdx", "web"],
            enabled={"tdx": True, "web": True, "reader": False, "cache": False, "synthetic": False},
        )

    def test_web_kline_downgrade_uses_raw_price(self, tmp_path):
        """tdx 失败降级 web 时，klines 必须显式 adjust=""（原始价）——
        禁止落入 WebQuoteClient.klines 默认 qfq 造成口径静默切换。"""
        from unittest.mock import patch

        router = DataSourceRouter(config=self._make_config(), vipdoc_root=tmp_path / "vipdoc")
        with (
            patch("tstdx.client.TdxClient") as MockTdx,
            patch("tstdx.web.WebQuoteClient") as MockWeb,
        ):
            MockTdx.return_value.__enter__.return_value.bars.side_effect = SourceUnavailable(
                "tdx down"
            )
            mock_klines = MockWeb.return_value.klines
            mock_klines.return_value = [{"close": 10.0}]  # 非空 → web 分支成功即返回

            bars = router.kline("600000", period="day", count=5, as_format="dict")

            assert bars == [{"close": 10.0}]
            kwargs = mock_klines.call_args.kwargs
            assert kwargs.get("adjust") == "", (
                f"web 降级必须显式原始价（adjust=''），实际收到 {kwargs!r}"
            )
