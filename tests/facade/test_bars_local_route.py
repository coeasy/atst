# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""bars() local 路由回归（v1.2.0 深审 W#1/W#3）。

旧实现叠加四重缺陷：normalize_symbol 返回 str 却访问 .market/.code、
调用不存在的 DayBarReader.resolve_path、DayBarReader 无 root 构造参数、
忽略 count/start 且默认 dict 输出违反 list[Bar] 契约——
route="local" 从第一行就裸崩 AttributeError，测试 fake 完全掩盖。
本文件以真实 DayBarReader + 合成 .day 文件锁定修复后语义。
"""

from __future__ import annotations

import struct
from collections.abc import Iterator
from pathlib import Path

import pytest

from tstdx.errors import TdxError
from tstdx.facade import UnifiedQuoteAPI

_CODE = "sh600519"
_SCALE = 100


def _build_day_file(root: Path, *, n: int = 8, base: int = 100) -> Path:
    """构造合成 .day 文件（int+scale 布局，升序日期，close 递增可断言窗口）。

    ``root`` 即 vipdoc 目录本身（与 resolve_vipdoc_path 语义一致）。
    """
    lday = root / "sh" / "lday"
    lday.mkdir(parents=True, exist_ok=True)
    recs = [
        struct.pack(
            "<IIIIIfII",
            20240101 + i,
            (base + i) * _SCALE,
            (base + 10 + i) * _SCALE,
            (base - 1 + i) * _SCALE,
            (base + 5 + i) * _SCALE,  # close = base+5+i：递增，用于窗口断言
            1_000_000.0 * (i + 1),
            1000 * (i + 1),
            (base + 4 + i) * _SCALE,
        )
        for i in range(n)
    ]
    path = lday / f"{_CODE}.day"
    path.write_bytes(b"".join(recs))
    return path


@pytest.fixture()
def vipdoc(tmp_path: Path) -> Iterator[Path]:
    _build_day_file(tmp_path)
    yield tmp_path


def test_bars_local_without_vipdoc_root_raises_tdxerror() -> None:
    """未配置 vipdoc_root：TdxError（可被路由体系捕获降级），非裸 AttributeError。"""
    api = UnifiedQuoteAPI(vipdoc_root=None)
    with pytest.raises(TdxError, match="vipdoc_root"):
        api.bars(_CODE, route="local")


def test_bars_local_returns_bar_objects_respecting_count(vipdoc: Path) -> None:
    """返回 list[Bar]（非 dict），count 窗口 = 最近 count 根。"""
    from tstdx.domain.models import Bar

    api = UnifiedQuoteAPI(vipdoc_root=str(vipdoc))
    bars = api.bars(_CODE, period="day", count=3, route="local")
    assert len(bars) == 3
    assert all(isinstance(b, Bar) for b in bars)
    # 升序文件取尾部 → 最近 3 根（close = base+5+i 的最后三个：110,111,112）
    assert [b.close for b in bars] == [110.0, 111.0, 112.0]


def test_bars_local_start_skips_most_recent(vipdoc: Path) -> None:
    """start 语义与 tdx 对齐：start=N 跳过最近 N 根再取 count 根。"""
    api = UnifiedQuoteAPI(vipdoc_root=str(vipdoc))
    bars = api.bars(_CODE, period="day", count=2, start=3, route="local")
    assert len(bars) == 2
    # 8 根升序（close=105..112）；start=3 → 跳过最近 3 根（110,111,112），
    # 再往前取 count=2 根 → 108,109
    assert [b.close for b in bars] == [108.0, 109.0]


def test_bars_local_rejects_week_month(vipdoc: Path) -> None:
    """vipdoc 本地仅 day：week/month 显式 TdxError（旧实现静默放行必炸）。"""
    api = UnifiedQuoteAPI(vipdoc_root=str(vipdoc))
    with pytest.raises(TdxError, match="仅支持 day"):
        api.bars(_CODE, period="week", route="local")


def test_bars_local_missing_file_raises_tdxerror(tmp_path: Path) -> None:
    """文件不存在：TdxError（供 auto 路由降级），非 AttributeError。"""
    (tmp_path / "vipdoc").mkdir()
    api = UnifiedQuoteAPI(vipdoc_root=str(tmp_path))
    with pytest.raises(TdxError, match="文件不存在"):
        api.bars(_CODE, route="local")
