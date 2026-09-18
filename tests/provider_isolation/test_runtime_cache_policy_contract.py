from tstdx.errors import ReadTimeout, ValidationError
from tstdx.runtime_cache_policy import should_negative_cache


def test_retryable_errors_are_not_negative_cached() -> None:
    assert should_negative_cache(ReadTimeout("timeout")) is False


def test_non_retryable_tstdx_errors_can_be_negative_cached() -> None:
    assert should_negative_cache(ValidationError("bad request")) is True


def test_unknown_exceptions_are_not_negative_cached() -> None:
    assert should_negative_cache(RuntimeError("unknown")) is False
