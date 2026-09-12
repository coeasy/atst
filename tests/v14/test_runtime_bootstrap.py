from __future__ import annotations

from tstdx.runtime import QueryRequest, create_runtime


class Client:
    def quotes(self, symbols):
        return list(symbols)


class EmptyCache(dict):
    pass


def test_create_runtime_registers_injected_backends_in_policy_order() -> None:
    runtime = create_runtime(
        cache=EmptyCache(),
        tdx=Client(),
        provider_order=("cache", "tdx"),
    )

    response = runtime.execute(QueryRequest("quotes", args=(["sh600519"],)))

    assert response.success is True
    assert response.data == ["sh600519"]
    assert response.metadata["provider"] == "tdx"


def test_create_runtime_stays_empty_without_backends() -> None:
    response = create_runtime().execute(QueryRequest("quotes"))

    assert response.success is False
    assert response.error == "unsupported operation: quotes"
