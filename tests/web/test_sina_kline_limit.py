"""新浪 K 线 count 上限防御（2026-10-20 实测修复回归）。

实测（``quotes.sina.cn/cn/api/json_v2.php/CN_MarketDataService.getKLineData``，
``sh600519 day ma=no``）：``datalen`` 到 1970 仍返回数组，1971 起整体返回 JSON 裸
``null``。旧实现把 ``json.loads`` 的结果直接当数组迭代，``for r in None`` 抛裸
``TypeError`` → 被执行器收口成 ``[E9000] 未处理异常``，调用方从错误里看不出
"请求条数超限"。修复后：单次请求钳到 :data:`~atst.web.limits.SINA_KLINE_MAX`
并告警；``parse_bars`` 对非数组载荷给出可读的 :class:`~atst.errors.SourceDeprecated`。
"""

from __future__ import annotations

import json
import logging

import pytest

from atst.errors import SourceDeprecated
from atst.web.base import HttpResponse, RateLimiter
from atst.web.limits import SINA_KLINE_MAX
from atst.web.sina.adapters import SinaHistoryKlineSource


class _FakeHttp:
    """固定罐头响应 + 记录请求 URL 的最小 HTTP 替身。"""

    def __init__(self, body: bytes) -> None:
        self.body = body
        self.calls: list[str] = []

    def get(self, url, **kwargs):  # noqa: ARG002
        self.calls.append(url)
        return HttpResponse(200, self.body, {})

    def close(self) -> None:
        pass


def _rows_body(n: int) -> bytes:
    """n 根合法日线罐头（钳制后的请求会消费它）。"""

    return json.dumps(
        [
            {
                "day": f"2026-01-{1 + i % 28:02d}",
                "open": "1.0",
                "high": "2.0",
                "low": "0.5",
                "close": "1.5",
                "volume": "100",
            }
            for i in range(n)
        ]
    ).encode("utf-8")


def _src(body: bytes) -> SinaHistoryKlineSource:
    src = SinaHistoryKlineSource(max_retries=0)
    src.client = _FakeHttp(body)
    src.rate_limiter = RateLimiter()
    return src


class TestSinaKlineCountClamp:
    def test_over_limit_is_clamped_and_warned(self, caplog: pytest.LogCaptureFixture) -> None:
        """count 超实测上限 → 实际请求钳到 SINA_KLINE_MAX 并打告警。"""

        src = _src(_rows_body(SINA_KLINE_MAX))
        with caplog.at_level(logging.WARNING, logger="atst.web.sina.adapters"):
            bars = src.fetch_bars("sh600519", period="day", count=2000)

        assert len(bars) == SINA_KLINE_MAX
        # 请求真的落在实测安全值上，而不是把 2000 原样发出去
        assert f"datalen={SINA_KLINE_MAX}" in src.client.calls[0]
        assert any("钳制" in rec.getMessage() for rec in caplog.records)

    def test_within_limit_not_clamped(self, caplog: pytest.LogCaptureFixture) -> None:
        src = _src(_rows_body(100))
        with caplog.at_level(logging.WARNING, logger="atst.web.sina.adapters"):
            bars = src.fetch_bars("sh600519", period="day", count=100)

        assert len(bars) == 100
        assert "datalen=100" in src.client.calls[0]
        assert not any("钳制" in rec.getMessage() for rec in caplog.records)


class TestSinaKlineNullPayload:
    def test_null_payload_is_typed_not_attributeerror(self) -> None:
        """超限返回的 JSON 裸 ``null`` → SourceDeprecated，而不是裸 TypeError。"""

        src = SinaHistoryKlineSource(max_retries=0)
        with pytest.raises(SourceDeprecated) as ei:
            src.parse_bars("null")

        assert "不是行数组" in str(ei.value)
        assert ei.value.code == "E7030"
        assert ei.value.context["payload_type"] == "NoneType"

    def test_object_payload_is_typed(self) -> None:
        """错误对象（非行数组）同走 SourceDeprecated，不再冒 AttributeError。"""

        src = SinaHistoryKlineSource(max_retries=0)
        with pytest.raises(SourceDeprecated):
            src.parse_bars('{"result": "error"}')
