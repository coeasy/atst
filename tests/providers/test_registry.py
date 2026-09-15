from __future__ import annotations

import pytest

from tstdx.errors import ValidationError
from tstdx.providers import PROVIDERS, ChannelSpec, resolve_provider


def test_tdx_is_only_default_provider() -> None:
    assert PROVIDERS.default_provider == "tdx"
    assert "tdx" in PROVIDERS.ids()
    assert "tencent" in PROVIDERS.ids()
    assert "sina" in PROVIDERS.ids()
    assert "eastmoney" in PROVIDERS.ids()


def test_vipdoc_is_standalone_local_provider_not_tdx_channel() -> None:
    # v13 起 local_vipdoc 是独立 Provider（见
    # docs/ARCHITECTURE_SEMANTIC_ALIGNMENT_v13.md: "local_vipdoc is a standalone
    # Provider"）。tdx 只拥有在线协议 channel，本地 vipdoc 不再挂在 tdx 上，
    # 以免本地历史文件冒充在线 TDX 行情。
    assert "vipdoc" not in PROVIDERS.ids()
    assert "local_vipdoc" in PROVIDERS.ids()
    assert PROVIDERS.get("local_vipdoc").channel("vipdoc").local is True
    assert PROVIDERS.supports("local_vipdoc", "bars", channel="vipdoc") is True
    with pytest.raises(ValidationError):
        PROVIDERS.get("tdx").channel("vipdoc")
    assert resolve_provider(provider="vipdoc") == "local_vipdoc"


def test_jsl_registry_exposes_only_verified_convertible_bond_channel() -> None:
    jsl = PROVIDERS.get("jsl")
    assert {channel.id for channel in jsl.channels} == {"bond"}
    # canonical capability 名为 convertible_bond（见 tstdx/typed_query.py），
    # 早期测试使用的 "bond" 是历史别名残留。
    assert jsl.channel("bond").capabilities == frozenset({"convertible_bond"})
    with pytest.raises(ValidationError):
        jsl.channel("etf")
    assert PROVIDERS.supports("jsl", "etf") is False


def test_source_is_only_provider_selector_alias() -> None:
    assert resolve_provider(provider="tdx") == "tdx"
    assert resolve_provider(source="tdx") == "tdx"
    assert resolve_provider(provider="tencent", source="qq") == "tencent"


def test_conflicting_provider_and_source_fail_fast() -> None:
    with pytest.raises(ValidationError):
        resolve_provider(provider="tdx", source="sina")


def test_market_and_channel_are_not_provider_ids() -> None:
    with pytest.raises(ValidationError):
        PROVIDERS.get("hk")
    with pytest.raises(ValidationError):
        PROVIDERS.get("kline")


def test_only_verified_capability_batch_limits_are_declared() -> None:
    quotation = PROVIDERS.get("tdx").channel("quotation")
    assert quotation.batch_limits == (("quotes", 60),)
    assert quotation.batch_limit_for("quotes") == 60
    assert quotation.batch_limit_for("bars") is None
    assert not hasattr(quotation, "batch_limit")
    assert PROVIDERS.get("tencent").channel("quote").batch_limits == ()
    assert PROVIDERS.get("sina").channel("quote").batch_limits == ()
    assert PROVIDERS.get("eastmoney").channel("quote").batch_limits == ()


def test_channel_spec_new_batch_limits_do_not_move_historical_notes_position() -> None:
    channel = ChannelSpec(
        "legacy",
        frozenset({"quotes"}),
        frozenset({"cn_a"}),
        True,
        False,
        "legacy positional notes",
    )
    assert channel.notes == "legacy positional notes"
    assert channel.batch_limits == ()


def test_channel_spec_rejects_batch_limit_for_undeclared_capability() -> None:
    with pytest.raises(ValueError, match="not declared"):
        ChannelSpec.build(
            "quote",
            {"quotes"},
            batch_limits={"bars": 60},
        )
