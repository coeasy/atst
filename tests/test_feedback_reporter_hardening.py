from __future__ import annotations

import json
from pathlib import Path

import pytest

from tstdx.errors import ConfigError
from tstdx.feedback import FeedbackReporter
from tstdx.feedback.reporter import _sanitize_value


def test_feedback_store_directory_expands_user_home_without_writing() -> None:
    reporter = FeedbackReporter(store_dir="~/.tstdx/feedback")

    assert reporter._store_dir == Path("~/.tstdx/feedback").expanduser()
    assert "~" not in reporter._store_dir.parts


@pytest.mark.parametrize(
    "endpoint",
    [
        "not-a-url",
        "file:///tmp/feedback.json",
        "ftp://feedback.example.com/report",
        "https:///missing-host",
        "https://user:secret@feedback.example.com/report",
    ],
)
def test_feedback_endpoint_rejects_non_http_or_embedded_credentials(endpoint: str) -> None:
    with pytest.raises(ConfigError):
        FeedbackReporter(endpoint=endpoint)


def test_feedback_endpoint_accepts_http_and_https() -> None:
    assert FeedbackReporter(endpoint="http://feedback.example.com/report")._endpoint.startswith("http://")
    assert FeedbackReporter(endpoint="https://feedback.example.com/report")._endpoint.startswith(
        "https://"
    )


@pytest.mark.parametrize("timeout", [0, -1, float("nan"), float("inf"), True, "1"])
def test_feedback_timeout_requires_positive_finite_number(timeout) -> None:
    with pytest.raises(ConfigError, match="timeout"):
        FeedbackReporter(timeout=timeout)  # type: ignore[arg-type]


def test_feedback_set_sanitization_does_not_rebuild_unhashable_transforms() -> None:
    sanitized = _sanitize_value({("alpha", "beta"), ("gamma", "delta")})

    assert isinstance(sanitized, list)
    assert sorted(sanitized) == [["alpha", "beta"], ["gamma", "delta"]]


def test_feedback_file_storage_never_overwrites_rapid_successive_reports(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("TSTDX_FEEDBACK", "1")
    reporter = FeedbackReporter(store_dir=tmp_path)

    assert reporter.report_usage("bars", 1.0, "ok") is True
    assert reporter.report_usage("quotes", 2.0, "ok") is True

    files = sorted(tmp_path.glob("feedback_*.json"))
    assert len(files) == 2
    payloads = [json.loads(path.read_text(encoding="utf-8")) for path in files]
    assert {payload["feature"] for payload in payloads} == {"bars", "quotes"}


def test_feedback_dry_run_does_not_create_store_directory(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    store = tmp_path / "not-created"
    monkeypatch.setenv("TSTDX_FEEDBACK", "dry-run")
    reporter = FeedbackReporter(store_dir=store)

    assert reporter.report_usage("bars", 1.0, "ok") is True
    assert not store.exists()


def test_feedback_disabled_does_not_create_store_directory(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    store = tmp_path / "not-created"
    monkeypatch.delenv("TSTDX_FEEDBACK", raising=False)
    reporter = FeedbackReporter(store_dir=store)

    assert reporter.report_usage("bars", 1.0, "ok") is False
    assert not store.exists()
