# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""行情时间出口：``quote_datetime_from_extra`` 与 ``normalize_quote`` 回填。

``Quote.datetime`` 是契约里唯一的行情时间字段，此前各 Web 源把时间拆在
``extra``（tencent 14 位紧凑串 / sina date+time / eastmoney ``f86`` /
baidu unix 秒）里，``datetime`` 全源恒为 ``None``。本文件锁死三件事：

1. 三种上游时间形态都拼成市场时区的完整字符串；
2. 拼不出完整日期＋时间就返回空串（不拿时刻碎片或机器时钟冒充行情时间）；
3. ``normalize_quote`` 会把它回填到 ``Quote.datetime``，且不覆盖已有值。
"""

from __future__ import annotations

import pytest

from atst.domain.models import Quote
from atst.web._base_core import BaseWebSource
from atst.web.normalize import market_timestamp, quote_datetime_from_extra

#: 一个真实存在的 unix 秒（2026-10-20 15:00:00 Asia/Shanghai）
UNIX_SECONDS = 1760941200


class _Source(BaseWebSource):
    """只借用 normalize_quote 回填逻辑的最小子类。"""

    source_name = "sina"

    def build_url(self, symbols, **kwargs):  # pragma: no cover - 不使用
        return ""

    def parse(self, text, symbols, **kwargs):  # pragma: no cover - 不使用
        return []


@pytest.fixture
def source() -> _Source:
    return _Source()


class TestQuoteDatetimeFromExtra:
    @pytest.mark.parametrize(
        ("extra", "expected"),
        [
            # tencent：14 位紧凑串 YYYYMMDDHHMMSS
            ({"time": "20261020150000"}, "2026-10-20 15:00:00"),
            # sina：date + 完整时刻
            ({"date": "2026-10-20", "time": "15:00:00"}, "2026-10-20 15:00:00"),
            # sina：date + HH:MM（无秒）——补 :00，不丢日期
            ({"date": "2026-10-20", "time": "15:00"}, "2026-10-20 15:00:00"),
            # sina：紧凑 date + 紧凑 time
            ({"date": "20261020", "time": "150000"}, "2026-10-20 15:00:00"),
        ],
    )
    def test_renders_full_market_datetime(self, extra: dict, expected: str) -> None:
        assert quote_datetime_from_extra(extra) == expected

    def test_unix_seconds_use_market_timezone(self) -> None:
        """eastmoney f86 / baidu time：unix 秒 → 市场时区字符串。"""

        expected = market_timestamp(UNIX_SECONDS, unit="s")
        assert quote_datetime_from_extra({"time": UNIX_SECONDS}) == expected
        # 浮点秒（部分源给 float）同样成立
        assert quote_datetime_from_extra({"time": float(UNIX_SECONDS)}) == expected

    @pytest.mark.parametrize(
        "extra",
        [
            {},
            {"time": ""},
            {"time": None},
            {"time": "15:00:00"},  # 只有时刻：没有日期的碎片不冒充完整时间
            {"time": "2026-10-20"},  # 只有日期
            {"time": True},  # bool 不是时间
            {"time": "not-a-time"},
            {"time": -1},
        ],
    )
    def test_unusable_fragments_return_empty(self, extra: dict) -> None:
        assert quote_datetime_from_extra(extra) == ""


class TestNormalizeQuoteBackfill:
    def test_backfills_datetime_from_extra(
        self, source: _Source, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("atst.web._base_core._normalize_price", lambda src, v: v, raising=False)
        q = Quote(code="sh600519", price=1324.97, extra={"time": "20261020150000"})
        out = source.normalize_quote(q)
        assert out.datetime == "2026-10-20 15:00:00"
        # 回填只写契约字段，不动 extra 本身
        assert out.extra == {"time": "20261020150000"}

    def test_does_not_overwrite_existing_datetime(self, source: _Source) -> None:
        q = Quote(code="sh600519", datetime="2000-01-01 00:00:00", extra={"time": "20261020150000"})
        assert source.normalize_quote(q).datetime == "2000-01-01 00:00:00"

    def test_stays_empty_when_no_usable_time(self, source: _Source) -> None:
        q = Quote(code="sh600519", extra={})
        assert source.normalize_quote(q).datetime == ""
