"""C9 · 腾讯 K 线超限自动分段测试（v5 PG8 落地）。

用 fake HTTP 层模拟「每次最多返回 800 根、按 end 日期向前翻页」的
上游行为，断言 ``fetch_bars(count=2000)`` 拼接去重后返回 2000 根，
且请求次数、分段右边界递进正确。
"""

from __future__ import annotations

from typing import Any

import pytest

from tstdx.web.adapters import KlineSource
from tstdx.web.limits import TENCENT_KLINE_MAX


class _FakeSegmentHttp:
    """模拟腾讯 fqkline 分段行为：忽略 end → 返回最新 800 根；带 end → 返回
    该日期（含）之前 seg 根。由测试预生成完整历史池。"""

    def __init__(self, pool: list[dict[str, Any]], *, honor_end: bool = True) -> None:
        self.pool = pool  # 按时间升序的完整历史
        self.honor_end = honor_end
        self.calls: list[tuple[int, str | None]] = []  # (seg, end)

    def get(self, url: str, timeout: float = 10.0, **kwargs: object) -> object:  # noqa: ARG002
        # 解析 param=symbol,period,,end,count,fq
        param = url.split("param=")[1]
        parts = param.split(",")
        end = parts[3] or None
        seg = int(parts[4])
        self.calls.append((seg, end))
        rows = self.pool
        if self.honor_end and end:
            rows = [r for r in rows if str(r["datetime"])[:10] <= end]
        return _json_response(rows[-seg:])


class _Resp:
    ok = True  # _request_http 检查 resp.ok

    def __init__(self, text: str) -> None:
        self._text = text

    def text(self, encoding: str = "utf-8") -> str:  # noqa: ARG002
        return self._text


def _json_response(rows: list[dict[str, Any]]) -> _Resp:
    import json

    data = [[r["datetime"], r["open"], r["close"], r["high"], r["low"], r["volume"]] for r in rows]
    return _Resp(json.dumps({"data": {"sh600519": {"day": data}}}))


def _pool(n: int) -> list[dict[str, Any]]:
    return [
        {
            "datetime": f"2020-{1 + i // 28:02d}-{1 + i % 28:02d}",
            "open": 1.0,
            "close": 2.0,
            "high": 3.0,
            "low": 0.5,
            "volume": 100.0,
        }
        for i in range(n)
    ]


@pytest.mark.parametrize("honor_end", [True, False])
def test_fetch_bars_over_limit_auto_segments(honor_end: bool) -> None:
    total = 2000
    http = _FakeSegmentHttp(_pool(total), honor_end=honor_end)
    src = KlineSource(client=http)  # type: ignore[arg-type]
    bars = src.fetch_bars("sh600519", count=total)
    if honor_end:
        assert len(bars) == total
        dts = [str(b.datetime) for b in bars]
        assert dts == sorted(dts)
        assert len(set(dts)) == total  # 无重复
    else:
        # 上游不支持 end 翻页 → 拿到首段即停，绝不死循环
        assert len(bars) == TENCENT_KLINE_MAX
    assert len(http.calls) <= 5
    assert all(seg <= TENCENT_KLINE_MAX for seg, _ in http.calls)


def test_fetch_bars_within_limit_single_call() -> None:
    http = _FakeSegmentHttp(_pool(100))
    src = KlineSource(client=http)  # type: ignore[arg-type]
    bars = src.fetch_bars("sh600519", count=100)
    assert len(bars) == 100
    assert len(http.calls) == 1
    assert http.calls[0] == (100, None)


def test_fetch_bars_history_exhausted() -> None:
    """池中只有 300 根，请求 2000 → 拿到全部 300 根，不死循环。"""
    http = _FakeSegmentHttp(_pool(300))
    src = KlineSource(client=http)  # type: ignore[arg-type]
    bars = src.fetch_bars("sh600519", count=2000)
    assert len(bars) == 300
