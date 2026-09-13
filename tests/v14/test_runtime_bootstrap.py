from __future__ import annotations

from tstdx.runtime import QueryRequest, create_runtime


class BarsOnlyClient:
    def bars(self, symbol):
        return [symbol]


class QuotesSource:
    def quotes(self, symbols):
        return list(symbols)


class LocalReader:
    def bars(self, symbol):
        return [f"local:{symbol}"]


def test_create_runtime_registers_canonical_web_provider_mapping() -> None:
    runtime = create_runtime(
        tdx=BarsOnlyClient(),
        web={"tencent": QuotesSource()},
        provider_order=("tdx", "tencent"),
    )

    response = runtime.execute(QueryRequest("quotes", args=(["sh600519"],)))

    assert response.success is True
    assert response.data == ["sh600519"]
    assert response.metadata["provider"] == "tencent"


def test_create_runtime_registers_local_vipdoc_identity() -> None:
    runtime = create_runtime(local=LocalReader(), provider_order=("local_vipdoc",))

    response = runtime.execute(QueryRequest("bars", args=("sh600519",)))

    assert response.success is True
    assert response.data == ["local:sh600519"]
    assert response.metadata["provider"] == "local_vipdoc"


def test_create_runtime_stays_empty_without_backends() -> None:
    response = create_runtime().execute(QueryRequest("quotes"))

    assert response.success is False
    assert response.error == "unsupported operation: quotes"
