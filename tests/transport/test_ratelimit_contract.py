from __future__ import annotations

import math
from types import SimpleNamespace

import pytest

from tstdx.transport.ratelimit import SessionRateLimiter, SessionState, TokenBucket


@pytest.mark.parametrize("rate", [0, -1, True, "10", float("nan"), float("inf")])
def test_token_bucket_rejects_invalid_rate_without_coercion(rate: object) -> None:
    with pytest.raises(ValueError, match="rate"):
        TokenBucket(rate)  # type: ignore[arg-type]


@pytest.mark.parametrize("burst", [0, -1, True, "10", float("nan")])
def test_token_bucket_rejects_invalid_burst_without_coercion(burst: object) -> None:
    with pytest.raises(ValueError, match="burst"):
        TokenBucket(10.0, burst=burst)  # type: ignore[arg-type]


def test_negative_or_zero_token_request_cannot_increase_bucket() -> None:
    bucket = TokenBucket(10.0, burst=10.0)
    before = bucket._tokens

    for tokens in (0, -1, True, float("nan")):
        with pytest.raises(ValueError, match="tokens"):
            bucket.acquire(tokens)  # type: ignore[arg-type]

    assert bucket._tokens <= before


def test_request_larger_than_burst_fails_instead_of_blocking_forever() -> None:
    bucket = TokenBucket(1.0, burst=1.0)

    with pytest.raises(ValueError, match="超过桶容量"):
        bucket.acquire(2.0, blocking=True)


@pytest.mark.parametrize("blocking", [0, 1, "yes", None])
def test_token_bucket_requires_real_boolean_blocking(blocking: object) -> None:
    bucket = TokenBucket(10.0)

    with pytest.raises(ValueError, match="blocking"):
        bucket.acquire(blocking=blocking)  # type: ignore[arg-type]


@pytest.mark.parametrize("timeout", [-1, True, "1", float("nan"), float("inf")])
def test_token_bucket_validates_timeout(timeout: object) -> None:
    bucket = TokenBucket(10.0)

    with pytest.raises(ValueError, match="timeout"):
        bucket.acquire(timeout=timeout)  # type: ignore[arg-type]


def test_set_rate_failure_does_not_mutate_existing_bucket() -> None:
    bucket = TokenBucket(10.0)
    original_rate = bucket.rate
    original_burst = bucket.burst

    with pytest.raises(ValueError, match="rate"):
        bucket.set_rate(-1)

    assert bucket.rate == original_rate
    assert bucket.burst == original_burst


def test_session_limiter_rejects_invalid_constructor_contract() -> None:
    with pytest.raises(ValueError, match="rates"):
        SessionRateLimiter([])  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="未知交易状态"):
        SessionRateLimiter({"unknown": 1.0})
    with pytest.raises(ValueError, match="rates"):
        SessionRateLimiter({SessionState.CONTINUOUS: float("nan")})
    with pytest.raises(ValueError, match="state_fn"):
        SessionRateLimiter(state_fn=object())  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="strict"):
        SessionRateLimiter(strict=1)  # type: ignore[arg-type]


def test_unknown_or_broken_state_function_fails_safe_to_closed() -> None:
    unknown = SessionRateLimiter(state_fn=lambda: "unknown")

    def broken() -> str:
        raise RuntimeError("clock unavailable")

    failing = SessionRateLimiter(state_fn=broken)

    assert unknown.state == SessionState.CLOSED
    assert failing.state == SessionState.CLOSED
    assert math.isfinite(unknown.rate)
    assert unknown.try_acquire() is True


def test_set_rates_is_transactional_on_invalid_update() -> None:
    limiter = SessionRateLimiter()
    original = dict(limiter._rates)

    with pytest.raises(ValueError, match="rates"):
        limiter.set_rates(
            **{
                SessionState.CONTINUOUS: 50.0,
                SessionState.CLOSED: 0.0,
            }
        )

    assert limiter._rates == original


def test_set_rates_rejects_unknown_state_without_partial_update() -> None:
    limiter = SessionRateLimiter()
    original = dict(limiter._rates)

    with pytest.raises(ValueError, match="未知交易状态"):
        limiter.set_rates(unknown=50.0)

    assert limiter._rates == original


def test_from_config_does_not_coerce_string_rates_or_truthy_strict() -> None:
    bad_rate = SimpleNamespace(
        rate_call_auction="80",
        rate_continuous=120.0,
        rate_noon_break=25.0,
        rate_closed=15.0,
        rate_limit_strict=False,
    )
    with pytest.raises(ValueError, match="rates"):
        SessionRateLimiter.from_config(bad_rate)

    bad_strict = SimpleNamespace(
        rate_call_auction=80.0,
        rate_continuous=120.0,
        rate_noon_break=25.0,
        rate_closed=15.0,
        rate_limit_strict=1,
    )
    with pytest.raises(ValueError, match="strict"):
        SessionRateLimiter.from_config(bad_strict)
