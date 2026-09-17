from tstdx.runtime_cache_policy import should_negative_cache


class RetryableError(Exception):
    retryable = True


class PermanentValidationError(Exception):
    retryable = False


def test_retryable_errors_are_not_negative_cached() -> None:
    assert should_negative_cache(RetryableError()) is False


def test_non_retryable_errors_can_be_negative_cached() -> None:
    assert should_negative_cache(PermanentValidationError()) is True
