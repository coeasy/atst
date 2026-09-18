from tstdx.executor_registry import resolve_executor


def test_executor_registry_resolves_exact_provider_binding() -> None:
    binding = resolve_executor("tdx", "quotation", "bars")

    assert binding.provider == "tdx"
    assert binding.channel == "quotation"
    assert binding.capability == "bars"
    assert binding.executor_name == "_tdx_bars"
