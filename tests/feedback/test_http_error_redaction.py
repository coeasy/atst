from __future__ import annotations

import urllib.error

import pytest

from atst.feedback.reporter import FeedbackReporter

_ENDPOINT = "https://collector.example/private/ingest?token=super-secret"


def test_feedback_urlerror_log_never_echoes_endpoint_path_or_query(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path,
) -> None:
    reporter = FeedbackReporter(endpoint=_ENDPOINT, store_dir=tmp_path)

    def fail(*_args, **_kwargs):
        raise urllib.error.URLError(f"connection failed for {_ENDPOINT}")

    monkeypatch.setattr("atst.feedback.reporter.urllib.request.urlopen", fail)

    assert reporter._send_http("{}") is False
    stderr = capsys.readouterr().err

    assert "https://collector.example" in stderr
    assert "/private/ingest" not in stderr
    assert "super-secret" not in stderr
    assert "token=" not in stderr
    assert "URLError" in stderr


def test_feedback_httperror_log_exposes_status_but_not_sensitive_url(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path,
) -> None:
    reporter = FeedbackReporter(endpoint=_ENDPOINT, store_dir=tmp_path)

    def fail(*_args, **_kwargs):
        raise urllib.error.HTTPError(_ENDPOINT, 401, "Unauthorized", None, None)

    monkeypatch.setattr("atst.feedback.reporter.urllib.request.urlopen", fail)

    assert reporter._send_http("{}") is False
    stderr = capsys.readouterr().err

    assert "https://collector.example" in stderr
    assert "status=401" in stderr
    assert "/private/ingest" not in stderr
    assert "super-secret" not in stderr
