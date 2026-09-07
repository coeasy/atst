"""C11 弃用策略测试：DeprecationPolicy 两 minor 窗口 + 警告/错误模式。"""

from __future__ import annotations

import warnings

import pytest

pytestmark = pytest.mark.unit

from tstdx.deprecation import (  # noqa: E402
    DeprecationError,
    DeprecationInfo,
    DeprecationPolicy,
    deprecated,
    get_deprecation_count,
    reset_deprecation_count,
)


@pytest.fixture(autouse=True)
def _reset():
    reset_deprecation_count()
    yield
    reset_deprecation_count()


def test_decorator_warns_by_default():
    @deprecated(reason="改用 bars()", since="0.2.0", removed_in="0.4.0")
    def old_api():
        return 42

    with pytest.warns(DeprecationWarning):
        assert old_api() == 42


def test_error_mode_raises():
    policy = DeprecationPolicy(mode="error")

    @policy.deprecated(reason="gone", since="0.2.0", removed_in="0.4.0")
    def old_api():
        return 1

    with pytest.raises(DeprecationError):
        old_api()


def test_warning_message_contains_versions_and_reason():
    @deprecated(reason="merged into foo", since="0.1.0", removed_in="0.3.0")
    def old_api():
        return None

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        old_api()
    assert caught, "expected a warning"
    msg = str(caught[0].message)
    assert "0.3.0" in msg
    assert "foo" in msg
    assert "old_api" in msg


def test_count_incremented_and_reset():
    @deprecated(reason="x", since="0.2.0", removed_in="0.4.0")
    def old_api():
        return None

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        old_api()
        old_api()

    assert get_deprecation_count() >= 2
    reset_deprecation_count()
    assert get_deprecation_count() == 0


def test_policy_modes_and_gaps():
    assert DeprecationPolicy().mode == "warning"
    assert DeprecationPolicy(mode="error").mode == "error"
    assert DeprecationPolicy(removal_gap=2).removal_gap == 2  # 两个 minor 窗口


def test_removal_gap_auto_computes_two_minors():
    """removed_in 缺省时按 since + removal_gap(2 minor) 自动推算。"""
    policy = DeprecationPolicy(mode="warning", removal_gap=2)
    deco = policy.deprecated(reason="x", since="0.4.0")

    @deco
    def old_api():
        return None

    assert old_api._deprecation_info.removed_in == "0.6.0"


def test_preserves_metadata_and_signature():
    @deprecated(reason="x", since="0.2.0", removed_in="0.4.0")
    def old_api(a, b=2):
        """old doc"""
        return a + b

    assert old_api.__name__ == "old_api"
    assert old_api.__doc__ == "old doc"
    assert old_api(1, b=3) == 4


def test_class_decoration_warns():
    policy = DeprecationPolicy(mode="warning")

    @policy.deprecated(reason="use NewThing", since="0.2.0", removed_in="0.4.0")
    class OldThing:
        def __init__(self) -> None:
            self.v = 1

    with pytest.warns(DeprecationWarning):
        obj = OldThing()
    assert obj.v == 1


def test_deprecation_info_str():
    info = DeprecationInfo(
        since="0.1.0",
        removed_in="0.3.0",
        message="moved",
        migration_guide="docs/migration.md",
    )
    text = str(info)
    assert "moved" in text
    assert "0.3.0" in text
    assert "docs/migration.md" in text
