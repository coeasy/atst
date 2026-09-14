from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any

from tstdx.integration import http_app


class FakeApp:
    def __init__(self) -> None:
        self.state = SimpleNamespace()
        self.middlewares: list[Any] = []
        self.exception_handlers: dict[type, Any] = {}
        self.routes: dict[tuple[str, str], Any] = {}

    def get(self, path: str, **_: Any):
        def register(fn):  # noqa: ANN001,ANN202
            self.routes[("GET", path)] = fn
            return fn

        return register

    def middleware(self, kind: str):
        assert kind == "http"

        def register(fn):  # noqa: ANN001,ANN202
            self.middlewares.append(fn)
            return fn

        return register

    def exception_handler(self, exc_type: type):
        def register(fn):  # noqa: ANN001,ANN202
            self.exception_handlers[exc_type] = fn
            return fn

        return register

    def on_event(self, name: str):
        assert name == "shutdown"

        def register(fn):  # noqa: ANN001,ANN202
            return fn

        return register


def _build(monkeypatch):  # noqa: ANN001,ANN202
    def fake_create_app(client):  # noqa: ANN001,ANN202
        app = FakeApp()
        app.state.client = client
        app.state.tasks = http_app._routes.TaskStore(max_tasks=4)
        return app

    monkeypatch.setattr(http_app._routes, "create_app", fake_create_app)
    client = SimpleNamespace(service=object(), close=lambda: None)
    return http_app.create_app(client)


def _request(headers: dict[str, str]) -> Any:
    return SimpleNamespace(
        headers=headers,
        state=SimpleNamespace(),
        url=SimpleNamespace(path="/query"),
    )


def test_official_http_body_size_rejection_uses_canonical_error_envelope(monkeypatch) -> None:  # noqa: ANN001
    app = _build(monkeypatch)
    called = False

    async def call_next(_request):  # noqa: ANN001,ANN202
        nonlocal called
        called = True
        raise AssertionError("oversized request must be rejected before legacy middleware")

    try:
        middleware = app.middlewares[-1]
        request = _request({"content-length": str(http_app._routes.MAX_BODY_BYTES + 1)})
        response = asyncio.run(middleware(request, call_next))
        payload = json.loads(response.body.decode("utf-8"))["error"]

        assert called is False
        assert response.status_code == 413
        assert response.headers["x-request-id"] == payload["request_id"]
        assert payload["code"] == "E1010"
        assert payload["phase"] == "http_validation"
        assert payload["context"]["max_body_bytes"] == http_app._routes.MAX_BODY_BYTES
        assert payload["fallback_allowed"] is False
        assert payload["provider_switch_allowed"] is False
    finally:
        app.state.tasks.close()


def test_official_http_rejects_unknown_size_chunked_body_before_legacy_error(monkeypatch) -> None:  # noqa: ANN001
    app = _build(monkeypatch)

    async def call_next(_request):  # noqa: ANN001,ANN202
        raise AssertionError("chunked body must not reach legacy guard")

    try:
        middleware = app.middlewares[-1]
        request = _request({"transfer-encoding": "chunked"})
        response = asyncio.run(middleware(request, call_next))
        payload = json.loads(response.body.decode("utf-8"))["error"]
        assert response.status_code == 413
        assert payload["phase"] == "http_validation"
        assert payload["context"]["fallback"] is False
    finally:
        app.state.tasks.close()
