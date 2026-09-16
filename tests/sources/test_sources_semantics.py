"""DataSourceRouter 语义测试（审计 §2-15 / §3-3，v12 Provider 契约）。

覆盖：

* kline 空结果与 quotes 语义对齐（显式 ``default_empty_ok`` 开关）；
* replay / synthetic 特殊模式显式授权，且**绝不**隐式降级；
* ``build_router`` 读全局配置失败 → warning + 回退默认路由；
* ``build_router`` 以 ``WebConfig.enabled`` 总闸门控 legacy web 选择器
  （总闸关闭时 ``route/order='web'`` fail-closed）。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from tstdx.errors import SourceUnavailable
from tstdx.sources import DataSourceRouter, build_router

pytestmark = pytest.mark.unit


class _FakeService:
    """最小服务替身：按注入行返回，空列表代表「Provider 返回空」。"""

    def __init__(self, rows: list | None = None) -> None:
        self.rows = rows if rows is not None else []
        self.calls: list[tuple[str, str | None]] = []

    def bars(self, symbol, *, period="day", count=320, start=0, adjust="", provider=None):  # noqa: ANN001
        self.calls.append(("bars", provider))
        return list(self.rows)

    def quotes(self, symbols, *, provider=None, source=None, with_meta=False):  # noqa: ANN001
        self.calls.append(("quotes", provider))
        return list(self.rows)

    def close(self) -> None:
        pass


class TestKlineEmptySemantics:
    """空 K 线 = 该 Provider 不可用；``default_empty_ok`` 显式接受空。"""

    def test_empty_raises_source_unavailable(self) -> None:
        service = _FakeService(rows=[])
        router = DataSourceRouter(service=service)  # type: ignore[arg-type]
        try:
            with pytest.raises(SourceUnavailable) as excinfo:
                router.kline("600000", period="day", count=5)
            assert excinfo.value.context["provider"] == "tdx"
            assert excinfo.value.context["fallback"] is False
            assert [s for s, _ in router.last_errors] == ["tdx"]
            assert router.last_source is None
        finally:
            router.close()

    def test_default_empty_ok_accepts_empty(self) -> None:
        service = _FakeService(rows=[])
        router = DataSourceRouter(service=service)  # type: ignore[arg-type]
        try:
            rows = router.kline("600000", period="day", default_empty_ok=True)
            assert rows == []
            # 空 = 该 Provider 的成功返回（ADR-012 D2），不算失败。
            assert router.last_source == "tdx"
            assert router.last_errors == []
        finally:
            router.close()

    def test_nonempty_still_normal(self) -> None:
        service = _FakeService(rows=[{"datetime": "2026-06-02", "close": 1.0}])
        router = DataSourceRouter(service=service)  # type: ignore[arg-type]
        try:
            rows = router.kline("600000", period="day")
            assert len(rows) == 1
            assert router.last_source == "tdx"
        finally:
            router.close()

    def test_empty_never_degrades_to_another_provider(self) -> None:
        """空结果不触发任何降级：服务只被调用一次。"""
        service = _FakeService(rows=[])
        router = DataSourceRouter(order=["tdx"], service=service)  # type: ignore[arg-type]
        try:
            with pytest.raises(SourceUnavailable):
                router.kline("600000", period="day")
            assert service.calls == [("bars", "tdx")]
        finally:
            router.close()


class TestReplayAndSyntheticPaths:
    """cache/synthetic 是显式授权的特殊模式，不是降级链的末级。"""

    def _make_golden(self, root: Path) -> Path:
        d = root / "quotation" / "0x052d_security_bars_600000_cat4" / "20260101-000000"
        d.mkdir(parents=True)
        (d / "meta.json").write_text(
            json.dumps({"response": {"zip_size": 4, "unzip_size": 40}}), encoding="utf-8"
        )
        (d / "payload.bin").write_bytes(b"\x00\x01\x02\x03\x04\x05\x06\x07" * 3)
        return root

    def test_missing_replay_sample_raises_unavailable(self, tmp_path: Path) -> None:
        router = DataSourceRouter(order=["cache"], allow_replay=True, golden_root=tmp_path)
        try:
            with pytest.raises(SourceUnavailable):
                router.kline("600999", period="day")
            assert [s for s, _ in router.last_errors] == ["tdx"]
            assert router.last_source is None
        finally:
            router.close()

    def test_broken_replay_sample_does_not_escape_as_struct_error(self, tmp_path: Path) -> None:
        """golden 样本畸形 → 受控 SourceUnavailable，不得炸出 struct.error。"""
        import struct

        import tstdx.protocol.registry as registry

        def _boom(*args: object, **kwargs: object) -> None:
            raise struct.error("unpack requires a buffer of 999 bytes")

        golden = self._make_golden(tmp_path)
        router = DataSourceRouter(
            order=["cache"],
            allow_replay=True,
            golden_root=golden,
        )
        original = registry.dispatch
        registry.dispatch = _boom
        try:
            with pytest.raises(SourceUnavailable):
                router.kline("600000", period="day")
        finally:
            registry.dispatch = original
            router.close()

    def test_synthetic_kline_is_marked_and_never_entered_implicitly(self) -> None:
        """synthetic 只有显式授权 + 显式选择才会被使用，且数据带 synthetic 标记。"""
        router = DataSourceRouter(order=["synthetic"], allow_synthetic=True)
        try:
            rows = router.kline("600000", period="day", count=3)
            assert rows and all(r.get("synthetic") for r in rows)
            assert router.last_source == "tdx"
        finally:
            router.close()

    def test_synthetic_is_not_reachable_from_default_selection(self) -> None:
        """默认选择是 tdx；synthetic 不会作为兜底被自动启用。"""
        router = DataSourceRouter(allow_synthetic=True)
        try:
            assert router._selection(provider=None, source=None, order=None) == (
                "tdx",
                None,
                None,
            )
        finally:
            router.close()


class TestBuildRouter:
    """全局配置读取失败 / web 总闸。"""

    @staticmethod
    def _config(*, web_enabled: bool, web_sources: list[str], timeout: float | None = None):  # noqa: ANN401
        """构造一份独立 Config（不污染进程级单例）。"""
        from tstdx.config.schema import Config
        from tstdx.config.schema import SourcesConfig as SC

        cfg = Config()
        cfg.sources = SC(order=["tdx"], enabled={"tdx": True})
        cfg.web.enabled = web_enabled
        cfg.web.enabled_sources = list(web_sources)
        if timeout is not None:
            cfg.core.timeout = timeout
        return cfg

    def test_config_read_failure_warns_and_falls_back(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        """配置中心不可用：warning + 回退到不依赖配置的路由（不得二次抛错）。"""
        import tstdx.config as config_mod

        def _boom() -> None:
            raise RuntimeError("配置中心不可用")

        monkeypatch.setattr(config_mod, "get_config", _boom)
        with caplog.at_level(logging.WARNING, logger="tstdx.sources"):
            router = build_router()
        try:
            assert isinstance(router, DataSourceRouter)
            assert any("build_router" in r.message for r in caplog.records)
            # 回退路由仍可完成一次确定性选择（tdx），不是坏对象。
            assert router._selection(provider=None, source=None, order=None) == (
                "tdx",
                None,
                None,
            )
        finally:
            router.close()

    def test_web_master_switch_gates_legacy_web_selector(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import tstdx.config as config_mod

        cfg = self._config(web_enabled=False, web_sources=["tencent"])
        monkeypatch.setattr(config_mod, "get_config", lambda: cfg)
        router = build_router()
        try:
            assert router.web_master_enabled is False
            # 总闸关闭 → legacy web 选择器 fail-closed，绝不放行
            with pytest.raises(SourceUnavailable) as caught:
                router._legacy_web_provider()
            assert caught.value.context["fallback"] is False
        finally:
            router.close()

    def test_web_enabled_keeps_legacy_web_selector(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import tstdx.config as config_mod

        cfg = self._config(web_enabled=True, web_sources=["sina"])
        monkeypatch.setattr(config_mod, "get_config", lambda: cfg)
        router = build_router()
        try:
            assert router.web_master_enabled is True
            assert router._legacy_web_provider() == "sina"
        finally:
            router.close()

    def test_timeout_and_web_sources_are_injected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import tstdx.config as config_mod

        cfg = self._config(web_enabled=True, web_sources=["eastmoney"], timeout=2.5)
        monkeypatch.setattr(config_mod, "get_config", lambda: cfg)
        router = build_router()
        try:
            assert router.timeout == pytest.approx(2.5)
            assert router.web_sources == ["eastmoney"]
        finally:
            router.close()
