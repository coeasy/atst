# Copyright (c) 2026 tstdx contributors
# Licensed under the MIT License

"""HTTP transport guard for Provider identity.

Legacy Web adapters predate the v12 Provider model and some of them historically
contained cross-site fallback logic.  A Provider-aware service must not trust
adapter control flow alone.  This module enforces the Provider boundary at the
HTTP client itself: a client created for ``tencent`` cannot request an Eastmoney
or Sina host, even if a legacy adapter attempts to do so.

Host policy is intentionally expressed as suffixes because one Provider may own
multiple equivalent endpoints/CDNs (for example ``qt.gtimg.cn`` and
``web.ifzq.gtimg.cn``).  The guard validates both the requested URL and the final
response/history URLs so redirects cannot silently change provenance.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any
from urllib.parse import urlparse

from ..errors import ValidationError
from . import normalize_provider_id

__all__ = [
    "PROVIDER_HTTP_HOST_SUFFIXES",
    "ProviderBoundHttpClient",
    "host_allowed",
]


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
    "baidu": (
        "baidu.com",
    ),
    "jsl": (
        "jisilu.cn",
    ),
    "boc": (
        "boc.cn",
    ),
    "iwencai": (
        "iwencai.com",
    ),
}


def _hostname(url: Any) -> str:
    if hasattr(url, "host"):
        host = getattr(url, "host")
        if host:
            return str(host).strip(".").lower()
    text = str(url)
    parsed = urlparse(text)
    return (parsed.hostname or "").strip(".").lower()


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

    def request(self, method: str, url: Any, *args: Any, **kwargs: Any) -> Any:
        self._check_url(url, phase="request")
        response = self._client.request(method, url, *args, **kwargs)
        return self._check_response(response)

    def get(self, url: Any, *args: Any, **kwargs: Any) -> Any:
        self._check_url(url, phase="request")
        response = self._client.get(url, *args, **kwargs)
        return self._check_response(response)

    def post(self, url: Any, *args: Any, **kwargs: Any) -> Any:
        self._check_url(url, phase="request")
        response = self._client.post(url, *args, **kwargs)
        return self._check_response(response)

    def put(self, url: Any, *args: Any, **kwargs: Any) -> Any:
        self._check_url(url, phase="request")
        response = self._client.put(url, *args, **kwargs)
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

    def close(self) -> None:
        self._client.close()

    @property
    def raw_client(self) -> Any:
        """Diagnostic/testing access; adapters should use the guarded proxy."""
        return self._client

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
        # Non-request attributes such as headers/cookies/timeout remain available.
        # Methods not explicitly wrapped above are intentionally not considered a
        # safe request path; callers needing another verb should add it here.
        return getattr(self._client, name)
