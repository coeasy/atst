import pytest

from tstdx.executor_binding_registry import (
    ExecutorBindingRegistryError,
    all_bindings,
    register_binding,
    resolve_binding,
)
from tstdx.executor_bindings import ExecutorBinding


def test_binding_registry_resolves_exact_binding() -> None:
    register_binding(
        ExecutorBinding(
            "tdx",
            "quotation",
            "bars",
            "_tdx_bars",
        )
    )

    binding = resolve_binding("tdx", "quotation", "bars")
    assert binding.executor_name == "_tdx_bars"


def test_binding_registry_rejects_duplicate() -> None:
    with pytest.raises(ExecutorBindingRegistryError):
        register_binding(
            ExecutorBinding(
                "tdx",
                "quotation",
                "bars",
                "_tdx_bars",
            )
        )


def test_registry_contains_only_explicit_bindings() -> None:
    assert isinstance(all_bindings(), tuple)
