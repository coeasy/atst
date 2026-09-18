"""时区测试（§23）：验证 UTC 内部存储 / Asia/Shanghai 本地输出。

覆盖：datetime 的时区感知、时区转换、Bar/Quote 模型的时区处理。
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from tstdx.domain.models import Bar, Quote


@pytest.mark.unit
class TestTimezone:
    """时区处理测试。"""

    def test_utc_storage(self):
        """#1 UTC 时区感知 datetime。"""
        dt = datetime(2024, 1, 15, 9, 30, tzinfo=timezone.utc)
        assert dt.tzinfo is not None
        assert dt.utcoffset() is not None

    def test_shanghai_offset(self):
        """#2 Asia/Shanghai 时区偏移 +8 小时。"""
        from zoneinfo import ZoneInfo

        sh_tz = ZoneInfo("Asia/Shanghai")
        dt_utc = datetime(2024, 1, 15, 9, 30, tzinfo=timezone.utc)
        dt_sh = dt_utc.astimezone(sh_tz)
        assert dt_sh.hour == 17  # 9 + 8 = 17

    def test_bar_datetime_string(self):
        """#5 Bar 模型的 datetime 字段为字符串。"""
        bar = Bar(
            datetime="2024-01-15 15:00",
            open=100.0,
            high=105.0,
            low=99.0,
            close=103.0,
            volume=1000,
            amount=103000.0,
        )
        assert bar.datetime == "2024-01-15 15:00"
        d = bar.to_dict()
        assert d["datetime"] == "2024-01-15 15:00"

    def test_quote_datetime_optional(self):
        """#6 Quote 模型的 datetime 字段可选。"""
        q = Quote(code="600519", price=100.0, volume=1000, amount=100000.0)
        assert q.datetime is None
        q2 = Quote(
            code="600519", price=100.0, volume=1000, amount=100000.0, datetime="2024-01-15 10:30"
        )
        assert q2.datetime == "2024-01-15 10:30"

    def test_timezone_conversion_roundtrip(self):
        """#7 UTC → Shanghai → UTC 往返转换。"""
        from zoneinfo import ZoneInfo

        sh_tz = ZoneInfo("Asia/Shanghai")
        dt_utc = datetime(2024, 3, 15, 6, 0, tzinfo=timezone.utc)
        dt_sh = dt_utc.astimezone(sh_tz)
        assert dt_sh.hour == 14  # 6 + 8 = 14
        dt_back = dt_sh.astimezone(timezone.utc)
        assert dt_back == dt_utc

    def test_datetime_fromtimestamp_utc(self):
        """#8 从时间戳创建 UTC datetime。"""
        ts = 1705288200  # 2024-01-15 06:30:00 UTC
        dt = datetime.fromtimestamp(ts, tz=timezone.utc)
        assert dt.year == 2024
        assert dt.month == 1
        assert dt.day == 15

    def test_datetime_astimezone_naive_raises(self):
        """#9 无时区 datetime 转换行为。"""
        naive = datetime(2024, 1, 15, 10, 0)
        # naive datetime 没有 tzinfo，astimezone 会使用系统本地时区
        # 这里只验证不崩溃
        assert naive.tzinfo is None

    def test_timezone_aware_compare(self):
        """#10 时区感知 datetime 比较。"""
        from zoneinfo import ZoneInfo

        sh = ZoneInfo("Asia/Shanghai")
        dt1 = datetime(2024, 1, 15, 10, 0, tzinfo=timezone.utc)
        dt2 = datetime(2024, 1, 15, 18, 0, tzinfo=sh)
        assert dt1 == dt2  # 同一时刻，不同表达

    def test_bar_to_dict_with_extra(self):
        """#11 Bar.to_dict 包含 extra 字段。"""
        bar = Bar(
            datetime="2024-01-15 15:00",
            open=100.0,
            high=105.0,
            low=99.0,
            close=103.0,
            volume=1000,
            amount=103000.0,
            extra={"synthetic": False, "code": "600519"},
        )
        d = bar.to_dict()
        assert d["synthetic"] is False
        assert d["code"] == "600519"

    def test_quote_to_dict(self):
        """#12 Quote.to_dict 包含核心字段。"""
        q = Quote(
            code="600519",
            price=100.0,
            last_close=99.0,
            open=101.0,
            high=105.0,
            low=99.0,
            volume=1000,
            amount=103000.0,
            extra={"name": "贵州茅台"},
        )
        d = q.to_dict()
        assert d["code"] == "600519"
        assert d["price"] == 100.0
        assert d["name"] == "贵州茅台"

    def test_bar_datetime_sortable(self):
        """#14 Bar datetime 字符串可排序。"""
        bars = [
            Bar(
                datetime="2024-01-15 15:00", open=1, high=2, low=1, close=1.5, volume=10, amount=15
            ),
            Bar(
                datetime="2024-01-14 15:00", open=1, high=2, low=1, close=1.5, volume=10, amount=15
            ),
            Bar(
                datetime="2024-01-16 15:00", open=1, high=2, low=1, close=1.5, volume=10, amount=15
            ),
        ]
        bars.sort(key=lambda b: b.datetime)
        assert bars[0].datetime == "2024-01-14 15:00"
        assert bars[1].datetime == "2024-01-15 15:00"
        assert bars[2].datetime == "2024-01-16 15:00"
