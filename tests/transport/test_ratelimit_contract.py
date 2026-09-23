from __future__ import annotations

import math
import threading
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


def test_capacity_shrink_cannot_park_a_waiter_forever() -> None:
    """等待期间 ``set_rate`` 收缩容量：已入站的调用方必须报错，不能永久停等。

    第 21 轮实测到的那一格（``scratch_v18b21/hang_repro.py``）：入口守卫读的是**锁外**
    的一次性快照，所以进了循环的调用方再也没有人复查它的需求是否还可满足——
    ``burst`` 从 50 收缩到 1 之后，令牌永远补不满 50，``timeout=None`` 就等价于挂死。

    ``entered`` 只在循环内第一次 ``_refill()`` 时置位，因此这个复现一定发生在
    "入口守卫已经放行"之后——否则旧实现会在守卫处就报错，测试就成了假绿。
    """
    bucket = TokenBucket(10.0, burst=50.0)
    assert bucket.acquire(50, blocking=False) is True  # 抽干令牌

    entered = threading.Event()
    original_refill = bucket._refill

    def refill_then_signal() -> None:
        original_refill()
        entered.set()

    bucket._refill = refill_then_signal  # type: ignore[method-assign]

    outcome: list[object] = []

    def wait_for_tokens() -> None:
        try:
            bucket.acquire(50, blocking=True, timeout=None)
            outcome.append("returned")
        except BaseException as exc:  # noqa: BLE001 - 原样带回线程判断
            outcome.append(exc)

    waiter = threading.Thread(target=wait_for_tokens, daemon=True)
    waiter.start()
    assert entered.wait(2.0), "等待方没进过取令牌循环，本复现不成立"
    bucket.set_rate(1.0)  # 容量收缩到 1，50 个令牌再也补不满
    waiter.join(5.0)

    assert not waiter.is_alive(), "等待方被容量收缩永久停在了循环里"
    assert isinstance(outcome[0], ValueError)


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
    from tstdx.config.schema import RateLimitConfig

    with pytest.raises(ValueError, match="rates"):
        SessionRateLimiter.from_config(RateLimitConfig(call_auction="80"))

    with pytest.raises(ValueError, match="strict"):
        SessionRateLimiter.from_config(RateLimitConfig(strict=1))


def test_from_config_refuses_the_pre_wiring_phantom_key_names() -> None:
    """旧键名（``rate_call_auction`` 等）从未存在于配置面，读它们会静默用内置速率。

    现在属性缺失必须立即报错，而不是退回默认值。
    """

    phantom = SimpleNamespace(
        rate_call_auction=1.0,
        rate_continuous=1.0,
        rate_noon_break=1.0,
        rate_closed=1.0,
        rate_limit_strict=False,
    )
    with pytest.raises(AttributeError, match="call_auction"):
        SessionRateLimiter.from_config(phantom)
