import pytest

from tstdx.executor_registry import (
    ExecutorResolutionError,
    resolve_executor,
)


def test_executor_registry_rejects_unknown_provider_binding() -> None:
    with pytest.raises(ExecutorResolutionError):
        resolve_executor(
            "unknown_provider",
            "quotation",
            "bars",
        )
