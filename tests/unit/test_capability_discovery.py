# Copyright (c) 2026 atst contributors
# Licensed under the MIT License

"""能力发现：中文别名检索、状态/Provider 过滤、单能力描述。"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from atst.cli.runtime_commands import cmd_capabilities
from atst.client.api import Client
from atst.errors import ValidationError
from atst.providers.discovery import (
    capability_catalog,
    describe_capability,
    search_capabilities,
)


def test_catalog_covers_every_published_name() -> None:
    catalog = capability_catalog()

    assert tuple(item["name"] for item in catalog) == Client.capabilities()
    assert all(item["status"] for item in catalog)


def test_chinese_alias_search() -> None:
    names = {item["name"] for item in search_capabilities("龙虎榜")}
    assert "longhu" in names

    names = {item["name"] for item in search_capabilities("资金流")}
    assert "fund_flow" in names


def test_english_name_prefix_wins() -> None:
    records = search_capabilities("bars", limit=5)

    assert records[0]["name"] == "bars"


def test_status_filter() -> None:
    records = search_capabilities("", status="offline", limit=217)

    assert records
    assert {item["status"] for item in records} == {"offline"}


def test_provider_filter() -> None:
    records = search_capabilities("quotes", provider="sina", limit=10)

    assert all("sina" in item["declared_providers"] for item in records)
    assert any(item["name"] == "quotes" for item in records)


def test_describe_core_capability() -> None:
    record = describe_capability("bars")

    assert record["core"] is True
    assert record["migrated"] is False
    assert record["status"] == "alive"
    assert record["default_provider"] == "tdx"
    assert "tdx" in record["providers"]
    assert any(item["channel"] for item in record["channels"])


def test_unknown_capability_is_a_validation_error() -> None:
    with pytest.raises(ValidationError, match="未知 capability"):
        describe_capability("not_a_capability")


def test_cli_search_keeps_the_names_only_shape(capsys: Any) -> None:
    args = SimpleNamespace(
        describe=None,
        query="龙虎榜",
        status=None,
        provider=None,
        limit=50,
    )

    assert cmd_capabilities(args) == 0

    payload = capsys.readouterr().out
    assert '"capabilities"' in payload
    assert "longhu" in payload
