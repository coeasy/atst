from __future__ import annotations

from typing import Any

import pytest

from tstdx.errors import ValidationError
from tstdx.providers.http import ProviderBoundHttpClient
from tstdx.service import ProviderManager


class FakeHttpClient:
    def __init__(self) -> None:
        self.close_calls = 0
        self.calls: list[str] = []

    def get(self, url: str, *args: Any, **kwargs: Any) -> Any:
        self.calls.append(url)
        raise AssertionError("allowed raw HTTP execution is not needed in this test")

    def close(self) -> None:
        self.close_calls += 1


class FakeAdapter:
    instances: list[FakeAdapter] = []

    def __init__(self, *, client: Any, timeout: float, **kwargs: Any) -> None:
        self.client = client
        self.timeout = timeout
        self.close_calls = 0
        self.kwargs = kwargs
        type(self).instances.append(self)

    def close(self) -> None:
        self.close_calls += 1


@pytest.fixture()
def fake_http(monkeypatch: pytest.MonkeyPatch) -> tuple[list[FakeHttpClient], type[FakeAdapter]]:
    from tstdx.web import base as base_mod

    created: list[FakeHttpClient] = []

    def factory(*args: Any, **kwargs: Any) -> FakeHttpClient:
        client = FakeHttpClient()
        created.append(client)
        return client

    monkeypatch.setattr(base_mod, "build_client", factory)
    FakeAdapter.instances = []
    return created, FakeAdapter


def test_same_provider_reuses_one_bound_http_client_across_channels(fake_http) -> None:  # noqa: ANN001
    created, adapter_cls = fake_http
    manager = ProviderManager(timeout=7.5)

    quote = manager.web_adapter("tencent", "quote", adapter_cls)
    bars = manager.web_adapter("tencent", "kline", adapter_cls)

    assert len(created) == 1
    assert quote is not bars
    assert isinstance(quote.client, ProviderBoundHttpClient)
    assert quote.client is bars.client
    assert quote.client is manager.http_client("tencent")
    assert quote.client is not created[0]
    assert quote.timeout == bars.timeout == 7.5

    manager.close()
    assert quote.close_calls == 1
    assert bars.close_calls == 1
    assert created[0].close_calls == 1


def test_different_providers_do_not_share_http_client(fake_http) -> None:  # noqa: ANN001
    created, adapter_cls = fake_http
    manager = ProviderManager()

    tencent = manager.web_adapter("tencent", "quote", adapter_cls)
    sina = manager.web_adapter("sina", "quote", adapter_cls)

    assert len(created) == 2
    assert isinstance(tencent.client, ProviderBoundHttpClient)
    assert isinstance(sina.client, ProviderBoundHttpClient)
    assert tencent.client is not sina.client
    assert tencent.client is not created[0]
    assert sina.client is not created[1]

    manager.close()
    assert created[0].close_calls == 1
    assert created[1].close_calls == 1


def test_manager_bound_client_rejects_cross_provider_url_before_raw_call(fake_http) -> None:  # noqa: ANN001
    created, _ = fake_http
    manager = ProviderManager()
    try:
        client = manager.http_client("tencent")
        assert isinstance(client, ProviderBoundHttpClient)
        assert len(created) == 1

        with pytest.raises(ValidationError):
            client.get("https://push2.eastmoney.com/api")

        assert created[0].calls == []
    finally:
        manager.close()


def test_runtime_resource_key_does_not_create_fake_registry_channel(fake_http) -> None:  # noqa: ANN001
    _, adapter_cls = fake_http
    manager = ProviderManager()

    profile = manager.web_adapter(
        "eastmoney",
        "corporate",
        adapter_cls,
        resource_key="corporate:profile",
    )
    notices = manager.web_adapter(
        "eastmoney",
        "corporate",
        adapter_cls,
        resource_key="corporate:notices",
    )
    profile_again = manager.web_adapter(
        "eastmoney",
        "corporate",
        adapter_cls,
        resource_key="corporate:profile",
    )

    assert profile is profile_again
    assert profile is not notices
    assert profile.client is notices.client
    assert isinstance(profile.client, ProviderBoundHttpClient)
    manager.close()


def test_unknown_channel_is_rejected_before_adapter_creation(fake_http) -> None:  # noqa: ANN001
    _, adapter_cls = fake_http
    manager = ProviderManager()

    with pytest.raises(ValidationError):
        manager.web_adapter("tencent", "fund_flow", adapter_cls)

    assert adapter_cls.instances == []
    manager.close()
