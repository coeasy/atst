from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace
from typing import Any

import pytest

from tstdx.errors import ValidationError
from tstdx.providers.http import ProviderBoundHttpClient, host_allowed


class FakeResponse:
    def __init__(self, url: str, *, history: tuple[Any, ...] = ()) -> None:
        self.url = url
        self.history = history


class FakeClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.headers = {"User-Agent": "test"}
        self._transport = object()

    def patch(self, url: str, *args: Any, **kwargs: Any) -> FakeResponse:
        self.calls.append(("patch", url))
        return FakeResponse(url)

    def send(self, request: Any, *args: Any, **kwargs: Any) -> FakeResponse:
        url = str(request.url)
        self.calls.append(("send", url))
        return FakeResponse(url)

    @contextmanager
    def stream(self, method: str, url: str, *args: Any, **kwargs: Any):  # noqa: ANN201
        self.calls.append(("stream", url))
        yield FakeResponse(url)

    def unchecked_request(self, url: str) -> FakeResponse:
        self.calls.append(("unchecked", url))
        return FakeResponse(url)

    def close(self) -> None:
        return None


class SingleHopClient(FakeClient):
    def __init__(self, routes: dict[str, tuple[int, dict[str, str]]]) -> None:
        super().__init__()
        self.routes = routes

    def _request_once(
        self,
        url: str,
        method: str,
        *,
        headers: Any,
        timeout: float,
        body: bytes | None,
    ) -> Any:
        del headers, timeout, body
        self.calls.append((method.lower(), url))
        status, response_headers = self.routes[url]
        return SimpleNamespace(status=status, headers=response_headers, body=b"")


def test_patch_rejects_cross_provider_host_before_raw_call() -> None:
    raw = FakeClient()
    client = ProviderBoundHttpClient("tencent", raw)

    with pytest.raises(ValidationError):
        client.patch("https://push2.eastmoney.com/api")

    assert raw.calls == []


def test_send_rejects_cross_provider_request_object_before_raw_call() -> None:
    raw = FakeClient()
    client = ProviderBoundHttpClient("sina", raw)
    request = SimpleNamespace(url="https://qt.gtimg.cn/q=sh600519")

    with pytest.raises(ValidationError):
        client.send(request)

    assert raw.calls == []


def test_stream_rejects_cross_provider_host_before_context_entry() -> None:
    raw = FakeClient()
    client = ProviderBoundHttpClient("eastmoney", raw)

    with pytest.raises(ValidationError), client.stream("GET", "https://hq.sinajs.cn/list=sh600519"):
        pass

    assert raw.calls == []


def test_unknown_callable_is_not_exposed_through_getattr() -> None:
    raw = FakeClient()
    client = ProviderBoundHttpClient("tencent", raw)

    with pytest.raises(AttributeError, match="unchecked callable"):
        client.unchecked_request("https://push2.eastmoney.com/api")

    assert raw.calls == []
    assert client.headers == {"User-Agent": "test"}


def test_public_guard_does_not_expose_raw_or_private_transport_escape_hatches() -> None:
    client = ProviderBoundHttpClient("tencent", FakeClient())

    with pytest.raises(AttributeError):
        _ = client.raw_client
    with pytest.raises(AttributeError):
        _ = client._transport

    assert client.headers == {"User-Agent": "test"}


def test_cross_provider_redirect_is_rejected_before_second_network_hop() -> None:
    start = "https://qt.gtimg.cn/start"
    forbidden = "https://push2.eastmoney.com/api"
    raw = SingleHopClient(
        {
            start: (302, {"Location": forbidden}),
            forbidden: (200, {}),
        }
    )
    client = ProviderBoundHttpClient("tencent", raw)

    with pytest.raises(ValidationError) as caught:
        client.get(start)

    assert raw.calls == [("get", start)]
    assert caught.value.context["phase"] == "redirect"
    assert caught.value.context["host"] == "push2.eastmoney.com"


def test_same_provider_redirect_is_followed_after_guard_validation() -> None:
    start = "https://qt.gtimg.cn/start"
    redirected = "https://web.ifzq.gtimg.cn/final"
    raw = SingleHopClient(
        {
            start: (302, {"Location": redirected}),
            redirected: (200, {}),
        }
    )
    client = ProviderBoundHttpClient("tencent", raw)

    response = client.get(start)

    assert response.status == 200
    assert raw.calls == [("get", start), ("get", redirected)]


def test_relative_redirect_stays_inside_provider_and_post_302_switches_to_get() -> None:
    start = "https://qt.gtimg.cn/post-start"
    redirected = "https://qt.gtimg.cn/final"
    raw = SingleHopClient(
        {
            start: (302, {"location": "/final"}),
            redirected: (200, {}),
        }
    )
    client = ProviderBoundHttpClient("tencent", raw)

    response = client.post(start, body=b"payload", content_type="application/json")

    assert response.status == 200
    assert raw.calls == [("post", start), ("get", redirected)]


def test_response_redirect_history_cannot_cross_provider_boundary() -> None:
    class RedirectingClient(FakeClient):
        def patch(self, url: str, *args: Any, **kwargs: Any) -> FakeResponse:
            self.calls.append(("patch", url))
            prior = FakeResponse("https://qt.gtimg.cn/q=sh600519")
            return FakeResponse(
                "https://push2.eastmoney.com/api",
                history=(prior,),
            )

    raw = RedirectingClient()
    client = ProviderBoundHttpClient("tencent", raw)

    with pytest.raises(ValidationError) as caught:
        client.patch("https://qt.gtimg.cn/q=sh600519")

    assert raw.calls == [("patch", "https://qt.gtimg.cn/q=sh600519")]
    assert caught.value.context["phase"] == "response"
    assert caught.value.context["provider"] == "tencent"


def test_boc_policy_allows_the_real_bankofchina_endpoint_only_within_boc_provider() -> None:
    assert host_allowed("boc", "srh.bankofchina.com") is True
    assert host_allowed("boc", "www.boc.cn") is True
    assert host_allowed("tencent", "srh.bankofchina.com") is False
