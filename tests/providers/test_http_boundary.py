from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace
from typing import Any

import pytest

from tstdx.errors import ValidationError
from tstdx.providers.http import ProviderBoundHttpClient


class FakeResponse:
    def __init__(self, url: str, *, history: tuple[Any, ...] = ()) -> None:
        self.url = url
        self.history = history


class FakeClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.headers = {"User-Agent": "test"}

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

    with pytest.raises(ValidationError):
        with client.stream("GET", "https://hq.sinajs.cn/list=sh600519"):
            pass

    assert raw.calls == []


def test_unknown_callable_is_not_exposed_through_getattr() -> None:
    raw = FakeClient()
    client = ProviderBoundHttpClient("tencent", raw)

    with pytest.raises(AttributeError, match="unchecked callable"):
        client.unchecked_request("https://push2.eastmoney.com/api")

    assert raw.calls == []
    assert client.headers == {"User-Agent": "test"}


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
