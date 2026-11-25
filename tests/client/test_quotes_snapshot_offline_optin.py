# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""T-5 regression: ``_t_quotes_snapshot`` must not send the known-offline 0x054C by default."""

from __future__ import annotations

import threading
from typing import Any

import pytest

from atst.client._templates_standard import _StandardFamilyTemplates
from atst.client._trampoline import _CallOp, _ReqOp
from atst.errors import TdxError
from atst.protocol.commands import CMD, Family


def _template() -> _StandardFamilyTemplates:
    template = _StandardFamilyTemplates()
    template.timeout = 5.0
    template.family = Family.STANDARD
    template._errors_lock = threading.Lock()
    return template


def test_default_skips_the_offline_batch_command() -> None:
    template = _template()
    generator = template._t_quotes_snapshot(["600519"])
    first = next(generator)
    assert isinstance(first, _CallOp) and first.name == "quotes", (
        "default path must go straight to the per-symbol fallback"
    )
    with pytest.raises(StopIteration) as stop:
        generator.send([{"code": "600519", "price": 1.0}])
    assert stop.value.args[0] == [{"code": "600519", "price": 1.0}]
    assert template.last_errors == []


def test_explicit_opt_in_keeps_the_old_offline_then_fallback_path() -> None:
    template = _template()
    generator = template._t_quotes_snapshot(["600519"], try_offline_snapshot=True)
    first = next(generator)
    assert isinstance(first, _ReqOp) and first.cmd == CMD["quotes_snapshot"]

    second: Any = generator.throw(TdxError("0x054C known offline"))
    assert isinstance(second, _CallOp) and second.name == "quotes"
    with pytest.raises(StopIteration):
        generator.send([{"code": "600519", "price": 1.0}])
    assert any("0x054C" in str(exc) for _, exc in template.last_errors)
