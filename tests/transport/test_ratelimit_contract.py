from __future__ import annotations

import ast
import inspect
import math
import textwrap
import threading
from types import SimpleNamespace

import pytest

from atst.transport.async_ import AsyncConnectionPool
from atst.transport.ratelimit import (
    DEFAULT_ACQUIRE_TIMEOUT,
    SessionRateLimiter,
    SessionState,
    TokenBucket,
)


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
    from atst.config.schema import RateLimitConfig

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


# --------------------------------------------------------------------------- #
# G29（第 24 轮真实并发测量后写下的两条**可判定**边界）
# --------------------------------------------------------------------------- #
# 并发契约的其余部分——非 FIFO、无界等待、饿死——都由 ``census24c_g29_concurrency.py``
# 的一次真实争用测量决定其出路 (a)「写成契约」，其数值记于 §33 台账。这里只钉住契约里
# 能被结构判定的那两条，好让 ``TokenBucket.acquire`` / ``_acquire_rate`` 的两段 docstring
# 里的话不会悄悄过期：若有人把异步等待改成了可传超时、或给桶加了排队结构，本判据当场红，
# 逼着同一只手去改文档。"不保证 FIFO" 是**减**承诺（宁可少承诺），无需时序断言。
_QUEUE_CONSTRUCTORS = frozenset(
    {"deque", "Queue", "LifoQueue", "PriorityQueue", "SimpleQueue", "list"}
)


def _self_attrs_assigned_a_queue(init_src: str) -> list[str]:
    """``__init__`` 源码里被赋成容器/队列（含 ``[]``）的 ``self.<name>`` 属性名。"""
    tree = ast.parse(textwrap.dedent(init_src))
    flagged: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        value = node.value
        if value is None:
            continue
        ctor: str | None
        if isinstance(value, ast.List):
            ctor = "list"
        elif isinstance(value, ast.Call):
            func = value.func
            ctor = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
        else:
            ctor = None
        if ctor not in _QUEUE_CONSTRUCTORS:
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        for target in targets:
            if (
                isinstance(target, ast.Attribute)
                and isinstance(target.value, ast.Name)
                and target.value.id == "self"
            ):
                flagged.append(target.attr)
    return flagged


def test_the_async_rate_wait_is_bounded_by_a_deadline() -> None:
    """异步限流等待**有超时形参**，且池把调用方预算递给了它。

    这里钉的是一条修掉的形状（2026-09-29）：过去 ``_acquire_rate`` 只有 ``self``，
    ``while not try_acquire(): await sleep(0.05)`` 是纯无界轮询，而它发生在
    ``conn.request(timeout=...)`` **之前**——调用方声明的 5 秒预算完全覆盖不到限流等待，
    速率档位配得低时一个协程可以永久挂住、且无法中断。

    判据两半各自都是真信号：签名上必须有 ``timeout``；源码里必须有 deadline 判定
    （只加形参不判，形同虚设）。
    """
    rate_params = list(inspect.signature(AsyncConnectionPool._acquire_rate).parameters)
    assert "timeout" in rate_params, f"异步限流等待丢了超时形参（回到无界等待）：{rate_params}"
    source = inspect.getsource(AsyncConnectionPool._acquire_rate)
    assert "deadline" in source, "有 timeout 形参却不判 deadline：等待仍然无界"
    assert "RateLimitedLocal" in source, "到点必须抛 RateLimitedLocal，而不是静默继续"
    # 正控：真正被超时覆盖的 I/O 那一步，它的签名里也必须能看到 timeout。
    assert "timeout" in inspect.signature(AsyncConnectionPool.request).parameters


def test_the_sync_rate_wait_is_bounded_by_a_deadline() -> None:
    """同步侧同一条口径：``SessionRateLimiter.acquire`` 必须能接超时，且有兜底上限。

    调用点（``ConnectionPool.request`` / ``iter_frames``）过去一律 ``acquire()`` 不传
    超时，而 :meth:`TokenBucket.acquire` 在 ``timeout=None`` 时就是"回填到取到为止"。
    """
    assert "timeout" in inspect.signature(SessionRateLimiter.acquire).parameters
    assert DEFAULT_ACQUIRE_TIMEOUT > 0, "兜底上限必须为正，否则又回到无界等待"
    source = inspect.getsource(SessionRateLimiter.acquire)
    assert "DEFAULT_ACQUIRE_TIMEOUT" in source, "timeout=None 时必须落到兜底上限"


def test_the_bucket_holds_its_wait_state_as_a_scalar_not_a_waiter_queue() -> None:
    """契约说"单个共享令牌池、无每等待者排队结构"——那 ``__init__`` 里就不该有队列容器。

    正控：把同一把分类器对准一个真的 ``self._waiters = deque()``，它必须抓到；而对
    ``self._lock = threading.Lock()``（同步原语，非等待队列）必须放过——否则"抓到 deque"
    只是尺子在乱指。
    """
    assert _self_attrs_assigned_a_queue(inspect.getsource(TokenBucket.__init__)) == []
    planted_init = (
        "def __init__(self):\n"
        "    import collections\n"
        "    self._waiters = collections.deque()\n"
        "    self._lock = threading.Lock()\n"
    )
    # deque 抓到、Lock（同步原语非等待队列）放过——否则"抓到 deque"只是尺子在乱指。
    assert _self_attrs_assigned_a_queue(planted_init) == ["_waiters"]
