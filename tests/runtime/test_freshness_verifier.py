# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""F-44 (a) 的判据面：``currentness`` 从声明口径变成可判据的运行期校验。

三段落笔：

1. **规则来源**：判定只读 ``ChannelSpec.local``（注册表事实）与 plan 的 currentness，
   不读墙钟、不解析行内时间戳——所以矩阵判据是注册表推导的，不是抄来的清单；
2. **执行面接线**：``DirectProviderExecutor.execute()`` 在任何 Provider I/O 之前调用它，
   ``strict`` 决定"无法证明"是失败（:class:`FreshnessViolation`）还是随结果出发的瑕疵；
3. **口径分家**：``live`` 打非 live channel 仍是规划期的输入错误（``ValidationError``/422），
   不在本步改写成 503——那是本步登记而不是本步执行的事。
"""

from __future__ import annotations

import warnings
from types import SimpleNamespace
from typing import Any

import pytest

from atst.diagnostics import WarningCode, warning_sink
from atst.errors import FreshnessViolation, ValidationError
from atst.providers import PROVIDERS
from atst.query import CurrentnessMode, QueryPlanner, QuerySpec
from atst.runtime.executor import DirectProviderExecutor
from atst.runtime.freshness import _REQUIRES_PROOF, verify_currentness

ALL_MODES = ("auto", "historical", "business", "live")


def _stub_plan(provider: str, channel: str, currentness: str) -> Any:
    return SimpleNamespace(
        provider=provider,
        channel=channel,
        spec=SimpleNamespace(capability="bars", currentness=currentness),
    )


def _verdict(provider: str, channel: str, currentness: str) -> bool:
    """``True`` = 该三元组被判为"currentness 无法证明"。

    同一次判据在两面上必须同源：非 ``strict`` 记一条瑕疵，``strict`` 就必须失败；
    两边不一致说明规则读到了墙面之外的状态。
    """
    with warning_sink() as collected, warnings.catch_warnings():
        warnings.simplefilter("ignore")
        verify_currentness(_stub_plan(provider, channel, currentness), strict=False)
    recorded = [item.code for item in collected]
    assert recorded in ([], [WarningCode.CURRENTNESS_UNPROVEN]), f"多余瑕疵: {recorded}"

    raised = False
    try:
        verify_currentness(_stub_plan(provider, channel, currentness), strict=True)
    except FreshnessViolation:
        raised = True
    assert raised is bool(recorded), f"两面不一致: recorded={recorded} strict_raises={raised}"
    return bool(recorded)


class TestRuleDerivation:
    def test_registry_scan_sees_the_local_channel_it_must_judge(self) -> None:
        """自检：判据扫不到任何 local channel 就等于门禁自身失效。"""
        locals_ = [
            (p, ch.id) for p in PROVIDERS.ids() for ch in PROVIDERS.get(p).channels if ch.local
        ]
        assert locals_, "注册表里没有 local channel：本文件的推导判据失去对象"

    def test_matrix_is_exactly_local_channel_under_a_current_claim(self) -> None:
        expected_local = {
            (p, ch.id) for p in PROVIDERS.ids() for ch in PROVIDERS.get(p).channels if ch.local
        }
        for provider in PROVIDERS.ids():
            for channel in PROVIDERS.get(provider).channels:
                for mode in ALL_MODES:
                    expected = (provider, channel.id) in expected_local and _parse(
                        mode
                    ) in _REQUIRES_PROOF
                    assert _verdict(provider, channel.id, mode) is expected, (
                        f"{provider}/{channel.id} currentness={mode!r}"
                    )


def _parse(mode: str) -> CurrentnessMode:
    from atst.query import _parse_currentness

    return _parse_currentness(mode)


class TestClaimSemantics:
    @pytest.mark.parametrize("mode", ["auto", "historical"])
    def test_no_current_claim_nothing_to_prove(self, mode: str) -> None:
        assert _verdict("local_vipdoc", "vipdoc", mode) is False

    @pytest.mark.parametrize("mode", ["business", "live"])
    def test_local_files_cannot_prove_a_current_claim(self, mode: str) -> None:
        assert _verdict("local_vipdoc", "vipdoc", mode) is True

    def test_violation_context_names_the_decision_not_just_the_failure(self) -> None:
        with pytest.raises(FreshnessViolation) as exc_info:
            verify_currentness(_stub_plan("local_vipdoc", "vipdoc", "business"), strict=True)
        ctx = exc_info.value.context
        assert ctx["provider"] == "local_vipdoc"
        assert ctx["channel"] == "vipdoc"
        assert ctx["capability"] == "bars"
        assert ctx["currentness"] == "business"
        assert ctx["local"] is True
        assert ctx["fallback"] is False
        assert ctx["provider_switch_allowed"] is False
        assert "当期" in ctx["reason"]


def _plan(capability: str, provider: str, currentness: str, **options: Any) -> Any:
    spec = QuerySpec.build(
        capability,
        symbols="sh600519",
        provider=provider,
        period="day",
        count=5,
        currentness=currentness,
        options=options or None,
    )
    return QueryPlanner().compile(spec)


class TestExecutorWiring:
    def test_local_bars_with_business_fails_before_any_disk_io(self, monkeypatch: Any) -> None:
        executor = DirectProviderExecutor(vipdoc_root="unused")
        calls: list[str] = []
        monkeypatch.setattr(
            executor, "_local_bars", lambda plan: calls.append("io") or [{"datetime": "x"}]
        )
        with pytest.raises(FreshnessViolation) as exc_info:
            executor.execute(_plan("bars", "local_vipdoc", "business", strict=True))
        assert calls == []
        assert exc_info.value.context["channel"] == "vipdoc"

    def test_non_strict_shape_rides_the_result_as_a_caveat(self, monkeypatch: Any) -> None:
        executor = DirectProviderExecutor(vipdoc_root="unused")
        monkeypatch.setattr(
            executor, "_local_bars", lambda plan: [{"datetime": "20260918", "close": 1.0}]
        )
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            result = executor.execute(_plan("bars", "local_vipdoc", "business"))
        assert [item.code.value for item in result.meta.warnings] == ["currentness_unproven"]
        assert result.data

    @pytest.mark.parametrize("currentness", ["auto", "historical"])
    def test_the_default_historical_shape_stays_clean(
        self, monkeypatch: Any, currentness: str
    ) -> None:
        executor = DirectProviderExecutor(vipdoc_root="unused")
        monkeypatch.setattr(
            executor, "_local_bars", lambda plan: [{"datetime": "20260918", "close": 1.0}]
        )
        result = executor.execute(_plan("bars", "local_vipdoc", currentness, strict=True))
        assert result.meta.warnings == ()

    def test_remote_channels_are_untouched(self, monkeypatch: Any) -> None:
        executor = DirectProviderExecutor()
        monkeypatch.setattr(executor, "_tdx_bars", lambda plan: [{"datetime": "20260918"}])
        for currentness in ALL_MODES:
            result = executor.execute(_plan("bars", "tdx", currentness, strict=True))
            assert result.meta.warnings == (), f"tdx bars currentness={currentness!r} 被误判"

    def test_live_on_a_non_live_channel_is_still_a_planning_time_input_error(self) -> None:
        """口径分家：输入错误留在 422，本步不把它改写成 503。"""
        with pytest.raises(ValidationError, match="不是 live channel"):
            _plan("bars", "local_vipdoc", "live")
