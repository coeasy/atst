# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""HTTP transport guard for Provider identity.

Legacy Web adapters predate the v12 Provider model and some of them historically
contained cross-site fallback logic. A Provider-aware service must not trust
adapter control flow alone. This module enforces the Provider boundary at the
HTTP client itself: a client created for ``tencent`` cannot request an Eastmoney
or Sina host, even if a legacy adapter attempts to do so.

Host policy is intentionally expressed as suffixes because one Provider may own
multiple equivalent endpoints/CDNs (for example ``qt.gtimg.cn`` and
``web.ifzq.gtimg.cn``). Provider-bound ``get``/``post`` requests use a single-hop
raw transport and follow redirects in this guard, so every ``Location`` is
validated before the next network request is sent. Standalone raw clients keep
their historical redirect behavior.
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator, Mapping
from typing import Any
from urllib.parse import urljoin, urlparse

from ..errors import ValidationError, WebSourceError
from . import normalize_provider_id

__all__ = [
    "PROVIDER_HTTP_HOST_SUFFIXES",
    "ProviderBoundHttpClient",
    "host_allowed",
]

_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
_MAX_REDIRECTS = 5


PROVIDER_HTTP_HOST_SUFFIXES: dict[str, tuple[str, ...]] = {
    "tencent": (
        "gtimg.cn",
        "qq.com",
    ),
    "sina": (
        "sina.com.cn",
        "sina.cn",
        "sinajs.cn",
    ),
    "eastmoney": (
        "eastmoney.com",
        "eastmoney.com.cn",
    ),
    "baidu": ("baidu.com",),
    "jsl": ("jisilu.cn",),
    "boc": (
        "boc.cn",
        "bankofchina.com",
    ),
    "iwencai": ("iwencai.com",),
}


def _hostname(url: Any) -> str:
    if hasattr(url, "host"):
        host = getattr(url, "host")
        if host:
            return str(host).strip(".").lower()
    text = str(url)
    parsed = urlparse(text)
    return (parsed.hostname or "").strip(".").lower()


def _header(headers: Any, name: str) -> str | None:
    if not isinstance(headers, Mapping):
        return None
    wanted = name.lower()
    for key, value in headers.items():
        if str(key).lower() == wanted and value is not None:
            return str(value)
    return None


def host_allowed(provider: str, host: str) -> bool:
    pid = normalize_provider_id(provider)
    normalized = str(host).strip(".").lower()
    suffixes = PROVIDER_HTTP_HOST_SUFFIXES.get(pid, ())
    return bool(normalized) and any(
        normalized == suffix or normalized.endswith(f".{suffix}")
        for suffix in suffixes
    )


class ProviderBoundHttpClient:
    """Proxy an HTTP client while enforcing one Provider's hostname policy."""

    def __init__(self, provider: str, client: Any) -> None:
        self.provider = normalize_provider_id(provider)
        if self.provider not in PROVIDER_HTTP_HOST_SUFFIXES:
            raise ValidationError(
                f"Provider {self.provider!r} 未声明 HTTP host policy",
                context={"provider": self.provider},
            )
        self._client = client

    def _check_url(self, url: Any, *, phase: str) -> None:
        host = _hostname(url)
        if not host_allowed(self.provider, host):
            raise ValidationError(
                f"Provider {self.provider!r} 禁止访问跨 Provider host {host!r}",
                context={
                    "provider": self.provider,
                    "host": host,
                    "phase": phase,
                    "reason": "provider_boundary",
                    "fallback": False,
                },
            )

    def _check_response(self, response: Any) -> Any:
        for prior in getattr(response, "history", ()) or ():
            url = getattr(prior, "url", None)
            if url is not None:
                self._check_url(url, phase="redirect")
        final_url = getattr(response, "url", None)
        if final_url is not None:
            self._check_url(final_url, phase="response")
        return response

    @staticmethod
    def _switch_redirect_to_get(method: str, status: int) -> bool:
        return method not in {"GET", "HEAD"} and status in {301, 302, 303}

    @staticmethod
    def _without_entity_headers(headers: Any) -> dict[str, str] | None:
        if headers is None:
            return None
        values = {
            str(key): str(value)
            for key, value in dict(headers).items()
            if str(key).lower() not in {"content-type", "content-length"}
        }
        return values

    def _single_hop(
        self,
        method: str,
        url: str,
        *,
        headers: Any = None,
        timeout: float = 5.0,
        body: bytes | None = None,
        content_type: str | None = None,
    ) -> Any:
        """Execute exactly one network hop on supported production raw clients."""
        once = getattr(self._client, "_request_once", None)
        if callable(once):
            request_headers = dict(headers or {})
            if content_type:
                request_headers["Content-Type"] = content_type
            elif method == "POST" and "content-type" not in {
                key.lower() for key in request_headers
            }:
                request_headers["Content-Type"] = "application/json"
            return once(
                url,
                method,
                headers=request_headers,
                timeout=float(timeout),
                body=body,
            )

        raw_httpx = getattr(self._client, "_client", None)
        httpx_module = getattr(self._client, "_httpx", None)
        if httpx_module is not None and raw_httpx is not None:
            request_headers = dict(headers or {})
            if content_type:
                request_headers.setdefault("Content-Type", content_type)
            elif method == "POST" and "content-type" not in {
                key.lower() for key in request_headers
            }:
                request_headers["Content-Type"] = "application/json"
            kwargs: dict[str, Any] = {
                "headers": request_headers,
                "timeout": float(timeout),
                "follow_redirects": False,
            }
            if method not in {"GET", "HEAD"}:
                kwargs["content"] = body if body is not None else b""
            try:
                response = raw_httpx.request(method, url, **kwargs)
            except Exception as exc:
                raise WebSourceError(
                    f"httpx 请求失败: {exc}",
                    context={"url": url},
                    cause=exc,
                ) from exc
            from ..web._base_http import HttpResponse

            return HttpResponse(
                response.status_code,
                response.content,
                dict(response.headers),
            )

        raise ValidationError(
            "ProviderBoundHttpClient raw backend 不支持单跳请求；拒绝可能绕过 Provider redirect 边界",
            context={
                "provider": self.provider,
                "phase": "redirect_guard",
                "backend": type(self._client).__name__,
                "fallback": False,
            },
        )

    def _guarded_redirect_request(
        self,
        method: str,
        url: Any,
        *,
        headers: Any = None,
        timeout: float = 5.0,
        body: bytes | None = None,
        content_type: str | None = None,
    ) -> Any:
        current_url = str(url)
        current_method = method.upper()
        current_headers = dict(headers or {})
        current_body = body
        current_content_type = content_type

        for redirect_count in range(_MAX_REDIRECTS + 1):
            self._check_url(
                current_url,
                phase="request" if redirect_count == 0 else "redirect",
            )
            response = self._single_hop(
                current_method,
                current_url,
                headers=current_headers,
                timeout=timeout,
                body=current_body,
                content_type=current_content_type,
            )
            location = _header(getattr(response, "headers", None), "location")
            status = int(getattr(response, "status", 0))
            if status not in _REDIRECT_STATUSES or not location:
                return response
            if redirect_count >= _MAX_REDIRECTS:
                break

            next_url = urljoin(current_url, location)
            self._check_url(next_url, phase="redirect")
            if self._switch_redirect_to_get(current_method, status):
                current_method = "GET"
                current_body = None
                current_content_type = None
                current_headers = self._without_entity_headers(current_headers) or {}
            current_url = next_url

        raise WebSourceError(
            f"Provider {self.provider!r} 重定向次数超过 {_MAX_REDIRECTS} 上限",
            context={
                "provider": self.provider,
                "url": str(url),
                "phase": "redirect_guard",
                "fallback": False,
            },
        )

    def request(self, method: str, url: Any, *args: Any, **kwargs: Any) -> Any:
        if args:
            raise TypeError("ProviderBoundHttpClient.request only accepts keyword request options")
        normalized = str(method).upper()
        if normalized in {"GET", "POST"}:
            return self._guarded_redirect_request(normalized, url, **kwargs)
        self._check_url(url, phase="request")
        response = self._client.request(method, url, **kwargs)
        return self._check_response(response)

    def get(self, url: Any, *args: Any, **kwargs: Any) -> Any:
        if args:
            raise TypeError("ProviderBoundHttpClient.get only accepts keyword request options")
        return self._guarded_redirect_request("GET", url, **kwargs)

    def post(self, url: Any, *args: Any, **kwargs: Any) -> Any:
        if args:
            raise TypeError("ProviderBoundHttpClient.post only accepts keyword request options")
        return self._guarded_redirect_request("POST", url, **kwargs)

    def put(self, url: Any, *args: Any, **kwargs: Any) -> Any:
        self._check_url(url, phase="request")
        response = self._client.put(url, *args, **kwargs)
        return self._check_response(response)

    def patch(self, url: Any, *args: Any, **kwargs: Any) -> Any:
        self._check_url(url, phase="request")
        response = self._client.patch(url, *args, **kwargs)
        return self._check_response(response)

    def delete(self, url: Any, *args: Any, **kwargs: Any) -> Any:
        self._check_url(url, phase="request")
        response = self._client.delete(url, *args, **kwargs)
        return self._check_response(response)

    def head(self, url: Any, *args: Any, **kwargs: Any) -> Any:
        self._check_url(url, phase="request")
        response = self._client.head(url, *args, **kwargs)
        return self._check_response(response)

    def options(self, url: Any, *args: Any, **kwargs: Any) -> Any:
        self._check_url(url, phase="request")
        response = self._client.options(url, *args, **kwargs)
        return self._check_response(response)

    def send(self, request: Any, *args: Any, **kwargs: Any) -> Any:
        self._check_url(getattr(request, "url", None), phase="request")
        response = self._client.send(request, *args, **kwargs)
        return self._check_response(response)

    @contextlib.contextmanager
    def stream(
        self,
        method: str,
        url: Any,
        *args: Any,
        **kwargs: Any,
    ) -> Iterator[Any]:
        self._check_url(url, phase="request")
        with self._client.stream(method, url, *args, **kwargs) as response:
            yield self._check_response(response)

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> ProviderBoundHttpClient:
        enter = getattr(self._client, "__enter__", None)
        if callable(enter):
            enter()
        return self

    def __exit__(self, *exc: Any) -> Any:
        exit_ = getattr(self._client, "__exit__", None)
        if callable(exit_):
            return exit_(*exc)
        self.close()
        return None

    def __getattr__(self, name: str) -> Any:
        # Never expose raw/private transport internals through the guard. Public
        # non-callable state such as ``headers``/``cookies`` remains available to
        # legacy adapters, while every callable transport path must be explicitly
        # wrapped above so URL provenance is checked.
        if name.startswith("_") or name == "raw_client":
            raise AttributeError(name)
        attr = getattr(self._client, name)
        if callable(attr):
            raise AttributeError(
                f"ProviderBoundHttpClient does not expose unchecked callable {name!r}"
            )
        return attr
