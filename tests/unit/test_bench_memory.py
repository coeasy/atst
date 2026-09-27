# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""Q4 内存基准场景的轻量测试（合成数据，离线可跑）。

不跑完整 ``bench_memory.py`` CLI（避免 10s+ 基准耗时长挂测试），只验证
三类场景函数在合成模式下可产出有效数据——CI benchmark-smoke job 负责
完整基准与 JSON 校验。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "benches"))

from bench_memory import (  # noqa: E402
    SCENARIOS,
    scenario_kline,
    scenario_market,
    scenario_vipdoc,
)


@pytest.mark.unit
def test_market_scenario_rows():
    out = scenario_market(synthetic=True)
    assert out["rows"] == 5400
    assert out["payload"] and out["payload"][0].code


@pytest.mark.unit
def test_kline_scenario_rows():
    out = scenario_kline(synthetic=True, count=1000)
    assert out["rows"] == 320_000
    assert out["payload"][0].close > 0


@pytest.mark.unit
def test_vipdoc_scenario_rows():
    out = scenario_vipdoc(synthetic=True)
    assert out["rows"] == 80_000
    assert out["payload"][0].volume > 0


@pytest.mark.unit
def test_scenario_registry_complete():
    assert set(SCENARIOS) == {"market", "kline", "vipdoc"}
