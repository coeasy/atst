from __future__ import annotations

import importlib
import sys
from types import ModuleType

import pytest


def _native_module():  # noqa: ANN202
    cached = sys.modules.get("tstdx.native")
    if cached is not None:
        return cached
    with pytest.warns(UserWarning, match="tstdx.native"):
        return importlib.import_module("tstdx.native")


def test_retired_native_layer_is_always_repository_owned_python() -> None:
    native = _native_module()

    assert native.native is None
    assert native.NATIVE_AVAILABLE is False
    assert native.disable_reason is not None
    assert "retired" in native.disable_reason


def test_external_same_name_module_cannot_reactivate_retired_native(monkeypatch) -> None:  # noqa: ANN001
    native = _native_module()
    fake = ModuleType("tstdx_native")
    fake.decode_tdx_float = lambda raw: -999.0  # type: ignore[attr-defined]
    fake.read_day_file = lambda path, market: [{"foreign": True}]  # type: ignore[attr-defined]
    fake.parse_kline_payload = lambda *args: [{"foreign": True}]  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "tstdx_native", fake)

    with pytest.warns(UserWarning, match="tstdx.native"):
        reloaded = importlib.reload(native)

    assert reloaded.native is None
    assert reloaded.NATIVE_AVAILABLE is False
    assert reloaded.decode_tdx_float(0) != -999.0


def test_retired_native_selftest_requires_python_fallback_parity() -> None:
    native = _native_module()

    result = native.selftest()

    assert result["importable"] is False
    assert result["native_available"] is False
    assert result["fallback_parity"] is True
    assert result["checks"] == {
        "native_policy": "ok: retired; external module not loaded",
        "decode_tdx_float": "ok",
        "read_day_file": "ok",
        "parse_kline_payload": "ok",
    }


def test_invalid_legacy_lot_factor_fails_closed() -> None:
    native = _native_module()

    with pytest.raises(NotImplementedError, match="lot_factor=2"):
        native.parse_kline_payload(b"\x00\x00", category=4, lot_factor=2)
