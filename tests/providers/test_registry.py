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


def test_the_registry_declares_no_batch_quota() -> None:
    """注册表不再声称批量上限（F-50）。

    旧形状是 ``ChannelSpec.batch_limits={"quotes": 60}`` → ``batch_limit_for()`` →
    ``plan.batch_limit`` → **无人读取**：一条完整的死链，而且那个 60 与真正生效的
    分片上限 ``tstdx/client/_mixin.py`` 的 ``_QUOTES_SNAPSHOT_BATCH = 80`` 直接矛盾。
    按"无理由孤儿一律接线或删除"，声称数字而无人执行的一侧删除，数字只留在执行它
    的那处代码里。
    """

    import dataclasses
    import inspect

    from tstdx.client._mixin import _QUOTES_SNAPSHOT_BATCH

    fields = {item.name for item in dataclasses.fields(ChannelSpec)}
    assert "batch_limits" not in fields
    assert not hasattr(ChannelSpec, "batch_limit_for")
    for spec in (PROVIDERS.get(pid) for pid in PROVIDERS.ids()):
        for channel in spec.channels:
            assert "batch_limit" not in repr(channel)
    assert "batch_limits" not in inspect.signature(ChannelSpec.build).parameters
    # 批量上限只由真正分片的那处代码持有，注册表不再另报一个数（旧值 60 与此矛盾）。
    assert _QUOTES_SNAPSHOT_BATCH == 80
    with pytest.raises(TypeError, match="batch_limits"):
        ChannelSpec.build("quote", {"quotes"}, batch_limits={"quotes": 60})


def test_channel_spec_positional_shape_after_the_claim_fields_are_gone() -> None:
    """位置式构造是注册表外的公开写法，删字段会静默改变位置含义——这里钉住新形状。

    ``markets`` / ``notes`` 删除（F-54）后位置为 id, capabilities, live, local, periods；
    两个已删字段作为关键字传入当场报错，不会被静默吞掉。
    """

    channel = ChannelSpec(
        "legacy",
        frozenset({"quotes"}),
        True,
        False,
        frozenset({"day"}),
    )
    assert channel.id == "legacy"
    assert channel.live is True
    assert channel.local is False
    assert channel.periods == frozenset({"day"})
    assert channel.supports_period("DAY")
    for kw in ("markets", "notes", "batch_limits"):
        with pytest.raises(TypeError, match=kw):
            ChannelSpec.build("quote", {"quotes"}, **{kw: ("cn_a",)})


def test_channel_spec_rejects_periods_for_undeclared_capability() -> None:
    """注册表仍然只校验它**真的**会执行的那条规则：周期只属于 bars channel。"""

    with pytest.raises(ValueError, match="non-bars"):
        ChannelSpec.build("quote", {"quotes"}, periods=("day",))


def _unread_fields(cls: type, owners: set[str]) -> tuple[set[str], int, set[str]]:
    """扫 ``tstdx/`` 找 ``cls`` 各字段的读取点：返回 (全部字段, 扫过的模块数, 命中的字段)。"""

    import ast
    import dataclasses
    from pathlib import Path

    fields = {item.name for item in dataclasses.fields(cls)}
    reads: set[str] = set()
    scanned = 0
    root = Path(__file__).resolve().parents[2]
    for path in sorted((root / "tstdx").rglob("*.py")):
        scanned += 1
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Attribute) or node.attr not in fields:
                continue
            base = node.value
            while isinstance(base, ast.Attribute):
                base = base.value
            if isinstance(base, ast.Name) and base.id in owners:
                reads.add(node.attr)
    return fields, scanned, reads


def test_every_channel_spec_field_has_a_reader() -> None:
    """``ChannelSpec`` 的每个字段都必须有人按它行动（F-54）。

    ``markets`` 曾在 23 个 channel 上声称支持哪些市场，读取点是 0——而且它的词汇（``cn_a``
    /``hk``/``us``/``option``…）在代码里没有任何一侧与 ``tstdx.domain.symbol.Market`` 对接：
    市场正确性实际由 ``Symbol.tdx_market`` 对 HK/US fail-closed 兜住，注册表这套是第二份没人执行
    的词汇表。
    ``notes`` 是写在代码里的注释，全仓唯一的"读取"来自测试本身。两者按 clean break 删除
    （与 F-50/F-52 同口径），市场与定位的说明留在 ``docs/providers/*.md``。
    """

    fields, scanned, reads = _unread_fields(
        ChannelSpec, {"self", "spec", "item", "channel", "selected", "ch", "value"}
    )
    assert fields, "ChannelSpec 已经没有字段了，判据自身失效"
    assert scanned > 30, f"只扫到 {scanned} 个模块，读取扫描自身失效"
    assert reads, "ChannelSpec 字段读取扫描一条都没命中，说明它自身失效了"
    orphans = sorted(fields - reads)
    assert orphans == [], f"ChannelSpec 字段没有任何读取点（无人兑现的声称）：{orphans}"
    assert fields == {"id", "capabilities", "live", "local", "periods"}, (
        f"注册表声称的形状变了：{sorted(fields)}"
    )


def test_every_provider_spec_field_has_a_reader() -> None:
    """``ProviderSpec`` 的每个字段都必须有人按它行动（F-52 注册表半边）。

    第 25 步的尺子搬到注册表自己头上：``display_name`` 与 ``role`` 被 11 个 Provider
    逐个写着，而全包对它们的读取点是 0——注册表里的"事实"若没有任何消费者，就只是一句
    没人兑现的声称。字段清单取自 dataclass 本身，新增字段没有读取点即当场变红。
    """

    from tstdx.providers import ProviderSpec

    fields, scanned, reads = _unread_fields(
        ProviderSpec, {"self", "spec", "provider", "pspec", "provider_spec", "item", "value"}
    )
    assert fields, "ProviderSpec 已经没有字段了，判据自身失效"
    assert scanned > 30, f"只扫到 {scanned} 个模块，读取扫描自身失效"
    assert reads, "ProviderSpec 字段读取扫描一条都没命中，说明它自身失效了"
    orphans = sorted(fields - reads)
    assert orphans == [], f"ProviderSpec 字段没有任何读取点（无人兑现的声称）：{orphans}"
