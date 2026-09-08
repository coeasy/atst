from __future__ import annotations

from typing import Any

import pytest

from tstdx.errors import ValidationError
from tstdx.integration.http_runtime import ProviderHttpClient
from tstdx.integration.tasks import TaskStore


class FakeQuotation:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    def snapshot(self, symbol: str) -> dict[str, Any]:
        self.calls.append(("snapshot", (symbol,)))
        return {"symbol": symbol}

    def auction_snapshot(self, symbol: str) -> dict[str, Any]:
        self.calls.append(("auction_snapshot", (symbol,)))
        return {"symbol": symbol}

    def minute_history(self, symbol: str, date: int) -> dict[str, Any]:
        self.calls.append(("minute_history", (symbol, date)))
        return {"symbol": symbol, "date": date}

    def block_quotes(self, block_type: int, start: int) -> dict[str, Any]:
        self.calls.append(("block_quotes", (block_type, start)))
        return {"block_type": block_type, "start": start}


class FakeF10:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    def download(self, symbol: str, filename: str) -> bytes:
        self.calls.append(("download", (symbol, filename)))
        return b"f10"

    def parse_text(self, payload: bytes) -> str:
        self.calls.append(("parse_text", (payload,)))
        return "parsed"

    def catalog(self, symbol: str) -> list[str]:
        self.calls.append(("catalog", (symbol,)))
        return ["profile.txt"]


class FakeTdx:
    def __init__(self) -> None:
        self.f10 = FakeF10()


class FakeManager:
    def __init__(self) -> None:
        self.quotation = FakeQuotation()

    def tdx_channel(self, channel: str) -> FakeQuotation:
        assert channel == "quotation"
        return self.quotation


class FakeService:
    def __init__(self) -> None:
        self.manager = FakeManager()
        self.tdx = FakeTdx()
        self.closed = False

    def close(self) -> None:
        self.closed = True


def test_http_server_globals_are_injected_with_v12_runtime() -> None:
    import tstdx.integration.http_server as http_server

    assert http_server._LazyClient is ProviderHttpClient
    assert http_server.TaskStore is TaskStore


def test_snapshot_batch_is_normalized_to_single_symbol_tdx_calls() -> None:
    service = FakeService()
    client = ProviderHttpClient(service_factory=lambda: service)  # type: ignore[arg-type]

    rows = client.snapshot(["sh600519", "sz000001"])

    assert rows == [{"symbol": "sh600519"}, {"symbol": "sz000001"}]
    assert service.manager.quotation.calls == [
        ("snapshot", ("sh600519",)),
        ("snapshot", ("sz000001",)),
    ]


def test_auction_batch_is_normalized_to_single_symbol_calls() -> None:
    service = FakeService()
    client = ProviderHttpClient(service_factory=lambda: service)  # type: ignore[arg-type]

    client.auction_snapshot(["sh600519", "sz000001"])

    assert service.manager.quotation.calls == [
        ("auction_snapshot", ("sh600519",)),
        ("auction_snapshot", ("sz000001",)),
    ]


def test_minute_history_normalizes_date_before_protocol_call() -> None:
    service = FakeService()
    client = ProviderHttpClient(service_factory=lambda: service)  # type: ignore[arg-type]

    row = client.minute_history("sh600519", "2026-09-08")

    assert row["date"] == 20260908
    assert service.manager.quotation.calls == [
        ("minute_history", ("sh600519", 20260908))
    ]


@pytest.mark.parametrize(
    ("value", "expected"),
    [("concept", 0), ("industry", 1), ("region", 2), ("index", 3), ("2", 2)],
)
def test_block_name_is_normalized_to_protocol_block_type(value: str, expected: int) -> None:
    service = FakeService()
    client = ProviderHttpClient(service_factory=lambda: service)  # type: ignore[arg-type]

    client.block_quotes(value, start=10)

    assert service.manager.quotation.calls == [("block_quotes", (expected, 10))]


def test_invalid_block_name_fails_before_protocol_call() -> None:
    service = FakeService()
    client = ProviderHttpClient(service_factory=lambda: service)  # type: ignore[arg-type]

    with pytest.raises(ValidationError):
        client.block_quotes("unknown")

    assert service.manager.quotation.calls == []


def test_invalid_history_date_fails_before_protocol_call() -> None:
    service = FakeService()
    client = ProviderHttpClient(service_factory=lambda: service)  # type: ignore[arg-type]

    with pytest.raises(ValidationError):
        client.minute_history("sh600519", "09/08/26")

    assert service.manager.quotation.calls == []


def test_f10_compatibility_methods_use_f10_channel_only() -> None:
    service = FakeService()
    client = ProviderHttpClient(service_factory=lambda: service)  # type: ignore[arg-type]

    assert client.file_download("sh600519", "profile.txt") == b"f10"
    assert client.parse_text(b"raw") == "parsed"
    assert client.f10_catalog("sh600519") == ["profile.txt"]

    assert service.manager.quotation.calls == []
    assert service.tdx.f10.calls == [
        ("download", ("sh600519", "profile.txt")),
        ("parse_text", (b"raw",)),
        ("catalog", ("sh600519",)),
    ]


def test_close_closes_single_underlying_service() -> None:
    service = FakeService()
    client = ProviderHttpClient(service_factory=lambda: service)  # type: ignore[arg-type]
    _ = client.service

    client.close()
    client.close()

    assert service.closed is True
