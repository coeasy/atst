from __future__ import annotations

import math
from pathlib import Path

import pytest

from tstdx.errors import ConfigError
from tstdx.feedback import FeedbackReporter


@pytest.mark.parametrize(
    ("feature", "duration_ms", "result"),
    [
        ("", 1.0, "ok"),
        ("bars", -1.0, "ok"),
        ("bars", math.nan, "ok"),
        ("bars", math.inf, "ok"),
        ("bars", True, "ok"),
        ("bars", 1.0, ""),
    ],
)
def test_enabled_usage_feedback_rejects_invalid_payload_fields(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    feature,
    duration_ms,
    result,
) -> None:
    monkeypatch.setenv("TSTDX_FEEDBACK", "1")
    reporter = FeedbackReporter(store_dir=tmp_path)

    with pytest.raises(ConfigError):
        reporter.report_usage(feature, duration_ms, result)

    assert list(tmp_path.glob("feedback_*.json")) == []


def test_disabled_usage_feedback_remains_noop_before_payload_validation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("TSTDX_FEEDBACK", raising=False)
    reporter = FeedbackReporter(store_dir=tmp_path)

    assert reporter.report_usage("", math.nan, "") is False
    assert not tmp_path.exists()


def test_non_finite_nested_profile_never_emits_nonstandard_json(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("TSTDX_FEEDBACK", "1")
    reporter = FeedbackReporter(store_dir=tmp_path)

    assert reporter.report_profile({"latency": math.nan}) is False
    assert list(tmp_path.glob("feedback_*.json")) == []


def test_non_finite_error_context_never_emits_nonstandard_json(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("TSTDX_FEEDBACK", "1")
    reporter = FeedbackReporter(store_dir=tmp_path)

    assert reporter.report_error(ValueError("boom"), {"metric": math.inf}) is False
    assert list(tmp_path.glob("feedback_*.json")) == []
