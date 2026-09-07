"""DataSourceRouter 语义测试（审计 §2-15 / §3-3）。

覆盖：
* kline 空结果与 quotes 语义对齐（``if data:`` 降级 + 显式
  ``default_empty_ok`` 开关）；
* 缓存分支 ``_safe_run`` 包裹：golden 样本损坏不再炸穿降级链；
* ``build_router`` 读全局配置失败 → warning + 回退默认路由；
* ``build_router`` 以 ``WebConfig.enabled`` 总闸门控 web 源。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from types import SimpleNamespace
from typing import Self

import pytest

from tstdx.config.schema import SourcesConfig
from tstdx.errors import AllSourcesExhausted
from tstdx.sources import DataSourceRouter, build_router

pytestmark = pytest.mark.unit


class _FakeTdxClient:
    """假 TdxClient：bars 按注入行为返回。"""

    behavior: object = staticmethod(lambda *a, **kw: [])

    def __init__(self, *args: object, **kwargs: object) -> None:
        self.hosts = kwargs.get("hosts")

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> bool:
        return False

    def bars(self, *args: object, **kwargs: object) -> list:
        return type(self).behavior(*args, **kwargs)


@pytest.fixture()
def fake_tdx(monkeypatch: pytest.MonkeyPatch) -> type[_FakeTdxClient]:
    import tstdx.client as client_mod

    monkeypatch.setattr(client_mod, "TdxClient", _FakeTdxClient)
    return _FakeTdxClient


class TestKlineEmptySemantics:
    """空 K 线 = 本源不可用（与 quotes 对齐）；default_empty_ok 显式接受空。"""

    def test_empty_degrades_to_next_source(self, fake_tdx: type[_FakeTdxClient]) -> None:
        cfg = SourcesConfig(order=["tdx"], enabled={"tdx": True})
        router = DataSourceRouter(config=cfg)
        with pytest.raises(AllSourcesExhausted) as excinfo:
            router.kline("600000")
        assert any(s == "tdx" and "空 K 线" in str(e) for s, e in router.last_errors)
        assert "空 K 线" in str(excinfo.value.context["errors"])

    def test_default_empty_ok_accepts_empty(self, fake_tdx: type[_FakeTdxClient]) -> None:
        cfg = SourcesConfig(order=["tdx"], enabled={"tdx": True})
        router = DataSourceRouter(config=cfg)
        rows = router.kline("600000", default_empty_ok=True)
        assert rows == []
        assert router.last_source == "tdx"

    def test_nonempty_still_normal(self, fake_tdx: type[_FakeTdxClient]) -> None:
        _FakeTdxClient.behavior = staticmethod(
            lambda *a, **kw: [{"datetime": "2026-06-02", "open": 1.0, "close": 1.0, "volume": 1}]
        )
        try:
            cfg = SourcesConfig(order=["tdx"], enabled={"tdx": True})
            router = DataSourceRouter(config=cfg)
            rows = router.kline("600000")
            assert len(rows) == 1
            assert router.last_source == "tdx"
        finally:
            _FakeTdxClient.behavior = staticmethod(lambda *a, **kw: [])


class TestCacheSafeRun:
    """缓存源异常走 _safe_run → 降级，不炸穿。"""

    def _make_golden(self, root: Path) -> Path:
        d = root / "quotation" / "0x052d_security_bars_600000_cat4" / "20260101-000000"
        d.mkdir(parents=True)
        (d / "meta.json").write_text(
            json.dumps({"response": {"zip_size": 4, "unzip_size": 40}}), encoding="utf-8"
        )
        (d / "payload.bin").write_bytes(b"\x00\x01\x02\x03\x04\x05\x06\x07" * 3)
        return root

    def test_broken_parse_falls_through_to_synthetic(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """golden 样本解析崩溃（非 TdxError 的 struct.error）→ 降级到下一源。"""
        import struct

        import tstdx.protocol.registry as registry

        def _boom(*args: object, **kwargs: object) -> None:
            raise struct.error("unpack requires a buffer of 999 bytes")

        monkeypatch.setattr(registry, "dispatch", _boom)
        golden = self._make_golden(tmp_path)
        cfg = SourcesConfig(
            order=["cache", "synthetic"], enabled={"cache": True, "synthetic": True}
        )
        router = DataSourceRouter(config=cfg, golden_root=golden)
        rows = router.kline("600000")
        assert router.last_source == "synthetic"
        assert rows and all(r.get("synthetic") for r in rows)
        assert any(s == "cache" for s, _ in router.last_errors)

    def test_missing_sample_records_unavailable(self, tmp_path: Path) -> None:
        cfg = SourcesConfig(
            order=["cache", "synthetic"], enabled={"cache": True, "synthetic": True}
        )
        router = DataSourceRouter(config=cfg, golden_root=tmp_path)
        rows = router.kline("600999")
        assert router.last_source == "synthetic"
        assert rows and all(r.get("synthetic") for r in rows)
        assert any(s == "cache" for s, _ in router.last_errors)


class TestBuildRouter:
    """全局配置读取失败 / web 总闸。"""

    def test_config_read_failure_warns_and_falls_back(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        import tstdx.config as config_mod

        def _boom() -> None:
            raise RuntimeError("配置中心不可用")

        monkeypatch.setattr(config_mod, "get_config", _boom)
        with caplog.at_level(logging.WARNING, logger="tstdx.sources"):
            router = build_router()
        assert isinstance(router, DataSourceRouter)
        assert any("build_router" in r.message for r in caplog.records)

    def test_web_master_switch_gates_web_source(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import tstdx.config as config_mod
        from tstdx.config.schema import SourcesConfig as SC

        fake_cfg = SimpleNamespace(
            sources=SC(
                order=["tdx", "web"],
                enabled={"tdx": True, "web": True},
            ),
            web=SimpleNamespace(enabled=False),
        )
        monkeypatch.setattr(config_mod, "get_config", lambda: fake_cfg)
        router = build_router()
        assert router.config.enabled["web"] is False  # 总闸为准
        active = router._active_sources()
        assert "web" not in active

    def test_web_enabled_keeps_web_source(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import tstdx.config as config_mod
        from tstdx.config.schema import SourcesConfig as SC

        fake_cfg = SimpleNamespace(
            sources=SC(order=["tdx", "web"], enabled={"tdx": True, "web": True}),
            web=SimpleNamespace(enabled=True),
        )
        monkeypatch.setattr(config_mod, "get_config", lambda: fake_cfg)
        router = build_router()
        assert router.config.enabled["web"] is True
