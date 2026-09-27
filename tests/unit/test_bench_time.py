# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""P6 耗时基准场景的轻量测试（合成数据，离线可跑）。

不跑完整 ``bench_time.py`` CLI（避免秒级基准耗时长挂测试），只验证三类
场景函数在合成模式下可产出有效数据——CI benchmark-smoke job 负责完整基准
与 JSON 校验。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "benches"))

from bench_time import (  # noqa: E402
    SCENARIOS,
    scenario_parse_kline,
    scenario_parse_quotes,
    scenario_serialize,
)


@pytest.mark.unit
def test_parse_quotes_scenario_rows():
    out = scenario_parse_quotes(synthetic=True)
    assert out["rows"] == 1000
    assert out["payload"][0].code
    assert out["payload"][0].price > 0


@pytest.mark.unit
def test_parse_kline_scenario_rows():
    out = scenario_parse_kline(synthetic=True)
    assert out["rows"] == 80_000
    assert out["payload"][0].volume > 0


@pytest.mark.unit
def test_serialize_scenario_rows():
    out = scenario_serialize(synthetic=True)
    assert out["rows"] == 5400
    assert out["payload"][0]["code"]


@pytest.mark.unit
def test_scenario_registry_complete():
    assert set(SCENARIOS) == {"parse_quotes", "parse_kline", "serialize"}
