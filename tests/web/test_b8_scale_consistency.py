"""B8 · 缩放口径三处一致性测试（spec ↔ normalizer ↔ 解析层锚点）。

数据契约：价格=元、成交量=股、成交额=元。三个维护点：
1. ``web/sources.py`` 的 ``SourceSpec.{price,volume,amount}_scale``；
2. ``web/normalize.py`` 的各源 ``VolumeNormalizer`` 缩放系数；
3. 解析层内联缩放（如腾讯 K 线按市场分支 ×100/×1）。

本测试断言 1↔2 一致（已知例外显式列出），并用锚点用例钉住解析层
实际行为，防止任何一处漂移。
"""

from __future__ import annotations

import pytest

from atst.web import normalize as norm
from atst.web.normalize import _NORMALIZER_REGISTRY
from atst.web.sources import KNOWN_SOURCES

#: 已知例外 A：normalizer 刻意 identity（解析层按市场分支缩放，见
#: normalize.py KlineNormalizer 注释——A 股 ×100、港美 ×1，spec 的
#: volume_scale=100 仅描述 A 股分支）
IDENTITY_NORMALIZERS = {"kline", "minute_kline"}

#: 已知例外 B：normalizer identity，但**解析层已内联缩放**（每处均在
#: normalizer docstring 中声明，下方锚点测试实证）
PARSE_LAYER_INLINE = {
    "minute",  # adapters_ext.py: cum_vol * 100.0（手→股）
    "ticks",  # ticks.py:168        volume * 100（手→股）
    "trends",  # ticks.py（东财 trends2）量手→股 内联
    "limit_pool",  # fundflow.py:593     price / 1000（×1000 整数→元）
}


def test_spec_and_normalizer_scales_consistent() -> None:
    """每个已注册 normalizer 的源，其 spec 缩放系数必须与 normalizer 一致。"""
    mismatch: list[str] = []
    for source, ncls in sorted(_NORMALIZER_REGISTRY.items()):
        spec = KNOWN_SOURCES.get(source)
        if spec is None:
            continue  # normalizer 无对应 spec（内部源），跳过
        if source in IDENTITY_NORMALIZERS | PARSE_LAYER_INLINE:
            continue  # 已知例外：解析层内联 / 按市场分支，spec 为声明元数据
        if ncls.volume_scale != spec.volume_scale:
            mismatch.append(f"{source}: volume spec={spec.volume_scale} norm={ncls.volume_scale}")
        if ncls.amount_scale != spec.amount_scale:
            mismatch.append(f"{source}: amount spec={spec.amount_scale} norm={ncls.amount_scale}")
        if ncls.price_scale != spec.price_scale:
            mismatch.append(f"{source}: price spec={spec.price_scale} norm={ncls.price_scale}")
    assert not mismatch, "缩放口径漂移（spec vs normalizer）:\n" + "\n".join(mismatch)


@pytest.mark.parametrize(
    ("source", "price", "volume", "amount"),
    [
        # 锚点（来源：normalize.py 各 Normalizer docstring + 逆向事实）
        ("sina", 1.0, 1.0, 1.0),  # 新浪原生即 股/元
        ("tencent", 1.0, 100.0, 10_000.0),  # 手→股 ×100；万元→元 ×10000
        ("eastmoney", 1 / 100.0, 100.0, 1.0),  # 价格 ×100 int；量手→股；额元
    ],
)
def test_scale_anchors(source: str, price: float, volume: float, amount: float) -> None:
    """解析层实际行为锚点：spec / normalizer / 已知逆向事实三者一致。"""
    spec = KNOWN_SOURCES[source]
    ncls = _NORMALIZER_REGISTRY[source]
    assert (spec.price_scale, spec.volume_scale, spec.amount_scale) == (price, volume, amount)
    assert (ncls.price_scale, ncls.volume_scale, ncls.amount_scale) == (price, volume, amount)


def test_normalizer_scale_application() -> None:
    """normalizer 实际应用缩放正确（钉住实现而非仅声明）。"""
    tencent = _NORMALIZER_REGISTRY["tencent"]()
    assert tencent.normalize_volume(123.0) == pytest.approx(12_300.0)  # 手→股
    assert tencent.normalize_amount(5.0) == pytest.approx(50_000.0)  # 万元→元
    east = _NORMALIZER_REGISTRY["eastmoney"]()
    assert east.normalize_price(16_955.0) == pytest.approx(169.55)  # ×100 int → 元
    assert east.normalize_volume(500.0) == pytest.approx(50_000.0)


def test_identity_normalizers_do_not_double_scale() -> None:
    """identity normalizer 必须 identity——防对解析层已缩放量二次 ×100。"""
    for name in IDENTITY_NORMALIZERS | PARSE_LAYER_INLINE:
        n = _NORMALIZER_REGISTRY[name]()
        assert n.normalize_volume(7.0) == 7.0
        assert n.normalize_price(7.0) == 7.0
        assert n.normalize_amount(7.0) == 7.0


def test_normalize_registry_exported() -> None:
    """registry 经由包公共入口可用（防私有化后测试误用失效）。"""
    assert "tencent" in norm._NORMALIZER_REGISTRY  # noqa: SLF001 —— 测试白盒
