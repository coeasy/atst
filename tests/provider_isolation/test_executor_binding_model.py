from tstdx.executor_bindings import ExecutorBinding


def test_executor_binding_key_is_exact_provider_boundary() -> None:
    binding = ExecutorBinding(
        provider="tdx",
        channel="quotation",
        capability="bars",
        executor_name="_tdx_bars",
    )

    assert binding.key == (
        "tdx",
        "quotation",
        "bars",
    )
