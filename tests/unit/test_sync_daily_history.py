# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""``scripts/sync_daily_history.py`` 里那几条"踩过才知道"的逻辑的判据。

脚本在 ``scripts/`` 下、不是 ``atst`` 包的一部分，所以这里按文件路径加载，
不去伪造一个包结构。要钉死的是**纯逻辑**——尤其是"指数位从代码段推出来"这条：
``0x052D`` 的 index 位弄反，主站回的是 `5616-57-83` 这种荒唐日期，
而它在本地看完全像一次网络抖动，极难定位。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = _ROOT / "scripts" / "sync_daily_history.py"

#: 模块级导入会 add sys.path 并 import atst，重复加载会换来一堆告警，缓存住。
@ pytest.fixture(scope="module")
def sync() -> ModuleType:
    spec = importlib.util.spec_from_file_location("atst_sync_daily_history", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # 必须先注册进 sys.modules：脚本里的 Target/State/Outcome 都是 @dataclass，
    # 而 dataclasses 靠 cls.__module__ 反查模块命名空间（本不存在的名字要解析成
    # 类型），没注册就会 AttributeError: 'NoneType' has no attribute '__dict__'。
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _row(day: str, *, high: float = 10.0, low: float = 9.0, close: float = 9.5,
         open_: float = 9.0) -> dict[str, object]:
    return {"date": day, "datetime": f"{day} 15:00", "open": open_,
            "high": high, "low": low, "close": close, "volume": 100}


# ---------------------------------------------------------------------------
# 指数位自动判定
# ---------------------------------------------------------------------------


def test_index_bit_is_derived_from_code_prefix(sync: ModuleType) -> None:
    """sh000* / sz399* 走指数位，其余走个股位；B 股（sh900*/sz200*）是个股。"""
    assert sync.Target.parse("sh000001").index is True
    assert sync.Target.parse("sz399006").index is True
    assert sync.Target.parse("sh000300").index is True
    assert sync.Target.parse("sh600519").index is False
    assert sync.Target.parse("sz900001").index is False


def test_symbol_prefix_is_tolerated_and_normalized(sync: ModuleType) -> None:
    """三种写法（带/不带市场前缀、大小写）必须归一到同一个代码。"""
    assert sync.Target.parse("sh600519") == sync.Target.parse("SH600519") == sync.Target.parse("600519")
    assert sync.Target.parse("600519").symbol == "sh600519"
    # 6 开头补 sh，其余补 sz
    assert sync.Target.parse("000001").symbol == "sz000001"
    assert sync.Target.parse("300750").symbol == "sz300750"


def test_index_flag_overrides_auto_detection(sync: ModuleType) -> None:
    """--index / --no-index 是整体覆盖，不是"默认再判一次"。"""
    assert sync.Target.parse("sh600519", force_index=True).index is True
    assert sync.Target.parse("sh000001", force_index=False).index is False


# ---------------------------------------------------------------------------
# 续拉：断点不依赖 state.json
# ---------------------------------------------------------------------------


def test_resume_count_falls_back_to_the_date_on_disk(sync: ModuleType, tmp_path: Path) -> None:
    """state 丢了也不退化成"最近 lookback 根从头再拉"——从磁盘那支尾部读回来。"""
    path = tmp_path / "sh600519.parquet"
    sync.write(
        [_row("2026-09-29"), _row("2026-09-30")],
        str(path),
        fmt="parquet",
    )
    empty_state = sync.State()

    assert sync._last_date_on_disk(path) == "2026-09-30"
    assert sync._resume_bar_count(empty_state, "sh600519", False, 320, "2026-09-30") == 320 + 30
    # 没有磁盘证据也没有 state 时才退回 lookback
    assert sync._resume_bar_count(empty_state, "sh600519", False, 320, "") == 320
    # --full 无视一切证据直接拉满窗口
    assert sync._resume_bar_count(empty_state, "sh600519", True, 320, "2026-09-30") == sync.MAX_WINDOW


def test_last_date_on_disk_tolerates_missing_and_broken_files(sync: ModuleType, tmp_path: Path) -> None:
    """坏文件不该让整轮同步崩掉，按"没存过"处理。"""
    assert sync._last_date_on_disk(tmp_path / "nope.parquet") == ""
    broken = tmp_path / "broken.parquet"
    broken.write_text("not a parquet", encoding="utf-8")
    assert sync._last_date_on_disk(broken) == ""


# ---------------------------------------------------------------------------
# 合并与自检
# ---------------------------------------------------------------------------


def test_rows_without_a_date_key_are_dropped(sync: ModuleType) -> None:
    """坏代码会回 `8414-91-57` 这种日期；没有日期键的行进不了时间序列。"""
    merged = sync._dedup_merge([_row("2026-01-05"), {"date": "8414-91-57"}, _row("2026-01-06")])
    assert [row["date"] for row in merged] == ["2026-01-06", "2026-01-05"] or len(merged) == 2
    assert all(row["date"] == "2026-01-05" or row["date"] == "2026-01-06" for row in merged)
    assert len(merged) == 2


def test_validate_rejects_unsorted_series_and_bad_ohlc(sync: ModuleType) -> None:
    """自检要在合并排序之后才做：原始批次倒序不该被当成"日期非升序"。"""
    assert sync._validate(sync._dedup_merge([_row("2026-01-06"), _row("2026-01-05")])) == []

    broken = sync._dedup_merge([_row("2026-01-05", high=9.0, low=10.0)])
    assert any("high<low" in problem for problem in sync._validate(broken))

    missing = sync._dedup_merge([{"date": "2026-01-05"}])
    assert any("缺 OHLC" in problem for problem in sync._validate(missing))


def test_scan_enumerates_every_code_in_a_segment(sync: ModuleType) -> None:
    """--scan 的探测器必须覆盖段内 000-999 全部候选。"""
    codes = list(sync.iter_scan_codes(["sh600"]))
    assert len(codes) == 1000
    assert codes[:3] == ["sh600000", "sh600001", "sh600002"]
    assert codes[-1] == "sh600999"


# ---------------------------------------------------------------------------
# 默认宇宙
# ---------------------------------------------------------------------------


def test_default_universe_is_real_and_mixture_of_stocks_and_indices(sync: ModuleType) -> None:
    """内置宇宙是"零参数可跑"的最后一道兜底，别让它掺进主站认不出来的代码。"""
    targets = [sync.Target.parse(symbol) for symbol, _ in sync.DEFAULT_UNIVERSE]
    assert targets, "内置宇宙不能是空的"
    assert any(target.index for target in targets), "没有指数的话默认宇宙覆盖不到指数位"
    assert any(not target.index for target in targets)
    for target in targets:
        assert target.symbol.startswith(("sh", "sz")), target.symbol
        assert target.index == target.symbol.startswith(sync.INDEX_PREFIXES)
