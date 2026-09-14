from __future__ import annotations

from tstdx.feedback import FeedbackReporter


def test_feedback_repr_only_exposes_endpoint_origin() -> None:
    reporter = FeedbackReporter(
        endpoint="https://feedback.example.com:8443/report/path?token=supersecret#internal"
    )

    rendered = repr(reporter)

    assert "https://feedback.example.com:8443" in rendered
    assert "/report/path" not in rendered
    assert "token" not in rendered
    assert "supersecret" not in rendered
    assert "internal" not in rendered


def test_feedback_repr_brackets_ipv6_origin() -> None:
    reporter = FeedbackReporter(endpoint="https://[::1]:8443/report?token=secret")

    rendered = repr(reporter)

    assert "https://[::1]:8443" in rendered
    assert "secret" not in rendered
