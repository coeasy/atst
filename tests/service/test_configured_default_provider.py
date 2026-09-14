from __future__ import annotations

import pytest

from tstdx.config import config_from_dict, reset_config, set_config
from tstdx.planned_service import UnifiedMarketDataService
from tstdx.query import QueryPlanner, QuerySpec


class NoopManager:
    def close(self) -> None:
        pass


def test_service_consumes_global_default_provider_once_at_construction() -> None:
    set_config(config_from_dict({"sources": {"default_provider": "tencent"}}))
    try:
        service = UnifiedMarketDataService(manager=NoopManager())
        first = service.compile(QuerySpec.build("quotes", symbols=["600519"]))
        assert first.provider == "tencent"
        assert first.channel == "quote"

        # A running service owns a frozen composition snapshot; later global
        # config changes must not mutate in-flight routing semantics.
        reset_config()
        second = service.compile(QuerySpec.build("quotes", symbols=["600519"]))
        assert second.provider == "tencent"
    finally:
        service.close()
        reset_config()


def test_explicit_provider_overrides_configured_default() -> None:
    set_config(config_from_dict({"sources": {"default_provider": "tencent"}}))
    try:
        service = UnifiedMarketDataService(manager=NoopManager())
        plan = service.compile(
            QuerySpec.build("quotes", symbols=["600519"], provider="sina")
        )
        assert plan.provider == "sina"
        assert plan.channel == "quote"
    finally:
        service.close()
        reset_config()


def test_explicit_constructor_default_provider_does_not_need_global_mutation() -> None:
    reset_config()
    service = UnifiedMarketDataService(
        manager=NoopManager(),
        default_provider="eastmoney",
    )
    try:
        plan = service.compile(QuerySpec.build("quotes", symbols=["600519"]))
        assert plan.provider == "eastmoney"
        assert plan.channel == "quote"
    finally:
        service.close()


def test_planner_and_default_provider_are_mutually_exclusive() -> None:
    with pytest.raises(ValueError, match="真相源"):
        UnifiedMarketDataService(
            manager=NoopManager(),
            planner=QueryPlanner(default_provider="tdx"),
            default_provider="tencent",
        )
