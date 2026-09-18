from tstdx.executor_registry import resolve_executor


def test_known_provider_capability_resolves_executor() -> None:
    binding = resolve_executor(
        "tdx",
        "quotation",
        "bars",
    )

    assert binding.provider == "tdx"
    assert binding.channel == "quotation"
    assert binding.capability == "bars"
    assert binding.executor_name == "_tdx_bars"


def test_executor_resolution_is_exact_provider_binding() -> None:
    try:
        resolve_executor(
            "eastmoney",
            "quotation",
            "bars",
        )
    except Exception:
        return

    raise AssertionError(
        "executor registry must not silently resolve another provider"
    )
