from __future__ import annotations

import pytest

from atst.client import TdxClient
from atst.errors import TruncatedDataError


def test_full_download_from_nonzero_offset_stops_at_absolute_total(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = TdxClient(pool=object())
    calls: list[int] = []

    def fake_once(market: int, code: str, filename: str, offset: int, length: int):
        del market, code, filename
        assert length == 0
        calls.append(offset)
        if offset == 100:
            return [{"total_len": 1000, "data": b"a" * 400}]
        if offset == 500:
            return [{"total_len": 1000, "data": b"b" * 500}]
        raise AssertionError(f"download issued an unnecessary request at offset={offset}")

    monkeypatch.setattr(client, "_file_download_once", fake_once)

    data = client.file_download(
        "600000",
        "finance.dat",
        offset=100,
        length=0,
        max_packets=3,
        strict=True,
    )

    assert len(data) == 900
    assert calls == [100, 500]


def test_incomplete_download_from_nonzero_offset_reports_absolute_end(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = TdxClient(pool=object())

    def fake_once(market: int, code: str, filename: str, offset: int, length: int):
        del market, code, filename, length
        if offset == 100:
            return [{"total_len": 1000, "data": b"a" * 400}]
        return []

    monkeypatch.setattr(client, "_file_download_once", fake_once)

    with pytest.raises(TruncatedDataError) as exc_info:
        client.file_download(
            "600000",
            "finance.dat",
            offset=100,
            max_packets=2,
            strict=True,
        )

    assert exc_info.value.context["offset"] == 100
    assert exc_info.value.context["returned"] == 400
    assert exc_info.value.context["end_offset"] == 500
    assert exc_info.value.context["total_len"] == 1000
