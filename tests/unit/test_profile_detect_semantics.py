"""profile.detect / presets 语义测试（审计 §2-15 专项）。

覆盖：
* 候选记录长度去重（top_rs 与 candidates 重叠不再双评）；
* ``price_scale`` 合理性步骤：×100→×1000 改判、降置信 50% 两分支；
* ``hint_market`` presets 先验接入真实权重（首要候选 +0.1 置信度）；
* ``match_preset`` 市场不匹配返回 None；
* reader/profile 与 profile/detect 的档案定位交叉引用。
"""

from __future__ import annotations

import struct

import pytest

from tstdx.errors import ProfileUndetectable
from tstdx.profile.detect import detect
from tstdx.profile.presets import match_preset

pytestmark = pytest.mark.unit


def _day_like_records(n: int = 40, price: int = 123456) -> bytes:
    """构造 32B/条的「A 股日线风」字节流：yyyymmdd@0 + 4 个 u32 价格字段。"""
    return b"".join(
        struct.pack("<IIIII", 20240101 + i, price, price, price, price) + b"\x00" * 12
        for i in range(n)
    )


class TestCandidateDedup:
    """步骤 2 候选去重（保序）。"""

    def test_top_rs_evaluated_once(self) -> None:
        result = detect(_day_like_records())
        scoring = [e for e in result.evidence if e.step == "scoring" and "rs=" in e.description]
        sizes = [e.details.get("record_size") for e in scoring]
        assert sizes and len(sizes) == len(set(sizes))

    def test_top_rs_still_first(self) -> None:
        result = detect(_day_like_records())
        scoring = [e for e in result.evidence if e.step == "scoring" and "rs=" in e.description]
        assert scoring[0].details.get("record_size") == 32


class TestPriceScaleSanity:
    """步骤 5'：价格缩放合理性。"""

    def test_normal_scale_untouched(self) -> None:
        result = detect(_day_like_records(price=123456))
        assert result.confidence > 0.5
        assert result.profile.price_scale == 100
        sanity = [e for e in result.evidence if e.step == "price_scale_sanity"]
        assert sanity and sanity[0].description.startswith("抽样价格量级")

    def test_rejudge_to_1000(self) -> None:
        # 600000/100 = 6000 元 > 5000 → 改判 ×1000 → 600 元 ∈ (0, 100000]
        result = detect(_day_like_records(price=600000))
        assert result.profile.price_scale == 1000
        sanity = [e for e in result.evidence if e.step == "price_scale_sanity"]
        assert "改判" in sanity[0].description
        # 置信度不因改判降低
        assert result.confidence > 0.5

    def test_downgrade_confidence_branch(self) -> None:
        # 中位数 2e8：/100=2e6 元 >5000，/1000=2e5 元 >100000 → 降置信 ×0.5
        data = _day_like_records(price=200_000_000)
        with pytest.raises(ProfileUndetectable) as excinfo:
            detect(data)
        # 降置信后的 0.37 低于阈值 0.5 → 明确报「无法探测」而非静默乱猜
        assert excinfo.value is not None

    def test_whitebox_downgrade_factor(self) -> None:
        from tstdx.profile.detect import PriceEncoding, _price_scale_sanity
        from tstdx.reader.profile import DataProfile

        profile = DataProfile(name="t", record_size=32, price_encoding=PriceEncoding.UINT32)
        new_scale, factor, note = _price_scale_sanity(
            _day_like_records(price=200_000_000), 32, profile
        )
        assert new_scale is None
        assert factor == pytest.approx(0.5)
        assert "降置信" in note


class TestHintMarketPrior:
    """hint_market presets 先验接入真实权重。"""

    def test_sh_hint_adds_confidence(self) -> None:
        data = _day_like_records()
        plain = detect(data)
        hinted = detect(data, hint_market=1)  # 1=沪 → SH_A 预设命中
        assert hinted.preset_name == "SH_A"
        assert plain.preset_name == ""
        assert hinted.confidence == pytest.approx(min(1.0, plain.confidence + 0.1))

    def test_unknown_market_no_prior(self) -> None:
        data = _day_like_records()
        hinted = detect(data, hint_market=250)  # 无此市场预设
        assert hinted.preset_name == ""
        assert hinted.confidence == pytest.approx(detect(data).confidence)


class TestMatchPreset:
    """match_preset 市场不匹配 → None（doctest 契约）。"""

    def test_cross_market_returns_none(self) -> None:
        assert match_preset("600519", 0) is None  # 600519 是沪市预设，市场 0=深

    def test_matching_market_returns_preset(self) -> None:
        preset = match_preset("600519", 1)
        assert preset is not None
        assert preset.name == "SH_A"

    def test_unknown_code_returns_none(self) -> None:
        assert match_preset("zz9999", 1) is None


class TestProfileDocCrossRef:
    """两套探测器的定位互指（防漂移）。"""

    def test_detect_docstring_points_to_reader_profile(self) -> None:
        import sys

        mod = sys.modules["tstdx.profile.detect"]  # 子模块对象（避开包属性被同名函数遮蔽）
        doc = mod.__doc__ or ""
        assert "reader/profile" in doc and "生产文件档案" in doc

    def test_reader_profile_docstring_points_back(self) -> None:
        import tstdx.reader.profile as m

        assert "profile/detect" in (m.__doc__ or "") or "网络帧档案" in (m.__doc__ or "")
