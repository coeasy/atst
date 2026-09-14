from __future__ import annotations

import threading

import tstdx.execution as execution


class UncopyableRuntimeError(RuntimeError):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.lock = threading.Lock()
        self.details = {"attempt": 1}


def test_clone_error_preserves_native_exception_type_when_deepcopy_fails() -> None:
    original = UncopyableRuntimeError("native failure")

    cloned = execution._clone_error(original)

    assert isinstance(cloned, UncopyableRuntimeError)
    assert cloned is not original
    assert cloned.args == original.args
    assert cloned.details == {"attempt": 1}
    assert cloned.details is not original.details
    cloned.details["attempt"] = 2
    assert original.details["attempt"] == 1


def test_clone_error_never_recategorizes_process_control_baseexceptions() -> None:
    for original in (KeyboardInterrupt("stop"), SystemExit(7)):
        cloned = execution._clone_error(original)
        assert type(cloned) is type(original)
        assert cloned.args == original.args
