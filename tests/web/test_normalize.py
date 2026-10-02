"""Web 归一化测试（§33.7）：验证 volume/amount/price 归一化契约。

覆盖：Sina ×1 / Tencent ×100 + ×10000 / Eastmoney ×100 /
自定义 normalizer 注册 / normalize_quote / normalize_bar。
"""

from __future__ import annotations

import pytest

pytest.importorskip("atst.web.normalize")

from atst.web.normalize import (
    BOC,
    EASTMONEY,
    HK,
    JSL,
    KLINE,
    SINA,
    TENCENT,
    VolumeNormalizer,
    market_timestamp,
    normalize_amount,
    normalize_bar,
    normalize_price,
    normalize_quote,
    normalize_volume,
    register_normalizer,
)


@pytest.mark.unit
class TestNormalize:
    """归一化函数测试。"""

    def test_sina_identity(self):
        """#1 Sina: volume ×1, amount ×1, price ×1（恒等）。"""
        assert normalize_volume(SINA, 1000) == 1000
        assert normalize_amount(SINA, 50000) == 50000
        assert normalize_price(SINA, 10.5) == 10.5

    def test_tencent_volume_x100(self):
        """#2 Tencent: volume 手→股 (×100)。"""
        assert normalize_volume(TENCENT, 5000) == 500000  # 5000 手 = 500000 股

    def test_tencent_amount_x10000(self):
        """#3 Tencent: amount 万元→元 (×10000)。"""
        assert normalize_amount(TENCENT, 100.0) == 1_000_000.0  # 100 万 = 100万

    def test_eastmoney_volume_x100(self):
        """#4 Eastmoney: volume 手→股 (×100)。"""
        assert normalize_volume(EASTMONEY, 2000) == 200000  # 2000 手 = 200000 股

    def test_eastmoney_price_div100(self):
        """#5 Eastmoney: price ×100 整数 → 元 (/100)。"""
        assert normalize_price(EASTMONEY, 10050) == 100.50  # 10050/100 = 100.50

    def test_eastmoney_amount_identity(self):
        """#6 Eastmoney: amount 元（恒等）。"""
        assert normalize_amount(EASTMONEY, 1000000) == 1000000

    def test_jsl_identity(self):
        """#7 JSL: 恒等。"""
        assert normalize_volume(JSL, 1000) == 1000
        assert normalize_amount(JSL, 50000) == 50000

    def test_hk_identity(self):
        """#8 HK: 恒等。"""
        assert normalize_volume(HK, 1000) == 1000
        assert normalize_amount(HK, 50000) == 50000

    def test_kline_identity_after_inline_scaling(self):
        """#9 Kline: 解析层已按市场内联缩放（A 股手→股 ×100 / 港美不缩放），
        归一器 identity 防二次缩放（深审 L6：旧 ×100 语义会造成 A 股量
        ×10000、港美股量错误 ×100 的双重缩放）。"""
        assert normalize_volume(KLINE, 3000) == 3000

    def test_boc_identity(self):
        """#10 BOC: 恒等（非行情语义）。"""
        assert normalize_volume(BOC, 1000) == 1000

    def test_unknown_source_identity(self):
        """#11 未知源 → 恒等回退。"""
        assert normalize_volume("unknown_source", 1000) == 1000
        assert normalize_amount("unknown_source", 50000) == 50000
        assert normalize_price("unknown_source", 10.5) == 10.5

    def test_normalize_quote(self):
        """#12 normalize_quote 完整字典归一化。"""
        raw = {
            "volume": 5000,  # 手
            "amount": 100.0,  # 万元
            "price": 10050,  # ×100 整数
            "open": 10000,
            "high": 10100,
            "low": 9900,
            "last_close": 9950,
            "limit_up": 11000,
            "limit_down": 9000,
            "avg_price": 10020,
        }
        result = normalize_quote(EASTMONEY, raw)
        assert result["volume"] == 500000  # ×100
        assert result["amount"] == 100.0  # 恒等
        assert result["price"] == 100.50  # /100
        assert result["open"] == 100.0
        assert result["high"] == 101.0
        assert result["low"] == 99.0
        assert result["last_close"] == 99.50
        assert result["limit_up"] == 110.0
        assert result["limit_down"] == 90.0
        assert result["avg_price"] == 100.20

    def test_normalize_bar(self):
        """#13 normalize_bar K 线归一化。"""
        raw = {
            "volume": 3000,  # 手
            "amount": 3000000.0,  # 元
            "open": 100.0,
            "close": 102.0,
            "high": 103.0,
            "low": 99.0,
        }
        result = normalize_bar(TENCENT, raw)
        assert result["volume"] == 300000  # ×100
        assert result["amount"] == 3000000.0 * 10000  # ×10000

    def test_register_custom_normalizer(self, monkeypatch: pytest.MonkeyPatch):
        """#14 自定义 normalizer 注册。

        注册表是模块级全局，登记进去的 ``test_custom`` 会一直留在里面，而
        ``tests/architecture/test_dispatch_targets.py`` 的 ⑥ 拿它与 web 源表**双向**对账：
        同一份测试文件先跑，那条判据就会凭空多出"没人能用上的归一化器"。所以这里在一份
        副本上注册，monkeypatch 收尾后全局表恢复原样。
        """
        from atst.web import normalize as normalize_module

        monkeypatch.setattr(
            normalize_module,
            "_NORMALIZER_REGISTRY",
            dict(normalize_module._NORMALIZER_REGISTRY),
        )

        @register_normalizer("test_custom")
        class CustomNormalizer(VolumeNormalizer):
            volume_scale = 200.0
            amount_scale = 1.0
            price_scale = 1.0

        assert normalize_volume("test_custom", 100) == 20000
        assert normalize_amount("test_custom", 100) == 100

    def test_volume_normalizer_rounding(self):
        """#15 归一化结果应 round 到 6 位小数。"""
        result = normalize_volume(TENCENT, 0.1)  # 0.1 手 = 10 股
        assert result == 10.0
        result2 = normalize_price(EASTMONEY, 100)  # 100/100 = 1.0
        assert result2 == 1.0

    def test_normalize_quote_preserves_extra_keys(self):
        """#16 normalize_quote 保留未识别的额外字段。"""
        raw = {"volume": 100, "amount": 200, "custom_field": "hello"}
        result = normalize_quote(SINA, raw)
        assert result["custom_field"] == "hello"


class TestMarketTimestamp:
    """市场时区渲染（v1.2.1 CI 红的一手根因）。

    时间戳必须按 ``Asia/Shanghai`` 渲染，**与运行机器的本地时区无关**——
    早先两个解析器用 ``time.localtime()``，同一份 golden 样本在本地（UTC+8）
    通过、在 CI（UTC）偏 8 小时，测试与数据双双漂移。
    """

    def test_renders_market_localtime(self):
        # 取自 tests/golden/v6/cninfo_announcements.json 的真机样本：
        # 1790697600 == 2026-09-29T16:00:00Z == 2026-09-30 00:00:00 +08:00
        assert market_timestamp(1_790_697_600, unit="s") == "2026-09-30 00:00:00"
        assert market_timestamp(1_790_697_600_000, unit="ms") == "2026-09-30 00:00:00"

    def test_result_is_independent_of_machine_timezone(self, monkeypatch):
        import os
        import time as _time

        original = os.environ.get("TZ")
        seen = set()
        for tz in ("UTC", "Asia/Shanghai", "America/New_York", "Pacific/Kiritimati"):
            monkeypatch.setenv("TZ", tz)
            if hasattr(_time, "tzset"):
                _time.tzset()
            seen.add(market_timestamp(1_790_697_600, unit="s"))
        monkeypatch.setenv("TZ", original or "UTC")
        if hasattr(_time, "tzset"):
            _time.tzset()
        assert seen == {"2026-09-30 00:00:00"}, f"渲染随机器时区漂移：{seen}"

    def test_rejects_non_numeric_and_non_positive(self):
        assert market_timestamp(None) == ""
        assert market_timestamp("2026-09-30") == ""
        assert market_timestamp(True) == ""  # bool 是 int 的子类，显式拒绝
        assert market_timestamp(0) == ""
        assert market_timestamp(-1) == ""
